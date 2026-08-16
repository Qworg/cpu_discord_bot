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
from Tickets.ticket_config import TICKET_GUILD_ID
from Tickets.ticket_reconciliation import reconcile_ticket_channels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


@cpu_discord_bot.event
async def on_ready() -> None:
    """Sync slash commands to the configured guild on startup."""
    guild = cpu_discord_bot.get_guild(TICKET_GUILD_ID)
    if guild is not None:
        await cpu_discord_bot.tree.sync(guild=guild)
        logging.info("Synced ticket commands to guild %s", guild.name)
    else:
        logging.warning("Guild %s not found - commands not synced", TICKET_GUILD_ID)
    logging.info("Ticket bot ready: %s", cpu_discord_bot.user.name)

    if not reconcile_ticket_channels.is_running():
        reconcile_ticket_channels.start()


def main() -> None:
    """Run the ticket bot."""
    with open("Config/config.json", encoding="utf-8") as config_file:
        config = json.load(config_file)

    cpu_discord_bot.run(config["token"])


if __name__ == "__main__":
    main()
