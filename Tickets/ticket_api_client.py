"""Async HTTP client for larpmanager Ticket API.

This module provides an async HTTP client for communicating with the
larpmanager ticket API endpoints.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from Tickets.ticket_config import (
    API_TIMEOUT,
    LARPMANAGER_API_KEY,
    LARPMANAGER_API_URL,
)

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Exception raised when API calls fail."""

    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


@dataclass
class TicketData:
    """Data class representing a ticket from the API."""

    uuid: str
    subject: str | None
    reason: str | None
    content: str | None
    status: str
    priority: str
    discord_channel_id: int | None
    discord_creator_id: int | None
    assigned_staff_discord_id: int | None
    association: dict | None
    member: dict | None
    email: str | None
    transcript: str | None
    created_at: str | None
    updated_at: str | None
    closed_at: str | None

    @classmethod
    def from_dict(cls, data: dict) -> "TicketData":
        """Create a TicketData instance from API response dict."""
        return cls(
            uuid=data.get("uuid", ""),
            subject=data.get("subject"),
            reason=data.get("reason"),
            content=data.get("content"),
            status=data.get("status", "open"),
            priority=data.get("priority", "low"),
            discord_channel_id=data.get("discord_channel_id"),
            discord_creator_id=data.get("discord_creator_id"),
            assigned_staff_discord_id=data.get("assigned_staff_discord_id"),
            association=data.get("association"),
            member=data.get("member"),
            email=data.get("email"),
            transcript=data.get("transcript"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            closed_at=data.get("closed_at"),
        )


@dataclass
class AssociationData:
    """Data class representing an association from the API."""

    uuid: str
    name: str
    slug: str

    @classmethod
    def from_dict(cls, data: dict) -> "AssociationData":
        """Create an AssociationData instance from API response dict."""
        return cls(
            uuid=data.get("uuid", ""),
            name=data.get("name", ""),
            slug=data.get("slug", ""),
        )


@dataclass
class MemberLinkStatus:
    """Data class representing Discord-Member link status."""

    linked: bool
    member_uuid: str | None = None
    member_name: str | None = None
    member_email: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "MemberLinkStatus":
        """Create a MemberLinkStatus instance from API response dict."""
        member_data = data.get("member", {})
        return cls(
            linked=data.get("linked", False),
            member_uuid=member_data.get("uuid") if member_data else None,
            member_name=member_data.get("name") if member_data else None,
            member_email=member_data.get("email") if member_data else None,
        )


class TicketAPIClient:
    """Async HTTP client for the larpmanager Ticket API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None,
    ):
        """Initialize the API client.

        Args:
            base_url: Base URL for the API (defaults to config)
            api_key: API key for authentication (defaults to config)
            timeout: Request timeout in seconds (defaults to config)

        """
        self.base_url = (base_url or LARPMANAGER_API_URL).rstrip("/")
        self.api_key = api_key or LARPMANAGER_API_KEY
        self.timeout = aiohttp.ClientTimeout(total=timeout or API_TIMEOUT)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    "X-API-Key": self.api_key,
                    "Content-Type": "application/json",
                },
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json_data: dict | None = None,
    ) -> dict:
        """Make an HTTP request to the API.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            endpoint: API endpoint path
            params: Query parameters
            json_data: JSON body data

        Returns:
            Parsed JSON response

        Raises:
            APIError: If the request fails

        """
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        try:
            async with session.request(
                method,
                url,
                params=params,
                json=json_data,
            ) as response:
                response_data = await response.json()

                if response.status >= 400:
                    error_msg = response_data.get("error", f"HTTP {response.status}")
                    raise APIError(error_msg, response.status, response_data)

                return response_data

        except aiohttp.ClientError as e:
            logger.error(f"API request failed: {e}")
            raise APIError(f"Connection error: {e}")
        except TimeoutError as e:
            logger.error(f"API request timed out: {e}")
            raise APIError(f"Request timed out: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"API response was not valid JSON: {e}")
            raise APIError(f"Invalid JSON response: {e}")

    # =========================================================================
    # Discord Linking Endpoints
    # =========================================================================

    async def check_discord_link(self, discord_id: int) -> MemberLinkStatus:
        """Check if a Discord user is linked to a larpmanager member.

        Args:
            discord_id: The Discord user ID to check

        Returns:
            MemberLinkStatus with linked status and member info

        """
        response = await self._request(
            "GET",
            f"/api/v1/discord/member/{discord_id}/",
        )
        return MemberLinkStatus.from_dict(response)

    async def get_oauth_url(self, discord_id: int) -> str:
        """Get the OAuth URL for linking a Discord account.

        Args:
            discord_id: The Discord user ID requesting to link

        Returns:
            OAuth URL string

        """
        response = await self._request(
            "GET",
            f"/api/v1/discord/link/{discord_id}/",
        )
        return response.get("oauth_url", "")

    # =========================================================================
    # Association Endpoints
    # =========================================================================

    async def get_associations(self) -> list[AssociationData]:
        """Get all available associations for ticket creation.

        Returns:
            List of AssociationData objects

        """
        response = await self._request("GET", "/api/v1/associations/")
        return [
            AssociationData.from_dict(assoc)
            for assoc in response.get("associations", [])
        ]

    # =========================================================================
    # Ticket Endpoints
    # =========================================================================

    async def create_ticket(
        self,
        association_uuid: str,
        discord_creator_id: int,
        discord_channel_id: int,
        subject: str | None = None,
        content: str | None = None,
        priority: str = "low",
    ) -> TicketData:
        """Create a new ticket.

        Args:
            association_uuid: UUID of the association
            discord_creator_id: Discord user ID of the creator
            discord_channel_id: Discord channel ID for the ticket
            subject: Ticket subject line
            content: Initial ticket content
            priority: Ticket priority (low, medium, high)

        Returns:
            Created TicketData object

        """
        response = await self._request(
            "POST",
            "/api/v1/tickets/",
            json_data={
                "association_uuid": association_uuid,
                "discord_creator_id": discord_creator_id,
                "discord_channel_id": discord_channel_id,
                "subject": subject,
                "content": content or "",
                "priority": priority,
            },
        )
        return TicketData.from_dict(response.get("ticket", {}))

    async def get_ticket(self, ticket_uuid: str) -> TicketData:
        """Get a ticket by UUID.

        Args:
            ticket_uuid: UUID of the ticket

        Returns:
            TicketData object

        """
        response = await self._request("GET", f"/api/v1/tickets/{ticket_uuid}/")
        return TicketData.from_dict(response.get("ticket", {}))

    async def get_ticket_by_channel(self, channel_id: int) -> TicketData | None:
        """Get a ticket by Discord channel ID.

        Args:
            channel_id: Discord channel ID

        Returns:
            TicketData object or None if not found

        """
        try:
            response = await self._request(
                "GET",
                f"/api/v1/tickets/channel/{channel_id}/",
            )
            return TicketData.from_dict(response.get("ticket", {}))
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    async def write_back_channel_id(self, ticket_uuid: str, channel_id: int) -> dict:
        """Report the Discord channel id for a ticket back to the API.

        Args:
            ticket_uuid: UUID of the ticket
            channel_id: Discord channel ID created for the ticket

        Returns:
            The API response dict.

        """
        return await self._request(
            "POST",
            f"/api/v1/tickets/{ticket_uuid}/channel/",
            json_data={"discord_channel_id": channel_id},
        )

    async def update_ticket(
        self,
        ticket_uuid: str,
        status: str | None = None,
        priority: str | None = None,
        assigned_staff_discord_id: int | None = None,
        subject: str | None = None,
        content: str | None = None,
        transcript: str | None = None,
    ) -> TicketData:
        """Update a ticket.

        Args:
            ticket_uuid: UUID of the ticket
            status: New status (open, working, done)
            priority: New priority (low, medium, high)
            assigned_staff_discord_id: Discord ID of assigned staff
            subject: New subject
            content: New content
            transcript: Transcript text

        Returns:
            Updated TicketData object

        """
        data = {}
        if status is not None:
            data["status"] = status
        if priority is not None:
            data["priority"] = priority
        if assigned_staff_discord_id is not None:
            data["assigned_staff_discord_id"] = assigned_staff_discord_id
        if subject is not None:
            data["subject"] = subject
        if content is not None:
            data["content"] = content
        if transcript is not None:
            data["transcript"] = transcript

        response = await self._request(
            "PATCH",
            f"/api/v1/tickets/{ticket_uuid}/",
            json_data=data,
        )
        return TicketData.from_dict(response.get("ticket", {}))

    async def close_ticket(
        self,
        ticket_uuid: str,
        transcript: str | None = None,
    ) -> TicketData:
        """Close a ticket.

        Args:
            ticket_uuid: UUID of the ticket
            transcript: Optional transcript to save

        Returns:
            Updated TicketData object

        """
        data = {}
        if transcript:
            data["transcript"] = transcript

        response = await self._request(
            "POST",
            f"/api/v1/tickets/{ticket_uuid}/close/",
            json_data=data,
        )
        return TicketData.from_dict(response.get("ticket", {}))

    async def reopen_ticket(self, ticket_uuid: str) -> TicketData:
        """Reopen a closed ticket.

        Args:
            ticket_uuid: UUID of the ticket

        Returns:
            Updated TicketData object

        """
        response = await self._request(
            "POST",
            f"/api/v1/tickets/{ticket_uuid}/reopen/",
        )
        return TicketData.from_dict(response.get("ticket", {}))

    # =========================================================================
    # Outbox Endpoints
    # =========================================================================

    async def get_events(self, since: int, limit: int = 100) -> list[dict]:
        """Fetch outbox events after a monotonic cursor.

        Args:
            since: Return events with id greater than this cursor.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts (empty when the outbox is drained).

        """
        response = await self._request(
            "GET",
            "/api/v1/tickets/events/",
            params={"since": since, "limit": limit},
        )
        return response.get("events", [])

    async def ack_events(self, ids: list[int]) -> dict:
        """Batch-ack applied outbox events.

        Args:
            ids: Event IDs to mark applied and acked.

        Returns:
            The API response dict.

        """
        return await self._request(
            "POST",
            "/api/v1/tickets/events/ack/",
            json_data={"ids": ids},
        )

    async def post_outbound_message(self, payload: dict) -> dict:
        """Report a Discord-originated message to the API.

        Args:
            payload: The outbound message payload.

        Returns:
            The API response dict.

        """
        return await self._request(
            "POST",
            "/api/v1/tickets/outbound/",
            json_data=payload,
        )

    async def update_message(self, payload: dict) -> dict:
        """Report an edited Discord message to the API.

        Reuses POST /outbound/; the server upserts on discord_message_id, so the
        existing row's fields (including the edited content) are replaced.

        Args:
            payload: The full outbound message payload with updated content.

        Returns:
            The API response dict.

        """
        return await self._request(
            "POST",
            "/api/v1/tickets/outbound/",
            json_data=payload,
        )

    async def delete_message(self, discord_channel_id: int, discord_message_id: int) -> dict:
        """Soft-delete a Discord message via the outbound endpoint.

        The server interprets a ``deleted: true`` payload as a soft delete of
        the TicketMessage row matching discord_message_id.

        Args:
            discord_channel_id: The ticket channel id.
            discord_message_id: The Discord message id to delete.

        Returns:
            The API response dict.

        """
        return await self._request(
            "POST",
            "/api/v1/tickets/outbound/",
            json_data={
                "discord_channel_id": discord_channel_id,
                "discord_message_id": discord_message_id,
                "deleted": True,
            },
        )

    async def list_tickets(
        self,
        status: str | None = None,
        association_uuid: str | None = None,
        discord_creator_id: int | None = None,
        discord_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TicketData], int]:
        """List tickets with optional filters.

        Args:
            status: Filter by status (open, working, done)
            association_uuid: Filter by association
            discord_creator_id: Filter by creator's Discord ID
            discord_only: Only return tickets created via Discord
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Tuple of (list of TicketData, total count)

        """
        params = {
            "limit": limit,
            "offset": offset,
            "discord_only": str(discord_only).lower(),
        }
        if status:
            params["status"] = status
        if association_uuid:
            params["association_uuid"] = association_uuid
        if discord_creator_id:
            params["discord_creator_id"] = discord_creator_id

        response = await self._request("GET", "/api/v1/tickets/", params=params)

        tickets = [
            TicketData.from_dict(t)
            for t in response.get("tickets", [])
        ]
        total = response.get("total", len(tickets))

        return tickets, total


# Global client instance
_client: TicketAPIClient | None = None


def get_api_client() -> TicketAPIClient:
    """Get the global API client instance."""
    global _client
    if _client is None:
        _client = TicketAPIClient()
    return _client


async def close_api_client() -> None:
    """Close the global API client instance."""
    global _client
    if _client:
        await _client.close()
        _client = None
