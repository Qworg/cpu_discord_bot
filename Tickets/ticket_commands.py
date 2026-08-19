"""Discord slash commands for the ticketing system.

This module registers all ticket-related slash commands with the bot.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from Shared.bot_instance import cpu_discord_bot
from Shared.Utilities.discord_utilities import send_message_safe
from Tickets.ticket_api_client import get_api_client
from Tickets.ticket_audit import log_denial
from Tickets.ticket_config import TICKET_GUILD_ID
from Tickets.ticket_manager import get_ticket_manager
from Tickets.ticket_views import LinkAccountView

logger = logging.getLogger(__name__)

# Guild object for command registration
CPU_GUILD = discord.Object(id=TICKET_GUILD_ID)


# =============================================================================
# Main Ticket Command Group
# =============================================================================


@cpu_discord_bot.tree.command(
    name="ticket",
    description="Support ticket management",
    guild=CPU_GUILD,
)
@app_commands.describe(
    action="The action to perform",
    user="User to assign (for assign action)",
    priority="Priority level (for priority action)",
    ticket_type="Ticket visibility (for create action)",
)
@app_commands.choices(action=[
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="close", value="close"),
    app_commands.Choice(name="assign", value="assign"),
    app_commands.Choice(name="list", value="list"),
    app_commands.Choice(name="priority", value="priority"),
    app_commands.Choice(name="reopen", value="reopen"),
])
@app_commands.choices(priority=[
    app_commands.Choice(name="low", value="low"),
    app_commands.Choice(name="medium", value="medium"),
    app_commands.Choice(name="high", value="high"),
])
@app_commands.choices(ticket_type=[
    app_commands.Choice(name="private", value="private"),
    app_commands.Choice(name="public", value="public"),
])
async def ticket_command(
    interaction: discord.Interaction,
    action: str,
    user: discord.Member | None = None,
    priority: str | None = None,
    ticket_type: str = "private",
) -> None:
    """Main ticket command handler.

    Args:
        interaction: The Discord interaction
        action: The action to perform (create, close, assign, list, priority, reopen)
        user: User to assign (for assign action)
        priority: Priority level (for priority action)
        ticket_type: Ticket visibility (private or public, for create action)

    """
    manager = get_ticket_manager()

    try:
        if action == "create":
            await manager.start_ticket_creation(interaction, ticket_type=ticket_type)

        elif action == "close":
            await manager.close_ticket(interaction)

        elif action == "assign":
            if not user:
                await send_message_safe(
                    interaction,
                    "Please specify a user to assign: `/ticket assign @user`",
                    ephemeral=True,
                )
                return
            await manager.assign_ticket(interaction, user)

        elif action == "list":
            await manager.list_tickets(interaction)

        elif action == "priority":
            if not priority:
                await send_message_safe(
                    interaction,
                    "Please specify a priority: `/ticket priority low/medium/high`",
                    ephemeral=True,
                )
                return
            await manager.set_priority(interaction, priority)

        elif action == "reopen":
            await manager.reopen_ticket(interaction)

        else:
            await send_message_safe(
                interaction,
                f"Unknown action: {action}",
                ephemeral=True,
            )

    except Exception as e:
        logger.exception(f"Error handling ticket command: {e}")
        await send_message_safe(
            interaction,
            f"An error occurred: {e}",
            ephemeral=True,
        )


# =============================================================================
# Link Account Command
# =============================================================================


@cpu_discord_bot.tree.command(
    name="link",
    description="Link your Discord account to larpmanager",
    guild=CPU_GUILD,
)
async def link_command(interaction: discord.Interaction) -> None:
    """Link account command handler.

    Args:
        interaction: The Discord interaction

    """
    api = get_api_client()

    try:
        # Check if already linked
        link_status = await api.check_discord_link(interaction.user.id)

        if link_status.linked:
            await send_message_safe(
                interaction,
                f"Your Discord account is already linked to **{link_status.member_name}**.",
                ephemeral=True,
            )
            return

        # Get OAuth URL
        oauth_url = await api.get_oauth_url(interaction.user.id)

        if oauth_url:
            view = LinkAccountView(oauth_url)
            await send_message_safe(
                interaction,
                "Click the button below to link your Discord account to larpmanager:",
                ephemeral=True,
                view=view,
            )
        else:
            await send_message_safe(
                interaction,
                "Unable to generate account linking URL. Please contact an administrator.",
                ephemeral=True,
            )

    except Exception as e:
        logger.exception(f"Error handling link command: {e}")
        await send_message_safe(
            interaction,
            f"An error occurred: {e}",
            ephemeral=True,
        )


# =============================================================================
# Ticket Info Command (for getting info about current ticket)
# =============================================================================


@cpu_discord_bot.tree.command(
    name="ticketinfo",
    description="Get information about the current ticket",
    guild=CPU_GUILD,
)
async def ticket_info_command(interaction: discord.Interaction) -> None:
    """Get ticket info command handler.

    Args:
        interaction: The Discord interaction

    """
    from Tickets.ticket_permissions import is_ticket_channel
    from Tickets.ticket_views import create_ticket_embed

    channel = interaction.channel

    if not is_ticket_channel(channel):
        await send_message_safe(
            interaction,
            "This command can only be used in a ticket channel.",
            ephemeral=True,
        )
        return

    api = get_api_client()

    try:
        ticket = await api.get_ticket_by_channel(channel.id)

        if not ticket:
            await send_message_safe(
                interaction,
                "Could not find ticket data for this channel.",
                ephemeral=True,
            )
            return

        embed = create_ticket_embed(ticket, title="Ticket Information")
        await send_message_safe(interaction, "", ephemeral=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.exception(f"Error handling ticketinfo command: {e}")
        await send_message_safe(
            interaction,
            f"An error occurred: {e}",
            ephemeral=True,
        )


# =============================================================================
# Add User to Ticket Command
# =============================================================================


@cpu_discord_bot.tree.command(
    name="ticketadd",
    description="Add a user to the current ticket",
    guild=CPU_GUILD,
)
@app_commands.describe(user="The user to add to this ticket")
async def ticket_add_command(
    interaction: discord.Interaction,
    user: discord.Member,
) -> None:
    """Add user to ticket command handler.

    Args:
        interaction: The Discord interaction
        user: The user to add

    """
    from Tickets.ticket_permissions import (
        add_user_to_ticket,
        is_ticket_channel,
        user_is_staff,
    )

    channel = interaction.channel

    if not is_ticket_channel(channel):
        await send_message_safe(
            interaction,
            "This command can only be used in a ticket channel.",
            ephemeral=True,
        )
        return

    if not user_is_staff(interaction.user):
        log_denial(interaction, "ticketadd", "not staff")
        await send_message_safe(
            interaction,
            "Only staff members can add users to tickets.",
            ephemeral=True,
        )
        return

    try:
        await add_user_to_ticket(channel, user)
        await channel.send(f"{user.mention} has been added to this ticket.")
        await send_message_safe(
            interaction,
            f"Added {user.display_name} to the ticket.",
            ephemeral=True,
        )

    except Exception as e:
        logger.exception(f"Error adding user to ticket: {e}")
        await send_message_safe(
            interaction,
            f"Failed to add user: {e}",
            ephemeral=True,
        )


# =============================================================================
# Remove User from Ticket Command
# =============================================================================


@cpu_discord_bot.tree.command(
    name="ticketremove",
    description="Remove a user from the current ticket",
    guild=CPU_GUILD,
)
@app_commands.describe(user="The user to remove from this ticket")
async def ticket_remove_command(
    interaction: discord.Interaction,
    user: discord.Member,
) -> None:
    """Remove user from ticket command handler.

    Args:
        interaction: The Discord interaction
        user: The user to remove

    """
    from Tickets.ticket_permissions import (
        is_ticket_channel,
        remove_user_from_ticket,
        user_is_staff,
    )

    channel = interaction.channel

    if not is_ticket_channel(channel):
        await send_message_safe(
            interaction,
            "This command can only be used in a ticket channel.",
            ephemeral=True,
        )
        return

    if not user_is_staff(interaction.user):
        log_denial(interaction, "ticketremove", "not staff")
        await send_message_safe(
            interaction,
            "Only staff members can remove users from tickets.",
            ephemeral=True,
        )
        return

    try:
        await remove_user_from_ticket(channel, user)
        await channel.send(f"{user.mention} has been removed from this ticket.")
        await send_message_safe(
            interaction,
            f"Removed {user.display_name} from the ticket.",
            ephemeral=True,
        )

    except Exception as e:
        logger.exception(f"Error removing user from ticket: {e}")
        await send_message_safe(
            interaction,
            f"Failed to remove user: {e}",
            ephemeral=True,
        )


logger.info("Ticket commands registered")
