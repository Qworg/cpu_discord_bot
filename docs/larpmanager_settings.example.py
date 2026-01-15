"""
LarpManager Django Settings for Discord Integration

Add these settings to your LarpManager Django settings file
(e.g., main/settings/local.py or main/settings/prod.py)

For full setup instructions, see the bot's README.md
"""

# =============================================================================
# Discord OAuth2 Configuration
# =============================================================================
# Required for account linking (Discord <-> LarpManager member profiles)
#
# Get these from: https://discord.com/developers/applications
# Select your application > OAuth2

# Your Discord Application's Client ID
DISCORD_CLIENT_ID = "your_discord_application_client_id"

# Your Discord Application's Client Secret
# KEEP THIS SECRET - never commit to version control!
DISCORD_CLIENT_SECRET = "your_discord_client_secret"

# OAuth2 redirect URI - must match exactly in Discord Developer Portal
# Add this URL to: Discord Developer Portal > Your App > OAuth2 > Redirects
DISCORD_REDIRECT_URI = "https://your-larpmanager-domain.com/discord/callback/"

# =============================================================================
# API Key Setup (via Django shell or admin)
# =============================================================================
# The bot needs an API key to authenticate with LarpManager.
# Create one using the Django shell:
#
# python manage.py shell
# >>> from larpmanager.models.base import PublisherApiKey
# >>> import secrets
# >>> key = secrets.token_urlsafe(32)
# >>> PublisherApiKey.objects.create(name="Discord Bot", key=key, active=True)
# >>> print(f"Add this to your bot config: {key}")
#
# Or create via Django admin at: /admin/larpmanager/publisherapikey/

# =============================================================================
# Optional: Customize Discord OAuth Scopes
# =============================================================================
# Default scopes request user identity only
# DISCORD_OAUTH_SCOPES = ["identify"]

# =============================================================================
# Example: Complete local development settings
# =============================================================================
"""
# main/settings/local.py

from .base import *

DEBUG = True

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'larpmanager',
        'USER': 'larpmanager',
        'PASSWORD': 'larpmanager',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

# Discord Integration
DISCORD_CLIENT_ID = "1234567890123456789"
DISCORD_CLIENT_SECRET = "your-secret-here"
DISCORD_REDIRECT_URI = "http://localhost:8000/discord/callback/"

# For local dev, you might want to allow HTTP redirects
# (Discord requires HTTPS in production)
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http"
"""
