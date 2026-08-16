"""Minimal entry point to run only the ticket system bot.

This avoids importing the split-out primary bot modules (GameManagement,
Rulebook, Schematics, Tables, Tests) which are not present in this repo.
"""

from __future__ import annotations

import json
import logging

from Shared.bot_instance import cpu_discord_bot
from Shared.Utilities.discord_utilities import CPU_GUILD_ID  # noqa: F401  (kept for parity)
from Tickets import ticket_commands  # noqa: F401  (registers slash commands)
from Tickets.ticket_api_client import APIError
from Tickets.ticket_config import TICKET_GUILD_ID
from Tickets.ticket_reconciliation import reconcile_ticket_channels
from Tickets.ticket_sync import (
    get_channel_resolver,
    get_outbound_relay,
    ticket_sync_loop,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


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

    if not reconcile_ticket_channels.is_running():
        reconcile_ticket_channels.start()

    if not ticket_sync_loop.is_running():
        ticket_sync_loop.start()

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


def main() -> None:
    """Run the ticket bot."""
    with open("Config/config.json", encoding="utf-8") as config_file:
        config = json.load(config_file)

    cpu_discord_bot.run(config["token"])


if __name__ == "__main__":
    main()
