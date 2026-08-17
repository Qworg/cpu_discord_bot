"""Tests for ticket outbox sync and outbound message relay."""

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
        """Build a ReconnectBackfill with per-channel pacing disabled."""
        kwargs.setdefault("channel_sleep", 0)
        kwargs.setdefault("channel_jitter", 0)
        return ReconnectBackfill(api=fake_api, bot=fake_bot, **kwargs)

    def make_channel(self, messages):
        """Build a fake Discord channel whose history yields ``messages``."""
        channel = MagicMock()
        channel.id = 456
        channel.history = AsyncMock(return_value=async_iter(messages))
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
            after=100,
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
