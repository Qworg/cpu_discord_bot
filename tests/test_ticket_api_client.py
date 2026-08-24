"""Tests for the ticket API client."""

import pytest
from aioresponses import CallbackResult, aioresponses

from Tickets.ticket_api_client import (
    APIError,
    AssociationData,
    MemberLinkStatus,
    TicketAPIClient,
    TicketData,
)


class _FakeResponse:
    """Minimal async context manager standing in for aiohttp.ClientResponse."""

    def __init__(self, status=200, data=None, json_exc=None, headers=None):
        self.status = status
        self._data = data
        self._json_exc = json_exc
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._data


class _FakeSession:
    """Minimal stand-in for aiohttp.ClientSession.

    ``response`` may be a single response reused for every call, or a list
    of responses consumed one per call (to simulate a retry sequence).
    """

    def __init__(self, response=None, request_exc=None):
        self.closed = False
        self._responses = list(response) if isinstance(response, list) else None
        self._response = response
        self._request_exc = request_exc

    def request(self, *args, **kwargs):
        if self._request_exc is not None:
            raise self._request_exc
        if self._responses is not None:
            return self._responses.pop(0)
        return self._response


@pytest.fixture
def client():
    """Create a test API client."""
    return TicketAPIClient(
        base_url="http://localhost:8000",
        api_key="test-api-key",
        timeout=10,
    )


class TestTicketAPIClient:
    """Tests for TicketAPIClient."""

    @pytest.mark.asyncio
    async def test_check_discord_link_linked(self, client, api_responses):
        """Test that check_discord_link returns MemberLinkStatus with linked=True."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/discord/member/123456789/",
                payload=api_responses["link_status_linked"],
            )

            result = await client.check_discord_link(123456789)

            assert result.linked is True
            assert result.member_name == "Test User"
            assert result.member_uuid == "mem-1"

            await client.close()

    @pytest.mark.asyncio
    async def test_check_discord_link_not_linked(self, client, api_responses):
        """Test that check_discord_link returns linked=False when not linked."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/discord/member/999999999/",
                payload=api_responses["link_status_not_linked"],
            )

            result = await client.check_discord_link(999999999)

            assert result.linked is False
            assert result.member_uuid is None

            await client.close()

    @pytest.mark.asyncio
    async def test_check_discord_link_api_error(self, client):
        """Test that check_discord_link raises APIError on failure."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/discord/member/123456789/",
                status=500,
                payload={"error": "Internal server error"},
            )

            with pytest.raises(APIError) as exc_info:
                await client.check_discord_link(123456789)

            assert exc_info.value.status_code == 500

            await client.close()

    @pytest.mark.asyncio
    async def test_get_oauth_url_success(self, client):
        """Test that get_oauth_url returns OAuth URL string."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/discord/link/123456789/",
                payload={"oauth_url": "https://discord.com/oauth2/authorize?client_id=..."},
            )

            result = await client.get_oauth_url(123456789)

            assert "discord.com" in result
            assert "oauth2" in result

            await client.close()

    @pytest.mark.asyncio
    async def test_get_associations_success(self, client, api_responses):
        """Test that get_associations returns list of AssociationData."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/associations/",
                payload={"associations": api_responses["associations"]},
            )

            result = await client.get_associations()

            assert len(result) == 2
            assert isinstance(result[0], AssociationData)
            assert result[0].name == "Org 1"

            await client.close()

    @pytest.mark.asyncio
    async def test_get_associations_empty(self, client):
        """Test that get_associations returns empty list when none exist."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/associations/",
                payload={"associations": []},
            )

            result = await client.get_associations()

            assert len(result) == 0

            await client.close()

    @pytest.mark.asyncio
    async def test_create_ticket_success(self, client, api_responses):
        """Test that create_ticket returns TicketData."""
        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/",
                payload={"ticket": api_responses["ticket"]},
            )

            result = await client.create_ticket(
                association_uuid="assoc-1",
                discord_creator_id=123456789,
                discord_channel_id=987654321,
                subject="Test Subject",
                content="Test content",
            )

            assert isinstance(result, TicketData)
            assert result.uuid == "abc-123"
            assert result.subject == "Test Ticket"

            await client.close()

    @pytest.mark.asyncio
    async def test_create_ticket_validation_error(self, client):
        """Test that create_ticket raises APIError on 400."""
        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/",
                status=400,
                payload={"error": "Missing required fields"},
            )

            with pytest.raises(APIError) as exc_info:
                await client.create_ticket(
                    association_uuid="assoc-1",
                    discord_creator_id=123456789,
                    discord_channel_id=987654321,
                )

            assert exc_info.value.status_code == 400

            await client.close()

    @pytest.mark.asyncio
    async def test_get_ticket_success(self, client, api_responses):
        """Test that get_ticket returns TicketData."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                payload={"ticket": api_responses["ticket"]},
            )

            result = await client.get_ticket("abc-123")

            assert isinstance(result, TicketData)
            assert result.uuid == "abc-123"

            await client.close()

    @pytest.mark.asyncio
    async def test_get_ticket_not_found(self, client):
        """Test that get_ticket raises APIError with 404."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/not-found/",
                status=404,
                payload={"error": "Ticket not found"},
            )

            with pytest.raises(APIError) as exc_info:
                await client.get_ticket("not-found")

            assert exc_info.value.status_code == 404

            await client.close()

    @pytest.mark.asyncio
    async def test_get_ticket_by_channel_success(self, client, api_responses):
        """Test that get_ticket_by_channel returns TicketData."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/channel/987654321/",
                payload={"ticket": api_responses["ticket"]},
            )

            result = await client.get_ticket_by_channel(987654321)

            assert isinstance(result, TicketData)
            assert result.discord_channel_id == 987654321

            await client.close()

    @pytest.mark.asyncio
    async def test_get_ticket_by_channel_not_found(self, client):
        """Test that get_ticket_by_channel returns None when not found."""
        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/channel/999999999/",
                status=404,
                payload={"error": "Ticket not found"},
            )

            result = await client.get_ticket_by_channel(999999999)

            assert result is None

            await client.close()

    @pytest.mark.asyncio
    async def test_write_back_channel_id(self, client):
        """Test that write_back_channel_id posts the channel id."""
        captured = {}

        def _capture(url, **kwargs):
            captured["json"] = kwargs.get("json")
            return CallbackResult(payload={"ok": True})

        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/abc-123/channel/",
                callback=_capture,
            )

            result = await client.write_back_channel_id("abc-123", 987654321)

            assert result == {"ok": True}
            assert captured["json"] == {"discord_channel_id": 987654321}

            await client.close()

    @pytest.mark.asyncio
    async def test_update_ticket_success(self, client, api_responses):
        """Test that update_ticket returns updated TicketData."""
        updated_ticket = api_responses["ticket"].copy()
        updated_ticket["status"] = "working"
        updated_ticket["priority"] = "high"
        updated_ticket["version"] = 2

        with aioresponses() as m:
            m.patch(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                payload={"ticket": updated_ticket},
            )

            result = await client.update_ticket(
                "abc-123",
                version=1,
                status="working",
                priority="high",
            )

            assert result.status == "working"
            assert result.priority == "high"
            assert result.version == 2

            await client.close()

    @pytest.mark.asyncio
    async def test_update_ticket_sends_version_in_body(self, client, api_responses):
        """update_ticket must always send `version` in the PATCH body (CONTRACT-1):
        the server hard-requires it and 400s without it."""
        captured = {}

        def _capture(url, **kwargs):
            captured["json"] = kwargs.get("json")
            return CallbackResult(payload={"ticket": api_responses["ticket"]})

        with aioresponses() as m:
            m.patch(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                callback=_capture,
            )

            await client.update_ticket("abc-123", version=5, priority="high")

            assert captured["json"] == {"version": 5, "priority": "high"}

            await client.close()

    @pytest.mark.asyncio
    async def test_update_ticket_does_not_accept_transcript(self, client):
        """The server treats `transcript` as read-only on PATCH and 400s if
        present; update_ticket must not expose a way to send it."""
        import inspect

        from Tickets.ticket_api_client import TicketAPIClient

        params = inspect.signature(TicketAPIClient.update_ticket).parameters
        assert "transcript" not in params

    @pytest.mark.asyncio
    async def test_update_ticket_requires_version(self, client):
        """version has no default, so calling update_ticket without one is a
        TypeError, not a silently-missing field the server 400s on."""
        with pytest.raises(TypeError):
            await client.update_ticket("abc-123", priority="high")

    @pytest.mark.asyncio
    async def test_update_ticket_409_raises_api_error_with_status(self, client):
        """A stale version surfaces as an APIError with status_code == 409 so
        callers can detect and retry it."""
        with aioresponses() as m:
            m.patch(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                status=409,
                payload={"error": "version mismatch"},
            )

            with pytest.raises(APIError) as exc_info:
                await client.update_ticket("abc-123", version=1, priority="high")

            assert exc_info.value.status_code == 409

            await client.close()

    @pytest.mark.asyncio
    async def test_close_ticket_success(self, client, api_responses):
        """Test that close_ticket returns closed TicketData."""
        closed_ticket = api_responses["ticket"].copy()
        closed_ticket["status"] = "done"
        closed_ticket["closed_at"] = "2024-01-01T13:00:00Z"

        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/abc-123/close/",
                payload={"ticket": closed_ticket},
            )

            result = await client.close_ticket("abc-123")

            assert result.status == "done"
            assert result.closed_at is not None

            await client.close()

    @pytest.mark.asyncio
    async def test_close_ticket_with_transcript(self, client, api_responses):
        """Test that close_ticket includes transcript."""
        closed_ticket = api_responses["ticket"].copy()
        closed_ticket["status"] = "done"
        closed_ticket["transcript"] = "# Transcript\n[12:00] User: Hello"

        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/abc-123/close/",
                payload={"ticket": closed_ticket},
            )

            result = await client.close_ticket(
                "abc-123",
                transcript="# Transcript\n[12:00] User: Hello",
            )

            assert result.transcript is not None
            assert "Transcript" in result.transcript

            await client.close()

    @pytest.mark.asyncio
    async def test_reopen_ticket_success(self, client, api_responses):
        """Test that reopen_ticket returns reopened TicketData."""
        reopened_ticket = api_responses["ticket"].copy()
        reopened_ticket["status"] = "open"
        reopened_ticket["closed_at"] = None

        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/abc-123/reopen/",
                payload={"ticket": reopened_ticket},
            )

            result = await client.reopen_ticket("abc-123")

            assert result.status == "open"
            assert result.closed_at is None

            await client.close()

    @pytest.mark.asyncio
    async def test_get_events_success(self, client):
        """Test that get_events returns the events list."""
        import re
        with aioresponses() as m:
            m.get(
                re.compile(r"http://localhost:8000/api/v1/tickets/events/\?.*"),
                payload={"events": [{"id": 1, "event_type": "closed"}]},
            )

            events = await client.get_events(since=0, limit=100)

            assert events == [{"id": 1, "event_type": "closed"}]

            await client.close()

    @pytest.mark.asyncio
    async def test_ack_events_success(self, client):
        """Test that ack_events posts the ids batch."""
        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/events/ack/",
                payload={"acked": [1, 2]},
            )

            result = await client.ack_events([1, 2])

            assert result == {"acked": [1, 2]}

            await client.close()

    @pytest.mark.asyncio
    async def test_post_outbound_message_success(self, client):
        """Test that post_outbound_message posts the message payload."""
        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/outbound/",
                payload={"ok": True},
            )

            result = await client.post_outbound_message(
                {"discord_channel_id": 1, "discord_message_id": 2}
            )

            assert result == {"ok": True}

            await client.close()

    @pytest.mark.asyncio
    async def test_update_message_posts_full_payload(self, client):
        """Test that update_message posts the edited payload to /outbound/."""
        payload = {"discord_channel_id": 1, "discord_message_id": 2, "content": "edited"}

        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/outbound/",
                payload={"ok": True},
            )

            result = await client.update_message(payload)

            assert result == {"ok": True}
            request = list(m.requests.values())[0][0]
            assert request.kwargs["json"] == payload

            await client.close()

    @pytest.mark.asyncio
    async def test_delete_message_posts_deleted_flag(self, client):
        """Test that delete_message posts a deleted flag to /outbound/."""
        with aioresponses() as m:
            m.post(
                "http://localhost:8000/api/v1/tickets/outbound/",
                payload={"ok": True},
            )

            result = await client.delete_message(1, 2)

            assert result == {"ok": True}
            request = list(m.requests.values())[0][0]
            assert request.kwargs["json"] == {
                "discord_channel_id": 1,
                "discord_message_id": 2,
                "deleted": True,
            }

            await client.close()

    @pytest.mark.asyncio
    async def test_list_tickets_success(self, client, api_responses):
        """Test that list_tickets returns (list, total) tuple."""
        import re
        with aioresponses() as m:
            # Use pattern to match URL with query params
            m.get(
                re.compile(r"http://localhost:8000/api/v1/tickets/\?.*"),
                payload={
                    "tickets": [api_responses["ticket"]],
                    "total": 1,
                },
            )

            tickets, total = await client.list_tickets()

            assert len(tickets) == 1
            assert total == 1
            assert isinstance(tickets[0], TicketData)

            await client.close()

    @pytest.mark.asyncio
    async def test_list_tickets_with_filters(self, client, api_responses):
        """Test that list_tickets applies filters correctly."""
        import re
        with aioresponses() as m:
            # Use pattern to match URL with query params
            m.get(
                re.compile(r"http://localhost:8000/api/v1/tickets/\?.*"),
                payload={
                    "tickets": [api_responses["ticket"]],
                    "total": 1,
                },
            )

            tickets, total = await client.list_tickets(
                status="open",
                association_uuid="assoc-1",
                discord_creator_id=123456789,
                limit=10,
                offset=0,
            )

            assert len(tickets) == 1

            await client.close()

    @pytest.mark.asyncio
    async def test_api_timeout_handling(self, client):
        """Test that APIError is raised on timeout."""
        import asyncio
        from aiohttp import ClientError

        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                exception=ClientError("Timeout"),
            )

            with pytest.raises(APIError):
                await client.get_ticket("abc-123")

            await client.close()

    @pytest.mark.asyncio
    async def test_api_connection_error(self, client):
        """Test that APIError is raised on connection failure."""
        from aiohttp import ClientConnectionError

        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                exception=ClientConnectionError("Connection refused"),
            )

            with pytest.raises(APIError):
                await client.get_ticket("abc-123")

            await client.close()

    @pytest.mark.asyncio
    async def test_request_wraps_timeout(self, client):
        """Test that a request timeout is wrapped into APIError."""
        client._session = _FakeSession(request_exc=TimeoutError("timed out"))

        with pytest.raises(APIError):
            await client.get_ticket("abc-123")

    @pytest.mark.asyncio
    async def test_request_wraps_json_decode_error(self, client):
        """Test that an invalid JSON response is wrapped into APIError."""
        import json as json_module

        response = _FakeResponse(
            json_exc=json_module.JSONDecodeError("bad json", "doc", 0)
        )
        client._session = _FakeSession(response=response)

        with pytest.raises(APIError):
            await client.get_ticket("abc-123")

    @pytest.mark.asyncio
    async def test_request_error_status_with_non_json_body_keeps_status_code(self, client):
        """CLIENT-1: a >=400 response with a non-JSON body (e.g. an HTML 503
        page) must still raise an APIError with the real status_code, not a
        confusing decode error with status_code=None."""
        import json as json_module

        response = _FakeResponse(
            status=503,
            json_exc=json_module.JSONDecodeError("bad json", "<html>...</html>", 0),
        )
        client._session = _FakeSession(response=response)

        with pytest.raises(APIError) as exc_info:
            await client.get_ticket("abc-123")

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_request_checks_status_before_parsing_body(self, client):
        """CLIENT-1: status is checked before the body is trusted, so a
        malformed-but-present error body still yields the right status_code
        and error message rather than raising confusingly."""
        response = _FakeResponse(status=400, data={"error": "bad request"})
        client._session = _FakeSession(response=response)

        with pytest.raises(APIError) as exc_info:
            await client.get_ticket("abc-123")

        assert exc_info.value.status_code == 400
        assert "bad request" in str(exc_info.value)


class TestRequestRateLimitBackoff:
    """Tests for SYNC-6: bounded retry/backoff on HTTP 429."""

    @pytest.mark.asyncio
    async def test_429_honours_retry_after_header(self, client, monkeypatch):
        """A 429 with a Retry-After header sleeps for that many seconds
        instead of the default backoff, then succeeds on retry."""
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("Tickets.ticket_api_client.asyncio.sleep", fake_sleep)

        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                status=429,
                headers={"Retry-After": "2"},
                payload={"error": "rate limited"},
            )
            m.get(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                payload={"ticket": {"uuid": "abc-123", "status": "open", "priority": "low"}},
            )

            result = await client.get_ticket("abc-123")

            assert result.uuid == "abc-123"
            assert sleeps == [2.0]

            await client.close()

    @pytest.mark.asyncio
    async def test_429_backs_off_exponentially_without_retry_after(self, client, monkeypatch):
        """Without a Retry-After header, backoff still happens (bounded), not
        a hot loop."""
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("Tickets.ticket_api_client.asyncio.sleep", fake_sleep)

        with aioresponses() as m:
            m.get(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                status=429,
                payload={"error": "rate limited"},
            )
            m.get(
                "http://localhost:8000/api/v1/tickets/abc-123/",
                payload={"ticket": {"uuid": "abc-123", "status": "open", "priority": "low"}},
            )

            result = await client.get_ticket("abc-123")

            assert result.uuid == "abc-123"
            assert len(sleeps) == 1
            assert sleeps[0] > 0

            await client.close()

    @pytest.mark.asyncio
    async def test_429_retries_are_bounded_then_raises(self, client, monkeypatch):
        """A 429 that never clears eventually raises APIError(status_code=429)
        instead of retrying forever."""
        async def fake_sleep(delay):
            return None

        monkeypatch.setattr("Tickets.ticket_api_client.asyncio.sleep", fake_sleep)

        with aioresponses() as m:
            # One more 429 than the client will retry, so the last one is
            # returned to the caller as a real failure.
            for _ in range(client.MAX_RATE_LIMIT_RETRIES + 1):
                m.get(
                    "http://localhost:8000/api/v1/tickets/abc-123/",
                    status=429,
                    payload={"error": "rate limited"},
                )

            with pytest.raises(APIError) as exc_info:
                await client.get_ticket("abc-123")

            assert exc_info.value.status_code == 429

            await client.close()


class TestDataClasses:
    """Tests for data classes."""

    def test_ticket_data_from_dict(self, api_responses):
        """Test TicketData.from_dict creates instance correctly."""
        data = api_responses["ticket"]
        ticket = TicketData.from_dict(data)

        assert ticket.uuid == "abc-123"
        assert ticket.subject == "Test Ticket"
        assert ticket.status == "open"
        assert ticket.priority == "low"

    def test_ticket_data_from_dict_extracts_version_and_strand_fields(self, api_responses):
        """version, stranded, and merged_into must be extracted, not dropped."""
        data = api_responses["ticket"].copy()
        data["version"] = 3
        data["stranded"] = True
        data["merged_into"] = "def-456"

        ticket = TicketData.from_dict(data)

        assert ticket.version == 3
        assert ticket.stranded is True
        assert ticket.merged_into == "def-456"

    def test_ticket_data_from_dict_defaults_missing_version_fields(self, api_responses):
        """Missing version/stranded/merged_into in a response must not raise."""
        ticket = TicketData.from_dict(api_responses["ticket"])

        assert ticket.version == 0
        assert ticket.stranded is False
        assert ticket.merged_into is None

    def test_association_data_from_dict(self, api_responses):
        """Test AssociationData.from_dict creates instance correctly."""
        data = api_responses["associations"][0]
        assoc = AssociationData.from_dict(data)

        assert assoc.uuid == "assoc-1"
        assert assoc.name == "Org 1"
        assert assoc.slug == "org1"

    def test_member_link_status_from_dict_linked(self, api_responses):
        """Test MemberLinkStatus.from_dict for linked member."""
        data = api_responses["link_status_linked"]
        status = MemberLinkStatus.from_dict(data)

        assert status.linked is True
        assert status.member_uuid == "mem-1"
        assert status.member_name == "Test User"

    def test_member_link_status_from_dict_not_linked(self, api_responses):
        """Test MemberLinkStatus.from_dict for unlinked member."""
        data = api_responses["link_status_not_linked"]
        status = MemberLinkStatus.from_dict(data)

        assert status.linked is False
        assert status.member_uuid is None
