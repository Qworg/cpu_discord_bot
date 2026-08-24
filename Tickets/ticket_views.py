"""Discord UI components for the ticketing system.

This module provides:
- Modals for ticket creation
- Select menus for association selection
- Buttons for various ticket actions
- Embeds for displaying ticket information
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Callable

import discord
from discord import ui

from Tickets.ticket_api_client import AssociationData, TicketData, get_api_client
from Tickets.ticket_config import (
    DEFAULT_EMBED_COLOR,
    MAX_SUBJECT_LENGTH,
    PRIORITY_COLORS,
    STATUS_COLORS,
    TICKET_CLOSE_MESSAGE,
    TICKET_REOPEN_MESSAGE,
    TICKET_STAFF_ROLE_IDS,
    TICKET_WELCOME_MESSAGE,
)

if TYPE_CHECKING:
    from discord import Interaction

logger = logging.getLogger(__name__)


# =============================================================================
# Embeds
# =============================================================================


def create_ticket_embed(
    ticket: TicketData,
    title: str = "Support Ticket",
) -> discord.Embed:
    """Create an embed displaying ticket information.

    Args:
        ticket: Ticket data
        title: Embed title

    Returns:
        Discord Embed object

    """
    color = STATUS_COLORS.get(ticket.status, DEFAULT_EMBED_COLOR)
    embed = discord.Embed(
        title=title,
        color=color,
    )

    if ticket.subject:
        embed.add_field(name="Subject", value=ticket.subject, inline=False)

    embed.add_field(name="Status", value=ticket.status.capitalize(), inline=True)
    embed.add_field(name="Priority", value=ticket.priority.capitalize(), inline=True)

    if ticket.association:
        embed.add_field(
            name="Association",
            value=ticket.association.get("name", "Unknown"),
            inline=True,
        )

    if ticket.assigned_staff_discord_id:
        embed.add_field(
            name="Assigned To",
            value=f"<@{ticket.assigned_staff_discord_id}>",
            inline=True,
        )

    if ticket.member:
        embed.add_field(
            name="Member",
            value=ticket.member.get("name", "Unknown"),
            inline=True,
        )

    embed.set_footer(text=f"Ticket ID: {ticket.uuid}")

    return embed


def create_welcome_embed(
    ticket: TicketData,
    creator: discord.Member | discord.User,
) -> discord.Embed:
    """Create a welcome embed for a new ticket.

    Args:
        ticket: Ticket data
        creator: The ticket creator

    Returns:
        Discord Embed object

    """
    embed = discord.Embed(
        title="Support Ticket Created",
        description=TICKET_WELCOME_MESSAGE.format(
            subject=ticket.subject or "No subject",
            creator=creator.mention,
            association=ticket.association.get("name", "Unknown") if ticket.association else "Unknown",
        ),
        color=STATUS_COLORS.get("open", DEFAULT_EMBED_COLOR),
    )

    embed.add_field(name="Status", value="Open", inline=True)
    embed.add_field(name="Priority", value=ticket.priority.capitalize(), inline=True)
    embed.set_footer(text=f"Ticket ID: {ticket.uuid}")

    staff_mentions = " ".join(
        f"<@&{role_id}>" for role_id in sorted(TICKET_STAFF_ROLE_IDS)
    )
    if staff_mentions:
        embed.add_field(name="Available Staff", value=staff_mentions, inline=False)

    return embed


def create_public_ticket_embed(
    ticket: TicketData,
    creator: discord.Member | discord.User,
) -> discord.Embed:
    """Create the public announcement embed for a public ticket."""
    embed = discord.Embed(
        title=f"Public ticket: {ticket.subject or 'No subject'}",
        description=ticket.content or "Join to participate.",
        color=DEFAULT_EMBED_COLOR,
    )
    embed.add_field(name="Status", value="Open", inline=True)
    embed.add_field(name="Priority", value=(ticket.priority or "low").capitalize(), inline=True)
    if ticket.association:
        embed.add_field(name="Organization", value=ticket.association.get("name", ""), inline=True)
    embed.set_footer(text=f"Ticket ID: {ticket.uuid} | Click Join to participate.")
    return embed


def create_close_embed(ticket: TicketData) -> discord.Embed:
    """Create an embed for a closed ticket.

    Args:
        ticket: Ticket data

    Returns:
        Discord Embed object

    """
    embed = discord.Embed(
        title="Ticket Closed",
        description=TICKET_CLOSE_MESSAGE,
        color=STATUS_COLORS.get("done", DEFAULT_EMBED_COLOR),
    )

    embed.add_field(name="Subject", value=ticket.subject or "No subject", inline=False)
    embed.set_footer(text=f"Ticket ID: {ticket.uuid}")

    return embed


def create_reopen_embed(ticket: TicketData) -> discord.Embed:
    """Create an embed for a reopened ticket.

    Args:
        ticket: Ticket data

    Returns:
        Discord Embed object

    """
    embed = discord.Embed(
        title="Ticket Reopened",
        description=TICKET_REOPEN_MESSAGE,
        color=STATUS_COLORS.get("open", DEFAULT_EMBED_COLOR),
    )

    embed.add_field(name="Subject", value=ticket.subject or "No subject", inline=False)
    embed.add_field(name="Status", value="Open", inline=True)
    embed.set_footer(text=f"Ticket ID: {ticket.uuid}")

    return embed


def create_ticket_list_embed(
    tickets: list[TicketData],
    page: int,
    total_pages: int,
    total_count: int,
) -> discord.Embed:
    """Create an embed for a paginated ticket list.

    Args:
        tickets: List of tickets for this page
        page: Current page number (1-indexed)
        total_pages: Total number of pages
        total_count: Total number of tickets

    Returns:
        Discord Embed object

    """
    embed = discord.Embed(
        title="Open Tickets",
        description=f"Showing {len(tickets)} of {total_count} tickets",
        color=DEFAULT_EMBED_COLOR,
    )

    for ticket in tickets:
        status_emoji = {
            "open": ":green_circle:",
            "working": ":orange_circle:",
            "done": ":white_circle:",
        }.get(ticket.status, ":grey_question:")

        priority_emoji = {
            "high": ":red_circle:",
            "medium": ":orange_circle:",
            "low": ":green_circle:",
        }.get(ticket.priority, "")

        field_name = f"{status_emoji} {ticket.subject or 'No subject'}"
        field_value = f"ID: `{ticket.uuid[:8]}` | Priority: {priority_emoji} {ticket.priority}"

        if ticket.discord_channel_id:
            field_value += f"\nChannel: <#{ticket.discord_channel_id}>"

        if ticket.assigned_staff_discord_id:
            field_value += f"\nAssigned: <@{ticket.assigned_staff_discord_id}>"

        embed.add_field(name=field_name, value=field_value, inline=False)

    embed.set_footer(text=f"Page {page}/{total_pages}")

    return embed


# =============================================================================
# Modals
# =============================================================================


class TicketCreateModal(ui.Modal, title="Create Support Ticket"):
    """Modal for entering ticket subject."""

    subject = ui.TextInput(
        label="Subject",
        placeholder="Brief description of your issue",
        max_length=MAX_SUBJECT_LENGTH,
        required=True,
    )

    content = ui.TextInput(
        label="Description (optional)",
        placeholder="Provide more details about your issue",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=False,
    )

    def __init__(self, callback: Callable[[Interaction, str, str], None]):
        """Initialize the modal.

        Args:
            callback: Async function to call with (interaction, subject, content)

        """
        super().__init__()
        self._callback = callback

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission."""
        await self._callback(
            interaction,
            self.subject.value,
            self.content.value or "",
        )


# =============================================================================
# Select Menus
# =============================================================================


class AssociationSelect(ui.Select):
    """Dropdown for selecting an association."""

    def __init__(
        self,
        associations: list[AssociationData],
        callback: Callable[[Interaction, str], None],
    ):
        """Initialize the select menu.

        Args:
            associations: List of available associations
            callback: Async function to call with (interaction, association_uuid)

        """
        options = [
            discord.SelectOption(
                label=assoc.name[:100],  # Discord limit
                value=assoc.uuid,
                description=f"Slug: {assoc.slug}"[:100],
            )
            for assoc in associations[:25]  # Discord limit of 25 options
        ]

        super().__init__(
            placeholder="Select an association...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._callback = callback

    async def callback(self, interaction: Interaction) -> None:
        """Handle selection."""
        await self._callback(interaction, self.values[0])


class AssociationSelectView(ui.View):
    """View containing the association select dropdown."""

    def __init__(
        self,
        associations: list[AssociationData],
        callback: Callable[[Interaction, str], None],
        timeout: float = 180.0,
    ):
        """Initialize the view.

        Args:
            associations: List of available associations
            callback: Async function to call with (interaction, association_uuid)
            timeout: View timeout in seconds

        """
        super().__init__(timeout=timeout)
        self.add_item(AssociationSelect(associations, callback))

    async def on_timeout(self) -> None:
        """Handle view timeout."""
        # Disable all items
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True


class PrioritySelect(ui.Select):
    """Dropdown for selecting ticket priority."""

    def __init__(self, callback: Callable[[Interaction, str], None]):
        """Initialize the select menu.

        Args:
            callback: Async function to call with (interaction, priority)

        """
        options = [
            discord.SelectOption(
                label="Low",
                value="low",
                description="Non-urgent issue",
                emoji=":green_circle:",
            ),
            discord.SelectOption(
                label="Medium",
                value="medium",
                description="Standard priority",
                emoji=":orange_circle:",
            ),
            discord.SelectOption(
                label="High",
                value="high",
                description="Urgent issue requiring immediate attention",
                emoji=":red_circle:",
            ),
        ]

        super().__init__(
            placeholder="Select priority...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._callback = callback

    async def callback(self, interaction: Interaction) -> None:
        """Handle selection."""
        await self._callback(interaction, self.values[0])


class PrioritySelectView(ui.View):
    """View containing the priority select dropdown."""

    def __init__(
        self,
        callback: Callable[[Interaction, str], None],
        timeout: float = 180.0,
    ):
        """Initialize the view.

        Args:
            callback: Async function to call with (interaction, priority)
            timeout: View timeout in seconds

        """
        super().__init__(timeout=timeout)
        self.add_item(PrioritySelect(callback))


# =============================================================================
# Buttons
# =============================================================================


class LinkAccountButton(ui.Button):
    """Button that opens the OAuth link URL."""

    def __init__(self, oauth_url: str):
        """Initialize the button.

        Args:
            oauth_url: OAuth URL to open

        """
        super().__init__(
            label="Link Account",
            style=discord.ButtonStyle.link,
            url=oauth_url,
        )


class LinkAccountView(ui.View):
    """View containing the link account button."""

    def __init__(self, oauth_url: str):
        """Initialize the view.

        Args:
            oauth_url: OAuth URL for linking

        """
        super().__init__(timeout=None)  # Link buttons don't need timeout
        self.add_item(LinkAccountButton(oauth_url))


PUBLIC_TICKET_JOIN_CUSTOM_ID_TEMPLATE = r"ticket_join:(?P<channel_id>\d+)"


class PublicTicketJoinButton(
    ui.DynamicItem[ui.Button],
    template=PUBLIC_TICKET_JOIN_CUSTOM_ID_TEMPLATE,
):
    """Join button granting the clicking user access to the ticket channel.

    Uses a dynamic ``custom_id`` (``ticket_join:<channel_id>``) so the button
    keeps working after a bot restart: Discord dispatches any click on a
    matching custom_id to this class (via `from_custom_id`) even for messages
    the running process never itself constructed a view for, so the channel
    id is always parsed from the custom_id at click time rather than read off
    `self`/instance state, which does not survive a restart. The class must be
    registered once via `bot.add_dynamic_items()` (see run_ticket_bot.py).
    """

    def __init__(self, channel_id: int) -> None:
        super().__init__(
            ui.Button(
                label="Join",
                style=discord.ButtonStyle.primary,
                custom_id=f"ticket_join:{channel_id}",
            )
        )
        self.channel_id = channel_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Item,
        match: re.Match[str],
        /,
    ) -> "PublicTicketJoinButton":
        """Reconstruct the button from a clicked custom_id (no instance state needed)."""
        return cls(int(match["channel_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:  # noqa: D102
        from Tickets.ticket_permissions import add_user_to_ticket

        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if channel is None:
            await interaction.response.send_message(
                "This ticket channel no longer exists.", ephemeral=True
            )
            return

        await add_user_to_ticket(channel, interaction.user)
        await interaction.response.send_message(
            f"Joined! Head to {channel.mention}.", ephemeral=True
        )


class PublicTicketJoinView(ui.View):
    """View containing the Join button for a public ticket announcement."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.add_item(PublicTicketJoinButton(channel_id))


class ConfirmCloseView(ui.View):
    """View for confirming ticket closure."""

    def __init__(
        self,
        on_confirm: Callable[[Interaction], None],
        on_cancel: Callable[[Interaction], None] | None = None,
        timeout: float = 60.0,
    ):
        """Initialize the view.

        Args:
            on_confirm: Async function to call on confirm
            on_cancel: Async function to call on cancel (optional)
            timeout: View timeout in seconds

        """
        super().__init__(timeout=timeout)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

    @ui.button(label="Confirm Close", style=discord.ButtonStyle.danger)
    async def confirm_button(
        self,
        interaction: Interaction,
        button: ui.Button,
    ) -> None:
        """Handle confirm button click."""
        # Disable buttons
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True
        await interaction.response.edit_message(view=self)

        await self._on_confirm(interaction)
        self.stop()

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self,
        interaction: Interaction,
        button: ui.Button,
    ) -> None:
        """Handle cancel button click."""
        # Disable buttons
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True
        await interaction.response.edit_message(view=self)

        if self._on_cancel:
            await self._on_cancel(interaction)
        self.stop()


class TicketListPaginatorView(ui.View):
    """View for paginating through ticket list."""

    def __init__(
        self,
        get_page: Callable[[int], tuple[list[TicketData], int, int]],
        timeout: float = 300.0,
    ):
        """Initialize the view.

        Args:
            get_page: Async function that returns (tickets, total_pages, total_count) for a page
            timeout: View timeout in seconds

        """
        super().__init__(timeout=timeout)
        self._get_page = get_page
        self._current_page = 1

    @ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self,
        interaction: Interaction,
        button: ui.Button,
    ) -> None:
        """Handle previous button click."""
        if self._current_page > 1:
            self._current_page -= 1
            await self._update_page(interaction)

    @ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(
        self,
        interaction: Interaction,
        button: ui.Button,
    ) -> None:
        """Handle next button click."""
        self._current_page += 1
        await self._update_page(interaction)

    async def _update_page(self, interaction: Interaction) -> None:
        """Update the embed with the current page."""
        tickets, total_pages, total_count = await self._get_page(self._current_page)

        # Clamp to valid page
        if self._current_page > total_pages:
            self._current_page = total_pages

        embed = create_ticket_list_embed(
            tickets,
            self._current_page,
            total_pages,
            total_count,
        )

        # Update button states
        self.previous_button.disabled = self._current_page <= 1
        self.next_button.disabled = self._current_page >= total_pages

        await interaction.response.edit_message(embed=embed, view=self)
