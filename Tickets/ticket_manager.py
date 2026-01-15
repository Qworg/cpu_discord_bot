"""Ticket management business logic.

This module coordinates between the Discord UI, API client, and
permission management to handle ticket operations.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from Tickets.ticket_api_client import (
    APIError,
    AssociationData,
    TicketData,
    get_api_client,
)
from Tickets.ticket_config import MAX_TICKETS_PER_USER
from Tickets.ticket_permissions import (
    add_user_to_ticket,
    close_ticket_channel,
    create_ticket_channel,
    is_ticket_channel,
    reopen_ticket_channel,
    user_is_staff,
)
from Tickets.ticket_views import (
    AssociationSelectView,
    ConfirmCloseView,
    LinkAccountView,
    PrioritySelectView,
    TicketCreateModal,
    TicketListPaginatorView,
    create_close_embed,
    create_reopen_embed,
    create_ticket_embed,
    create_ticket_list_embed,
    create_welcome_embed,
)

if TYPE_CHECKING:
    from discord import Interaction, Member, TextChannel, User

logger = logging.getLogger(__name__)


class TicketManager:
    """Manages ticket operations and coordinates between components."""

    def __init__(self):
        """Initialize the ticket manager."""
        self._pending_tickets: dict[int, dict] = {}  # user_id -> pending ticket data

    async def check_user_linked(self, interaction: Interaction) -> bool:
        """Check if the user's Discord account is linked and prompt to link if not.

        Args:
            interaction: The Discord interaction

        Returns:
            True if linked, False if not linked (and user was prompted)

        """
        api = get_api_client()
        user_id = interaction.user.id

        try:
            link_status = await api.check_discord_link(user_id)

            if link_status.linked:
                return True

            # User is not linked - get OAuth URL and show button
            oauth_url = await api.get_oauth_url(user_id)

            if oauth_url:
                view = LinkAccountView(oauth_url)
                await interaction.response.send_message(
                    "Your Discord account is not linked to larpmanager.\n"
                    "Please click the button below to link your account, "
                    "then try creating a ticket again.",
                    view=view,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Unable to generate account linking URL. "
                    "Please contact an administrator.",
                    ephemeral=True,
                )

            return False

        except APIError as e:
            logger.error(f"Failed to check user link status: {e}")
            await interaction.response.send_message(
                f"Failed to check account status: {e}",
                ephemeral=True,
            )
            return False

    async def check_ticket_limit(self, interaction: Interaction) -> bool:
        """Check if user has reached their ticket limit.

        Args:
            interaction: The Discord interaction

        Returns:
            True if under limit, False if at limit

        """
        if MAX_TICKETS_PER_USER <= 0:
            return True

        api = get_api_client()

        try:
            # Get user's open tickets
            tickets, total = await api.list_tickets(
                discord_creator_id=interaction.user.id,
                status="open",
            )

            # Also check "working" status
            working_tickets, working_total = await api.list_tickets(
                discord_creator_id=interaction.user.id,
                status="working",
            )

            total_open = total + working_total

            if total_open >= MAX_TICKETS_PER_USER:
                await interaction.response.send_message(
                    f"You have reached the maximum number of open tickets "
                    f"({MAX_TICKETS_PER_USER}). Please close an existing ticket "
                    f"before creating a new one.",
                    ephemeral=True,
                )
                return False

            return True

        except APIError as e:
            logger.error(f"Failed to check ticket limit: {e}")
            # Allow ticket creation on error (fail open)
            return True

    async def start_ticket_creation(self, interaction: Interaction) -> None:
        """Start the ticket creation flow.

        This shows the ticket creation modal to the user.

        Args:
            interaction: The Discord interaction

        """
        # Check if user is linked
        if not await self.check_user_linked(interaction):
            return

        # Check ticket limit
        if not await self.check_ticket_limit(interaction):
            return

        # Show modal for ticket subject
        modal = TicketCreateModal(callback=self._on_ticket_subject_submitted)
        await interaction.response.send_modal(modal)

    async def _on_ticket_subject_submitted(
        self,
        interaction: Interaction,
        subject: str,
        content: str,
    ) -> None:
        """Handle ticket subject modal submission.

        Args:
            interaction: The Discord interaction
            subject: The ticket subject
            content: The ticket description

        """
        # Store pending ticket data
        self._pending_tickets[interaction.user.id] = {
            "subject": subject,
            "content": content,
        }

        # Get associations and show selection
        api = get_api_client()

        try:
            associations = await api.get_associations()

            if not associations:
                await interaction.response.send_message(
                    "No associations available. Please contact an administrator.",
                    ephemeral=True,
                )
                return

            # If only one association, skip selection
            if len(associations) == 1:
                await self._create_ticket_with_association(
                    interaction,
                    associations[0].uuid,
                )
                return

            # Show association selection
            view = AssociationSelectView(
                associations,
                callback=self._on_association_selected,
            )

            await interaction.response.send_message(
                "Please select which organization this ticket is for:",
                view=view,
                ephemeral=True,
            )

        except APIError as e:
            logger.error(f"Failed to get associations: {e}")
            await interaction.response.send_message(
                f"Failed to load associations: {e}",
                ephemeral=True,
            )

    async def _on_association_selected(
        self,
        interaction: Interaction,
        association_uuid: str,
    ) -> None:
        """Handle association selection.

        Args:
            interaction: The Discord interaction
            association_uuid: The selected association UUID

        """
        await self._create_ticket_with_association(interaction, association_uuid)

    async def _create_ticket_with_association(
        self,
        interaction: Interaction,
        association_uuid: str,
    ) -> None:
        """Create a ticket with the selected association.

        Args:
            interaction: The Discord interaction
            association_uuid: The association UUID

        """
        # Get pending ticket data
        pending = self._pending_tickets.pop(interaction.user.id, None)
        if not pending:
            await interaction.response.send_message(
                "Ticket creation session expired. Please try again.",
                ephemeral=True,
            )
            return

        subject = pending["subject"]
        content = pending["content"]

        # Defer response since channel creation may take time
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        api = get_api_client()

        try:
            # Create the Discord channel first
            channel = await create_ticket_channel(
                guild=interaction.guild,
                creator=interaction.user,
                subject=subject,
                ticket_uuid="pending",  # Will update topic after API call
                association_name=None,  # Will update after API call
            )

            # Create ticket in API
            ticket = await api.create_ticket(
                association_uuid=association_uuid,
                discord_creator_id=interaction.user.id,
                discord_channel_id=channel.id,
                subject=subject,
                content=content,
            )

            # Update channel topic with real ticket ID
            assoc_name = ticket.association.get("name") if ticket.association else None
            topic = f"Support ticket: {subject}"
            if assoc_name:
                topic += f" | Association: {assoc_name}"
            topic += f" | ID: {ticket.uuid}"

            await channel.edit(topic=topic)

            # Send welcome message to channel
            embed = create_welcome_embed(ticket, interaction.user)
            await channel.send(embed=embed)

            # Send confirmation to user
            await interaction.followup.send(
                f"Ticket created! Please go to {channel.mention} to continue.",
                ephemeral=True,
            )

            logger.info(
                f"Created ticket {ticket.uuid} for user {interaction.user.id} "
                f"in channel {channel.id}"
            )

        except ValueError as e:
            # Channel creation failed due to config issue
            await interaction.followup.send(
                f"Failed to create ticket channel: {e}",
                ephemeral=True,
            )

        except APIError as e:
            logger.error(f"Failed to create ticket in API: {e}")
            # Try to clean up the channel if it was created
            if "channel" in locals():
                try:
                    await channel.delete(reason="Ticket API creation failed")
                except Exception:
                    pass
            await interaction.followup.send(
                f"Failed to create ticket: {e}",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to create channels. "
                "Please contact an administrator.",
                ephemeral=True,
            )

        except discord.HTTPException as e:
            logger.error(f"Discord API error creating ticket: {e}")
            await interaction.followup.send(
                f"Discord error creating ticket: {e}",
                ephemeral=True,
            )

    async def close_ticket(self, interaction: Interaction) -> None:
        """Close a ticket.

        Args:
            interaction: The Discord interaction (must be in a ticket channel)

        """
        channel = interaction.channel

        # Verify this is a ticket channel
        if not is_ticket_channel(channel):
            await interaction.response.send_message(
                "This command can only be used in a ticket channel.",
                ephemeral=True,
            )
            return

        # Get the ticket from API
        api = get_api_client()

        try:
            ticket = await api.get_ticket_by_channel(channel.id)

            if not ticket:
                await interaction.response.send_message(
                    "Could not find ticket data for this channel.",
                    ephemeral=True,
                )
                return

            # Check if user is authorized to close
            is_creator = ticket.discord_creator_id == interaction.user.id
            is_staff = user_is_staff(interaction.user)

            if not is_creator and not is_staff:
                await interaction.response.send_message(
                    "You don't have permission to close this ticket.",
                    ephemeral=True,
                )
                return

            # Show confirmation
            async def on_confirm(confirm_interaction: Interaction) -> None:
                await self._do_close_ticket(confirm_interaction, ticket, channel)

            view = ConfirmCloseView(on_confirm=on_confirm)

            await interaction.response.send_message(
                "Are you sure you want to close this ticket?\n"
                "The conversation transcript will be saved.",
                view=view,
                ephemeral=True,
            )

        except APIError as e:
            logger.error(f"Failed to get ticket for close: {e}")
            await interaction.response.send_message(
                f"Failed to get ticket data: {e}",
                ephemeral=True,
            )

    async def _do_close_ticket(
        self,
        interaction: Interaction,
        ticket: TicketData,
        channel: TextChannel,
    ) -> None:
        """Actually close the ticket after confirmation.

        Args:
            interaction: The Discord interaction
            ticket: The ticket data
            channel: The ticket channel

        """
        api = get_api_client()

        try:
            # Generate transcript
            transcript = await self._generate_transcript(channel)

            # Close in API
            updated_ticket = await api.close_ticket(ticket.uuid, transcript)

            # Close Discord channel
            creator = None
            if ticket.discord_creator_id:
                creator = channel.guild.get_member(ticket.discord_creator_id)

            await close_ticket_channel(channel, creator)

            # Send close message to channel
            embed = create_close_embed(updated_ticket)
            await channel.send(embed=embed)

            # Confirm to user who closed it
            await interaction.followup.send(
                "Ticket has been closed.",
                ephemeral=True,
            )

            logger.info(f"Closed ticket {ticket.uuid}")

        except APIError as e:
            logger.error(f"Failed to close ticket in API: {e}")
            await interaction.followup.send(
                f"Failed to close ticket: {e}",
                ephemeral=True,
            )

    async def reopen_ticket(self, interaction: Interaction) -> None:
        """Reopen a closed ticket.

        Args:
            interaction: The Discord interaction (must be in a ticket channel)

        """
        channel = interaction.channel

        # Verify this is a ticket channel
        if not is_ticket_channel(channel):
            await interaction.response.send_message(
                "This command can only be used in a ticket channel.",
                ephemeral=True,
            )
            return

        # Only staff can reopen
        if not user_is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff members can reopen tickets.",
                ephemeral=True,
            )
            return

        api = get_api_client()

        try:
            ticket = await api.get_ticket_by_channel(channel.id)

            if not ticket:
                await interaction.response.send_message(
                    "Could not find ticket data for this channel.",
                    ephemeral=True,
                )
                return

            if ticket.status != "done":
                await interaction.response.send_message(
                    "This ticket is not closed.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Reopen in API
            updated_ticket = await api.reopen_ticket(ticket.uuid)

            # Reopen Discord channel
            creator = None
            if ticket.discord_creator_id:
                creator = channel.guild.get_member(ticket.discord_creator_id)

            await reopen_ticket_channel(channel, creator)

            # Send reopen message
            embed = create_reopen_embed(updated_ticket)
            await channel.send(embed=embed)

            await interaction.followup.send(
                "Ticket has been reopened.",
                ephemeral=True,
            )

            logger.info(f"Reopened ticket {ticket.uuid}")

        except APIError as e:
            logger.error(f"Failed to reopen ticket: {e}")
            await interaction.followup.send(
                f"Failed to reopen ticket: {e}",
                ephemeral=True,
            )

    async def assign_ticket(
        self,
        interaction: Interaction,
        staff_member: Member,
    ) -> None:
        """Assign a ticket to a staff member.

        Args:
            interaction: The Discord interaction
            staff_member: The staff member to assign

        """
        channel = interaction.channel

        if not is_ticket_channel(channel):
            await interaction.response.send_message(
                "This command can only be used in a ticket channel.",
                ephemeral=True,
            )
            return

        if not user_is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff members can assign tickets.",
                ephemeral=True,
            )
            return

        api = get_api_client()

        try:
            ticket = await api.get_ticket_by_channel(channel.id)

            if not ticket:
                await interaction.response.send_message(
                    "Could not find ticket data for this channel.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Update assignment in API
            await api.update_ticket(
                ticket.uuid,
                assigned_staff_discord_id=staff_member.id,
                status="working",
            )

            # Add staff member to channel if not already
            await add_user_to_ticket(channel, staff_member)

            # Notify in channel
            await channel.send(
                f"Ticket assigned to {staff_member.mention}"
            )

            await interaction.followup.send(
                f"Ticket assigned to {staff_member.display_name}.",
                ephemeral=True,
            )

            logger.info(f"Assigned ticket {ticket.uuid} to {staff_member.id}")

        except APIError as e:
            logger.error(f"Failed to assign ticket: {e}")
            await interaction.followup.send(
                f"Failed to assign ticket: {e}",
                ephemeral=True,
            )

    async def set_priority(
        self,
        interaction: Interaction,
        priority: str,
    ) -> None:
        """Set the priority of a ticket.

        Args:
            interaction: The Discord interaction
            priority: The new priority (low, medium, high)

        """
        channel = interaction.channel

        if not is_ticket_channel(channel):
            await interaction.response.send_message(
                "This command can only be used in a ticket channel.",
                ephemeral=True,
            )
            return

        if not user_is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff members can change ticket priority.",
                ephemeral=True,
            )
            return

        api = get_api_client()

        try:
            ticket = await api.get_ticket_by_channel(channel.id)

            if not ticket:
                await interaction.response.send_message(
                    "Could not find ticket data for this channel.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Update priority in API
            await api.update_ticket(ticket.uuid, priority=priority)

            # Notify in channel
            await channel.send(
                f"Ticket priority changed to **{priority}** by {interaction.user.mention}"
            )

            await interaction.followup.send(
                f"Ticket priority set to {priority}.",
                ephemeral=True,
            )

            logger.info(f"Set ticket {ticket.uuid} priority to {priority}")

        except APIError as e:
            logger.error(f"Failed to set ticket priority: {e}")
            await interaction.followup.send(
                f"Failed to set priority: {e}",
                ephemeral=True,
            )

    async def list_tickets(self, interaction: Interaction) -> None:
        """List open tickets (staff only).

        Args:
            interaction: The Discord interaction

        """
        if not user_is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff members can list tickets.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        api = get_api_client()
        page_size = 10

        async def get_page(page: int) -> tuple[list[TicketData], int, int]:
            """Get a page of tickets."""
            offset = (page - 1) * page_size
            tickets, total = await api.list_tickets(
                status="open",
                limit=page_size,
                offset=offset,
            )

            # Also get working tickets
            working_tickets, working_total = await api.list_tickets(
                status="working",
                limit=page_size,
                offset=max(0, offset - total),
            )

            # Combine for display
            all_tickets = tickets + working_tickets
            total_count = total + working_total
            total_pages = max(1, (total_count + page_size - 1) // page_size)

            return all_tickets[:page_size], total_pages, total_count

        try:
            tickets, total_pages, total_count = await get_page(1)

            if not tickets:
                await interaction.followup.send(
                    "No open tickets found.",
                    ephemeral=True,
                )
                return

            embed = create_ticket_list_embed(tickets, 1, total_pages, total_count)

            if total_pages > 1:
                view = TicketListPaginatorView(get_page=get_page)
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)

        except APIError as e:
            logger.error(f"Failed to list tickets: {e}")
            await interaction.followup.send(
                f"Failed to list tickets: {e}",
                ephemeral=True,
            )

    async def _generate_transcript(self, channel: TextChannel) -> str:
        """Generate a text transcript of a ticket channel.

        Args:
            channel: The ticket channel

        Returns:
            Formatted transcript string

        """
        lines = [
            f"# Ticket Transcript",
            f"Channel: {channel.name}",
            f"Generated: {datetime.utcnow().isoformat()}",
            "",
            "---",
            "",
        ]

        async for message in channel.history(limit=1000, oldest_first=True):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = f"{message.author.name}"
            if message.author.bot:
                author += " [BOT]"

            lines.append(f"[{timestamp}] {author}")
            if message.content:
                lines.append(message.content)
            if message.attachments:
                for att in message.attachments:
                    lines.append(f"[Attachment: {att.filename}]")
            if message.embeds:
                for embed in message.embeds:
                    if embed.title:
                        lines.append(f"[Embed: {embed.title}]")
            lines.append("")

        return "\n".join(lines)


# Global manager instance
_manager: TicketManager | None = None


def get_ticket_manager() -> TicketManager:
    """Get the global ticket manager instance."""
    global _manager
    if _manager is None:
        _manager = TicketManager()
    return _manager
