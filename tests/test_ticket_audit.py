"""Tests for ticket command denial audit logging (Req 3d scoped).

Covers the observable bot-command denials: non-staff users attempting
staff-only ticket commands must produce an audit entry.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from Tickets.ticket_audit import log_denial


def _command_callback(command):
    """Return the underlying callback from a discord Command object."""
    return getattr(command, "callback", command)


class TestLogDenialHelper:
    """Tests for the log_denial helper itself."""

    def test_log_denial_writes_log_line_and_json(
        self, mock_interaction, tmp_path, caplog, monkeypatch
    ):
        """Test that a denial is logged and appended to the JSON-lines file."""
        audit_file = tmp_path / "denials.jsonl"
        monkeypatch.setenv("TICKET_AUDIT_LOG_PATH", str(audit_file))

        with caplog.at_level(logging.INFO, logger="Tickets.ticket_audit"):
            log_denial(mock_interaction, "ticket reopen", "not staff")

        records = [
            json.loads(line)
            for line in audit_file.read_text().strip().splitlines()
        ]
        assert len(records) == 1
        record = records[0]
        assert record["event"] == "command_denied"
        assert record["command"] == "ticket reopen"
        assert record["reason"] == "not staff"
        assert record["user_id"] == 123456789
        assert record["guild_id"] == 540903470484553729

        assert "command denied" in caplog.text
        assert "123456789" in caplog.text
        assert "ticket reopen" in caplog.text


class TestManagerDenialLogging:
    """Tests that staff-only manager paths trigger log_denial."""

    @pytest.mark.asyncio
    async def test_reopen_ticket_non_staff_logs_denial(
        self, mock_interaction, mock_channel
    ):
        """Test that a non-staff reopen attempt is audited."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_manager.user_is_staff", return_value=False):
                with patch("Tickets.ticket_manager.log_denial") as mock_log:
                    await manager.reopen_ticket(mock_interaction)

        mock_log.assert_called_once_with(
            mock_interaction, "ticket reopen", "not staff"
        )

    @pytest.mark.asyncio
    async def test_close_ticket_unauthorized_logs_denial(
        self, mock_interaction, mock_api_client, mock_channel
    ):
        """Test that closing someone else's ticket as non-staff is audited."""
        from Tickets.ticket_manager import TicketManager

        manager = TicketManager()
        mock_interaction.channel = mock_channel

        ticket = MagicMock(uuid="ticket-123", discord_creator_id=999999999)
        mock_api_client.get_ticket_by_channel.return_value = ticket

        with patch("Tickets.ticket_manager.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_manager.is_ticket_channel", return_value=True):
                with patch("Tickets.ticket_manager.user_is_staff", return_value=False):
                    with patch("Tickets.ticket_manager.log_denial") as mock_log:
                        await manager.close_ticket(mock_interaction)

        mock_log.assert_called_once_with(
            mock_interaction, "ticket close", "not ticket creator and not staff"
        )


class TestCommandDenialLogging:
    """Tests that staff-only slash commands trigger log_denial."""

    @pytest.mark.asyncio
    async def test_ticketadd_non_staff_logs_denial(
        self, mock_interaction, mock_channel, mock_member
    ):
        """Test that a non-staff /ticketadd attempt is audited."""
        from Tickets.ticket_commands import ticket_add_command

        mock_interaction.channel = mock_channel
        callback = _command_callback(ticket_add_command)

        with patch("Tickets.ticket_permissions.is_ticket_channel", return_value=True):
            with patch("Tickets.ticket_permissions.user_is_staff", return_value=False):
                with patch("Tickets.ticket_commands.log_denial") as mock_log:
                    with patch("Tickets.ticket_commands.send_message_safe"):
                        await callback(mock_interaction, mock_member)

        mock_log.assert_called_once_with(
            mock_interaction, "ticketadd", "not staff"
        )
