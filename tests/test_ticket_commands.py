"""Tests for Discord slash commands for the ticketing system."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTicketCommand:
    """Tests for the /ticket command."""

    @pytest.mark.asyncio
    async def test_ticket_create_invokes_manager(self, mock_interaction):
        """Test that create action calls start_ticket_creation."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()
        mock_manager.start_ticket_creation = AsyncMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            await ticket_command(mock_interaction, action="create")

        mock_manager.start_ticket_creation.assert_called_once_with(mock_interaction)

    @pytest.mark.asyncio
    async def test_ticket_close_invokes_manager(self, mock_interaction):
        """Test that close action calls close_ticket."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()
        mock_manager.close_ticket = AsyncMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            await ticket_command(mock_interaction, action="close")

        mock_manager.close_ticket.assert_called_once_with(mock_interaction)

    @pytest.mark.asyncio
    async def test_ticket_assign_invokes_manager(self, mock_interaction, mock_member):
        """Test that assign action calls assign_ticket."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()
        mock_manager.assign_ticket = AsyncMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            await ticket_command(mock_interaction, action="assign", user=mock_member)

        mock_manager.assign_ticket.assert_called_once_with(mock_interaction, mock_member)

    @pytest.mark.asyncio
    async def test_ticket_assign_no_user_error(self, mock_interaction):
        """Test that assign without user shows error."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                await ticket_command(mock_interaction, action="assign", user=None)

        mock_send.assert_called_once()
        assert "user" in str(mock_send.call_args).lower()

    @pytest.mark.asyncio
    async def test_ticket_list_invokes_manager(self, mock_interaction):
        """Test that list action calls list_tickets."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()
        mock_manager.list_tickets = AsyncMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            await ticket_command(mock_interaction, action="list")

        mock_manager.list_tickets.assert_called_once_with(mock_interaction)

    @pytest.mark.asyncio
    async def test_ticket_priority_invokes_manager(self, mock_interaction):
        """Test that priority action calls set_priority."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()
        mock_manager.set_priority = AsyncMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            await ticket_command(mock_interaction, action="priority", priority="high")

        mock_manager.set_priority.assert_called_once_with(mock_interaction, "high")

    @pytest.mark.asyncio
    async def test_ticket_priority_no_value_error(self, mock_interaction):
        """Test that priority without value shows error."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                await ticket_command(mock_interaction, action="priority", priority=None)

        mock_send.assert_called_once()
        assert "priority" in str(mock_send.call_args).lower()

    @pytest.mark.asyncio
    async def test_ticket_reopen_invokes_manager(self, mock_interaction):
        """Test that reopen action calls reopen_ticket."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()
        mock_manager.reopen_ticket = AsyncMock()

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            await ticket_command(mock_interaction, action="reopen")

        mock_manager.reopen_ticket.assert_called_once_with(mock_interaction)


class TestLinkCommand:
    """Tests for the /link command."""

    @pytest.mark.asyncio
    async def test_link_command_not_linked(self, mock_interaction, mock_api_client):
        """Test that link button is shown when not linked."""
        from Tickets.ticket_commands import link_command

        mock_api_client.check_discord_link.return_value = MagicMock(linked=False)
        mock_api_client.get_oauth_url.return_value = "https://discord.com/oauth2/..."

        with patch("Tickets.ticket_commands.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                with patch("Tickets.ticket_commands.LinkAccountView"):
                    await link_command(mock_interaction)

        mock_send.assert_called()
        # Should include view parameter (the link button)
        assert mock_send.call_args[1].get("view") is not None or "view" in str(mock_send.call_args)

    @pytest.mark.asyncio
    async def test_link_command_already_linked(self, mock_interaction, mock_api_client):
        """Test that message is shown when already linked."""
        from Tickets.ticket_commands import link_command

        mock_api_client.check_discord_link.return_value = MagicMock(
            linked=True,
            member_name="Test User",
        )

        with patch("Tickets.ticket_commands.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                await link_command(mock_interaction)

        mock_send.assert_called()
        assert "already linked" in str(mock_send.call_args).lower()


class TestTicketInfoCommand:
    """Tests for the /ticketinfo command."""

    @pytest.mark.asyncio
    async def test_ticketinfo_shows_embed(self, mock_interaction, mock_api_client, mock_channel):
        """Test that ticket embed is shown."""
        from Tickets.ticket_commands import ticket_info_command

        mock_interaction.channel = mock_channel

        ticket = MagicMock(
            uuid="ticket-123",
            subject="Test",
            status="open",
            priority="low",
        )
        mock_api_client.get_ticket_by_channel.return_value = ticket

        with patch("Tickets.ticket_commands.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_commands.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                    with patch("Tickets.ticket_commands.create_ticket_embed") as mock_embed:
                        await ticket_info_command(mock_interaction)

        mock_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_ticketinfo_not_ticket_channel(self, mock_interaction, mock_channel):
        """Test that error is shown for non-ticket channel."""
        from Tickets.ticket_commands import ticket_info_command

        mock_interaction.channel = mock_channel

        with patch("Tickets.ticket_commands.is_ticket_channel", return_value=False):
            with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                await ticket_info_command(mock_interaction)

        mock_send.assert_called()
        assert "ticket channel" in str(mock_send.call_args).lower()


class TestTicketAddCommand:
    """Tests for the /ticketadd command."""

    @pytest.mark.asyncio
    async def test_ticketadd_adds_user(self, mock_interaction, mock_channel, mock_member, mock_staff_member):
        """Test that user is added to ticket."""
        from Tickets.ticket_commands import ticket_add_command

        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        with patch("Tickets.ticket_commands.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_commands.user_is_staff", return_value=True):
                with patch("Tickets.ticket_commands.add_user_to_ticket") as mock_add:
                    with patch("Tickets.ticket_commands.send_message_safe"):
                        await ticket_add_command(mock_interaction, mock_member)

        mock_add.assert_called_once_with(mock_channel, mock_member)

    @pytest.mark.asyncio
    async def test_ticketadd_not_staff_denied(self, mock_interaction, mock_channel, mock_member):
        """Test that non-staff is denied."""
        from Tickets.ticket_commands import ticket_add_command

        mock_interaction.channel = mock_channel

        with patch("Tickets.ticket_commands.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_commands.user_is_staff", return_value=False):
                with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                    await ticket_add_command(mock_interaction, mock_member)

        mock_send.assert_called()
        assert "staff" in str(mock_send.call_args).lower()


class TestTicketRemoveCommand:
    """Tests for the /ticketremove command."""

    @pytest.mark.asyncio
    async def test_ticketremove_removes_user(self, mock_interaction, mock_channel, mock_member, mock_staff_member):
        """Test that user is removed from ticket."""
        from Tickets.ticket_commands import ticket_remove_command

        mock_interaction.channel = mock_channel
        mock_interaction.user = mock_staff_member

        with patch("Tickets.ticket_commands.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_commands.user_is_staff", return_value=True):
                with patch("Tickets.ticket_commands.remove_user_from_ticket") as mock_remove:
                    with patch("Tickets.ticket_commands.send_message_safe"):
                        await ticket_remove_command(mock_interaction, mock_member)

        mock_remove.assert_called_once_with(mock_channel, mock_member)

    @pytest.mark.asyncio
    async def test_ticketremove_not_staff_denied(self, mock_interaction, mock_channel, mock_member):
        """Test that non-staff is denied."""
        from Tickets.ticket_commands import ticket_remove_command

        mock_interaction.channel = mock_channel

        with patch("Tickets.ticket_commands.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_commands.user_is_staff", return_value=False):
                with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                    await ticket_remove_command(mock_interaction, mock_member)

        mock_send.assert_called()
        assert "staff" in str(mock_send.call_args).lower()


class TestCommandErrorHandling:
    """Tests for command error handling."""

    @pytest.mark.asyncio
    async def test_command_error_handling(self, mock_interaction):
        """Test that errors are caught and reported."""
        from Tickets.ticket_commands import ticket_command

        mock_manager = MagicMock()
        mock_manager.start_ticket_creation = AsyncMock(side_effect=Exception("Test error"))

        with patch("Tickets.ticket_commands.get_ticket_manager", return_value=mock_manager):
            with patch("Tickets.ticket_commands.send_message_safe") as mock_send:
                await ticket_command(mock_interaction, action="create")

        mock_send.assert_called()
        assert "error" in str(mock_send.call_args).lower()
