"""Audit logging for ticket bot command denials.

Only *observable* permission denials are recorded here (see
requirementsB.plan.md S0.3 / SW3): non-staff users attempting staff-only
ticket commands. Discord-native channel-access denial is platform-enforced and
unobservable to the bot, so it is intentionally not logged.

Each denial is emitted as a structured log line and appended to a JSON-lines
file in the bot working directory.
"""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# JSON-lines audit log path. Overridable via TICKET_AUDIT_LOG_PATH (tests).
DEFAULT_AUDIT_LOG_PATH = os.path.join(os.getcwd(), "ticket_denials.jsonl")


def _audit_log_path() -> str:
    """Return the path of the denial audit log file."""
    return os.environ.get("TICKET_AUDIT_LOG_PATH", DEFAULT_AUDIT_LOG_PATH)


def log_denial(interaction, command: str, reason: str) -> None:
    """Record an observable permission denial for a bot command.

    Args:
        interaction: The Discord interaction that was denied.
        command: The slash command, e.g. ``ticket close``.
        reason: Human-readable reason the command was denied.

    """
    user = getattr(interaction, "user", None)
    guild = getattr(interaction, "guild", None)
    channel = getattr(interaction, "channel", None)

    record = {
        "event": "command_denied",
        "timestamp": time.time(),
        "user_id": getattr(user, "id", None),
        "user_name": getattr(user, "display_name", None),
        "guild_id": getattr(guild, "id", None),
        "channel_id": getattr(channel, "id", None),
        "command": command,
        "reason": reason,
    }

    logger.info(
        "command denied: user_id=%s guild_id=%s command=%s reason=%s",
        record["user_id"],
        record["guild_id"],
        command,
        reason,
    )

    try:
        with open(_audit_log_path(), "a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(record) + "\n")
    except OSError as error:
        logger.error("Failed to write denial audit log: %s", error)
