"""Tests for ticket management business logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestCheckUserLinked:
    """Tests for user link checking."""

    @pytest.mark.asyncio
    async def test_check_user_linked_returns_true(self, mock_interaction, mock_api_client):
        """Test that check returns True when user is linked."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_api_client.check_discord_link.return_value = MagicMock(linked=True)

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            result = await manager.check_user_linked(mock_interaction)

        assert result is True
        mock_interaction.response.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_user_linked_shows_button(self, mock_interaction, mock_api_client):
        """Test that link button is shown when user is not linked."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_api_client.check_discord_link.return_value = MagicMock(linked=False)
        mock_api_client.get_oauth_url.return_value = "https://discord.com/oauth2/..."

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.LinkAccountView"):
                result = await manager.check_user_linked(mock_interaction)

        assert result is False
        mock_interaction.response.send_message.assert_called_once()


class TestCheckTicketLimit:
    """Tests for ticket limit checking."""

    @pytest.mark.asyncio
    async def test_check_ticket_limit_under_limit(self, mock_interaction, mock_api_client):
        """Test that check returns True when under limit."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_api_client.list_tickets.return_value = ([], 0)

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.MAX_TICKETS_PER_USER", 5):
                result = await manager.check_ticket_limit(mock_interaction)

        assert result is True

    @pytest.mark.asyncio
    async def test_check_ticket_limit_at_limit(self, mock_interaction, mock_api_client):
        """Test that check returns False and shows message at limit."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        # Return 5 open tickets
        mock_api_client.list_tickets.side_effect = [
            ([MagicMock()] * 3, 3),  # open tickets
            ([MagicMock()] * 2, 2),  # working tickets
        ]

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.MAX_TICKETS_PER_USER", 5):
                result = await manager.check_ticket_limit(mock_interaction)

        assert result is False
        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_ticket_limit_unlimited(self, mock_interaction, mock_api_client):
        """Test that check returns True when MAX_TICKETS_PER_USER is 0.

        Regression test: the previous version of this test never patched
        ``get_api_client`` into the code path, so ``mock_api_client`` was
        never reachable by the SUT and the "API not called" assertion was
        vacuously true even if the unlimited short-circuit were removed.
        Patching ``get_api_client`` itself (not just the client it returns)
        and asserting it was never invoked makes the check meaningful.
        """
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()

        with patch(
            "Tickets.ticket_manager.get_api_client", return_value=mock_api_client
        ) as mock_get_api_client:
            with patch("Tickets.ticket_manager.MAX_TICKETS_PER_USER", 0):
                result = await manager.check_ticket_limit(mock_interaction)

        assert result is True
        # The API client must never even be fetched when unlimited.
        mock_get_api_client.assert_not_called()
        mock_api_client.list_tickets.assert_not_called()


class TestStartTicketCreation:
    """Tests for starting ticket creation flow."""

    @pytest.mark.asyncio
    async def test_start_ticket_creation_shows_modal(self, mock_interaction, mock_api_client):
        """Test that modal is shown when user is linked."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_api_client.check_discord_link.return_value = MagicMock(linked=True)
        mock_api_client.list_tickets.return_value = ([], 0)

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.MAX_TICKETS_PER_USER", 5):
                with patch("Tickets.ticket_manager.TicketCreateModal") as MockModal:
                    await manager.start_ticket_creation(mock_interaction)

        mock_interaction.response.send_modal.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_ticket_creation_shows_link_button(self, mock_interaction, mock_api_client):
        """Test that link button is shown when user is not linked."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_api_client.check_discord_link.return_value = MagicMock(linked=False)
        mock_api_client.get_oauth_url.return_value = "https://discord.com/oauth2/..."

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.LinkAccountView"):
                await manager.start_ticket_creation(mock_interaction)

        # Modal should not be shown, but message should be sent
        mock_interaction.response.send_modal.assert_not_called()
        mock_interaction.response.send_message.assert_called_once()


class TestCreateTicket:
    """Tests for ticket creation."""

    @pytest.mark.asyncio
    async def test_create_ticket_success(self, mock_interaction, mock_api_client, mock_guild):
        """Test that ticket is created successfully."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        manager._pending_tickets[123456789] = {
            "subject": "Test Subject",
            "content": "Test Content",
        }

        mock_interaction.guild = mock_guild
        mock_interaction.response.is_done.return_value = False

        ticket_data = MagicMock(
            uuid="ticket-123",
            subject="Test Subject",
            association={"name": "Test Org"},
        )
        mock_api_client.create_ticket.return_value = ticket_data

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.create_ticket_channel") as mock_create_channel:
                mock_channel = AsyncMock()
                mock_channel.id = 999888777
                mock_channel.mention = "<#999888777>"
                mock_create_channel.return_value = mock_channel

                with patch("Tickets.ticket_manager.create_welcome_embed"):
                    await manager._create_ticket_with_association(
                        mock_interaction,
                        "assoc-1",
                    )

        mock_api_client.create_ticket.assert_called_once()
        mock_interaction.followup.send.assert_called()

    @pytest.mark.asyncio
    async def test_create_ticket_renames_channel_with_uuid(self, mock_interaction, mock_api_client, mock_guild):
        """Test that the channel is renamed with the real ticket UUID."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        manager._pending_tickets[123456789] = {
            "subject": "Test Subject",
            "content": "Test Content",
        }

        mock_interaction.guild = mock_guild
        mock_interaction.response.is_done.return_value = False

        ticket_data = MagicMock(
            uuid="12345678-1234-1234-1234-123456789012",
            subject="Test Subject",
            association={"name": "Test Org"},
        )
        mock_api_client.create_ticket.return_value = ticket_data

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.create_ticket_channel") as mock_create_channel:
                mock_channel = AsyncMock()
                mock_channel.id = 999888777
                mock_channel.mention = "<#999888777>"
                mock_create_channel.return_value = mock_channel

                with patch("Tickets.ticket_manager.create_welcome_embed"):
                    await manager._create_ticket_with_association(
                        mock_interaction,
                        "assoc-1",
                    )

        mock_channel.edit.assert_called_once()
        assert "12345678" in mock_channel.edit.call_args.kwargs["name"]

    @pytest.mark.asyncio
    async def test_create_ticket_api_failure_cleans_up(self, mock_interaction, mock_api_client, mock_guild):
        """Test that channel is deleted on API error."""
        from Tickets.ticket_manager import TicketManager
        from Tickets.ticket_api_client import APIError

        manager = TicketManager()
        manager._pending_tickets[123456789] = {
            "subject": "Test Subject",
            "content": "Test Content",
        }

        mock_interaction.guild = mock_guild
        mock_interaction.response.is_done.return_value = False

        mock_api_client.create_ticket.side_effect = APIError("API Error")

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.create_ticket_channel") as mock_create_channel:
                mock_channel = AsyncMock()
                mock_channel.id = 999888777
                mock_create_channel.return_value = mock_channel

                await manager._create_ticket_with_association(
                    mock_interaction,
                    "assoc-1",
                )

        # Channel should be deleted on API failure
        mock_channel.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_ticket_channel_failure(self, mock_interaction, mock_api_client, mock_guild):
        """Test that permission error is handled."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        manager._pending_tickets[123456789] = {
            "subject": "Test Subject",
            "content": "Test Content",
        }

        mock_interaction.guild = mock_guild
        mock_interaction.response.is_done.return_value = False

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.create_ticket_channel") as mock_create_channel:
                mock_create_channel.side_effect = ValueError("Category not configured")

                await manager._create_ticket_with_association(
                    mock_interaction,
                    "assoc-1",
                )

        mock_interaction.followup.send.assert_called()
        assert "Failed" in str(mock_interaction.followup.send.call_args)

    @pytest.mark.asyncio
    async def test_create_ticket_survives_cosmetic_channel_edit_failure(
        self, mock_interaction, mock_api_client, mock_guild
    ):
        """A discord.HTTPException from the post-creation channel.edit() must
        not be reported as ticket-creation failure: the ticket and channel
        both already exist by that point, so telling the user creation
        failed would prompt a duplicate retry (MGR-2).
        """
        import discord

        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        manager._pending_tickets[123456789] = {
            "subject": "Test Subject",
            "content": "Test Content",
        }

        mock_interaction.guild = mock_guild
        mock_interaction.response.is_done.return_value = False

        ticket_data = MagicMock(
            uuid="ticket-123",
            subject="Test Subject",
            association={"name": "Test Org"},
        )
        mock_api_client.create_ticket.return_value = ticket_data

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.create_ticket_channel") as mock_create_channel:
                mock_channel = AsyncMock()
                mock_channel.id = 999888777
                mock_channel.mention = "<#999888777>"
                mock_channel.edit = AsyncMock(
                    side_effect=discord.HTTPException(MagicMock(status=500), "edit failed")
                )
                mock_create_channel.return_value = mock_channel

                with patch("Tickets.ticket_manager.create_welcome_embed"):
                    await manager._create_ticket_with_association(
                        mock_interaction,
                        "assoc-1",
                    )

        # The channel referenced by the already-created ticket must not be
        # deleted just because a cosmetic follow-up step failed.
        mock_channel.delete.assert_not_called()
        # The user must still be told the ticket was created.
        mock_interaction.followup.send.assert_called_once()
        message = str(mock_interaction.followup.send.call_args)
        assert "Ticket created" in message
        assert "Failed" not in message

    @pytest.mark.asyncio
    async def test_create_ticket_survives_cosmetic_welcome_embed_failure(
        self, mock_interaction, mock_api_client, mock_guild
    ):
        """A discord.HTTPException sending the welcome embed is logged and
        swallowed rather than aborting the rest of the flow (MGR-2).
        """
        import discord

        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        manager._pending_tickets[123456789] = {
            "subject": "Test Subject",
            "content": "Test Content",
        }

        mock_interaction.guild = mock_guild
        mock_interaction.response.is_done.return_value = False

        ticket_data = MagicMock(
            uuid="ticket-123",
            subject="Test Subject",
            association={"name": "Test Org"},
        )
        mock_api_client.create_ticket.return_value = ticket_data

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.create_ticket_channel") as mock_create_channel:
                mock_channel = AsyncMock()
                mock_channel.id = 999888777
                mock_channel.mention = "<#999888777>"
                mock_channel.send = AsyncMock(
                    side_effect=discord.HTTPException(MagicMock(status=500), "send failed")
                )
                mock_create_channel.return_value = mock_channel

                with patch("Tickets.ticket_manager.create_welcome_embed"):
                    await manager._create_ticket_with_association(
                        mock_interaction,
                        "assoc-1",
                    )

        mock_channel.delete.assert_not_called()
        mock_interaction.followup.send.assert_called_once()
        message = str(mock_interaction.followup.send.call_args)
        assert "Ticket created" in message
        assert "Failed" not in message


class TestCloseTicket:
    """Tests for closing tickets."""

    @pytest.mark.asyncio
    async def test_close_ticket_as_creator(self, mock_interaction, mock_api_client, mock_channel):
        """Test that creator can close their ticket."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        ticket = MagicMock(
            uuid="ticket-123",
            discord_creator_id=123456789,  # Same as mock_interaction.user.id
            status="open",
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=False):
                    with patch("Tickets.ticket_manager.ConfirmCloseView"):
                        await manager.close_ticket(mock_interaction)

        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_ticket_as_staff(self, mock_interaction, mock_api_client, mock_channel, mock_staff_member):
        """Test that staff can close any ticket."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(
            uuid="ticket-123",
            discord_creator_id=999999999,  # Different from staff member
            status="open",
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch("Tickets.ticket_manager.ConfirmCloseView"):
                        await manager.close_ticket(mock_interaction)

        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_ticket_unauthorized(self, mock_interaction, mock_api_client, mock_channel):
        """Test that non-creator/non-staff is denied."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        ticket = MagicMock(
            uuid="ticket-123",
            discord_creator_id=999999999,  # Different from interaction.user.id
            status="open",
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=False):
                    with patch("Tickets.ticket_manager.log_denial"):
                        await manager.close_ticket(mock_interaction)

        # Should send unauthorized message
        assert "permission" in str(mock_interaction.response.send_message.call_args).lower()

    @pytest.mark.asyncio
    async def test_close_ticket_not_ticket_channel(self, mock_interaction, mock_api_client, mock_channel):
        """Test that error is shown for non-ticket channel."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        with patch("Tickets.ticket_manager.is_ticket_channel", return_value=False):
            await manager.close_ticket(mock_interaction)

        assert "ticket channel" in str(mock_interaction.response.send_message.call_args).lower()

    @pytest.mark.asyncio
    async def test_close_ticket_does_not_scrape_transcript(self, mock_interaction, mock_api_client, mock_channel):
        """Test that close does not send a scraped transcript to the API."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        ticket = MagicMock(
            uuid="ticket-123",
            discord_creator_id=123456789,
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket
        mock_api_client.close_ticket.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch("Tickets.ticket_manager.close_ticket_channel"):
                        with patch("Tickets.ticket_manager.create_close_embed"):
                            await manager._do_close_ticket(
                                mock_interaction,
                                ticket,
                                mock_channel,
                            )

        # close_ticket is called with only the uuid (no transcript argument).
        mock_api_client.close_ticket.assert_called_once_with("ticket-123")

    @pytest.mark.asyncio
    async def test_close_ticket_permission_failure_surfaces_message(
        self, mock_interaction, mock_api_client, mock_channel
    ):
        """Test that a permission failure after API close does not 500."""
        import discord

        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        ticket = MagicMock(
            uuid="ticket-123",
            discord_creator_id=123456789,
        )
        mock_api_client.close_ticket.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch(
                "Tickets.ticket_manager.close_ticket_channel",
                side_effect=discord.HTTPException(
                    MagicMock(status=500), "permission failure"
                ),
            ):
                await manager._do_close_ticket(
                    mock_interaction,
                    ticket,
                    mock_channel,
                )

        # The API close succeeded, but the interaction did not raise.
        mock_api_client.close_ticket.assert_called_once()
        mock_interaction.followup.send.assert_called()
        assert "review" in str(mock_interaction.followup.send.call_args).lower()


class TestReopenTicket:
    """Tests for reopening tickets."""

    @pytest.mark.asyncio
    async def test_reopen_ticket_success(self, mock_interaction, mock_api_client, mock_channel, mock_staff_member):
        """Test that closed ticket is reopened."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(
            uuid="ticket-123",
            discord_creator_id=123456789,
            status="done",
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket
        mock_api_client.reopen_ticket.return_value = MagicMock(status="open")

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch("Tickets.ticket_manager.reopen_ticket_channel"):
                        with patch("Tickets.ticket_manager.create_reopen_embed"):
                            await manager.reopen_ticket(mock_interaction)

        mock_api_client.reopen_ticket.assert_called_once()

    @pytest.mark.asyncio
    async def test_reopen_ticket_staff_only(self, mock_interaction, mock_api_client, mock_channel):
        """Test that non-staff is denied."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_manager.user_is_staff", return_value=False):
                with patch("Tickets.ticket_manager.log_denial"):
                    await manager.reopen_ticket(mock_interaction)

        assert "staff" in str(mock_interaction.response.send_message.call_args).lower()

    @pytest.mark.asyncio
    async def test_reopen_ticket_not_closed(self, mock_interaction, mock_api_client, mock_channel, mock_staff_member):
        """Test that error is shown if ticket not closed."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(
            uuid="ticket-123",
            status="open",  # Not closed
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    await manager.reopen_ticket(mock_interaction)

        assert "not closed" in str(mock_interaction.response.send_message.call_args).lower()

    @pytest.mark.asyncio
    async def test_reopen_ticket_permission_failure_surfaces_message(
        self, mock_interaction, mock_api_client, mock_channel, mock_staff_member
    ):
        """Test that a permission failure after API reopen does not 500."""
        import discord

        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(
            uuid="ticket-123",
            discord_creator_id=123456789,
            status="done",
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket
        mock_api_client.reopen_ticket.return_value = MagicMock(status="open")

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch(
                        "Tickets.ticket_manager.reopen_ticket_channel",
                        side_effect=discord.HTTPException(
                            MagicMock(status=500), "permission failure"
                        ),
                    ):
                        await manager.reopen_ticket(mock_interaction)

        # The API reopen succeeded, but the interaction did not raise.
        mock_api_client.reopen_ticket.assert_called_once()
        mock_interaction.followup.send.assert_called()
        assert "review" in str(mock_interaction.followup.send.call_args).lower()


class TestAssignTicket:
    """Tests for ticket assignment."""

    @pytest.mark.asyncio
    async def test_assign_ticket_success(self, mock_interaction, mock_api_client, mock_channel, mock_staff_member):
        """Test that ticket is assigned successfully."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(uuid="ticket-123", version=3)
        mock_api_client.get_ticket_by_channel.return_value = ticket

        assignee = MagicMock()
        assignee.id = 555666777
        assignee.display_name = "Assignee"

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch("Tickets.ticket_manager.add_user_to_ticket"):
                        await manager.assign_ticket(mock_interaction, assignee)

        mock_api_client.update_ticket.assert_called_once_with(
            "ticket-123",
            version=3,
            assigned_staff_discord_id=assignee.id,
            status="working",
        )

    @pytest.mark.asyncio
    async def test_assign_ticket_adds_to_channel(self, mock_interaction, mock_api_client, mock_channel, mock_staff_member):
        """Test that staff member is added to channel."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(uuid="ticket-123", version=1)
        mock_api_client.get_ticket_by_channel.return_value = ticket

        assignee = MagicMock()
        assignee.id = 555666777

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch("Tickets.ticket_manager.add_user_to_ticket") as mock_add:
                        await manager.assign_ticket(mock_interaction, assignee)

        mock_add.assert_called_once_with(mock_channel, assignee)

    @pytest.mark.asyncio
    async def test_assign_ticket_retries_once_on_version_conflict(
        self, mock_interaction, mock_api_client, mock_channel, mock_staff_member
    ):
        """A 409 version conflict triggers exactly one refetch-and-retry."""
        from Tickets.ticket_api_client import APIError
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        stale_ticket = MagicMock(uuid="ticket-123", version=1)
        fresh_ticket = MagicMock(uuid="ticket-123", version=2)
        # First get_ticket_by_channel call (in assign_ticket) returns the
        # stale ticket; the refetch after the 409 returns the fresh one.
        mock_api_client.get_ticket_by_channel.side_effect = [stale_ticket, fresh_ticket]
        mock_api_client.update_ticket.side_effect = [
            APIError("version conflict", status_code=409),
            MagicMock(),
        ]

        assignee = MagicMock()
        assignee.id = 555666777
        assignee.display_name = "Assignee"

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch("Tickets.ticket_manager.add_user_to_ticket"):
                        await manager.assign_ticket(mock_interaction, assignee)

        assert mock_api_client.get_ticket_by_channel.call_count == 2
        assert mock_api_client.update_ticket.call_count == 2
        mock_api_client.update_ticket.assert_any_call(
            "ticket-123", version=1, assigned_staff_discord_id=assignee.id, status="working"
        )
        mock_api_client.update_ticket.assert_any_call(
            "ticket-123", version=2, assigned_staff_discord_id=assignee.id, status="working"
        )
        # The command still reports success to the user after the retry.
        mock_interaction.followup.send.assert_called_once()
        assert "assigned" in str(mock_interaction.followup.send.call_args).lower()

    @pytest.mark.asyncio
    async def test_assign_ticket_gives_up_after_second_conflict(
        self, mock_interaction, mock_api_client, mock_channel, mock_staff_member
    ):
        """A second consecutive 409 is not retried again and surfaces an error."""
        from Tickets.ticket_api_client import APIError
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        stale_ticket = MagicMock(uuid="ticket-123", version=1)
        fresh_ticket = MagicMock(uuid="ticket-123", version=2)
        mock_api_client.get_ticket_by_channel.side_effect = [stale_ticket, fresh_ticket]
        mock_api_client.update_ticket.side_effect = [
            APIError("version conflict", status_code=409),
            APIError("version conflict", status_code=409),
        ]

        assignee = MagicMock()
        assignee.id = 555666777
        assignee.display_name = "Assignee"

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    with patch("Tickets.ticket_manager.add_user_to_ticket"):
                        await manager.assign_ticket(mock_interaction, assignee)

        # Exactly one retry attempt, not an unbounded loop.
        assert mock_api_client.update_ticket.call_count == 2
        mock_interaction.followup.send.assert_called_once()
        assert "failed" in str(mock_interaction.followup.send.call_args).lower()


class TestUpdateTicketWithVersionRetry:
    """Direct tests for the ``_update_ticket_with_version_retry`` helper."""

    @pytest.mark.asyncio
    async def test_reraises_original_409_when_refetch_finds_no_ticket(
        self, mock_api_client, mock_channel
    ):
        """When the channel no longer resolves to a ticket after a 409 (the
        ticket was e.g. deleted/merged between the stale read and the
        retry), the refetch returns None and the original 409 must be
        re-raised as-is, not swallowed or replaced."""
        from Tickets.ticket_api_client import APIError
        from Tickets.ticket_manager import _update_ticket_with_version_retry

        stale_ticket = MagicMock(uuid="ticket-123", version=1)
        conflict = APIError("version conflict", status_code=409)
        mock_api_client.update_ticket.side_effect = conflict
        mock_api_client.get_ticket_by_channel.return_value = None

        with pytest.raises(APIError) as exc_info:
            await _update_ticket_with_version_retry(
                mock_api_client, mock_channel, stale_ticket, status="working"
            )

        # The exact original exception is propagated (not a new/generic one).
        assert exc_info.value is conflict
        assert exc_info.value.status_code == 409
        # No blind retry attempted once the refetch came back empty.
        mock_api_client.update_ticket.assert_called_once_with(
            "ticket-123", version=1, status="working"
        )
        mock_api_client.get_ticket_by_channel.assert_called_once_with(mock_channel.id)


class TestSetPriority:
    """Tests for setting ticket priority."""

    @pytest.mark.asyncio
    async def test_set_priority_success(self, mock_interaction, mock_api_client, mock_channel, mock_staff_member):
        """Test that priority is updated successfully."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(uuid="ticket-123", version=7)
        mock_api_client.get_ticket_by_channel.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    await manager.set_priority(mock_interaction, "high")

        mock_api_client.update_ticket.assert_called_once_with("ticket-123", version=7, priority="high")

    @pytest.mark.asyncio
    async def test_set_priority_staff_only(self, mock_interaction, mock_api_client, mock_channel):
        """Test that non-staff is denied."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_manager.user_is_staff", return_value=False):
                with patch("Tickets.ticket_manager.log_denial"):
                    await manager.set_priority(mock_interaction, "high")

        assert "staff" in str(mock_interaction.response.send_message.call_args).lower()

    @pytest.mark.asyncio
    async def test_set_priority_retries_once_on_version_conflict(
        self, mock_interaction, mock_api_client, mock_channel, mock_staff_member
    ):
        """A 409 version conflict triggers exactly one refetch-and-retry."""
        from Tickets.ticket_api_client import APIError
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        stale_ticket = MagicMock(uuid="ticket-123", version=4)
        fresh_ticket = MagicMock(uuid="ticket-123", version=5)
        mock_api_client.get_ticket_by_channel.side_effect = [stale_ticket, fresh_ticket]
        mock_api_client.update_ticket.side_effect = [
            APIError("version conflict", status_code=409),
            MagicMock(),
        ]

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    await manager.set_priority(mock_interaction, "high")

        assert mock_api_client.get_ticket_by_channel.call_count == 2
        mock_api_client.update_ticket.assert_any_call("ticket-123", version=4, priority="high")
        mock_api_client.update_ticket.assert_any_call("ticket-123", version=5, priority="high")
        mock_interaction.followup.send.assert_called_once()
        assert "high" in str(mock_interaction.followup.send.call_args).lower()

    @pytest.mark.asyncio
    async def test_set_priority_non_conflict_error_not_retried(
        self, mock_interaction, mock_api_client, mock_channel, mock_staff_member
    ):
        """A non-409 APIError is surfaced directly with no retry attempt."""
        from Tickets.ticket_api_client import APIError
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        ticket = MagicMock(uuid="ticket-123", version=1)
        mock_api_client.get_ticket_by_channel.return_value = ticket
        mock_api_client.update_ticket.side_effect = APIError("server error", status_code=500)

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                    await manager.set_priority(mock_interaction, "high")

        # No refetch/retry for a non-conflict error.
        mock_api_client.get_ticket_by_channel.assert_called_once()
        mock_api_client.update_ticket.assert_called_once_with("ticket-123", version=1, priority="high")
        mock_interaction.followup.send.assert_called_once()
        assert "failed" in str(mock_interaction.followup.send.call_args).lower()


class TestListTickets:
    """Tests for listing tickets."""

    @pytest.mark.asyncio
    async def test_list_tickets_success(self, mock_interaction, mock_api_client, mock_staff_member):
        """Test that ticket list is returned."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.user = mock_staff_member

        mock_api_client.list_tickets.return_value = (
            [MagicMock(uuid="ticket-1")],
            1,
        )

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.user_is_staff", return_value=True):
                with patch("Tickets.ticket_manager.create_ticket_list_embed"):
                    await manager.list_tickets(mock_interaction)

        mock_interaction.followup.send.assert_called()

    @pytest.mark.asyncio
    async def test_list_tickets_staff_only(self, mock_interaction, mock_api_client):
        """Test that non-staff is denied."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()

        with patch("Tickets.ticket_manager.user_is_staff", return_value=False):
            with patch("Tickets.ticket_manager.log_denial"):
                await manager.list_tickets(mock_interaction)

        assert "staff" in str(mock_interaction.response.send_message.call_args).lower()
