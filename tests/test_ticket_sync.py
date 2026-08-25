"""Tests for ticket outbox sync and outbound message relay."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from Tickets.ticket_api_client import APIError
from Tickets.ticket_permissions import generate_channel_name
from Tickets.ticket_sync import (
    ChannelTicketResolver,
    OutboundMessageRelay,
    ReconnectBackfill,
    TicketEventSync,
)


def make_ticket(**overrides):
    """Build a fake ticket object with ticket-sync-relevant attributes."""
    ticket = MagicMock()
    ticket.uuid = "ticket-123"
    ticket.subject = "Test subject"
    ticket.status = "open"
    ticket.priority = "low"
    ticket.discord_channel_id = None
    ticket.discord_creator_id = None
    ticket.association = None
    for key, value in overrides.items():
        setattr(ticket, key, value)
    return ticket


def make_event(event_id, event_type, ticket_uuid="ticket-123", payload=None):
    """Build a fake outbox event dict."""
    return {
        "id": event_id,
        "event_type": event_type,
        "ticket_uuid": ticket_uuid,
        "payload": payload or {},
        "source": "api",
    }


def make_history_message(message_id, content="hello", author_id=123, is_bot=False, channel_id=456):
    """Build a fake Discord message as returned by ``channel.history``."""
    message = MagicMock()
    message.id = message_id
    message.content = content
    message.channel = MagicMock()
    message.channel.id = channel_id
    message.author = MagicMock()
    message.author.id = author_id
    message.author.bot = is_bot
    message.author.display_name = "TestUser"
    message.author.name = "TestUser"
    message.created_at = MagicMock()
    message.created_at.isoformat.return_value = "2024-01-01T12:00:00+00:00"
    message.attachments = []
    return message


def async_iter(items):
    """Wrap a list in an async iterator (mirrors discord's history iterator)."""
    async def _gen():
        for item in items:
            yield item

    return _gen()


@pytest.fixture
def fake_api():
    """Build a fake TicketAPIClient."""
    api = MagicMock()
    api.get_events = AsyncMock(return_value=[])
    api.ack_events = AsyncMock()
    api.get_ticket = AsyncMock()
    api.get_ticket_by_channel = AsyncMock(return_value=None)
    api.list_tickets = AsyncMock(return_value=([], 0))
    api.post_outbound_message = AsyncMock()
    api.update_message = AsyncMock()
    api.delete_message = AsyncMock()
    api.write_back_channel_id = AsyncMock()
    return api


@pytest.fixture
def fake_bot():
    """Build a fake discord bot exposing get_guild/get_channel."""
    bot = MagicMock()
    guild = MagicMock()
    guild.get_member.return_value = None
    guild.get_channel.return_value = None
    bot.get_guild.return_value = guild
    bot.get_channel.return_value = None
    return bot


def make_sync(fake_api, fake_bot, **kwargs):
    """Build a TicketEventSync with sleeps and state persistence disabled."""
    kwargs.setdefault("state_file", None)
    return TicketEventSync(api=fake_api, bot=fake_bot, **kwargs)


class TestTicketEventSync:
    """Tests for the outbox poll/apply loop."""

    @pytest.mark.asyncio
    async def test_channel_create_creates_channel_and_acks(self, fake_api, fake_bot):
        """channel_create with no existing channel creates one and acks."""
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.return_value = make_ticket(
            discord_channel_id=None,
            discord_creator_id=None,
        )
        created = MagicMock()
        created.id = 999888777

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.create_ticket_channel", new=AsyncMock(return_value=created)) as create:
            applied = await sync.poll_once()

        assert applied == 1
        create.assert_awaited_once()
        assert sync.last_acked_id == 1
        fake_api.ack_events.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_channel_create_is_idempotent_on_redelivery(self, fake_api, fake_bot):
        """A re-delivered channel_create does not create a second channel."""
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.return_value = make_ticket()
        created = MagicMock()
        created.id = 999888777

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.create_ticket_channel", new=AsyncMock(return_value=created)) as create:
            await sync.poll_once()
            # Simulate the server re-delivering the same event (ack was lost).
            await sync.poll_once()

        create.assert_awaited_once()
        fake_api.ack_events.assert_awaited_with([1])

    @pytest.mark.asyncio
    async def test_channel_create_adopts_existing_channel(self, fake_api, fake_bot):
        """channel_create with discord_channel_id already set adopts, no duplicate."""
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.return_value = make_ticket(discord_channel_id=555)
        existing = MagicMock()
        existing.id = 555
        fake_bot.get_channel.return_value = existing

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.create_ticket_channel", new=AsyncMock()) as create:
            applied = await sync.poll_once()

        assert applied == 1
        create.assert_not_called()
        fake_api.ack_events.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_closed_event_closes_channel(self, fake_api, fake_bot):
        """A closed event calls close_ticket_channel for the ticket channel."""
        ticket = make_ticket(discord_channel_id=123, discord_creator_id=456)
        channel = MagicMock()
        channel.name = "ticket-test-abc123"
        channel.id = 123
        fake_api.get_events.return_value = [make_event(1, "closed")]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()) as close:
            await sync.poll_once()

        close.assert_awaited_once_with(channel, discord_creator_id=456)
        fake_api.ack_events.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_closed_event_skips_when_already_closed(self, fake_api, fake_bot):
        """A closed event is a no-op when the channel is already closed-."""
        ticket = make_ticket(discord_channel_id=123, discord_creator_id=456)
        channel = MagicMock()
        channel.name = "closed-ticket-test-abc123"
        channel.id = 123
        fake_api.get_events.return_value = [make_event(1, "closed")]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()) as close:
            await sync.poll_once()

        close.assert_not_called()
        fake_api.ack_events.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_reopened_event_reopens_channel(self, fake_api, fake_bot):
        """A reopened event calls reopen_ticket_channel."""
        ticket = make_ticket(discord_channel_id=123, discord_creator_id=456)
        channel = MagicMock()
        channel.name = "closed-ticket-test-abc123"
        channel.id = 123
        fake_api.get_events.return_value = [make_event(1, "reopened")]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.reopen_ticket_channel", new=AsyncMock()) as reopen:
            await sync.poll_once()

        reopen.assert_awaited_once_with(channel, discord_creator_id=456)
        fake_api.ack_events.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_reopened_event_skips_when_already_open(self, fake_api, fake_bot):
        """A reopened event is a no-op when the channel is already open."""
        ticket = make_ticket(discord_channel_id=123, discord_creator_id=456)
        channel = MagicMock()
        channel.name = "ticket-test-abc123"
        channel.id = 123
        fake_api.get_events.return_value = [make_event(1, "reopened")]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.reopen_ticket_channel", new=AsyncMock()) as reopen:
            await sync.poll_once()

        reopen.assert_not_called()
        fake_api.ack_events.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_ack_batch_collapses_round_trip(self, fake_api, fake_bot):
        """Multiple applied events are acked in a single batch."""
        fake_api.get_events.return_value = [
            make_event(1, "channel_create"),
            make_event(2, "priority_changed"),
            make_event(3, "closed"),
        ]
        fake_api.get_ticket.return_value = make_ticket(discord_channel_id=123)
        channel = MagicMock()
        channel.name = "ticket-test-abc123"
        channel.id = 123
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with (
            patch("Tickets.ticket_sync.create_ticket_channel", new=AsyncMock(return_value=MagicMock(id=9))),
            patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()),
        ):
            applied = await sync.poll_once()

        assert applied == 3
        fake_api.ack_events.assert_awaited_once_with([1, 2, 3])
        assert sync.last_acked_id == 3

    @pytest.mark.asyncio
    async def test_adaptive_backoff_and_reset(self, fake_api, fake_bot):
        """Empty polls back off 5->10->15 and reset to base on activity."""
        sync = make_sync(fake_api, fake_bot, poll_seconds=3, max_attempts=1)

        # Three consecutive empty polls reach the first backoff step.
        for _ in range(3):
            await sync.poll_once()
        assert sync.empty_polls == 3
        assert sync.current_interval == 5.0

        await sync.poll_once()
        assert sync.current_interval == 10.0

        await sync.poll_once()
        assert sync.current_interval == 15.0

        # Ceiling holds at 15s.
        await sync.poll_once()
        assert sync.current_interval == 15.0

        # A non-empty poll resets to the injected base interval.
        fake_api.get_events.return_value = [make_event(1, "priority_changed")]
        await sync.poll_once()
        assert sync.empty_polls == 0
        assert sync.current_interval == 3.0

    @pytest.mark.asyncio
    async def test_injectable_interval(self, fake_api, fake_bot):
        """The base poll interval is injectable and used after reset."""
        sync = make_sync(fake_api, fake_bot, poll_seconds=7, max_attempts=1)

        for _ in range(4):
            await sync.poll_once()
        assert sync.current_interval == 10.0

        fake_api.get_events.return_value = [make_event(1, "priority_changed")]
        await sync.poll_once()
        assert sync.current_interval == 7.0

    @pytest.mark.asyncio
    async def test_apply_failure_sets_last_error_and_does_not_drop(self, fake_api, fake_bot):
        """A permanently failing event sets sync_failed/last_error and is not acked."""
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.side_effect = APIError("boom", 500)

        sync = make_sync(fake_api, fake_bot, max_attempts=3)

        with patch("Tickets.ticket_sync.asyncio.sleep", new=AsyncMock()):
            applied = await sync.poll_once()

        assert applied == 0
        assert sync.sync_failed == 1
        assert sync.last_error is not None
        assert "boom" in sync.last_error
        # The failed event is left in the outbox (never acked).
        fake_api.ack_events.assert_not_called()
        assert sync.last_acked_id == 0

    @pytest.mark.asyncio
    async def test_poll_fetch_failure_is_non_fatal(self, fake_api, fake_bot):
        """A fetch error is logged and does not advance state."""
        fake_api.get_events.side_effect = APIError("down", 503)

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        applied = await sync.poll_once()

        assert applied == 0
        assert sync.last_acked_id == 0
        assert sync.sync_failed == 0

    @pytest.mark.asyncio
    async def test_assigned_adds_user_and_skips_when_present(self, fake_api, fake_bot):
        """assigned grants access once; a second delivery is a no-op."""
        ticket = make_ticket(discord_channel_id=123)
        channel = MagicMock()
        channel.name = "ticket-test-abc123"
        channel.id = 123
        channel.guild = fake_bot.get_guild.return_value

        member = MagicMock()
        member.id = 777
        channel.guild.get_member.return_value = member

        # First delivery: member lacks access -> add_user_to_ticket called.
        channel.permissions_for.return_value = MagicMock(
            read_messages=False, send_messages=False
        )
        fake_api.get_events.return_value = [
            make_event(1, "assigned", payload={"assigned_staff_discord_id": 777})
        ]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.add_user_to_ticket", new=AsyncMock()) as add:
            await sync.poll_once()

        add.assert_awaited_once_with(channel, member)
        fake_api.ack_events.assert_awaited_once_with([1])

        # Second delivery (already applied) -> no additional add call.
        await sync.poll_once()
        add.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_create_writes_back_channel_id(self, fake_api, fake_bot):
        """channel_create reports the new channel id back to the API."""
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.return_value = make_ticket()
        created = MagicMock()
        created.id = 999888777

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch(
            "Tickets.ticket_sync.create_ticket_channel",
            new=AsyncMock(return_value=created),
        ):
            await sync.poll_once()

        fake_api.write_back_channel_id.assert_awaited_once_with(
            "ticket-123", 999888777
        )

    @pytest.mark.asyncio
    async def test_channel_create_adopts_existing_channel_by_name(self, fake_api, fake_bot):
        """A channel_create adopts an existing same-named channel (no duplicate)."""
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.return_value = make_ticket()  # discord_channel_id=None

        existing = MagicMock()
        existing.id = 555
        existing.name = generate_channel_name("Test subject", "ticket-123")

        guild = fake_bot.get_guild.return_value
        category = MagicMock()
        category.text_channels = [existing]
        guild.get_channel.return_value = category

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.create_ticket_channel", new=AsyncMock()) as create:
            applied = await sync.poll_once()

        create.assert_not_called()
        fake_api.write_back_channel_id.assert_awaited_once_with("ticket-123", 555)
        fake_api.ack_events.assert_awaited_once_with([1])
        assert applied == 1

    @pytest.mark.asyncio
    async def test_restart_does_not_duplicate_channel(self, fake_api, fake_bot, tmp_path):
        """A re-delivered channel_create after a restart is a no-op via state."""
        state_file = str(tmp_path / "sync_state.json")
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.return_value = make_ticket()
        created = MagicMock()
        created.id = 999888777

        # First run: create the channel, but the ack is lost (crash before ack).
        fake_api.ack_events = AsyncMock(side_effect=APIError("ack lost", 500))
        sync1 = make_sync(fake_api, fake_bot, max_attempts=1, state_file=state_file)

        with (
            patch(
                "Tickets.ticket_sync.create_ticket_channel",
                new=AsyncMock(return_value=created),
            ) as create,
            pytest.raises(APIError),
        ):
            await sync1.poll_once()
        create.assert_awaited_once()

        # Restart: a fresh instance reloads the persisted applied-id set.
        fake_api.ack_events = AsyncMock()
        sync2 = make_sync(fake_api, fake_bot, max_attempts=1, state_file=state_file)

        with patch("Tickets.ticket_sync.create_ticket_channel", new=AsyncMock()) as create2:
            applied = await sync2.poll_once()

        create2.assert_not_called()
        fake_api.ack_events.assert_awaited_once_with([1])
        assert applied == 0
        assert sync2.last_acked_id == 1

    @pytest.mark.asyncio
    async def test_channel_requiring_event_raises_when_channel_missing(
        self, fake_api, fake_bot
    ):
        """A channel-requiring event with no channel surfaces, not acked."""
        fake_api.get_events.return_value = [make_event(1, "closed")]
        fake_api.get_ticket.return_value = make_ticket(discord_channel_id=123)
        fake_bot.get_channel.return_value = None

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        applied = await sync.poll_once()

        assert applied == 0
        assert sync.sync_failed == 1
        fake_api.ack_events.assert_not_called()
        assert sync.last_acked_id == 0

    @pytest.mark.asyncio
    async def test_channel_update_rename_failure_raises(self, fake_api, fake_bot):
        """A rename failure is surfaced via retry, never acked as success."""
        ticket = make_ticket(discord_channel_id=123)
        channel = MagicMock()
        channel.name = "ticket-old-abc123"
        channel.id = 123
        channel.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "rename failed")
        )

        fake_api.get_events.return_value = [
            make_event(1, "channel_update", payload={"subject": "New subject"})
        ]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        applied = await sync.poll_once()

        assert applied == 0
        assert sync.sync_failed == 1
        fake_api.ack_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_archive_missing_category_raises(self, fake_api, fake_bot):
        """An archive with a missing category surfaces, not acked as success."""
        ticket = make_ticket(discord_channel_id=123)
        channel = MagicMock()
        channel.name = "ticket-test-abc123"
        channel.id = 123
        channel.category_id = 999
        channel.guild = MagicMock()
        channel.guild.get_channel.return_value = None

        fake_api.get_events.return_value = [make_event(1, "deleted")]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.TICKET_ARCHIVE_CATEGORY_ID", 987654321):
            applied = await sync.poll_once()

        assert applied == 0
        assert sync.sync_failed == 1
        fake_api.ack_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_changed_missing_to_status_is_noop(self, fake_api, fake_bot):
        """A status_changed without to_status does not reopen a closed channel."""
        ticket = make_ticket(discord_channel_id=123)
        channel = MagicMock()
        channel.name = "closed-ticket-test-abc123"
        channel.id = 123

        fake_api.get_events.return_value = [make_event(1, "status_changed", payload={})]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with (
            patch("Tickets.ticket_sync.reopen_ticket_channel", new=AsyncMock()) as reopen,
            patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()) as close,
        ):
            applied = await sync.poll_once()

        reopen.assert_not_called()
        close.assert_not_called()
        fake_api.ack_events.assert_awaited_once_with([1])
        assert applied == 1

    @pytest.mark.asyncio
    async def test_reply_sends_message(self, fake_api, fake_bot):
        """A reply event posts the web-authored message in the channel."""
        ticket = make_ticket(discord_channel_id=123)
        channel = MagicMock()
        channel.id = 123
        channel.name = "ticket-test-abc123"
        channel.send = AsyncMock()

        fake_api.get_events.return_value = [
            make_event(1, "reply", payload={"content": "Hello from web", "author_name": "Alice"})
        ]
        fake_api.get_ticket.return_value = ticket
        fake_bot.get_channel.return_value = channel

        sync = make_sync(fake_api, fake_bot, max_attempts=1)
        applied = await sync.poll_once()

        channel.send.assert_awaited_once_with("**Alice**: Hello from web")
        assert applied == 1
        fake_api.ack_events.assert_awaited_once_with([1])

    @pytest.mark.asyncio
    async def test_failing_event_cooldown_skips_and_counts_distinct(
        self, fake_api, fake_bot
    ):
        """A permanently failing event is cooled down, not retried every poll."""
        fake_api.get_events.return_value = [make_event(1, "channel_create")]
        fake_api.get_ticket.side_effect = APIError("boom", 500)

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.asyncio.sleep", new=AsyncMock()):
            await sync.poll_once()

        assert sync.sync_failed == 1

        # Inside the cooldown window the event is skipped entirely.
        with patch("Tickets.ticket_sync.time.monotonic", return_value=0.0):
            await sync.poll_once()

        assert sync.sync_failed == 1  # distinct id counted once, not per poll
        assert fake_api.get_ticket.await_count == 1

    @pytest.mark.asyncio
    async def test_fetch_failure_increments_empty_polls(self, fake_api, fake_bot):
        """A down server backs off the same way an empty outbox does."""
        fake_api.get_events.side_effect = APIError("down", 503)

        sync = make_sync(fake_api, fake_bot, poll_seconds=3, max_attempts=1)

        for _ in range(3):
            await sync.poll_once()

        assert sync.empty_polls == 3
        assert sync.current_interval == 5.0

    @pytest.mark.asyncio
    async def test_permanent_failure_does_not_block_subsequent_events(
        self, fake_api, fake_bot
    ):
        """A permanently-failing event (unresolvable channel) must not block
        later events for other tickets behind it in the outbox (MINE-5)."""
        ticket1 = make_ticket(uuid="ticket-1", discord_channel_id=111)
        ticket2 = make_ticket(uuid="ticket-2", discord_channel_id=222)

        channel2 = MagicMock()
        channel2.name = "ticket-two-abc123"
        channel2.id = 222

        fake_api.get_events.return_value = [
            make_event(1, "closed", ticket_uuid="ticket-1"),
            make_event(2, "closed", ticket_uuid="ticket-2"),
        ]

        async def get_ticket(ticket_uuid):
            return ticket1 if ticket_uuid == "ticket-1" else ticket2

        fake_api.get_ticket.side_effect = get_ticket
        fake_bot.get_channel.side_effect = lambda cid: channel2 if cid == 222 else None

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()) as close_mock:
            applied = await sync.poll_once()

        # ticket-2's event applied even though ticket-1's event (ordered
        # first in the outbox) permanently failed on an unresolvable
        # channel.
        assert applied == 1
        close_mock.assert_awaited_once_with(
            channel2, discord_creator_id=ticket2.discord_creator_id
        )
        assert sync.sync_failed == 1
        # The permanently-failed event is left un-acked (visible/retryable),
        # never silently dropped, but it did not block event 2's ack either.
        acked_ids = [call.args[0] for call in fake_api.ack_events.await_args_list]
        assert acked_ids == [[2]]

    @pytest.mark.asyncio
    async def test_permanent_failure_skipped_not_blocking_while_cooling_down(
        self, fake_api, fake_bot
    ):
        """Once recorded, a permanent failure is skipped (not re-attempted,
        and not a queue-blocking break) on the very next poll cycle too, so
        that a later, not-yet-applied event ordered behind it still gets
        applied instead of being starved by the cooling-down failure.

        A prior version of this test only had a second event that was
        already applied+acked in cycle 1, so it short-circuited on the
        ``applied_ids`` check before the continue-vs-break branch under test
        was ever reached; it passed even with the fix reverted. Event 3 here
        is new as of cycle 2 and is not in ``applied_ids``, so it can only
        apply if the loop actually continues past event 1 instead of
        breaking.
        """
        ticket1 = make_ticket(uuid="ticket-1", discord_channel_id=111)
        ticket2 = make_ticket(uuid="ticket-2", discord_channel_id=222)
        ticket3 = make_ticket(uuid="ticket-3", discord_channel_id=333)
        channel2 = MagicMock()
        channel2.name = "ticket-two-abc123"
        channel2.id = 222
        channel3 = MagicMock()
        channel3.name = "ticket-three-abc123"
        channel3.id = 333

        # Event 3 only shows up in the outbox from the second poll onward
        # (e.g. it was created after cycle 1 ran), so it is genuinely
        # unapplied when cycle 2 reaches it.
        fake_api.get_events.side_effect = [
            [
                make_event(1, "closed", ticket_uuid="ticket-1"),
                make_event(2, "closed", ticket_uuid="ticket-2"),
            ],
            [
                make_event(1, "closed", ticket_uuid="ticket-1"),
                make_event(2, "closed", ticket_uuid="ticket-2"),
                make_event(3, "closed", ticket_uuid="ticket-3"),
            ],
        ]

        tickets_by_uuid = {"ticket-1": ticket1, "ticket-2": ticket2, "ticket-3": ticket3}
        channels_by_id = {222: channel2, 333: channel3}

        async def get_ticket(ticket_uuid):
            return tickets_by_uuid[ticket_uuid]

        fake_api.get_ticket.side_effect = get_ticket
        fake_bot.get_channel.side_effect = lambda cid: channels_by_id.get(cid)

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()) as close_mock:
            await sync.poll_once()

            assert sync.sync_failed == 1
            fake_api.get_ticket.reset_mock(side_effect=False)
            fake_api.get_ticket.side_effect = get_ticket

            # Second poll: event 1 is still well within its cooldown window
            # (the real monotonic clock is used; the cooldown is 60s+),
            # event 2 was already applied/acked in the previous cycle, and
            # event 3 is new.
            applied = await sync.poll_once()

        assert applied == 1
        close_mock.assert_any_call(channel3, discord_creator_id=ticket3.discord_creator_id)
        # Only event 3 required a ticket fetch this cycle: event 1 is
        # skipped while cooling down, event 2 is a no-op re-ack.
        fake_api.get_ticket.assert_awaited_once_with("ticket-3")
        acked_ids = [call.args[0] for call in fake_api.ack_events.await_args_list]
        assert acked_ids[-1] == [2, 3]

    @pytest.mark.asyncio
    async def test_cursor_never_skips_a_permanently_failed_event(
        self, fake_api, fake_bot
    ):
        """The local cursor (last_acked_id) must never advance to or past an
        id that was skipped (a permanent failure, never acked), even though
        a *higher* id was acked in the same cycle (FIX-BOT-1).

        This is currently harmless only because the live server ignores
        `since` and re-serves every unacked event regardless of cursor. But
        `get_events`'s documented contract is "events with id > since", so
        this test's double -- unlike every other test double in this file --
        ACTUALLY enforces that filter, proving the bot-side cursor logic
        alone (not a lenient server) is what keeps a permanently-failed
        event from being silently, permanently dropped once whatever made
        it fail (e.g. an unresolvable channel) clears up.
        """
        ticket1 = make_ticket(uuid="ticket-1", discord_channel_id=111)
        ticket2 = make_ticket(uuid="ticket-2", discord_channel_id=222)

        channel1 = MagicMock()
        channel1.name = "ticket-one-abc123"
        channel1.id = 111
        channel2 = MagicMock()
        channel2.name = "ticket-two-abc123"
        channel2.id = 222

        all_events = [
            make_event(1, "closed", ticket_uuid="ticket-1"),
            make_event(2, "closed", ticket_uuid="ticket-2"),
        ]

        # Channel 111 starts unresolvable (event 1 permanently fails), then
        # becomes resolvable before the second poll (e.g. the bot regains
        # visibility into it) -- at which point event 1 must still be
        # deliverable.
        channel_available = {111: False}

        def get_channel(cid):
            if cid == 111:
                return channel1 if channel_available[111] else None
            if cid == 222:
                return channel2
            return None

        fake_bot.get_channel.side_effect = get_channel

        async def get_ticket(ticket_uuid):
            return ticket1 if ticket_uuid == "ticket-1" else ticket2

        fake_api.get_ticket.side_effect = get_ticket

        # A get_events double that ACTUALLY enforces `since` (unlike the
        # live server today): only events with id > since are returned.
        async def get_events(since, limit):
            return [e for e in all_events if e["id"] > since]

        fake_api.get_events.side_effect = get_events

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        with patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()):
            applied = await sync.poll_once()

        # Event 1 (ticket-1, unresolvable channel) permanently failed; event
        # 2 (a higher id) applied and was acked.
        assert applied == 1
        assert sync.sync_failed == 1
        # The cursor must NOT have advanced to/past event 1's id, even
        # though event 2 was acked -- otherwise a since-filtering server
        # would never re-serve event 1 again.
        assert sync.last_acked_id == 0

        # Event 1's channel becomes resolvable and its cooldown expires.
        channel_available[111] = True
        with (
            patch("Tickets.ticket_sync.time.monotonic", return_value=1e9),
            patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()) as close_mock2,
        ):
            applied = await sync.poll_once()

        # With since=last_acked_id=0, the enforcing double still returns
        # event 1 -- proving it was not stranded below the cursor. Had the
        # cursor incorrectly advanced to 2 in the first cycle, this
        # since=0-preserved poll would be the only chance to observe the
        # regression: event 1 would never be requested from the server
        # again, a silent, permanent loss.
        assert applied == 1
        assert sync.sync_failed == 0
        close_mock2.assert_awaited_once_with(
            channel1, discord_creator_id=ticket1.discord_creator_id
        )

    @pytest.mark.asyncio
    async def test_state_persisted_after_each_event_not_only_batch_end(
        self, fake_api, fake_bot, tmp_path
    ):
        """State is persisted after each applied event, not once at the end
        of the batch, so a mid-batch abort cannot replay events already
        applied earlier in the same batch (SYNC-1..4)."""
        state_file = str(tmp_path / "state.json")
        fake_api.get_events.return_value = [
            make_event(1, "channel_create"),
            make_event(2, "channel_create", ticket_uuid="ticket-456"),
        ]

        sync = TicketEventSync(
            api=fake_api, bot=fake_bot, max_attempts=1, state_file=state_file
        )

        seen_before_second_event = []

        async def fake_apply(event):
            if event["id"] == 2:
                with open(state_file, encoding="utf-8") as f:  # noqa: ASYNC230 - test-only inline read
                    seen_before_second_event.append(json.load(f)["applied_ids"])

        sync.apply_event = fake_apply

        await sync.poll_once()

        # By the time event 2 was being applied, event 1's success had
        # already been durably written to disk.
        assert seen_before_second_event == [[1]]

    @pytest.mark.asyncio
    async def test_cancellation_saves_progress_before_reraising(
        self, fake_api, fake_bot, tmp_path
    ):
        """A CancelledError mid-batch (e.g. a graceful restart) persists the
        progress made so far and re-raises rather than being swallowed
        (SYNC-1..4). Ordinary ``except Exception`` handling must never catch
        it, since ``CancelledError`` is a BaseException."""
        state_file = str(tmp_path / "state.json")
        fake_api.get_events.return_value = [
            make_event(1, "channel_create"),
            make_event(2, "channel_create", ticket_uuid="ticket-456"),
        ]

        sync = TicketEventSync(
            api=fake_api, bot=fake_bot, max_attempts=1, state_file=state_file
        )

        async def fake_apply(event):
            if event["id"] == 2:
                raise asyncio.CancelledError()

        sync.apply_event = fake_apply

        with pytest.raises(asyncio.CancelledError):
            await sync.poll_once()

        with open(state_file, encoding="utf-8") as f:  # noqa: ASYNC230 - test-only inline read
            on_disk = json.load(f)
        assert on_disk["applied_ids"] == [1]
        # The batch never reached the ack call for event 1.
        fake_api.ack_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_create_unresolvable_category_is_permanent_not_blocking(
        self, fake_api, fake_bot
    ):
        """A channel_create event whose ticket category cannot be resolved
        (a misconfigured or deleted TICKET_CATEGORY_ID) must classify as a
        PERMANENT failure, not a transient one, so it is skipped rather than
        blocking every later event in the globally-ordered outbox (the
        head-of-line-blocking regression fixed here).

        Unlike the other channel_create tests in this file,
        ``create_ticket_channel`` is NOT mocked: the real function (and its
        real ``ValueError``) runs end to end through
        ``_apply_channel_create``'s conversion to ``PermanentApplyError``.
        """
        ticket1 = make_ticket(uuid="ticket-1", discord_channel_id=None, discord_creator_id=None)
        ticket2 = make_ticket(uuid="ticket-2", discord_channel_id=222)

        async def get_ticket(ticket_uuid):
            return ticket1 if ticket_uuid == "ticket-1" else ticket2

        fake_api.get_ticket.side_effect = get_ticket

        channel2 = MagicMock()
        channel2.name = "ticket-two-abc123"
        channel2.id = 222
        fake_bot.get_channel.side_effect = lambda cid: channel2 if cid == 222 else None

        fake_api.get_events.return_value = [
            make_event(1, "channel_create", ticket_uuid="ticket-1"),
            make_event(2, "closed", ticket_uuid="ticket-2"),
        ]

        sync = make_sync(fake_api, fake_bot, max_attempts=1)

        # fake_bot's guild.get_channel already defaults to returning None,
        # so whatever TICKET_CATEGORY_ID is configured, the category lookup
        # inside the real create_ticket_channel fails with the real
        # ValueError ("category not found in guild").
        with (
            patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", 999999999),
            patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()) as close_mock,
        ):
            applied = await sync.poll_once()

        # Event 2 (closed, ticket-2) applied and was acked even though
        # event 1 (channel_create, ticket-1) permanently failed ahead of it.
        assert applied == 1
        close_mock.assert_awaited_once_with(
            channel2, discord_creator_id=ticket2.discord_creator_id
        )
        assert sync.sync_failed == 1
        acked_ids = [call.args[0] for call in fake_api.ack_events.await_args_list]
        assert acked_ids[-1] == [2]

        # Prove the classification itself directly, not just its
        # downstream effect: _apply_with_retry on the failing event alone
        # must report permanent=True (this is exactly what round 2 got
        # wrong for this site: it returned permanent=False).
        applied_ok, permanent = await sync._apply_with_retry(
            make_event(1, "channel_create", ticket_uuid="ticket-1")
        )
        assert applied_ok is False
        assert permanent is True

    @pytest.mark.asyncio
    async def test_permanent_failure_does_not_grow_state_unbounded(
        self, fake_api, fake_bot, tmp_path
    ):
        """While a permanent failure holds ``last_acked_id`` capped below it
        (by design -- see ``test_cursor_never_skips_a_permanently_failed_event``
        above), ``applied_ids`` (and the persisted state file, rewritten
        after every applied event) must not grow without bound as later,
        unrelated events keep being applied and acked cycle after cycle:
        every id above the cursor is retained forever otherwise, since
        nothing is ever <= the (permanently capped) cursor to prune.

        ``MAX_RETAINED_APPLIED_IDS`` is patched down to a small value here
        so the cap is exercised (and the test runs fast) without needing
        thousands of cycles; production uses a larger default that a
        normal redelivery-dedup window never approaches.
        """
        state_file = str(tmp_path / "state.json")

        channels: dict[int, MagicMock] = {}
        fake_bot.get_channel.side_effect = lambda cid: channels.get(cid)

        async def get_ticket(ticket_uuid):
            if ticket_uuid == "ticket-bad":
                return make_ticket(uuid="ticket-bad", discord_channel_id=111)
            channel_id = int(ticket_uuid.split("-")[1])
            return make_ticket(
                uuid=ticket_uuid, discord_channel_id=channel_id, discord_creator_id=None
            )

        fake_api.get_ticket.side_effect = get_ticket

        sync = TicketEventSync(
            api=fake_api, bot=fake_bot, max_attempts=1, state_file=state_file
        )

        next_id = 2
        applied_ids_sizes = []
        with (
            patch("Tickets.ticket_sync.MAX_RETAINED_APPLIED_IDS", 4),
            patch("Tickets.ticket_sync.close_ticket_channel", new=AsyncMock()),
        ):
            for _cycle in range(10):
                events = [make_event(1, "closed", ticket_uuid="ticket-bad")]
                for _ in range(2):
                    channel_id = 1000 + next_id
                    chan = MagicMock()
                    chan.name = f"ticket-good-{next_id}"
                    chan.id = channel_id
                    channels[channel_id] = chan
                    events.append(
                        make_event(next_id, "closed", ticket_uuid=f"ticket-{channel_id}")
                    )
                    next_id += 1
                fake_api.get_events.return_value = events
                await sync.poll_once()
                applied_ids_sizes.append(len(sync.applied_ids))

        # The permanent failure (event 1, ticket-bad) keeps the cursor
        # capped at 0 for all 10 cycles, exactly as designed.
        assert sync.last_acked_id == 0
        assert sync.sync_failed == 1

        # Every cycle applies and acks 2 new, unrelated events: 20 ids
        # accumulate in total over the run. Without this fix, applied_ids
        # (and the persisted file, rewritten after every event) would grow
        # by 2 every cycle forever (2, 4, 6, ... 20) because nothing above
        # the permanently-capped cursor is ever pruned. With the fix, the
        # retained set never exceeds the (patched-small) cap, from partway
        # through the run onward.
        assert max(applied_ids_sizes) <= 4
        assert applied_ids_sizes[-1] <= 4
        # New events keep being delivered and acked throughout -- the cap
        # bounds memory/disk, it does not stop sync from making progress.
        acked_ids = [call.args[0] for call in fake_api.ack_events.await_args_list]
        assert acked_ids[-1] == [next_id - 2, next_id - 1]

        with open(state_file, encoding="utf-8") as f:  # noqa: ASYNC230 - test-only inline read
            on_disk = json.load(f)
        assert len(on_disk["applied_ids"]) <= 4


class TestChannelTicketResolver:
    """Tests for the channel->ticket resolver."""

    @pytest.mark.asyncio
    async def test_prime_populates_map(self, fake_api):
        """prime() fills the map from discord-only tickets."""
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=111), make_ticket(discord_channel_id=222)],
            2,
        )

        resolver = ChannelTicketResolver(api=fake_api)
        await resolver.prime()

        assert await resolver.resolve(111) == "ticket-123"
        assert await resolver.resolve(222) == "ticket-123"

    @pytest.mark.asyncio
    async def test_resolve_falls_back_to_api(self, fake_api):
        """A map miss falls back to get_ticket_by_channel and caches."""
        fake_api.get_ticket_by_channel.return_value = make_ticket(
            discord_channel_id=333
        )

        resolver = ChannelTicketResolver(api=fake_api)
        assert await resolver.resolve(333) == "ticket-123"
        # Second resolve uses the cache, not the API.
        assert await resolver.resolve(333) == "ticket-123"
        fake_api.get_ticket_by_channel.assert_awaited_once_with(333)

    @pytest.mark.asyncio
    async def test_resolve_unknown_channel_returns_none(self, fake_api):
        """An unknown channel resolves to None."""
        resolver = ChannelTicketResolver(api=fake_api)
        assert await resolver.resolve(999) is None


class TestOutboundMessageRelay:
    """Tests for the Discord-originated message relay."""

    def make_message(self, content="hello", author_id=123, channel_id=456, is_bot=False):
        """Build a fake Discord message."""
        message = MagicMock()
        message.id = 789
        message.content = content
        message.channel = MagicMock()
        message.channel.id = channel_id
        message.author = MagicMock()
        message.author.id = author_id
        message.author.bot = is_bot
        message.author.display_name = "TestUser"
        message.author.name = "TestUser"
        message.created_at = MagicMock()
        message.created_at.isoformat.return_value = "2024-01-01T12:00:00+00:00"
        message.attachments = []
        return message

    def make_relay(self, fake_api, resolver=None):
        return OutboundMessageRelay(
            api=fake_api,
            resolver=resolver or MagicMock(),
            bot_user_id=999,
        )

    @pytest.mark.asyncio
    async def test_posts_outbound_message_for_ticket_channel(self, fake_api):
        """A message in a ticket channel is posted to /outbound/."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        await relay.handle(self.make_message())

        fake_api.post_outbound_message.assert_awaited_once()
        payload = fake_api.post_outbound_message.await_args.args[0]
        assert payload["discord_channel_id"] == 456
        assert payload["discord_message_id"] == 789
        assert payload["author_discord_id"] == 123
        assert payload["content"] == "hello"
        assert payload["is_bot"] is False

    @pytest.mark.asyncio
    async def test_skips_bot_self_message(self, fake_api):
        """The bot never relays its own messages."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        await relay.handle(self.make_message(author_id=999))

        fake_api.post_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_command_prefixed_message(self, fake_api):
        """Command-prefixed messages are not relayed."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        await relay.handle(self.make_message(content="/ticket close"))

        fake_api.post_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_ticket_channel(self, fake_api):
        """Messages in non-ticket channels are ignored."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value=None)

        relay = self.make_relay(fake_api, resolver)
        await relay.handle(self.make_message())

        fake_api.post_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_is_non_fatal_and_counted(self, fake_api):
        """A failed relay is counted and logged, not raised."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")
        fake_api.post_outbound_message.side_effect = APIError("down", 500)

        relay = self.make_relay(fake_api, resolver)
        await relay.handle(self.make_message())

        assert relay.failed == 1
        assert relay.last_error is not None

    @pytest.mark.asyncio
    async def test_edit_handler(self, fake_api):
        """An edited ticket-channel message is relayed as an API update."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_edit(self.make_message(content="edited"))

        fake_api.update_message.assert_awaited_once()
        payload = fake_api.update_message.await_args.args[0]
        assert payload["discord_channel_id"] == 456
        assert payload["discord_message_id"] == 789
        assert payload["content"] == "edited"

    @pytest.mark.asyncio
    async def test_delete_handler(self, fake_api):
        """A deleted ticket-channel message is relayed as a soft delete."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_delete(self.make_message())

        fake_api.delete_message.assert_awaited_once_with(456, 789)

    @pytest.mark.asyncio
    async def test_delete_handles_uncached_message(self, fake_api):
        """A delete of an uncached message (no author) is still relayed."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        message = self.make_message()
        message.author = None
        await relay.handle_delete(message)

        fake_api.delete_message.assert_awaited_once_with(456, 789)

    @pytest.mark.asyncio
    async def test_resolve_failure_in_handle_is_counted(self, fake_api):
        """A resolver APIError during handle is caught and counted."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(side_effect=APIError("down", 500))

        relay = self.make_relay(fake_api, resolver)
        await relay.handle(self.make_message())

        assert relay.failed == 1
        assert relay.last_error is not None
        fake_api.post_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_failure_in_handle_edit_is_counted(self, fake_api):
        """A resolver APIError during handle_edit is caught and counted."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(side_effect=APIError("down", 500))

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_edit(self.make_message())

        assert relay.failed == 1
        assert relay.last_error is not None
        fake_api.update_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_failure_in_handle_delete_is_counted(self, fake_api):
        """A resolver APIError during handle_delete is caught and counted."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(side_effect=APIError("down", 500))

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_delete(self.make_message())

        assert relay.failed == 1
        assert relay.last_error is not None
        fake_api.delete_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_skips_bot_self_message(self, fake_api):
        """The bot never relays edits of its own messages."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_edit(self.make_message(author_id=999))

        fake_api.update_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_skips_bot_self_message(self, fake_api):
        """The bot never relays deletes of its own messages."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value="ticket-123")

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_delete(self.make_message(author_id=999))

        fake_api.delete_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_skips_non_ticket_channel(self, fake_api):
        """Edits in non-ticket channels are ignored."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value=None)

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_edit(self.make_message())

        fake_api.update_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_skips_non_ticket_channel(self, fake_api):
        """Deletes in non-ticket channels are ignored."""
        resolver = MagicMock()
        resolver.resolve = AsyncMock(return_value=None)

        relay = self.make_relay(fake_api, resolver)
        await relay.handle_delete(self.make_message())

        fake_api.delete_message.assert_not_called()


class TestReconnectBackfill:
    """Tests for reconnect catch-up backfill (6e/D13)."""

    def make_backfill(self, fake_api, fake_bot, **kwargs):
        """Build a ReconnectBackfill with per-channel/per-message pacing disabled."""
        kwargs.setdefault("channel_sleep", 0)
        kwargs.setdefault("channel_jitter", 0)
        kwargs.setdefault("message_sleep", 0)
        kwargs.setdefault("rate_limit_sleep", 0)
        return ReconnectBackfill(api=fake_api, bot=fake_bot, **kwargs)

    def make_channel(self, messages):
        """Build a fake Discord channel whose history yields ``messages``."""
        channel = MagicMock()
        channel.id = 456
        channel.history = MagicMock(return_value=async_iter(messages))
        return channel

    @pytest.mark.asyncio
    async def test_backfill_dedup(self, fake_api, fake_bot):
        """Each message delta from history is POSTed to /outbound/ (dedup is
        server-side via the discord_message_id upsert)."""
        channel = self.make_channel(
            [make_history_message(1, "one"), make_history_message(2, "two")]
        )
        fake_bot.get_channel.return_value = channel
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456)],
            1,
        )

        backfill = self.make_backfill(fake_api, fake_bot)
        posted = await backfill.run_once()

        assert posted == 2
        assert fake_api.post_outbound_message.await_count == 2
        ids = [
            call.args[0]["discord_message_id"]
            for call in fake_api.post_outbound_message.await_args_list
        ]
        assert ids == [1, 2]

    @pytest.mark.asyncio
    async def test_backfill_advances_watermark(self, fake_api, fake_bot):
        """A non-null watermark makes the bot fetch history after it."""
        channel = self.make_channel([make_history_message(200)])
        fake_bot.get_channel.return_value = channel
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456, last_synced_message_id=100)],
            1,
        )

        backfill = self.make_backfill(fake_api, fake_bot)
        await backfill.run_once()

        channel.history.assert_called_once_with(
            limit=1000,
            oldest_first=True,
            after=discord.Object(id=100),
        )
        fake_api.post_outbound_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_backfill_legacy_null_watermark_caps_history(self, fake_api, fake_bot):
        """A legacy NULL watermark caps the first backfill (no ``after``, D12)."""
        channel = self.make_channel([make_history_message(1)])
        fake_bot.get_channel.return_value = channel
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456, last_synced_message_id=None)],
            1,
        )

        backfill = self.make_backfill(fake_api, fake_bot)
        await backfill.run_once()

        channel.history.assert_called_once_with(limit=1000, oldest_first=True)

    @pytest.mark.asyncio
    async def test_backfill_skips_old_closed(self, fake_api, fake_bot):
        """A channel closed more than 7 days ago is not re-scraped (D13)."""
        channel = self.make_channel([])
        fake_bot.get_channel.return_value = channel
        old_closed = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456, status="done", closed_at=old_closed)],
            1,
        )

        backfill = self.make_backfill(fake_api, fake_bot)
        posted = await backfill.run_once()

        assert posted == 0
        channel.history.assert_not_called()
        fake_api.post_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_recently_closed_included(self, fake_api, fake_bot):
        """A channel closed within 7 days is still backfilled (D13)."""
        channel = self.make_channel([make_history_message(1)])
        fake_bot.get_channel.return_value = channel
        recent_closed = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456, status="done", closed_at=recent_closed)],
            1,
        )

        backfill = self.make_backfill(fake_api, fake_bot)
        posted = await backfill.run_once()

        assert posted == 1
        fake_api.post_outbound_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_backfill_skips_bot_self(self, fake_api, fake_bot):
        """The bot never backfills its own messages."""
        channel = self.make_channel(
            [make_history_message(1, author_id=999, is_bot=True)]
        )
        fake_bot.get_channel.return_value = channel
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456)],
            1,
        )

        backfill = self.make_backfill(fake_api, fake_bot, bot_user_id=999)
        posted = await backfill.run_once()

        assert posted == 0
        fake_api.post_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_skips_missing_channel(self, fake_api, fake_bot):
        """A ticket whose Discord channel is gone is skipped gracefully."""
        fake_bot.get_channel.return_value = None
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456)],
            1,
        )

        backfill = self.make_backfill(fake_api, fake_bot)
        posted = await backfill.run_once()

        assert posted == 0
        fake_api.post_outbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_paces_per_message(self, fake_api, fake_bot):
        """Each successfully posted message is paced (SYNC-6), not just the
        gap between channels, so a sustained failure cannot hot-loop."""
        channel = self.make_channel(
            [make_history_message(1), make_history_message(2)]
        )
        fake_bot.get_channel.return_value = channel
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456)],
            1,
        )

        backfill = self.make_backfill(
            fake_api, fake_bot, message_sleep=0.05, rate_limit_sleep=1.0
        )

        with patch(
            "Tickets.ticket_sync.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock:
            posted = await backfill.run_once()

        assert posted == 2
        delays = [call.args[0] for call in sleep_mock.await_args_list]
        # One per-message pacing sleep for each of the two posted messages.
        assert delays.count(0.05) == 2

    @pytest.mark.asyncio
    async def test_backfill_backs_off_harder_on_rate_limit(self, fake_api, fake_bot):
        """A 429 from the outbound API backs off longer than the normal
        per-message pace, instead of hot-looping (SYNC-6)."""
        channel = self.make_channel([make_history_message(1)])
        fake_bot.get_channel.return_value = channel
        fake_api.list_tickets.return_value = (
            [make_ticket(discord_channel_id=456)],
            1,
        )
        fake_api.post_outbound_message.side_effect = APIError("rate limited", 429)

        backfill = self.make_backfill(
            fake_api, fake_bot, message_sleep=0.05, rate_limit_sleep=2.5
        )

        with patch(
            "Tickets.ticket_sync.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock:
            posted = await backfill.run_once()

        assert posted == 0
        assert backfill.failed == 1
        delays = [call.args[0] for call in sleep_mock.await_args_list]
        assert 2.5 in delays
        # The rate-limit backoff replaces the normal per-message pace for
        # that message, it does not additionally sleep the short pace too.
        assert 0.05 not in delays
