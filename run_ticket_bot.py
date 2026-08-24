"""Minimal entry point to run only the ticket system bot.

This avoids importing the split-out primary bot modules (GameManagement,
Rulebook, Schematics, Tables, Tests) which are not present in this repo.
"""

from __future__ import annotations

import json
import logging
import os

from Shared.bot_instance import cpu_discord_bot
from Shared.Utilities.discord_utilities import CPU_GUILD_ID  # noqa: F401  (kept for parity)
from Tickets import ticket_commands  # noqa: F401  (registers slash commands)
from Tickets.ticket_api_client import APIError
from Tickets.ticket_config import TICKET_GUILD_ID
from Tickets.ticket_reconciliation import reconcile_ticket_channels
from Tickets.ticket_sync import (
    backfill_ticket_channels,
    get_channel_resolver,
    get_outbound_relay,
    ticket_sync_loop,
)
from Tickets.ticket_views import PublicTicketJoinButton

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Guards against re-registering dynamic items on every on_ready (discord.py
# fires it on every reconnect, not just the first connection).
_dynamic_items_registered = False


@cpu_discord_bot.event
async def on_ready() -> None:
    """Sync slash commands and start background loops on startup."""
    guild = cpu_discord_bot.get_guild(TICKET_GUILD_ID)
    if guild is not None:
        await cpu_discord_bot.tree.sync(guild=guild)
        logging.info("Synced ticket commands to guild %s", guild.name)
    else:
        logging.warning("Guild %s not found - commands not synced", TICKET_GUILD_ID)
    logging.info("Ticket bot ready: %s", cpu_discord_bot.user.name)

    global _dynamic_items_registered
    if not _dynamic_items_registered:
        # Re-register the persistent public-ticket Join button so clicks on
        # announcements posted in a previous process still resolve - the
        # channel id lives in the custom_id, not in any Python instance.
        cpu_discord_bot.add_dynamic_items(PublicTicketJoinButton)
        _dynamic_items_registered = True

    if not reconcile_ticket_channels.is_running():
        reconcile_ticket_channels.start()

    if not ticket_sync_loop.is_running():
        ticket_sync_loop.start()

    # Reconnect backfill (6e): one-shot catch-up for messages missed while the
    # bot was offline. Runs in the background after the poll loop is up.
    if not backfill_ticket_channels.is_running():
        backfill_ticket_channels.start()

    # Prime the channel->ticket resolver (best-effort; misses fall back to a
    # per-channel API lookup).
    try:
        await get_channel_resolver().prime()
    except APIError as e:
        logging.warning("Failed to prime channel resolver: %s", e)


@cpu_discord_bot.event
async def on_message(message) -> None:
    """Relay ticket-channel messages to the API (non-fatally)."""
    await get_outbound_relay().handle(message)


@cpu_discord_bot.event
async def on_message_edit(before, after) -> None:
    """Relay edited ticket-channel messages to the API (non-fatally)."""
    await get_outbound_relay().handle_edit(after)


@cpu_discord_bot.event
async def on_message_delete(message) -> None:
    """Relay deleted ticket-channel messages to the API (non-fatally)."""
    await get_outbound_relay().handle_delete(message)


def main() -> None:
    """Run the ticket bot."""
    config: dict = {}
    try:
        with open("Config/config.json", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        logging.info("Config/config.json not found - relying on DISCORD_TOKEN env var")
    except json.JSONDecodeError as e:
        logging.warning("Config/config.json is not valid JSON (%s) - ignoring it", e)

    token = os.environ.get("DISCORD_TOKEN") or config.get("token")
    if not token:
        raise SystemExit(
            "No Discord bot token found. Set the DISCORD_TOKEN environment "
            "variable or add a \"token\" key to Config/config.json."
        )

    cpu_discord_bot.run(token)


if __name__ == "__main__":
    main()
