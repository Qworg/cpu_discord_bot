Comprehensive Test Implementation Plan
Overview
This plan implements ~110 tests covering both larpmanager and cpu_discord_bot, plus Playwright E2E tests for the OAuth flow, and CI integration for both repositories.
---
Part 1: larpmanager Tests
1.1 New Test Files
File: larpmanager/tests/unit/test_ticket_model.py
Purpose: Test the LarpManagerTicket model with new Discord fields
| Test Method | Description |
|-------------|-------------|
| test_ticket_creation_with_discord_fields | Create ticket with all Discord fields, verify saved |
| test_ticket_str_with_subject | __str__ returns subject when present |
| test_ticket_str_without_subject_with_reason | __str__ falls back to reason |
| test_ticket_str_without_subject_or_reason | __str__ returns "No reason" |
| test_ticket_discord_channel_id_unique_constraint | Unique constraint enforced |
| test_ticket_status_transitions | Status can be changed open→working→done |
| test_ticket_priority_choices | All priority values work |
| test_ticket_closed_at_set_on_close | closed_at populated when status=done |
Factory method to add to BaseTestCase:
def create_larpmanager_ticket(self, association=None, **kwargs):
    """Create a LarpManagerTicket for testing"""
    from larpmanager.models.larpmanager import LarpManagerTicket
    if association is None:
        association = self.get_association()
    defaults = {
        "association": association,
        "reason": "Test Ticket",
        "content": "Test content",
        "status": "open",
        "priority": "low",
    }
    defaults.update(kwargs)
    return LarpManagerTicket.objects.create(**defaults)
---
File: larpmanager/tests/unit/test_api_tickets.py
Purpose: Test the ticket REST API endpoints
| Test Method | Description |
|-------------|-------------|
| test_list_tickets_requires_auth | GET /tickets/ returns 401 without key |
| test_list_tickets_with_valid_api_key | Returns ticket list |
| test_list_tickets_filter_by_status | ?status=open filters correctly |
| test_list_tickets_filter_by_association | ?association_uuid= filters correctly |
| test_list_tickets_filter_by_discord_creator | ?discord_creator_id= filters correctly |
| test_list_tickets_filter_discord_only | ?discord_only=true filters correctly |
| test_list_tickets_pagination | ?limit=&offset= work correctly |
| test_create_ticket_success | POST creates ticket with all fields |
| test_create_ticket_missing_required_fields | POST returns 400 for missing fields |
| test_create_ticket_invalid_association | POST returns 404 for bad association |
| test_create_ticket_links_member | Creates with member if discord_id linked |
| test_get_ticket_by_uuid | GET /tickets/{uuid}/ returns ticket |
| test_get_ticket_not_found | GET returns 404 for unknown uuid |
| test_update_ticket_status | PATCH updates status |
| test_update_ticket_priority | PATCH updates priority |
| test_update_ticket_assignment | PATCH updates assigned_staff_discord_id |
| test_update_ticket_invalid_status | PATCH returns 400 for invalid status |
| test_close_ticket_success | POST /close/ sets status=done, closed_at |
| test_close_ticket_with_transcript | POST /close/ saves transcript |
| test_reopen_ticket_success | POST /reopen/ sets status=open |
| test_reopen_ticket_not_closed | POST /reopen/ returns 400 if not done |
| test_get_ticket_by_channel | GET /channel/{id}/ returns ticket |
| test_get_ticket_by_channel_not_found | Returns 404 for unknown channel |
| test_list_associations | GET /associations/ returns list |
Test pattern:
from django.test import Client
from larpmanager.tests.unit.base import BaseTestCase
from larpmanager.models.base import PublisherApiKey
class TestTicketAPI(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.api_key = PublisherApiKey.objects.create(
            name="Test Key", key="test-api-key", active=True
        )
        self.headers = {"HTTP_X_API_KEY": "test-api-key"}
    
    def test_list_tickets_requires_auth(self):
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 401)
    
    def test_list_tickets_with_valid_api_key(self):
        ticket = self.create_larpmanager_ticket()
        response = self.client.get("/api/v1/tickets/", **self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["tickets"]), 1)
---
File: larpmanager/tests/unit/test_api_discord.py
Purpose: Test Discord OAuth and linking API endpoints
| Test Method | Description |
|-------------|-------------|
| test_member_check_linked_returns_true | Returns linked: true with member data |
| test_member_check_not_linked_returns_false | Returns linked: false |
| test_member_check_requires_auth | Returns 401 without API key |
| test_get_oauth_url_success | Returns valid OAuth URL with state |
| test_get_oauth_url_missing_config | Returns 500 if CLIENT_ID not set |
| test_link_discord_to_member_creates_config | Creates MemberConfig entry |
| test_link_discord_to_member_updates_existing | Updates existing config |
| test_get_member_by_discord_id_found | Returns member when linked |
| test_get_member_by_discord_id_not_found | Returns None when not linked |
| test_unlink_discord_success | Deletes MemberConfig entry |
| test_unlink_discord_not_found | Returns 404 if not linked |
| test_validate_bot_api_key_with_header | Validates X-API-Key header |
| test_validate_bot_api_key_with_param | Validates ?api_key= param |
| test_validate_bot_api_key_invalid | Returns 401 for invalid key |
---
File: larpmanager/tests/playwright/discord_oauth_test.py
Purpose: E2E tests for Discord OAuth flow
| Test Function | Description |
|---------------|-------------|
| test_oauth_success_page_renders | Success template renders correctly |
| test_oauth_error_page_renders | Error template renders correctly |
| test_oauth_callback_missing_code_shows_error | Shows error for missing code |
| test_oauth_callback_invalid_state_shows_error | Shows error for bad state |
| test_discord_link_complete_no_pending | Shows error if no pending link |
Test pattern:
import pytest
from larpmanager.tests.utils import go_to, login_user, expect_normalized
pytestmark = pytest.mark.e2e
def test_oauth_success_page_renders(pw_page):
    page, live_server, context = pw_page
    # Directly visit success page (normally reached via OAuth callback)
    go_to(page, live_server, "/discord/callback/?code=test&state=123:nonce:sig")
    # Since we can't mock Discord, we expect an error
    # This tests that the route exists and renders the error template
    expect_normalized(page, page.locator("body"), "Discord Link")
def test_oauth_error_page_shows_message(pw_page):
    page, live_server, context = pw_page
    go_to(page, live_server, "/discord/callback/?error=access_denied")
    expect_normalized(page, page.locator(".card-body"), "authorization was denied")
---
1.2 Modifications to Existing Files
Update: larpmanager/tests/unit/base.py
Add factory method for tickets:
def create_larpmanager_ticket(self, association=None, member=None, **kwargs):
    """Create a LarpManagerTicket for testing"""
    from larpmanager.models.larpmanager import LarpManagerTicket, TicketStatus, TicketPriority
    
    if association is None:
        association = self.get_association()
    
    defaults = {
        "association": association,
        "member": member,
        "reason": "Test Ticket",
        "content": "Test ticket content",
        "status": TicketStatus.OPEN,
        "priority": TicketPriority.LOW,
    }
    defaults.update(kwargs)
    return LarpManagerTicket.objects.create(**defaults)
def create_discord_linked_member(self, discord_id=123456789, **kwargs):
    """Create a member with Discord ID linked"""
    from larpmanager.models.member import MemberConfig
    
    member = self.create_member(**kwargs)
    MemberConfig.objects.create(
        member=member,
        name="discord_id",
        value=str(discord_id),
    )
    return member
---
Part 2: cpu_discord_bot Tests
2.1 New Test Infrastructure
File: tests/conftest.py
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
---
File: pyproject.toml (add test configuration)
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "-v --tb=short"
[tool.coverage.run]
source = ["Tickets"]
omit = ["tests/*"]
---
File: requirements-test.txt
pytest>=7.4.0
pytest-asyncio>=0.21.0
aioresponses>=0.7.4
pytest-cov>=4.1.0
---
2.2 Test Files
File: tests/test_ticket_api_client.py
| Test Method | Description |
|-------------|-------------|
| test_check_discord_link_linked | Returns MemberLinkStatus with linked=True |
| test_check_discord_link_not_linked | Returns linked=False |
| test_check_discord_link_api_error | Raises APIError on failure |
| test_get_oauth_url_success | Returns OAuth URL string |
| test_get_associations_success | Returns list of AssociationData |
| test_get_associations_empty | Returns empty list |
| test_create_ticket_success | Returns TicketData |
| test_create_ticket_validation_error | Raises APIError on 400 |
| test_get_ticket_success | Returns TicketData |
| test_get_ticket_not_found | Raises APIError with 404 |
| test_get_ticket_by_channel_success | Returns TicketData |
| test_get_ticket_by_channel_not_found | Returns None |
| test_update_ticket_success | Returns updated TicketData |
| test_close_ticket_success | Returns closed TicketData |
| test_close_ticket_with_transcript | Transcript included |
| test_reopen_ticket_success | Returns reopened TicketData |
| test_list_tickets_success | Returns (list, total) |
| test_list_tickets_with_filters | Filters applied correctly |
| test_api_timeout_handling | Raises APIError on timeout |
| test_api_connection_error | Raises APIError on connection failure |
Test pattern using aioresponses:
import pytest
from aioresponses import aioresponses
from Tickets.ticket_api_client import TicketAPIClient, APIError
@pytest.mark.asyncio
async def test_check_discord_link_linked(api_responses):
    with aioresponses() as m:
        m.get(
            "http://localhost:8000/api/v1/discord/member/123456789/",
            payload=api_responses["link_status_linked"],
        )
        
        client = TicketAPIClient(base_url="http://localhost:8000", api_key="test")
        result = await client.check_discord_link(123456789)
        
        assert result.linked is True
        assert result.member_name == "Test User"
        
        await client.close()
---
File: tests/test_ticket_permissions.py
| Test Method | Description |
|-------------|-------------|
| test_slugify_basic | "Hello World" → "hello-world" |
| test_slugify_special_chars | Removes special characters |
| test_slugify_max_length | Truncates to max_length |
| test_slugify_empty | Returns "ticket" for empty |
| test_generate_channel_name | Returns correct format |
| test_create_ticket_channel_success | Creates channel with permissions |
| test_create_ticket_channel_no_category_configured | Raises ValueError |
| test_create_ticket_channel_category_not_found | Raises ValueError |
| test_add_user_to_ticket | Sets correct permission overwrite |
| test_remove_user_from_ticket | Removes overwrite |
| test_close_ticket_channel_removes_send | Creator can't send |
| test_close_ticket_channel_moves_to_archive | Moved if archive configured |
| test_close_ticket_channel_renames | Adds "closed-" prefix |
| test_reopen_ticket_channel_restores_send | Creator can send again |
| test_reopen_ticket_channel_removes_prefix | Removes "closed-" prefix |
| test_is_ticket_channel_by_category | Returns True for ticket category |
| test_is_ticket_channel_by_name | Returns True for ticket- prefix |
| test_is_ticket_channel_false | Returns False for other channels |
| test_user_is_staff_true | Returns True for admin role |
| test_user_is_staff_false | Returns False for no admin role |
---
File: tests/test_ticket_views.py
| Test Method | Description |
|-------------|-------------|
| test_create_ticket_embed_fields | Embed has subject, status, priority |
| test_create_ticket_embed_color | Color matches status |
| test_create_welcome_embed_content | Contains welcome message |
| test_create_close_embed_content | Contains close message |
| test_create_reopen_embed_content | Contains reopen message |
| test_create_ticket_list_embed_pagination | Shows page X/Y |
| test_create_ticket_list_embed_tickets | Shows ticket fields |
| test_ticket_create_modal_callback | Callback invoked with subject/content |
| test_association_select_callback | Callback invoked with uuid |
| test_link_account_button_url | Button URL is correct |
| test_confirm_close_view_confirm | Confirm callback invoked |
| test_confirm_close_view_cancel | Cancel stops view |
---
File: tests/test_ticket_manager.py
| Test Method | Description |
|-------------|-------------|
| test_check_user_linked_returns_true | Returns True when linked |
| test_check_user_linked_shows_button | Shows link button when not linked |
| test_check_ticket_limit_under_limit | Returns True |
| test_check_ticket_limit_at_limit | Returns False, shows message |
| test_check_ticket_limit_unlimited | Returns True when MAX=0 |
| test_start_ticket_creation_shows_modal | Sends modal when linked |
| test_start_ticket_creation_shows_link_button | Shows button when not linked |
| test_create_ticket_success | Creates channel and API ticket |
| test_create_ticket_api_failure_cleans_up | Deletes channel on API error |
| test_create_ticket_channel_failure | Handles permission error |
| test_close_ticket_as_creator | Creator can close |
| test_close_ticket_as_staff | Staff can close |
| test_close_ticket_unauthorized | Non-creator/non-staff denied |
| test_close_ticket_not_ticket_channel | Error for non-ticket channel |
| test_close_ticket_generates_transcript | Transcript saved to API |
| test_reopen_ticket_success | Reopens closed ticket |
| test_reopen_ticket_staff_only | Non-staff denied |
| test_reopen_ticket_not_closed | Error if not closed |
| test_assign_ticket_success | Updates assignment |
| test_assign_ticket_adds_to_channel | Staff added to channel |
| test_set_priority_success | Updates priority |
| test_set_priority_staff_only | Non-staff denied |
| test_list_tickets_success | Returns paginated list |
| test_list_tickets_staff_only | Non-staff denied |
| test_generate_transcript_format | Correct format with timestamps |
---
File: tests/test_ticket_commands.py
| Test Method | Description |
|-------------|-------------|
| test_ticket_create_invokes_manager | Calls start_ticket_creation |
| test_ticket_close_invokes_manager | Calls close_ticket |
| test_ticket_assign_invokes_manager | Calls assign_ticket |
| test_ticket_assign_no_user_error | Shows error message |
| test_ticket_list_invokes_manager | Calls list_tickets |
| test_ticket_priority_invokes_manager | Calls set_priority |
| test_ticket_priority_no_value_error | Shows error message |
| test_ticket_reopen_invokes_manager | Calls reopen_ticket |
| test_link_command_not_linked | Shows link button |
| test_link_command_already_linked | Shows already linked message |
| test_ticketinfo_shows_embed | Shows ticket embed |
| test_ticketinfo_not_ticket_channel | Shows error |
| test_ticketadd_adds_user | Calls add_user_to_ticket |
| test_ticketadd_not_staff_denied | Non-staff denied |
| test_ticketremove_removes_user | Calls remove_user_from_ticket |
| test_ticketremove_not_staff_denied | Non-staff denied |
| test_command_error_handling | Errors caught and reported |
---
Part 3: CI/CD Integration
3.1 larpmanager Pipeline Update
Modify: .github/workflows/pipeline.yml
Add the new test files to the existing pipeline. No changes needed since pytest auto-discovers test files.
---
3.2 cpu_discord_bot GitHub Actions
New file: .github/workflows/test.yml
name: Tests
on:
  push:
    branches: [main, CPUTix]
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install discord.py aiohttp
        pip install -r requirements-test.txt
    
    - name: Run tests
      run: |
        pytest tests/ -v --cov=Tickets --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        files: ./coverage.xml
        fail_ci_if_error: false
---
Part 4: File Structure Summary
larpmanager (new/modified files)
larpmanager/
├── larpmanager/
│   └── tests/
│       ├── unit/
│       │   ├── base.py                    # MODIFY: Add ticket factory methods
│       │   ├── test_ticket_model.py       # NEW: 8 tests
│       │   ├── test_api_tickets.py        # NEW: 24 tests
│       │   └── test_api_discord.py        # NEW: 14 tests
│       └── playwright/
│           └── discord_oauth_test.py      # NEW: 5 tests
cpu_discord_bot (new files)
cpu_discord_bot/
├── tests/
│   ├── __init__.py
│   ├── conftest.py                        # NEW: Fixtures
│   ├── test_ticket_api_client.py          # NEW: 20 tests
│   ├── test_ticket_permissions.py         # NEW: 20 tests
│   ├── test_ticket_views.py               # NEW: 12 tests
│   ├── test_ticket_manager.py             # NEW: 24 tests
│   └── test_ticket_commands.py            # NEW: 17 tests
├── pyproject.toml                         # NEW: pytest config
├── requirements-test.txt                  # NEW: test dependencies
└── .github/
    └── workflows/
        └── test.yml                       # NEW: CI workflow
---
Part 5: Test Count Summary
| Location | File | Test Count |
|----------|------|------------|
| larpmanager | test_ticket_model.py | 8 |
| larpmanager | test_api_tickets.py | 24 |
| larpmanager | test_api_discord.py | 14 |
| larpmanager | discord_oauth_test.py (Playwright) | 5 |
| cpu_discord_bot | test_ticket_api_client.py | 20 |
| cpu_discord_bot | test_ticket_permissions.py | 20 |
| cpu_discord_bot | test_ticket_views.py | 12 |
| cpu_discord_bot | test_ticket_manager.py | 24 |
| cpu_discord_bot | test_ticket_commands.py | 17 |
| Total | | 144 tests |
---
Implementation Order
1. Phase 1: larpmanager unit tests (46 tests)
   - Update base.py with factory methods
   - Create test_ticket_model.py
   - Create test_api_tickets.py
   - Create test_api_discord.py
2. Phase 2: cpu_discord_bot test infrastructure 
   - Create tests/ directory structure
   - Create conftest.py with fixtures
   - Create pyproject.toml
   - Create requirements-test.txt
3. Phase 3: cpu_discord_bot unit tests (93 tests)
   - Create test_ticket_api_client.py
   - Create test_ticket_permissions.py
   - Create test_ticket_views.py
   - Create test_ticket_manager.py
   - Create test_ticket_commands.py
4. Phase 4: Playwright E2E tests (5 tests)
   - Create discord_oauth_test.py
5. Phase 5: CI integration
   - Create .github/workflows/test.yml for cpu_discord_bot
---
Ready to proceed with implementation? I'll start with Phase 1 (larpmanager unit tests) once you confirm.
Session Summary: Discord Ticket System Implementation
What Was Accomplished
We rebuilt a ticketing system from ticketbot/ (a complex microservices architecture) into a simpler implementation using the Shared/ bot framework, integrated with ../larpmanager/ as the backend/web interface.
Completed Implementation
1. larpmanager Changes (../larpmanager/)
Modified Files:
- larpmanager/models/larpmanager.py - Added Discord fields to LarpManagerTicket model:
  - discord_channel_id (BigInteger, unique, indexed)
  - discord_creator_id (BigInteger, indexed)
  - assigned_staff_discord_id (BigInteger)
  - subject (CharField)
  - transcript (TextField)
  - closed_at (DateTimeField)
  - Updated __str__ method to show subject
- larpmanager/urls/__init__.py - Added 12 new API routes for Discord OAuth and ticket management
New Files Created:
- larpmanager/migrations/0127_larpmanagerticket_discord_fields.py - Migration for new fields
- larpmanager/views/api_discord.py - Discord OAuth endpoints (member check, OAuth URL, callback, linking)
- larpmanager/views/api_tickets.py - Ticket REST API (CRUD, close, reopen, list associations)
- larpmanager/templates/discord_link_success.html - OAuth success page
- larpmanager/templates/discord_link_error.html - OAuth error page
2. Discord Bot Changes (cpu_discord_bot/)
New Module Created: Tickets/
- __init__.py - Module docstring
- ticket_config.py - Configuration (category IDs, API URL, staff roles, colors, messages)
- ticket_api_client.py - Async HTTP client for larpmanager API with dataclasses (TicketData, AssociationData, MemberLinkStatus)
- ticket_permissions.py - Channel creation, permission management (add/remove users, close/reopen channels)
- ticket_views.py - Discord UI components (modals, selects, buttons, embeds)
- ticket_manager.py - Business logic coordinator (ticket lifecycle management)
- ticket_commands.py - Slash commands: /ticket, /link, /ticketinfo, /ticketadd, /ticketremove
Modified Files:
- Shared/discord_bot.py - Added import for Tickets module
- Shared/Utilities/discord_utilities.py - Added ticket category constants
New Files:
- Config/config.json.example - Example configuration
- docs/TICKET_SYSTEM_SETUP.md - Comprehensive setup documentation
3. Commits Made (on branch CPUTix in both repos)
- cpu_discord_bot: e7c7eb8 - "Add Discord ticketing system integrated with larpmanager" (12 files, +3092 lines)
- larpmanager: de544cb1 - "Add Discord ticket integration API and OAuth support" (7 files, +1096 lines)
Note: Push failed due to SSH key issues - commits are local only, need manual push.
Current Task: Test Implementation
We created a comprehensive test plan for ~144 tests covering:
larpmanager Tests (51 tests):
- test_ticket_model.py - 8 model tests
- test_api_tickets.py - 24 API endpoint tests  
- test_api_discord.py - 14 OAuth/linking tests
- discord_oauth_test.py - 5 Playwright E2E tests
cpu_discord_bot Tests (93 tests):
- test_ticket_api_client.py - 20 tests
- test_ticket_permissions.py - 20 tests
- test_ticket_views.py - 12 tests
- test_ticket_manager.py - 24 tests
- test_ticket_commands.py - 17 tests
Plus CI/CD: .github/workflows/test.yml for cpu_discord_bot
Key Technical Decisions Made
1. Storage: larpmanager API integration (not JSON files or SQLite)
2. Authentication: Discord OAuth with link-on-first-ticket flow
3. Association Selection: User selects association when creating ticket via dropdown
4. Channels: Private Discord channels per ticket (not threads or DMs)
5. Staff Roles: Existing ADMIN_INDY_ROLE_ID and ADMIN_SEATTLE_ROLE_ID
6. Features: All requested - create, close, assign, list, priority, reopen, transcript generation
Files Structure
cpu_discord_bot/
├── Tickets/
│   ├── __init__.py
│   ├── ticket_api_client.py
│   ├── ticket_commands.py
│   ├── ticket_config.py
│   ├── ticket_manager.py
│   ├── ticket_permissions.py
│   └── ticket_views.py
├── Config/config.json.example
├── docs/TICKET_SYSTEM_SETUP.md
└── Shared/
    ├── discord_bot.py (modified)
    └── Utilities/discord_utilities.py (modified)
../larpmanager/larpmanager/
├── models/larpmanager.py (modified)
├── migrations/0127_larpmanagerticket_discord_fields.py (new)
├── views/api_discord.py (new)
├── views/api_tickets.py (new)
├── urls/__init__.py (modified)
└── templates/discord_link_*.html (new)
Next Steps
Implement the test suite in this order:
1. Phase 1: larpmanager unit tests
   - Update larpmanager/tests/unit/base.py with create_larpmanager_ticket() and create_discord_linked_member() factory methods
   - Create larpmanager/tests/unit/test_ticket_model.py
   - Create larpmanager/tests/unit/test_api_tickets.py
   - Create larpmanager/tests/unit/test_api_discord.py
2. Phase 2: cpu_discord_bot test infrastructure
   - Create tests/ directory
   - Create tests/conftest.py with Discord mock fixtures
   - Create pyproject.toml with pytest config
   - Create requirements-test.txt
3. Phase 3: cpu_discord_bot unit tests (5 test files)
4. Phase 4: Playwright E2E tests for OAuth flow
5. Phase 5: GitHub Actions CI workflow
