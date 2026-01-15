"""Tests for Discord UI components for the ticketing system."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord


class TestTicketEmbed:
    """Tests for ticket embed creation."""

    def test_create_ticket_embed_fields(self):
        """Test that embed has subject, status, priority fields."""
        from Tickets.ticket_views import create_ticket_embed

        ticket = MagicMock(
            uuid="abc-123",
            subject="Test Subject",
            status="open",
            priority="low",
            association={"name": "Test Org"},
            assigned_staff_discord_id=None,
            member=None,
        )

        with patch("Tickets.ticket_views.STATUS_COLORS", {"open": 0x00FF00}):
            with patch("Tickets.ticket_views.DEFAULT_EMBED_COLOR", 0x808080):
                embed = create_ticket_embed(ticket)

        assert embed.title == "Support Ticket"
        # Check fields exist
        field_names = [f.name for f in embed.fields]
        assert "Subject" in field_names
        assert "Status" in field_names
        assert "Priority" in field_names

    def test_create_ticket_embed_color(self):
        """Test that embed color matches status."""
        from Tickets.ticket_views import create_ticket_embed

        ticket = MagicMock(
            uuid="abc-123",
            subject="Test",
            status="open",
            priority="low",
            association=None,
            assigned_staff_discord_id=None,
            member=None,
        )

        with patch("Tickets.ticket_views.STATUS_COLORS", {"open": 0x00FF00, "done": 0xFF0000}):
            with patch("Tickets.ticket_views.DEFAULT_EMBED_COLOR", 0x808080):
                embed = create_ticket_embed(ticket)

        assert embed.color.value == 0x00FF00


class TestWelcomeEmbed:
    """Tests for welcome embed creation."""

    def test_create_welcome_embed_content(self):
        """Test that welcome embed contains welcome message."""
        from Tickets.ticket_views import create_welcome_embed

        ticket = MagicMock(
            uuid="abc-123",
            subject="Test Subject",
            status="open",
            priority="low",
            association={"name": "Test Org"},
        )
        creator = MagicMock(mention="<@123456789>")

        with patch("Tickets.ticket_views.TICKET_WELCOME_MESSAGE", "Welcome {creator}! Subject: {subject}"):
            with patch("Tickets.ticket_views.STATUS_COLORS", {"open": 0x00FF00}):
                with patch("Tickets.ticket_views.DEFAULT_EMBED_COLOR", 0x808080):
                    embed = create_welcome_embed(ticket, creator)

        assert embed.title == "Support Ticket Created"
        assert "<@123456789>" in embed.description
        assert "Test Subject" in embed.description


class TestCloseReopenEmbeds:
    """Tests for close/reopen embeds."""

    def test_create_close_embed_content(self):
        """Test that close embed contains close message."""
        from Tickets.ticket_views import create_close_embed

        ticket = MagicMock(
            uuid="abc-123",
            subject="Test Subject",
        )

        with patch("Tickets.ticket_views.TICKET_CLOSE_MESSAGE", "This ticket has been closed."):
            with patch("Tickets.ticket_views.STATUS_COLORS", {"done": 0xFF0000}):
                with patch("Tickets.ticket_views.DEFAULT_EMBED_COLOR", 0x808080):
                    embed = create_close_embed(ticket)

        assert embed.title == "Ticket Closed"
        assert "closed" in embed.description.lower()

    def test_create_reopen_embed_content(self):
        """Test that reopen embed contains reopen message."""
        from Tickets.ticket_views import create_reopen_embed

        ticket = MagicMock(
            uuid="abc-123",
            subject="Test Subject",
        )

        with patch("Tickets.ticket_views.TICKET_REOPEN_MESSAGE", "This ticket has been reopened."):
            with patch("Tickets.ticket_views.STATUS_COLORS", {"open": 0x00FF00}):
                with patch("Tickets.ticket_views.DEFAULT_EMBED_COLOR", 0x808080):
                    embed = create_reopen_embed(ticket)

        assert embed.title == "Ticket Reopened"


class TestTicketListEmbed:
    """Tests for ticket list embed."""

    def test_create_ticket_list_embed_pagination(self):
        """Test that ticket list shows page X/Y."""
        from Tickets.ticket_views import create_ticket_list_embed

        tickets = [
            MagicMock(
                uuid="abc-123",
                subject="Ticket 1",
                status="open",
                priority="low",
                discord_channel_id=123456789,
                assigned_staff_discord_id=None,
            ),
        ]

        with patch("Tickets.ticket_views.DEFAULT_EMBED_COLOR", 0x808080):
            embed = create_ticket_list_embed(tickets, page=2, total_pages=5, total_count=50)

        assert "Page 2/5" in embed.footer.text

    def test_create_ticket_list_embed_tickets(self):
        """Test that ticket list shows ticket fields."""
        from Tickets.ticket_views import create_ticket_list_embed

        tickets = [
            MagicMock(
                uuid="abc-12345678",
                subject="Test Ticket 1",
                status="open",
                priority="high",
                discord_channel_id=123456789,
                assigned_staff_discord_id=444555666,
            ),
        ]

        with patch("Tickets.ticket_views.DEFAULT_EMBED_COLOR", 0x808080):
            embed = create_ticket_list_embed(tickets, page=1, total_pages=1, total_count=1)

        assert len(embed.fields) == 1
        assert "Test Ticket 1" in embed.fields[0].name
        # Check ticket ID is truncated
        assert "abc-1234" in embed.fields[0].value


class TestTicketCreateModal:
    """Tests for ticket creation modal."""

    @pytest.mark.asyncio
    async def test_ticket_create_modal_callback(self):
        """Test that modal callback is invoked with subject/content."""
        from Tickets.ticket_views import TicketCreateModal

        callback_called = False
        received_subject = None
        received_content = None

        async def mock_callback(interaction, subject, content):
            nonlocal callback_called, received_subject, received_content
            callback_called = True
            received_subject = subject
            received_content = content

        modal = TicketCreateModal(callback=mock_callback)
        modal.subject._value = "Test Subject"
        modal.content._value = "Test Content"

        interaction = AsyncMock()
        await modal.on_submit(interaction)

        assert callback_called is True
        assert received_subject == "Test Subject"
        assert received_content == "Test Content"


class TestAssociationSelect:
    """Tests for association select dropdown."""

    @pytest.mark.asyncio
    async def test_association_select_callback(self):
        """Test that association select callback is invoked with uuid."""
        from Tickets.ticket_views import AssociationSelect

        callback_called = False
        received_uuid = None

        async def mock_callback(interaction, uuid):
            nonlocal callback_called, received_uuid
            callback_called = True
            received_uuid = uuid

        associations = [
            MagicMock(uuid="assoc-1", name="Org 1", slug="org1"),
            MagicMock(uuid="assoc-2", name="Org 2", slug="org2"),
        ]

        select = AssociationSelect(associations, mock_callback)
        select._values = ["assoc-1"]

        interaction = AsyncMock()
        await select.callback(interaction)

        assert callback_called is True
        assert received_uuid == "assoc-1"


class TestLinkAccountButton:
    """Tests for link account button."""

    def test_link_account_button_url(self):
        """Test that button URL is correct."""
        from Tickets.ticket_views import LinkAccountButton

        oauth_url = "https://discord.com/oauth2/authorize?client_id=123"
        button = LinkAccountButton(oauth_url)

        assert button.url == oauth_url
        assert button.label == "Link Account"
        assert button.style == discord.ButtonStyle.link


class TestConfirmCloseView:
    """Tests for confirm close view."""

    @pytest.mark.asyncio
    async def test_confirm_close_view_confirm(self):
        """Test that confirm callback is invoked."""
        from Tickets.ticket_views import ConfirmCloseView

        confirm_called = False

        async def on_confirm(interaction):
            nonlocal confirm_called
            confirm_called = True

        view = ConfirmCloseView(on_confirm=on_confirm)
        interaction = AsyncMock()

        # Get the button and call its callback method properly
        # Discord.py buttons store the callback differently
        button = view.confirm_button
        # The callback expects (self, interaction, button) for decorated methods
        # But we need to simulate the button click
        await button.callback(interaction)

        assert confirm_called is True
        assert view.is_finished()

    @pytest.mark.asyncio
    async def test_confirm_close_view_cancel(self):
        """Test that cancel stops the view."""
        from Tickets.ticket_views import ConfirmCloseView

        cancel_called = False

        async def on_cancel(interaction):
            nonlocal cancel_called
            cancel_called = True

        view = ConfirmCloseView(on_confirm=AsyncMock(), on_cancel=on_cancel)
        interaction = AsyncMock()

        button = view.cancel_button
        await button.callback(interaction)

        assert cancel_called is True
        assert view.is_finished()
