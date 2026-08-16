"""Periodic reconciliation of ticket channels with the ticket API.

Archives Discord ticket channels whose backing ticket is soft-deleted or no
longer present in the API.
"""
from __future__ import annotations

import asyncio
import logging
import random

import discord
from discord.ext import tasks

from Shared.bot_instance import cpu_discord_bot
from Tickets.ticket_api_client import APIError, get_api_client
from Tickets.ticket_config import (
    TICKET_ARCHIVE_CATEGORY_ID,
    TICKET_CATEGORY_ID,
    TICKET_GUILD_ID,
)
from Tickets.ticket_permissions import is_pending_ticket_channel, is_ticket_channel

logger = logging.getLogger(__name__)

# Rate-limit pacing (seconds) between channel operations, plus jitter.
_RECONCILE_BASE_SLEEP = 1.0
_RECONCILE_JITTER = 0.5


async def _fetch_live_channel_ids(api) -> set[int]:
    """Fetch all live Discord channel IDs from the ticket API.

    Args:
        api: The ticket API client

    Returns:
        Set of Discord channel IDs that still have a live ticket

    """
    live_ids: set[int] = set()
    offset = 0
    page_size = 100

    while True:
        tickets, total = await api.list_tickets(
            discord_only=True,
            limit=page_size,
            offset=offset,
        )
        for ticket in tickets:
            if ticket.discord_channel_id:
                live_ids.add(ticket.discord_channel_id)

        offset += len(tickets)
        if not tickets or offset >= total:
            break

    return live_ids


async def _archive_channel(guild: discord.Guild, channel: discord.TextChannel) -> None:
    """Archive a ticket channel by moving it to the archive category.

    Non-destructive: leaves the channel in place when no archive category is
    configured.

    Args:
        guild: The Discord guild
        channel: The channel to archive

    """
    if not TICKET_ARCHIVE_CATEGORY_ID:
        logger.info(
            "Reconciliation: no archive category; leaving %s in place",
            channel.name,
        )
        return

    archive_category = guild.get_channel(TICKET_ARCHIVE_CATEGORY_ID)
    if archive_category is None:
        logger.warning(
            "Reconciliation: archive category %s not found",
            TICKET_ARCHIVE_CATEGORY_ID,
        )
        return

    await channel.edit(
        category=archive_category,
        reason="Ticket missing or soft-deleted",
    )
    logger.info("Reconciliation: archived orphan channel %s", channel.name)


async def _reconcile_orphan_channels(guild: discord.Guild) -> int:
    """Archive ticket channels whose ticket no longer exists in the API.

    Args:
        guild: The Discord guild to reconcile

    Returns:
        Number of channels archived

    """
    if not TICKET_CATEGORY_ID:
        return 0

    category = guild.get_channel(TICKET_CATEGORY_ID)
    text_channels = getattr(category, "text_channels", None)
    if text_channels is None:
        logger.warning(
            "Reconciliation: ticket category %s not found",
            TICKET_CATEGORY_ID,
        )
        return 0

    api = get_api_client()

    try:
        live_channel_ids = await _fetch_live_channel_ids(api)
    except APIError as e:
        logger.error("Reconciliation: failed to fetch tickets: %s", e)
        return 0

    archived = 0
    for channel in list(text_channels):
        # Only actual ticket channels are reconciled; info/notice channels in
        # the same category are left untouched.
        if not is_ticket_channel(channel):
            continue

        # A channel still carrying the pending marker is mid-creation and must
        # not be archived as an orphan before its API row exists.
        if is_pending_ticket_channel(channel):
            continue

        if channel.id in live_channel_ids:
            continue

        try:
            await _archive_channel(guild, channel)
            archived += 1
        except discord.HTTPException as e:
            logger.error(
                "Reconciliation: failed to archive %s: %s",
                channel.name,
                e,
            )

        # Rate-limit pacing with jitter between channel operations.
        await asyncio.sleep(_RECONCILE_BASE_SLEEP + random.uniform(0, _RECONCILE_JITTER))

    if archived:
        logger.info(
            "Reconciliation: archived %d orphan ticket channels",
            archived,
        )

    return archived


@tasks.loop(hours=1)
async def reconcile_ticket_channels() -> None:
    """Hourly reconciliation of ticket channels against the API."""
    guild = cpu_discord_bot.get_guild(TICKET_GUILD_ID)
    if guild is None:
        logger.warning("Reconciliation: guild %s not found", TICKET_GUILD_ID)
        return

    await _reconcile_orphan_channels(guild)


@reconcile_ticket_channels.before_loop
async def _before_reconcile() -> None:
    """Wait for the bot to be ready before starting reconciliation."""
    await cpu_discord_bot.wait_until_ready()
    logger.info("Starting ticket channel reconciliation loop")


@reconcile_ticket_channels.error
async def _reconcile_error(error: BaseException) -> None:
    """Log an unhandled reconciliation exception and keep the loop alive."""
    logger.error("Reconciliation loop error: %s", error, exc_info=error)
