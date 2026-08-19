"""Ticket system configuration.

This module contains all configuration constants for the ticketing system.
Update these values to match your Discord server setup.
"""
import json
import os

from Shared.Utilities.discord_utilities import (
    ADMIN_INDY_ROLE_ID,
    ADMIN_SEATTLE_ROLE_ID,
    CPU_GUILD_ID,
)

# Load additional config from config.json if available
_config_dir = os.path.join(os.getcwd(), "Config")
_config_path = os.path.join(_config_dir, "config.json")
_config = {}

if os.path.exists(_config_path):
    with open(_config_path) as f:
        _config = json.load(f)


# =============================================================================
# Discord Server Configuration
# =============================================================================

# The guild (server) where tickets will be created
# Configurable via config.json "guild_id" (falls back to the CPU guild)
TICKET_GUILD_ID = _config.get("guild_id", CPU_GUILD_ID)

# Category ID where ticket channels will be created
# NOTE: You need to create a "Tickets" category in your Discord server
# and put the ID here. To get a category ID:
# 1. Enable Developer Mode in Discord (Settings > Advanced > Developer Mode)
# 2. Right-click the category and select "Copy ID"
TICKET_CATEGORY_ID = _config.get("ticket_category_id", None)

# Archive category for closed tickets (optional - if None, tickets stay in place)
TICKET_ARCHIVE_CATEGORY_ID = _config.get("ticket_archive_category_id", None)

# Channel where public tickets are announced (with a Join button). If None,
# the announcement is posted in the channel where the command was run.
PUBLIC_TICKET_CHANNEL_ID = _config.get("public_ticket_channel_id", None)


# =============================================================================
# Role Configuration
# =============================================================================

# Staff roles that can manage tickets (see all tickets, assign, close, etc.)
# Configurable via config.json "staff_role_ids" (falls back to CPU admin roles)
TICKET_STAFF_ROLE_IDS = set(
    _config.get("staff_role_ids", [ADMIN_INDY_ROLE_ID, ADMIN_SEATTLE_ROLE_ID])
)

# Role IDs that get automatically added to new ticket channels (optional)
# These users will be able to see all tickets
TICKET_VIEWER_ROLE_IDS = set(_config.get("ticket_viewer_role_ids", []))


# =============================================================================
# larpmanager API Configuration
# =============================================================================

# Base URL for the larpmanager API
LARPMANAGER_API_URL = os.environ.get("LARPMANAGER_API_URL", _config.get("larpmanager_api_url", "http://localhost:8000"))

# API key for authenticating with larpmanager
# This should be a PublisherApiKey created in larpmanager admin
LARPMANAGER_API_KEY = os.environ.get("LARPMANAGER_API_KEY", _config.get("larpmanager_api_key", ""))

# Request timeout in seconds
API_TIMEOUT = _config.get("api_timeout", 30)


# =============================================================================
# Ticket Settings
# =============================================================================

# Maximum subject length for ticket names
MAX_SUBJECT_LENGTH = 100

# Channel name prefix (ticket channels will be named: {prefix}-{subject_slug}-{short_id})
TICKET_CHANNEL_PREFIX = "ticket"

# Maximum open tickets per user (0 = unlimited)
MAX_TICKETS_PER_USER = _config.get("max_tickets_per_user", 5)


# =============================================================================
# Outbox Sync Settings
# =============================================================================

# Base poll interval (seconds) for the ticket outbox sync loop. The loop backs
# off adaptively after consecutive empty polls and resets to this on activity.
TICKET_POLL_SECONDS = float(
    os.environ.get(
        "TICKET_POLL_SECONDS",
        _config.get("ticket_poll_seconds", 3),
    )
)

# Maximum number of events fetched per outbox poll.
TICKET_POLL_LIMIT = int(
    os.environ.get(
        "TICKET_POLL_LIMIT",
        _config.get("ticket_poll_limit", 100),
    )
)

# Maximum apply attempts per event before it is surfaced as failed.
TICKET_SYNC_MAX_ATTEMPTS = int(
    os.environ.get(
        "TICKET_SYNC_MAX_ATTEMPTS",
        _config.get("ticket_sync_max_attempts", 5),
    )
)

# Auto-close inactive tickets after X hours (0 = disabled)
AUTO_CLOSE_HOURS = _config.get("auto_close_hours", 0)


# =============================================================================
# Embed Colors
# =============================================================================

# Colors for ticket embeds based on status
STATUS_COLORS = {
    "open": 0x00FF00,      # Green
    "working": 0xFFAA00,   # Orange
    "done": 0x808080,      # Gray
}

# Priority colors
PRIORITY_COLORS = {
    "low": 0x00FF00,       # Green
    "medium": 0xFFAA00,    # Orange
    "high": 0xFF0000,      # Red
}

# Default embed color
DEFAULT_EMBED_COLOR = 0x5865F2  # Discord blurple


# =============================================================================
# Messages
# =============================================================================

# Welcome message sent when a ticket is created
TICKET_WELCOME_MESSAGE = """
Welcome to your support ticket!

A staff member will be with you shortly. Please describe your issue in detail.

**Ticket Information:**
- Subject: {subject}
- Created by: {creator}
- Association: {association}
"""

# Message sent when a ticket is closed
TICKET_CLOSE_MESSAGE = """
This ticket has been closed.

If you need further assistance, please create a new ticket.
Thank you for contacting support!
"""

# Message sent when a ticket is reopened
TICKET_REOPEN_MESSAGE = """
This ticket has been reopened.

A staff member will review your request.
"""
