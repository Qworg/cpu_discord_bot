"""Tests for ticket channel reconciliation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestReconciliation:
    """Tests for orphan ticket channel reconciliation."""

    @pytest.mark.asyncio
    async def test_reconciliation_archives_orphans(self, mock_api_client):
        """Test that channels without a live ticket are archived."""
        from Tickets.ticket_reconciliation import _reconcile_orphan_channels

        guild = MagicMock()

        ticket_category = MagicMock()
        archive_category = MagicMock()

        guild.get_channel.side_effect = {
            123456789: ticket_category,
            987654321: archive_category,
        }.__getitem__

        orphan = MagicMock()
        orphan.id = 111
        orphan.name = "ticket-orphan-abc"
        orphan.category_id = None
        orphan.edit = AsyncMock()

        live = MagicMock()
        live.id = 222
        live.name = "ticket-live-abc"
        live.category_id = None
        live.edit = AsyncMock()

        # A non-ticket channel in the same category must not be archived.
        info = MagicMock()
        info.id = 333
        info.name = "info"
        info.category_id = None
        info.edit = AsyncMock()

        ticket_category.text_channels = [orphan, live, info]

        # Only the live channel has a backing ticket in the API.
        mock_api_client.list_tickets.return_value = (
            [MagicMock(discord_channel_id=222)],
            1,
        )

        with patch("Tickets.ticket_reconciliation.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_reconciliation.TICKET_CATEGORY_ID", 123456789):
                with patch("Tickets.ticket_reconciliation.TICKET_ARCHIVE_CATEGORY_ID", 987654321):
                    with patch("Tickets.ticket_reconciliation.asyncio.sleep", AsyncMock()):
                        archived = await _reconcile_orphan_channels(guild)

        assert archived == 1
        orphan.edit.assert_called_once()
        assert orphan.edit.call_args.kwargs["category"] == archive_category
        live.edit.assert_not_called()
        info.edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconciliation_skips_pending_channels(self, mock_api_client):
        """Test that channels still mid-creation are not archived."""
        from Tickets.ticket_reconciliation import _reconcile_orphan_channels

        guild = MagicMock()
        ticket_category = MagicMock()
        archive_category = MagicMock()

        guild.get_channel.side_effect = {
            123456789: ticket_category,
            987654321: archive_category,
        }.__getitem__

        pending = MagicMock()
        pending.id = 111
        pending.name = "ticket-subject-pending"
        pending.category_id = None
        pending.topic = "Support ticket: subject | ID: pending"
        pending.edit = AsyncMock()

        ticket_category.text_channels = [pending]
        mock_api_client.list_tickets.return_value = ([], 0)

        with patch("Tickets.ticket_reconciliation.get_api_client", return_value=mock_api_client):
            with patch("Tickets.ticket_reconciliation.TICKET_CATEGORY_ID", 123456789):
                with patch("Tickets.ticket_reconciliation.TICKET_ARCHIVE_CATEGORY_ID", 987654321):
                    with patch("Tickets.ticket_reconciliation.asyncio.sleep", AsyncMock()):
                        archived = await _reconcile_orphan_channels(guild)

        assert archived == 0
        pending.edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_error_handler_logs(self):
        """Test that the loop error handler logs and does not raise."""
        from Tickets.ticket_reconciliation import _reconcile_error

        with patch("Tickets.ticket_reconciliation.logger") as mock_logger:
            await _reconcile_error(RuntimeError("boom"))

        mock_logger.error.assert_called_once()

    def test_reconcile_loop_registers_error_handler(self):
        """Test that an error handler is registered on the reconcile loop."""
        from Tickets.ticket_reconciliation import reconcile_ticket_channels

        assert reconcile_ticket_channels._error is not None
