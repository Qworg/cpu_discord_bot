"""Outbox polling and Discord-originated message relay for the ticket system.

This module provides:

- :class:`TicketEventSync`: a poll loop that drains the larpmanager ticket
  outbox (``GET /api/v1/tickets/events/``) and applies ``source=api`` events
  idempotently to Discord before batch-acking them.
- :class:`ChannelTicketResolver`: an in-memory ``discord_channel_id`` ->
  ``ticket_uuid`` map primed from ``GET /api/v1/tickets/?discord_only=true``,
  used to resolve message channels without relying on the spoofable channel
  name prefix.
- :class:`OutboundMessageRelay`: relays Discord messages in ticket channels to
  the API via ``POST /api/v1/tickets/outbound/``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from discord import Object
from discord.ext import tasks

from Shared.bot_instance import cpu_discord_bot
from Tickets.ticket_api_client import APIError, TicketData, get_api_client
from Tickets.ticket_config import (
    TICKET_ARCHIVE_CATEGORY_ID,
    TICKET_CATEGORY_ID,
    TICKET_GUILD_ID,
    TICKET_POLL_LIMIT,
    TICKET_POLL_SECONDS,
    TICKET_SYNC_MAX_ATTEMPTS,
)
from Tickets.ticket_permissions import (
    add_user_to_ticket,
    close_ticket_channel,
    create_ticket_channel,
    generate_channel_name,
    reopen_ticket_channel,
)

if TYPE_CHECKING:
    from discord import Message, TextChannel

logger = logging.getLogger(__name__)

# Adaptive cadence (D11): after this many consecutive empty polls the loop
# backs off through the schedule below, capping at the last value.
EMPTY_POLL_BACKOFF_THRESHOLD = 3
EMPTY_POLL_BACKOFF = (5.0, 10.0, 15.0)

# Retry pacing for a single failing apply (exponential backoff + jitter).
RETRY_BACKOFF_BASE = 1.0
RETRY_BACKOFF_MAX = 10.0
RETRY_JITTER = 0.5

# Persisted cursor/apply-set state (D6). Path lives under the bot working dir;
# reloaded on startup so re-delivery after a restart stays a no-op.
STATE_FILE = os.environ.get(
    "TICKET_SYNC_STATE_FILE",
    os.path.join(os.getcwd(), "ticket_sync_state.json"),
)

# Per-id cooldown for permanently failing events (avoids a 3s retry storm).
# The cooldown doubles on each consecutive failure up to the cap.
FAILING_EVENT_COOLDOWN_BASE = 60.0
FAILING_EVENT_COOLDOWN_MAX = 900.0

# Event types that require no Discord mutation (history/audit only).
NOOP_EVENT_TYPES = frozenset(
    {"created", "channel_synced", "access_denied"}
)

_STATUS_LABELS = {"open": "Open", "working": "Working", "done": "Done"}
_PRIORITY_LABELS = {"low": "Low", "medium": "Medium", "high": "High"}

# Event types that mutate a ticket channel and therefore must surface when the
# channel cannot be resolved (rather than being silently acked as success).
CHANNEL_REQUIRING_EVENT_TYPES = frozenset(
    {
        "closed",
        "reopened",
        "status_changed",
        "assigned",
        "channel_update",
        "channel_archive",
        "deleted",
    }
)

# Reconnect backfill (6e/D13): history page size doubles as the legacy
# first-run cap (D12); closed channels older than the window are not re-scraped.
BACKFILL_HISTORY_LIMIT = 1000
BACKFILL_RECONNECT_WINDOW = timedelta(days=7)

# Per-channel pacing between backfilled channels (jitter added, section 7.3).
BACKFILL_CHANNEL_SLEEP = 2.0
BACKFILL_CHANNEL_JITTER = 1.0


class ChannelTicketResolver:
    """Resolve Discord channel IDs to ticket UUIDs.

    The map is primed from ``GET /api/v1/tickets/?discord_only=true`` and
    falls back to ``GET /api/v1/tickets/channel/<id>/`` on a miss so that a
    freshly created channel resolves before the next priming pass.
    """

    def __init__(self, api=None):
        self.api = api or get_api_client()
        self._map: dict[int, str] = {}

    async def prime(self) -> None:
        """Populate the channel->ticket map from the API."""
        offset = 0
        page_size = 100

        while True:
            tickets, total = await self.api.list_tickets(
                discord_only=True,
                limit=page_size,
                offset=offset,
            )
            for ticket in tickets:
                if ticket.discord_channel_id:
                    self._map[ticket.discord_channel_id] = ticket.uuid

            offset += len(tickets)
            if not tickets or offset >= total:
                break

    async def resolve(self, channel_id: int) -> str | None:
        """Return the ticket UUID for a Discord channel, or None.

        Args:
            channel_id: The Discord channel ID to resolve.

        Returns:
            Ticket UUID if the channel belongs to a ticket, else None.

        """
        ticket_uuid = self._map.get(channel_id)
        if ticket_uuid is not None:
            return ticket_uuid

        ticket = await self.api.get_ticket_by_channel(channel_id)
        if ticket is not None:
            self._map[channel_id] = ticket.uuid
            return ticket.uuid

        return None

    def known_channel_ids(self) -> set[int]:
        """Return the set of channel IDs currently mapped to a ticket."""
        return set(self._map)


def build_outbound_payload(message: Message) -> dict:
    """Build the outbound message payload from a Discord message."""
    author = message.author
    author_name = getattr(author, "display_name", None) or getattr(author, "name", "")
    return {
        "discord_channel_id": message.channel.id,
        "discord_message_id": message.id,
        "author_discord_id": author.id,
        "author_name": author_name,
        "content": message.content,
        "sent_at": message.created_at.isoformat(),
        "attachments": [
            {"url": att.url, "filename": att.filename, "size": att.size}
            for att in message.attachments
        ],
        "is_bot": bool(author.bot),
    }


def is_bot_self(author, bot_user_id: int | None = None) -> bool:
    """Return True when ``author`` is the bot itself.

    In production the bot user id is only known after login, so fall back to
    the live bot user when no id was injected (tests inject one explicitly).
    """
    if author is None:
        return False

    bot_user = getattr(cpu_discord_bot, "user", None)
    bot_id = bot_user_id
    if bot_id is None and bot_user is not None:
        bot_id = getattr(bot_user, "id", None)
    return bot_id is not None and getattr(author, "id", None) == bot_id


def should_skip_message(
    message: Message,
    command_prefix: str = "/",
    bot_user_id: int | None = None,
) -> bool:
    """Return True when a message must not be relayed.

    Skips messages without an author, the bot's own messages, and
    command-prefixed messages.
    """
    author = getattr(message, "author", None)
    if author is None:
        return True
    if is_bot_self(author, bot_user_id):
        return True
    content = getattr(message, "content", "") or ""
    return content.startswith(command_prefix)


class OutboundMessageRelay:
    """Relay Discord messages from ticket channels to the API.

    Skips bot-self and command-prefixed messages; non-ticket channels are
    ignored. Failures are logged and counted, never raised to the message
    dispatch loop.
    """

    def __init__(
        self,
        api=None,
        resolver: ChannelTicketResolver | None = None,
        command_prefix: str = "/",
        bot_user_id: int | None = None,
    ):
        self.api = api or get_api_client()
        self.resolver = resolver or get_channel_resolver()
        self.command_prefix = command_prefix
        self.bot_user_id = bot_user_id
        self.failed = 0
        self.last_error: str | None = None

    def _build_payload(self, message: Message) -> dict:
        """Build the outbound message payload from a Discord message."""
        return build_outbound_payload(message)

    def _is_bot_self(self, author) -> bool:
        """Return True when ``author`` is the bot itself."""
        return is_bot_self(author, self.bot_user_id)

    def _should_skip(self, message: Message) -> bool:
        """Return True when a message must not be relayed."""
        return should_skip_message(message, self.command_prefix, self.bot_user_id)

    def _should_skip_delete(self, message: Message) -> bool:
        """Return True when a delete must not be relayed.

        A delete only needs the channel and message ids, so an uncached
        message (which has no author or content) is still relayed. Only the
        bot's own message deletes are skipped.
        """
        return self._is_bot_self(getattr(message, "author", None))

    async def handle(self, message: Message) -> None:
        """Handle an incoming Discord message.

        Args:
            message: The Discord message received.

        """
        if self._should_skip(message):
            return

        channel = getattr(message, "channel", None)
        if channel is None:
            return

        try:
            ticket_uuid = await self.resolver.resolve(channel.id)
            if ticket_uuid is None:
                return

            payload = self._build_payload(message)
            await self.api.post_outbound_message(payload)
        except APIError as e:
            self.failed += 1
            self.last_error = str(e)
            logger.error(
                "Outbound message relay failed for channel %s: %s",
                channel.id,
                e,
            )

    async def handle_edit(self, message: Message) -> None:
        """Report an edited ticket-channel message to the API.

        Args:
            message: The edited Discord message.

        """
        if self._should_skip(message):
            return

        channel = getattr(message, "channel", None)
        if channel is None:
            return

        try:
            ticket_uuid = await self.resolver.resolve(channel.id)
            if ticket_uuid is None:
                return

            payload = self._build_payload(message)
            await self.api.update_message(payload)
        except APIError as e:
            self.failed += 1
            self.last_error = str(e)
            logger.error(
                "Outbound message edit relay failed for channel %s: %s",
                channel.id,
                e,
            )

    async def handle_delete(self, message: Message) -> None:
        """Report a deleted ticket-channel message to the API.

        Args:
            message: The deleted Discord message.

        """
        if self._should_skip_delete(message):
            return

        channel = getattr(message, "channel", None)
        if channel is None:
            return

        try:
            ticket_uuid = await self.resolver.resolve(channel.id)
            if ticket_uuid is None:
                return

            await self.api.delete_message(channel.id, message.id)
        except APIError as e:
            self.failed += 1
            self.last_error = str(e)
            logger.error(
                "Outbound message delete relay failed for channel %s: %s",
                channel.id,
                e,
            )


class ReconnectBackfill:
    """Reconnect catch-up: scrape ticket channels into the outbound endpoint.

    On startup/reconnect, walks open and recently-closed (<= 7 days) ticket
    channels and POSTs the message delta after the server watermark to
    ``/outbound/``. The server upserts on ``discord_message_id`` and advances
    ``last_synced_message_id`` via GREATEST, so re-delivery dedups.
    """

    def __init__(
        self,
        api=None,
        bot=None,
        history_limit: int = BACKFILL_HISTORY_LIMIT,
        reconnect_window: timedelta = BACKFILL_RECONNECT_WINDOW,
        channel_sleep: float = BACKFILL_CHANNEL_SLEEP,
        channel_jitter: float = BACKFILL_CHANNEL_JITTER,
        command_prefix: str = "/",
        bot_user_id: int | None = None,
    ):
        self.api = api or get_api_client()
        self.bot = bot or cpu_discord_bot
        self.history_limit = history_limit
        self.reconnect_window = reconnect_window
        self.channel_sleep = channel_sleep
        self.channel_jitter = channel_jitter
        self.command_prefix = command_prefix
        self.bot_user_id = bot_user_id
        self.posted = 0
        self.failed = 0
        self.last_error: str | None = None

    async def run_once(self) -> int:
        """Run one backfill pass and return the number of messages posted."""
        try:
            tickets = await self._list_discord_tickets()
        except APIError as e:
            logger.error("Reconnect backfill: failed to list tickets: %s", e)
            self.last_error = str(e)
            return 0

        for ticket in tickets:
            self.posted += await self._backfill_ticket(ticket)
            await asyncio.sleep(self._pacing_delay())

        return self.posted

    async def _list_discord_tickets(self) -> list[TicketData]:
        """Paginate all Discord-originated tickets from the API."""
        tickets: list[TicketData] = []
        offset = 0
        page_size = 100

        while True:
            page, total = await self.api.list_tickets(
                discord_only=True,
                limit=page_size,
                offset=offset,
            )
            tickets.extend(page)
            offset += len(page)
            if not page or offset >= total:
                break

        return tickets

    def _pacing_delay(self) -> float:
        """Per-channel pacing delay with jitter (section 7.3)."""
        return self.channel_sleep + random.uniform(0, self.channel_jitter)

    def _should_skip_closed(self, ticket: TicketData) -> bool:
        """Skip closed channels older than the reconnect window (D13)."""
        closed_at = _parse_closed_at(getattr(ticket, "closed_at", None))
        if closed_at is None:
            return False
        return datetime.now(UTC) - closed_at > self.reconnect_window

    def _history_kwargs(self, ticket: TicketData) -> dict:
        """Build ``channel.history`` kwargs from the ticket watermark.

        A NULL watermark is a legacy pre-cutover ticket: the first backfill is
        capped at ``history_limit`` (D12) and the snapshot is authoritative.
        """
        kwargs = {"limit": self.history_limit, "oldest_first": True}
        watermark = getattr(ticket, "last_synced_message_id", None)
        if watermark is not None:
            kwargs["after"] = Object(id=watermark)
        return kwargs

    async def _backfill_ticket(self, ticket: TicketData) -> int:
        """Backfill one ticket channel and return the number of messages posted."""
        channel_id = getattr(ticket, "discord_channel_id", None)
        if not channel_id:
            return 0

        if self._should_skip_closed(ticket):
            return 0

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return 0

        posted = 0
        try:
            async for message in channel.history(**self._history_kwargs(ticket)):
                if should_skip_message(message, self.command_prefix, self.bot_user_id):
                    continue
                payload = build_outbound_payload(message)
                try:
                    await self.api.post_outbound_message(payload)
                    posted += 1
                except APIError as e:
                    self.failed += 1
                    self.last_error = str(e)
                    logger.error(
                        "Reconnect backfill: outbound post failed for message %s: %s",
                        getattr(message, "id", None),
                        e,
                    )
        except Exception as e:  # noqa: BLE001 - surfaced, never dropped
            self.failed += 1
            self.last_error = str(e)
            logger.error(
                "Reconnect backfill: history fetch failed for channel %s: %s",
                channel_id,
                e,
            )

        return posted


def _parse_closed_at(value) -> datetime | None:
    """Parse an ISO8601 closed_at string, or None when absent/invalid."""
    if not value:
        return None
    try:
        closed_at = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=UTC)
    return closed_at


class TicketEventSync:
    """Poll the ticket outbox and apply events idempotently.

    Tracks the monotonic cursor (``last_acked_id``) and an in-memory set of
    already-applied event IDs so re-delivery is a no-op (D6). Applies are
    retried with backoff + jitter; after ``max_attempts`` the event is left in
    the outbox (never acked) and surfaced via ``sync_failed``/``last_error``.
    """

    def __init__(
        self,
        api=None,
        bot=None,
        poll_seconds: float | None = None,
        poll_limit: int | None = None,
        max_attempts: int | None = None,
        state_file: str | None = STATE_FILE,
    ):
        self.api = api or get_api_client()
        self.bot = bot or cpu_discord_bot
        self.poll_seconds = float(
            poll_seconds if poll_seconds is not None else TICKET_POLL_SECONDS
        )
        self.poll_limit = poll_limit if poll_limit is not None else TICKET_POLL_LIMIT
        self.max_attempts = (
            max_attempts if max_attempts is not None else TICKET_SYNC_MAX_ATTEMPTS
        )
        self.state_file = state_file

        self.last_acked_id = 0
        self.applied_ids: set[int] = set()
        self.empty_polls = 0
        self.last_error: str | None = None
        self.current_interval = self.poll_seconds

        # Distinct failing event ids (surfaced via ``sync_failed``) and their
        # per-id retry cooldown timestamps (monotonic clock).
        self._failed_ids: set[int] = set()
        self._failing_ids: dict[int, float] = {}
        self._fail_counts: dict[int, int] = {}

        self._load_state()

    @property
    def sync_failed(self) -> int:
        """Number of distinct event ids currently failing (never dropped)."""
        return len(self._failed_ids)

    def _load_state(self) -> None:
        """Reload the persisted cursor and applied-id set (D6)."""
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, encoding="utf-8") as state_file:
                data = json.load(state_file)
            self.last_acked_id = int(data.get("last_acked_id", 0))
            self.applied_ids = {int(e) for e in data.get("applied_ids", [])}
        except (OSError, ValueError, TypeError) as e:
            logger.warning(
                "Ticket sync: failed to load state file %s: %s",
                self.state_file,
                e,
            )

    def _save_state(self) -> None:
        """Persist the cursor and applied-id set atomically.

        Only ids beyond the acked cursor can ever be re-delivered, so the
        persisted set is pruned to keep the file small.

        """
        if not self.state_file:
            return
        pending = sorted(e for e in self.applied_ids if e > self.last_acked_id)
        data = {"last_acked_id": self.last_acked_id, "applied_ids": pending}
        tmp_path = f"{self.state_file}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as state_file:
                json.dump(data, state_file)
            os.replace(tmp_path, self.state_file)
        except OSError as e:
            logger.warning(
                "Ticket sync: failed to persist state file %s: %s",
                self.state_file,
                e,
            )

    def _find_channel_in_category(self, guild, name: str):
        """Find a channel by name inside the ticket category, if visible."""
        if guild is None:
            return None
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if category is None:
            return None
        text_channels = getattr(category, "text_channels", None)
        if text_channels is None:
            return None
        for channel in text_channels:
            if getattr(channel, "name", None) == name:
                return channel
        return None

    def _register_channel_mapping(self, ticket_uuid: str, channel_id: int) -> None:
        """Register a channel->ticket mapping in the in-memory resolver."""
        get_channel_resolver()._map[channel_id] = ticket_uuid

    async def _write_back_channel(self, ticket_uuid: str, channel) -> None:
        """Report a ticket's channel id to the API and cache it in-process."""
        await self.api.write_back_channel_id(ticket_uuid, channel.id)
        self._register_channel_mapping(ticket_uuid, channel.id)

    def next_interval(self) -> float:
        """Return the desired poll interval for the current backlog state."""
        if self.empty_polls >= EMPTY_POLL_BACKOFF_THRESHOLD:
            idx = min(
                self.empty_polls - EMPTY_POLL_BACKOFF_THRESHOLD,
                len(EMPTY_POLL_BACKOFF) - 1,
            )
            return EMPTY_POLL_BACKOFF[idx]
        return self.poll_seconds

    def _retry_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter for a failing apply."""
        delay = min(RETRY_BACKOFF_BASE * (2 ** (attempt - 1)), RETRY_BACKOFF_MAX)
        return delay + random.uniform(0, RETRY_JITTER)

    async def _fetch_ticket(self, ticket_uuid: str) -> TicketData | None:
        """Fetch a ticket, returning None when it no longer exists."""
        try:
            return await self.api.get_ticket(ticket_uuid)
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    def _get_ticket_channel(self, ticket: TicketData | None) -> TextChannel | None:
        """Resolve a ticket's Discord channel, or None when unavailable.

        Falls back to a name-based lookup in the ticket category so a channel
        created before its id write-back landed still resolves.

        """
        if ticket is None:
            return None

        if ticket.discord_channel_id:
            channel = self.bot.get_channel(ticket.discord_channel_id)
            if channel is not None:
                return channel

        guild = self.bot.get_guild(TICKET_GUILD_ID)
        return self._find_channel_in_category(
            guild,
            generate_channel_name(ticket.subject or "", ticket.uuid),
        )

    async def poll_once(self) -> int:
        """Perform one outbox poll cycle.

        Returns:
            Number of events successfully applied in this cycle.

        """
        try:
            events = await self.api.get_events(
                since=self.last_acked_id,
                limit=self.poll_limit,
            )
        except APIError as e:
            logger.error("Ticket sync poll failed: %s", e)
            # A down server must back off the same way an empty outbox does.
            self.empty_polls += 1
            self.current_interval = self.next_interval()
            return 0

        if not events:
            self.empty_polls += 1
            self.current_interval = self.next_interval()
            return 0

        self.empty_polls = 0
        self.current_interval = self.next_interval()

        to_ack: list[int] = []
        applied = 0

        for event in events:
            event_id = event.get("id")
            if event_id is None:
                continue

            if event_id in self.applied_ids:
                # Applied in a prior cycle whose ack was lost; just re-ack.
                to_ack.append(event_id)
                continue

            if event_id in self._failing_ids:
                if time.monotonic() < self._failing_ids[event_id]:
                    # Still cooling down; leave this event (and everything
                    # after it) in the outbox this cycle.
                    break
                self._failing_ids.pop(event_id, None)

            if not await self._apply_with_retry(event):
                # Leave the failed event (and anything after it) in the
                # outbox by not advancing the cursor past it.
                break

            self.applied_ids.add(event_id)
            to_ack.append(event_id)
            applied += 1

        if to_ack:
            # Persist applied ids before acking so a lost ack cannot cause a
            # duplicate mutation after a restart.
            self._save_state()
            await self.api.ack_events(to_ack)
            self.last_acked_id = max(self.last_acked_id, to_ack[-1])

        self._save_state()

        return applied

    async def _apply_with_retry(self, event: dict) -> bool:
        """Apply one event with backoff + jitter retries.

        Returns:
            True if the event applied, False after exhausting retries.

        """
        event_id = event.get("id")
        last_exc: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                await self.apply_event(event)
                # Success clears any prior failure tracking for this id.
                self._failed_ids.discard(event_id)
                self._failing_ids.pop(event_id, None)
                self._fail_counts.pop(event_id, None)
                return True
            except Exception as e:  # noqa: BLE001 - surfaced, never dropped
                last_exc = e
                if attempt < self.max_attempts:
                    delay = self._retry_delay(attempt)
                    logger.warning(
                        "Ticket sync apply attempt %d/%d failed for event %s: %s; "
                        "retrying in %.2fs",
                        attempt,
                        self.max_attempts,
                        event_id,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)

        # Track a distinct failing id with an exponentially growing cooldown
        # so the poll loop does not re-attempt it every 3s forever.
        self._failed_ids.add(event_id)
        fail_count = self._fail_counts.get(event_id, 0) + 1
        self._fail_counts[event_id] = fail_count
        cooldown = min(
            FAILING_EVENT_COOLDOWN_BASE * (2 ** (fail_count - 1)),
            FAILING_EVENT_COOLDOWN_MAX,
        )
        self._failing_ids[event_id] = time.monotonic() + cooldown
        self.last_error = str(last_exc)
        logger.error(
            "Ticket sync event %s failed after %d attempts (cooldown %.0fs): %s",
            event_id,
            self.max_attempts,
            cooldown,
            last_exc,
        )
        return False

    async def apply_event(self, event: dict) -> None:
        """Apply a single outbox event to Discord.

        The apply rules follow the event payload contract in the plan; each
        mutation checks Discord state first so re-delivery is a no-op.

        Args:
            event: The outbox event dict.

        """
        event_type = event.get("event_type")
        ticket_uuid = event.get("ticket_uuid")
        payload = event.get("payload") or {}

        if event_type in NOOP_EVENT_TYPES:
            return

        if event_type == "channel_create":
            await self._apply_channel_create(ticket_uuid)
            return

        ticket = await self._fetch_ticket(ticket_uuid)

        if ticket is None:
            # Ticket no longer exists; there is nothing to mutate. Orphan
            # channels are handled by reconciliation.
            logger.warning(
                "Ticket sync: ticket %s gone for event %s",
                ticket_uuid,
                event_type,
            )
            return

        channel = self._get_ticket_channel(ticket)

        if channel is None:
            if event_type in CHANNEL_REQUIRING_EVENT_TYPES:
                # A mutation that cannot resolve its channel must surface via
                # the retry path instead of being acked as success.
                raise ValueError(
                    f"Ticket sync: no Discord channel for event {event_type} "
                    f"on ticket {ticket_uuid}"
                )
            logger.warning(
                "Ticket sync: no Discord channel for event %s on ticket %s",
                event_type,
                ticket_uuid,
            )
            return

        if event_type == "closed":
            await self._apply_close(ticket, channel)
        elif event_type == "reopened":
            await self._apply_reopen(ticket, channel)
        elif event_type == "status_changed":
            await self._apply_status_changed(event, ticket, channel)
        elif event_type == "assigned":
            await self._apply_assigned(channel, payload)
        elif event_type == "priority_changed":
            await self._apply_priority_changed(event, channel)
        elif event_type == "channel_update":
            await self._apply_channel_update(ticket, channel, payload)
        elif event_type in ("channel_archive", "deleted"):
            await self._apply_archive(channel)
        else:
            logger.warning("Ticket sync: unknown event type %s", event_type)

    async def _apply_channel_create(self, ticket_uuid: str) -> None:
        """Create (or adopt) the Discord channel for a ticket."""
        ticket = await self._fetch_ticket(ticket_uuid)
        if ticket is None:
            raise ValueError(f"channel_create: ticket {ticket_uuid} not found")

        if ticket.discord_channel_id:
            channel = self.bot.get_channel(ticket.discord_channel_id)
            if channel is None:
                logger.warning(
                    "channel_create: ticket %s has discord_channel_id %s but "
                    "channel is not visible to the bot",
                    ticket_uuid,
                    ticket.discord_channel_id,
                )
            else:
                logger.info(
                    "channel_create: ticket %s already has channel %s; adopting",
                    ticket_uuid,
                    ticket.discord_channel_id,
                )
                self._register_channel_mapping(ticket_uuid, channel.id)
            return

        guild = self.bot.get_guild(TICKET_GUILD_ID)
        if guild is None:
            raise ValueError(f"channel_create: guild {TICKET_GUILD_ID} not found")

        # Idempotency by state: a prior create whose write-back/ack was lost
        # (e.g. crash between create and ack) may have already produced a
        # channel with the expected name. Adopt it instead of duplicating.
        expected_name = generate_channel_name(ticket.subject or "", ticket.uuid)
        existing = self._find_channel_in_category(guild, expected_name)
        if existing is not None:
            logger.info(
                "channel_create: adopting existing channel %s for ticket %s",
                existing.id,
                ticket_uuid,
            )
            await self._write_back_channel(ticket_uuid, existing)
            return

        creator = None
        if ticket.discord_creator_id is not None:
            creator = guild.get_member(ticket.discord_creator_id)

        association_name = None
        if ticket.association:
            association_name = ticket.association.get("name")

        channel = await create_ticket_channel(
            guild=guild,
            creator=creator,
            subject=ticket.subject or "",
            ticket_uuid=ticket.uuid,
            association_name=association_name,
        )
        await self._write_back_channel(ticket_uuid, channel)
        logger.info(
            "channel_create: created channel %s for ticket %s",
            channel.id,
            ticket_uuid,
        )

    async def _apply_close(self, ticket: TicketData, channel: TextChannel) -> None:
        """Close a ticket channel, skipping when already closed."""
        if channel.name.startswith("closed-"):
            return
        await close_ticket_channel(
            channel,
            discord_creator_id=ticket.discord_creator_id,
        )

    async def _apply_reopen(self, ticket: TicketData, channel: TextChannel) -> None:
        """Reopen a ticket channel, skipping when already open."""
        if not channel.name.startswith("closed-"):
            return
        await reopen_ticket_channel(
            channel,
            discord_creator_id=ticket.discord_creator_id,
        )

    async def _apply_status_changed(
        self,
        event: dict,
        ticket: TicketData,
        channel: TextChannel,
    ) -> None:
        """Handle a general status change (close on done, reopen otherwise)."""
        to_status = (event.get("payload") or {}).get("to_status")
        if to_status is None:
            to_status = event.get("to_status")

        # A missing to_status carries no target state; defaulting it would
        # reopen a closed channel. Treat it as a no-op.
        if to_status is None:
            return

        if to_status == "done":
            await self._apply_close(ticket, channel)
        elif channel.name.startswith("closed-"):
            await self._apply_reopen(ticket, channel)
        else:
            status_label = _STATUS_LABELS.get(to_status, to_status)
            from_status = event.get("from_status")
            if from_status:
                from_label = _STATUS_LABELS.get(from_status, from_status)
                await channel.send(f"Status updated: {from_label} -> {status_label}")
            else:
                await channel.send(f"Status updated: {status_label}")

    async def _apply_priority_changed(self, event: dict, channel: TextChannel) -> None:
        """Send a visible priority-change notice in the channel."""
        payload = event.get("payload") or {}
        to_priority = payload.get("to_priority") or event.get("to_priority")
        if to_priority is None:
            return
        priority_label = _PRIORITY_LABELS.get(to_priority, to_priority)
        await channel.send(f"Priority updated: {priority_label}")

    async def _apply_assigned(self, channel: TextChannel, payload: dict) -> None:
        """Grant the assigned staff member access, skipping if present."""
        assigned_id = payload.get("assigned_staff_discord_id")
        if not assigned_id:
            return

        member = channel.guild.get_member(assigned_id)
        if member is None:
            logger.warning("assigned: member %s not found in guild", assigned_id)
            return

        permissions = channel.permissions_for(member)
        if permissions.read_messages and permissions.send_messages:
            return

        await add_user_to_ticket(channel, member)

    async def _apply_channel_update(
        self,
        ticket: TicketData,
        channel: TextChannel,
        payload: dict,
    ) -> None:
        """Rename a ticket channel, skipping when the name already matches."""
        subject = payload.get("subject")
        if subject is None:
            return

        target = generate_channel_name(subject, ticket.uuid)
        expected = f"closed-{target}" if channel.name.startswith("closed-") else target
        if channel.name == expected:
            return

        # Failures must propagate so _apply_with_retry retries and surfaces
        # them instead of the event being acked as success.
        await channel.edit(name=expected)

    async def _apply_archive(self, channel: TextChannel) -> None:
        """Archive a ticket channel, skipping when already archived."""
        if not TICKET_ARCHIVE_CATEGORY_ID:
            return
        if channel.category_id == TICKET_ARCHIVE_CATEGORY_ID:
            return

        archive_category = channel.guild.get_channel(TICKET_ARCHIVE_CATEGORY_ID)
        if archive_category is None:
            # A configured-but-missing archive category is a failure to
            # surface, not a silent success.
            raise ValueError(
                f"archive: archive category {TICKET_ARCHIVE_CATEGORY_ID} not found"
            )

        await channel.edit(category=archive_category, reason="Ticket archived")


# =============================================================================
# Singletons and the discord.ext.tasks loop wiring
# =============================================================================

_sync: TicketEventSync | None = None
_channel_resolver: ChannelTicketResolver | None = None
_outbound_relay: OutboundMessageRelay | None = None
_backfill: ReconnectBackfill | None = None


def get_ticket_sync() -> TicketEventSync:
    """Get the global ticket sync instance."""
    global _sync
    if _sync is None:
        _sync = TicketEventSync()
    return _sync


def get_channel_resolver() -> ChannelTicketResolver:
    """Get the global channel->ticket resolver instance."""
    global _channel_resolver
    if _channel_resolver is None:
        _channel_resolver = ChannelTicketResolver()
    return _channel_resolver


def get_outbound_relay() -> OutboundMessageRelay:
    """Get the global outbound message relay instance."""
    global _outbound_relay
    if _outbound_relay is None:
        _outbound_relay = OutboundMessageRelay()
    return _outbound_relay


def get_backfill() -> ReconnectBackfill:
    """Get the global reconnect backfill instance."""
    global _backfill
    if _backfill is None:
        _backfill = ReconnectBackfill()
    return _backfill


@tasks.loop(seconds=1.0, count=1)
async def backfill_ticket_channels() -> None:
    """Run one reconnect backfill pass on startup (one-shot)."""
    await get_backfill().run_once()


@backfill_ticket_channels.error
async def _backfill_error(error: BaseException) -> None:
    """Log an unhandled backfill exception and let the one-shot end."""
    logger.error("Reconnect backfill error: %s", error, exc_info=error)


@tasks.loop(seconds=TICKET_POLL_SECONDS)
async def ticket_sync_loop() -> None:
    """Poll the outbox and apply pending events (adaptive cadence)."""
    sync = get_ticket_sync()
    try:
        await sync.poll_once()
    finally:
        # Update the interval even when poll_once raises, so a transient
        # failure still applies the adaptive cadence on the next iteration.
        ticket_sync_loop.change_interval(seconds=sync.current_interval)


@ticket_sync_loop.before_loop
async def _before_sync() -> None:
    """Wait for the bot to be ready before polling."""
    await cpu_discord_bot.wait_until_ready()
    logger.info("Starting ticket outbox sync loop")


@ticket_sync_loop.error
async def _sync_error(error: BaseException) -> None:
    """Log an unhandled sync exception and keep the loop alive."""
    logger.error("Ticket sync loop error: %s", error, exc_info=error)
