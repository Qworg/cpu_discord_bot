# Ticket System Setup Guide

This document describes how to set up the Discord ticketing system that integrates with larpmanager.

## Overview

The ticket system allows users to create support tickets via Discord slash commands. Tickets are stored in larpmanager's database and can be managed through both Discord and the larpmanager web interface.

**Features:**
- Create tickets with subject and association selection
- Private Discord channels per ticket
- Staff assignment and priority management
- Automatic transcript generation on close
- Discord-to-larpmanager account linking via OAuth
- Reopen closed tickets

---

## Prerequisites

- A running larpmanager instance
- A Discord bot with the following permissions:
  - Manage Channels
  - Manage Roles
  - Send Messages
  - Embed Links
  - Attach Files
  - Read Message History
  - Manage Messages
- Python packages: `discord.py`, `aiohttp`

---

## Step 1: larpmanager Configuration

### 1.1 Run Database Migrations

```bash
cd /path/to/larpmanager
python manage.py migrate
```

This applies the migration `0127_larpmanagerticket_discord_fields.py` which adds:
- `discord_channel_id` - Discord channel ID for the ticket
- `discord_creator_id` - Discord user ID of ticket creator
- `assigned_staff_discord_id` - Assigned staff member's Discord ID
- `subject` - Short ticket subject line
- `transcript` - Full conversation transcript
- `closed_at` - Timestamp when ticket was closed

### 1.2 Create a Discord OAuth Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to "OAuth2" > "General"
4. Copy the **Client ID** and **Client Secret**
5. Add a redirect URI: `https://your-larpmanager-domain.com/discord/callback/`

### 1.3 Add Django Settings

Add the following to your larpmanager Django settings (e.g., `main/settings.py` or via environment variables):

```python
# Discord OAuth Configuration
DISCORD_CLIENT_ID = 'your-discord-app-client-id'
DISCORD_CLIENT_SECRET = 'your-discord-app-client-secret'
DISCORD_REDIRECT_URI = 'https://your-larpmanager-domain.com/discord/callback/'

# API key for the Discord bot to authenticate with larpmanager
# Generate a secure random string for this
DISCORD_BOT_API_KEY = 'your-secure-api-key-for-bot'
```

Alternatively, you can create a `PublisherApiKey` in the Django admin panel for the bot to use.

### 1.4 Create an API Key for the Bot

Option A: Use `DISCORD_BOT_API_KEY` setting (above)

Option B: Create a `PublisherApiKey` in Django admin:
1. Go to `/admin/`
2. Navigate to "Publisher Api Keys"
3. Add a new key with a descriptive name (e.g., "Discord Ticket Bot")
4. Copy the generated key value

---

## Step 2: Discord Server Configuration

### 2.1 Create Ticket Categories

1. In your Discord server, create a category for tickets:
   - Name: "Tickets" (or your preference)
   - Set permissions so only staff and the bot can see it by default

2. (Optional) Create an archive category for closed tickets:
   - Name: "Closed Tickets"

### 2.2 Get Category IDs

1. Enable Developer Mode in Discord:
   - User Settings > Advanced > Developer Mode: ON
2. Right-click the "Tickets" category > "Copy ID"
3. (Optional) Right-click the "Closed Tickets" category > "Copy ID"

---

## Step 3: Discord Bot Configuration

### 3.1 Install Dependencies

```bash
pip install aiohttp
```

### 3.2 Create Configuration File

Copy the example config and fill in your values:

```bash
cd /path/to/cpu_discord_bot
cp Config/config.json.example Config/config.json
```

Edit `Config/config.json`:

```json
{
    "token": "YOUR_DISCORD_BOT_TOKEN",

    "ticket_category_id": 123456789012345678,
    "ticket_archive_category_id": null,

    "larpmanager_api_url": "https://your-larpmanager-domain.com",
    "larpmanager_api_key": "YOUR_LARPMANAGER_API_KEY",

    "max_tickets_per_user": 5,
    "api_timeout": 30,

    "ticket_viewer_role_ids": []
}
```

**Configuration Options:**

| Key | Required | Description |
|-----|----------|-------------|
| `token` | Yes | Your Discord bot token |
| `ticket_category_id` | Yes | Discord category ID where ticket channels will be created |
| `ticket_archive_category_id` | No | Category for closed tickets (null = tickets stay in place) |
| `larpmanager_api_url` | Yes | Base URL of your larpmanager instance |
| `larpmanager_api_key` | Yes | API key for authenticating with larpmanager |
| `max_tickets_per_user` | No | Maximum open tickets per user (default: 5, 0 = unlimited) |
| `api_timeout` | No | API request timeout in seconds (default: 30) |
| `ticket_viewer_role_ids` | No | Role IDs that can view all tickets (read-only access) |

### 3.3 Verify Staff Roles

The ticket system uses existing admin roles for staff permissions:
- `ADMIN_INDY_ROLE_ID` (752234266871726111)
- `ADMIN_SEATTLE_ROLE_ID` (928109603919634432)

If you need different staff roles, modify `Tickets/ticket_config.py`:

```python
TICKET_STAFF_ROLE_IDS = {
    YOUR_STAFF_ROLE_ID_1,
    YOUR_STAFF_ROLE_ID_2,
}
```

---

## Step 4: Start the Bot

```bash
cd /path/to/cpu_discord_bot/Shared
python discord_bot.py
```

The bot will:
1. Connect to Discord
2. Sync slash commands to your guild
3. Log "Ticket commands registered"

---

## Usage

### Creating a Ticket

1. User runs `/ticket create`
2. If not linked to larpmanager:
   - Bot shows "Link Account" button
   - User clicks button, logs into larpmanager, authorizes Discord
   - User returns and runs `/ticket create` again
3. Modal appears for entering ticket subject
4. Dropdown appears for selecting association
5. Private channel is created with:
   - User has read/write access
   - Staff roles have full access
   - Everyone else cannot see it

### Managing Tickets

| Command | Who Can Use | Description |
|---------|-------------|-------------|
| `/ticket create` | Anyone | Create a new ticket |
| `/ticket close` | Creator or Staff | Close the ticket |
| `/ticket assign @user` | Staff only | Assign ticket to staff member |
| `/ticket priority low/medium/high` | Staff only | Change priority |
| `/ticket list` | Staff only | View all open tickets |
| `/ticket reopen` | Staff only | Reopen a closed ticket |
| `/ticketinfo` | Anyone in ticket | View ticket details |
| `/ticketadd @user` | Staff only | Add user to ticket |
| `/ticketremove @user` | Staff only | Remove user from ticket |
| `/link` | Anyone | Link Discord to larpmanager |

### Viewing Tickets in larpmanager

Tickets created via Discord appear in the larpmanager admin panel at:
- `/admin/larpmanager/larpmanagerticket/`

Staff can view and manage tickets through the web interface, including:
- Viewing transcripts
- Changing status/priority
- Seeing Discord channel information

---

## API Endpoints Reference

All endpoints require authentication via `X-API-Key` header or `api_key` query parameter.

### Discord Linking

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/discord/member/<discord_id>/` | GET | Check if user is linked |
| `/api/v1/discord/link/<discord_id>/` | GET | Get OAuth URL |
| `/api/v1/discord/unlink/?discord_id=<id>` | GET | Unlink account |

### Tickets

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/associations/` | GET | List associations |
| `/api/v1/tickets/` | GET | List tickets (with filters) |
| `/api/v1/tickets/` | POST | Create ticket |
| `/api/v1/tickets/<uuid>/` | GET | Get ticket details |
| `/api/v1/tickets/<uuid>/` | PATCH | Update ticket |
| `/api/v1/tickets/<uuid>/` | DELETE | Delete ticket |
| `/api/v1/tickets/<uuid>/close/` | POST | Close with transcript |
| `/api/v1/tickets/<uuid>/reopen/` | POST | Reopen ticket |
| `/api/v1/tickets/channel/<channel_id>/` | GET | Get by Discord channel |

### Query Parameters for `/api/v1/tickets/`

| Parameter | Description |
|-----------|-------------|
| `status` | Filter by status (open, working, done) |
| `association_uuid` | Filter by association |
| `discord_creator_id` | Filter by creator's Discord ID |
| `discord_channel_id` | Filter by channel ID |
| `discord_only` | Only Discord-created tickets (true/false) |
| `limit` | Max results (default 50, max 100) |
| `offset` | Pagination offset |

---

## Troubleshooting

### "Ticket category not configured"

Set `ticket_category_id` in `Config/config.json` to a valid Discord category ID.

### "Unable to generate account linking URL"

Check that `DISCORD_CLIENT_ID` is set in larpmanager Django settings.

### "Invalid API key"

Verify that:
1. `larpmanager_api_key` in config matches a `PublisherApiKey` in larpmanager, OR
2. `DISCORD_BOT_API_KEY` is set in Django settings and matches the config

### Bot can't create channels

Ensure the bot has these permissions in the ticket category:
- View Channel
- Manage Channels
- Manage Roles
- Send Messages
- Embed Links
- Attach Files
- Read Message History
- Manage Messages

### Bot can't close/reopen tickets (403 Forbidden "Missing Permissions")

Closing a ticket edits **member-level** channel permission overwrites (stripping the
customer and any `/ticketadd` users). That requires the **Manage Roles** permission,
not just Manage Channels. Ensure the bot's **role** (or the bot's guild-level
permissions) includes **Manage Roles** and **Manage Channels** — a channel-only
overwrite is not sufficient. After granting it, the stuck outbox event retries
within its cooldown (or restart the bot).

### Commands not showing in Discord

1. Wait a few minutes for Discord to sync
2. Check the bot log for errors during startup
3. Verify the bot is in the correct guild
4. Try kicking and re-inviting the bot

---

## File Structure

```
cpu_discord_bot/
├── Config/
│   ├── config.json           # Your configuration (create from example)
│   └── config.json.example   # Example configuration
├── Tickets/
│   ├── __init__.py          # Module init
│   ├── ticket_api_client.py # HTTP client for larpmanager
│   ├── ticket_commands.py   # Slash command definitions
│   ├── ticket_config.py     # Configuration loading
│   ├── ticket_manager.py    # Business logic
│   ├── ticket_permissions.py # Channel/permission management
│   └── ticket_views.py      # Discord UI components
└── Shared/
    ├── discord_bot.py       # Main bot (imports Tickets)
    └── Utilities/
        └── discord_utilities.py # Constants and helpers

larpmanager/
├── larpmanager/
│   ├── models/
│   │   └── larpmanager.py   # LarpManagerTicket model (modified)
│   ├── views/
│   │   ├── api_discord.py   # Discord OAuth endpoints
│   │   └── api_tickets.py   # Ticket API endpoints
│   ├── urls/
│   │   └── __init__.py      # URL routes (modified)
│   ├── migrations/
│   │   └── 0127_larpmanagerticket_discord_fields.py
│   └── templates/
│       ├── discord_link_success.html
│       └── discord_link_error.html
```
