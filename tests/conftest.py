"""Pytest configuration and fixtures for Discord bot tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_interaction():
    """Mock Discord interaction."""
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.is_done.return_value = False
    interaction.followup = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 123456789
    interaction.user.display_name = "TestUser"
    interaction.user.mention = "<@123456789>"
    interaction.user.roles = []
    interaction.guild = MagicMock()
    interaction.guild.id = 540903470484553729
    interaction.channel = MagicMock()
    interaction.channel.id = 987654321
    return interaction


@pytest.fixture
def mock_guild():
    """Mock Discord guild."""
    guild = MagicMock()
    guild.id = 540903470484553729
    guild.default_role = MagicMock()
    guild.me = MagicMock()
    guild.me.id = 111111111

    # Mock get_channel to return a category
    category = MagicMock()
    category.id = 123456789
    guild.get_channel.return_value = category

    # Mock get_role to return a role
    role = MagicMock()
    role.id = 752234266871726111
    guild.get_role.return_value = role

    # Mock get_member
    member = MagicMock()
    member.id = 123456789
    guild.get_member.return_value = member

    # Mock create_text_channel
    channel = AsyncMock()
    channel.id = 999888777
    channel.name = "ticket-test-abc123"
    guild.create_text_channel = AsyncMock(return_value=channel)

    return guild


@pytest.fixture
def mock_channel():
    """Mock Discord text channel."""
    channel = AsyncMock()
    channel.id = 987654321
    channel.name = "ticket-test-abc123"
    channel.category_id = 123456789
    channel.guild = MagicMock()
    channel.guild.id = 540903470484553729
    channel.set_permissions = AsyncMock()
    channel.edit = AsyncMock()
    channel.send = AsyncMock()
    channel.delete = AsyncMock()
    channel.overwrites = {}

    # Mock history for transcript generation
    async def mock_history(*args, **kwargs):
        messages = [
            MagicMock(
                created_at=MagicMock(strftime=lambda x: "2024-01-01 12:00:00"),
                author=MagicMock(name="TestUser", bot=False),
                content="Test message",
                attachments=[],
                embeds=[],
            )
        ]
        for msg in messages:
            yield msg

    channel.history = mock_history
    return channel


@pytest.fixture
def mock_member():
    """Mock Discord member."""
    member = MagicMock()
    member.id = 111222333
    member.display_name = "StaffMember"
    member.mention = "<@111222333>"
    member.roles = []
    return member


@pytest.fixture
def mock_staff_member():
    """Mock Discord staff member with admin role."""
    member = MagicMock()
    member.id = 444555666
    member.display_name = "Admin"
    member.mention = "<@444555666>"

    # Create a role with staff ID
    staff_role = MagicMock()
    staff_role.id = 752234266871726111  # ADMIN_INDY_ROLE_ID
    member.roles = [staff_role]

    return member


@pytest.fixture
def mock_api_client():
    """Mock TicketAPIClient."""
    client = AsyncMock()

    # Default responses
    client.check_discord_link.return_value = MagicMock(
        linked=True,
        member_uuid="mem-123",
        member_name="Test Member",
    )

    client.get_oauth_url.return_value = "https://discord.com/oauth2/authorize?..."

    client.get_associations.return_value = [
        MagicMock(uuid="assoc-1", name="Org 1", slug="org1"),
        MagicMock(uuid="assoc-2", name="Org 2", slug="org2"),
    ]

    client.create_ticket.return_value = MagicMock(
        uuid="ticket-123",
        subject="Test Ticket",
        status="open",
        priority="low",
        discord_channel_id=987654321,
        discord_creator_id=123456789,
        association={"uuid": "assoc-1", "name": "Org 1"},
        member={"uuid": "mem-123", "name": "Test"},
    )

    client.get_ticket.return_value = MagicMock(
        uuid="ticket-123",
        subject="Test Ticket",
        status="open",
        priority="low",
        discord_channel_id=987654321,
        discord_creator_id=123456789,
        association={"uuid": "assoc-1", "name": "Org 1"},
    )

    client.get_ticket_by_channel.return_value = MagicMock(
        uuid="ticket-123",
        subject="Test Ticket",
        status="open",
        priority="low",
        discord_channel_id=987654321,
        discord_creator_id=123456789,
    )

    client.list_tickets.return_value = ([], 0)

    return client


@pytest.fixture
def api_responses():
    """Common API response data."""
    return {
        "ticket": {
            "uuid": "abc-123",
            "subject": "Test Ticket",
            "reason": "Support",
            "content": "Help needed",
            "status": "open",
            "priority": "low",
            "discord_channel_id": 987654321,
            "discord_creator_id": 123456789,
            "assigned_staff_discord_id": None,
            "association": {"uuid": "assoc-1", "name": "Test Org", "slug": "test"},
            "member": {"uuid": "mem-1", "name": "Test User"},
            "email": "test@example.com",
            "transcript": None,
            "created_at": "2024-01-01T12:00:00Z",
            "updated_at": "2024-01-01T12:00:00Z",
            "closed_at": None,
        },
        "associations": [
            {"uuid": "assoc-1", "name": "Org 1", "slug": "org1"},
            {"uuid": "assoc-2", "name": "Org 2", "slug": "org2"},
        ],
        "link_status_linked": {
            "linked": True,
            "member": {"uuid": "mem-1", "name": "Test User", "email": "test@example.com"},
        },
        "link_status_not_linked": {
            "linked": False,
        },
    }


@pytest.fixture
def mock_ticket_config():
    """Mock ticket configuration values."""
    with patch.multiple(
        "Tickets.ticket_config",
        TICKET_CATEGORY_ID=123456789,
        TICKET_ARCHIVE_CATEGORY_ID=987654321,
        TICKET_STAFF_ROLE_IDS=[752234266871726111],
        TICKET_VIEWER_ROLE_IDS=[],
        TICKET_CHANNEL_PREFIX="ticket",
        LARPMANAGER_API_URL="http://localhost:8000",
        LARPMANAGER_API_KEY="test-api-key",
        MAX_TICKETS_PER_USER=5,
    ):
        yield
