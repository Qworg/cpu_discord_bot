# CPU Discord Bot - Ticket System

A Discord bot with an integrated ticketing system that syncs with [LarpManager](https://larpmanager.com).

## Features

- Create support tickets via Discord slash commands
- Automatic channel creation for each ticket
- Link Discord accounts to LarpManager member profiles
- Sync ticket status between Discord and LarpManager
- Staff role-based permissions
- Ticket transcripts on close

## Quick Start

### Prerequisites

- Python 3.11+
- A Discord server where you have admin permissions
- A LarpManager instance (for full functionality)

### Installation

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd cpu_discord_bot
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/Mac
   # or: .venv\Scripts\activate  # Windows
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements-test.txt
   pip install discord.py aiohttp
   ```

4. Copy the example config and edit it:
   ```bash
   cp Config/config.json.example Config/config.json
   # Edit Config/config.json with your values
   ```

5. Run the bot:
   ```bash
   python -m Shared.discord_bot
   ```

## Configuration

### Discord Bot Setup

1. **Create a Discord Application:**
   - Go to https://discord.com/developers/applications
   - Click "New Application" and name it
   - Go to the **Bot** tab and click "Add Bot"
   - Copy the **Bot Token** for your config file
   - Enable **Privileged Gateway Intents**:
     - Server Members Intent
     - Message Content Intent

2. **Invite the Bot to Your Server:**
   - Go to **OAuth2 > URL Generator**
   - Select scopes: `bot`, `applications.commands`
   - Select bot permissions:
     - Manage Channels
     - Send Messages
     - Embed Links
     - Read Message History
     - Manage Messages
     - Use Slash Commands
   - Copy the generated URL and open it to invite the bot

3. **Get Discord IDs** (enable Developer Mode in Discord settings first):
   - Right-click your server > Copy ID (Guild ID)
   - Right-click a category > Copy ID (Category ID)
   - Right-click a role > Copy ID (Role ID)

### Bot Configuration

Edit `Config/config.json`:

```json
{
    "token": "YOUR_DISCORD_BOT_TOKEN",
    "ticket_category_id": 123456789012345678,
    "ticket_archive_category_id": null,
    "larpmanager_api_url": "https://your-larpmanager.com",
    "larpmanager_api_key": "your-api-key",
    "max_tickets_per_user": 5,
    "api_timeout": 30,
    "ticket_viewer_role_ids": []
}
```

| Setting | Description |
|---------|-------------|
| `token` | Your Discord bot token |
| `ticket_category_id` | Category ID where ticket channels are created |
| `ticket_archive_category_id` | Optional category for closed tickets |
| `larpmanager_api_url` | URL of your LarpManager instance |
| `larpmanager_api_key` | API key created in LarpManager |
| `max_tickets_per_user` | Max open tickets per user (0 = unlimited) |
| `ticket_viewer_role_ids` | Role IDs that can view all tickets |

### LarpManager Setup

1. **Add Discord settings** to your LarpManager configuration:

   ```python
   # In your Django settings (e.g., settings/local.py)
   DISCORD_CLIENT_ID = "your-discord-application-client-id"
   DISCORD_CLIENT_SECRET = "your-discord-application-client-secret"
   DISCORD_REDIRECT_URI = "https://your-larpmanager.com/discord/callback/"
   ```

2. **Create an API key** for the bot:

   ```python
   # In Django shell: python manage.py shell
   from larpmanager.models.base import PublisherApiKey
   import secrets
   
   api_key = secrets.token_urlsafe(32)
   PublisherApiKey.objects.create(
       name="Discord Bot",
       key=api_key,
       active=True
   )
   print(f"API Key: {api_key}")  # Add this to your bot's config.json
   ```

3. **Add OAuth2 Redirect URI** in Discord Developer Portal:
   - Go to your application > OAuth2
   - Add redirect: `https://your-larpmanager.com/discord/callback/`

## Usage

### Slash Commands

| Command | Description |
|---------|-------------|
| `/ticket create` | Create a new support ticket |
| `/ticket close` | Close the current ticket |
| `/ticket list` | List your open tickets |
| `/ticket link` | Link your Discord to LarpManager account |
| `/ticket info` | Show current ticket information |

### Staff Commands

| Command | Description |
|---------|-------------|
| `/ticket assign @user` | Assign ticket to a staff member |
| `/ticket priority <low/medium/high>` | Set ticket priority |
| `/ticket reopen` | Reopen a closed ticket |

## Testing

Run the test suite:

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run tests
pytest tests/ -v
```

## Project Structure

```
cpu_discord_bot/
├── Config/
│   └── config.json.example    # Example configuration
├── Shared/
│   ├── discord_bot.py         # Main bot entry point
│   └── Utilities/             # Shared utilities
├── Tickets/
│   ├── ticket_api_client.py   # LarpManager API client
│   ├── ticket_commands.py     # Slash command handlers
│   ├── ticket_config.py       # Configuration loader
│   ├── ticket_manager.py      # Core ticket logic
│   ├── ticket_permissions.py  # Permission checks
│   └── ticket_views.py        # Discord UI components
├── tests/                     # Test suite
├── docs/
│   └── test_plan.md          # Test documentation
└── README.md
```

## Troubleshooting

### Bot doesn't respond to commands
- Ensure the bot has the `applications.commands` scope
- Check that slash commands are synced (may take up to 1 hour for global commands)
- Verify the bot has permissions in the channel

### "Discord OAuth not configured" error
- Add `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` to LarpManager settings
- Ensure the redirect URI matches exactly

### API connection errors
- Verify `larpmanager_api_url` is correct and accessible
- Check that the API key exists and is active in LarpManager
- Ensure the LarpManager server is running

### Permission errors creating channels
- Verify the bot has "Manage Channels" permission
- Check that the ticket category allows the bot to create channels
- Ensure the bot's role is high enough in the role hierarchy

## License

[Add your license here]
