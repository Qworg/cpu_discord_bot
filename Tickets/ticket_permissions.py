"""Discord channel and permission management for tickets.

This module handles:
- Creating private ticket channels
- Managing channel permissions
- Adding/removing users from tickets
- Archiving closed tickets
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord

from Tickets.ticket_config import (
    TICKET_ARCHIVE_CATEGORY_ID,
    TICKET_CATEGORY_ID,
    TICKET_CHANNEL_PREFIX,
    TICKET_STAFF_ROLE_IDS,
    TICKET_VIEWER_ROLE_IDS,
)

if TYPE_CHECKING:
    from discord import Guild, Member, TextChannel, User

logger = logging.getLogger(__name__)


def slugify(text: str, max_length: int = 20) -> str:
    """Convert text to a URL-safe slug for channel names.

    Args:
        text: Text to convert
        max_length: Maximum length of the slug

    Returns:
        Slugified text

    """
    # Convert to lowercase and replace spaces with dashes
    slug = text.lower().replace(" ", "-")
    # Remove non-alphanumeric characters (except dashes)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    # Remove multiple consecutive dashes
    slug = re.sub(r"-+", "-", slug)
    # Remove leading/trailing dashes
    slug = slug.strip("-")
    # Truncate to max length
    return slug[:max_length] if slug else "ticket"


def generate_channel_name(subject: str, ticket_uuid: str) -> str:
    """Generate a channel name for a ticket.

    Args:
        subject: Ticket subject
        ticket_uuid: Ticket UUID (will use first 8 chars)

    Returns:
        Channel name string

    """
    slug = slugify(subject) if subject else "support"
    short_id = ticket_uuid[:8] if ticket_uuid else "unknown"
    return f"{TICKET_CHANNEL_PREFIX}-{slug}-{short_id}"


async def create_ticket_channel(
    guild: Guild,
    creator: Member | User,
    subject: str,
    ticket_uuid: str,
    association_name: str | None = None,
) -> TextChannel:
    """Create a private ticket channel.

    Args:
        guild: The Discord guild
        creator: The user creating the ticket
        subject: Ticket subject for channel name
        ticket_uuid: Ticket UUID for unique identification
        association_name: Name of the association (for channel topic)

    Returns:
        The created TextChannel

    Raises:
        ValueError: If ticket category is not configured
        discord.Forbidden: If bot lacks permissions
        discord.HTTPException: If channel creation fails

    """
    if not TICKET_CATEGORY_ID:
        raise ValueError(
            "Ticket category not configured. Please set TICKET_CATEGORY_ID in config."
        )

    # Get the ticket category
    category = guild.get_channel(TICKET_CATEGORY_ID)
    if category is None:
        raise ValueError(
            f"Ticket category {TICKET_CATEGORY_ID} not found in guild."
        )

    # Generate channel name
    channel_name = generate_channel_name(subject, ticket_uuid)

    # Build permission overwrites
    overwrites = {
        # Deny everyone by default
        guild.default_role: discord.PermissionOverwrite(
            read_messages=False,
            send_messages=False,
        ),
        # Allow the ticket creator
        creator: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
        ),
        # Allow the bot itself
        guild.me: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
        ),
    }

    # Add staff roles
    for role_id in TICKET_STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
            )

    # Add viewer roles (read-only)
    for role_id in TICKET_VIEWER_ROLE_IDS:
        role = guild.get_role(role_id)
        if role and role not in overwrites:
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=False,
                read_message_history=True,
            )

    # Create the channel
    topic = f"Support ticket: {subject}"
    if association_name:
        topic += f" | Association: {association_name}"
    topic += f" | ID: {ticket_uuid}"

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        topic=topic,
        overwrites=overwrites,
        reason=f"Ticket created by {creator}",
    )

    logger.info(
        f"Created ticket channel {channel.name} (ID: {channel.id}) for {creator}"
    )

    return channel


async def add_user_to_ticket(
    channel: TextChannel,
    user: Member | User,
    can_send: bool = True,
) -> None:
    """Add a user to a ticket channel.

    Args:
        channel: The ticket channel
        user: The user to add
        can_send: Whether the user can send messages

    """
    await channel.set_permissions(
        user,
        read_messages=True,
        send_messages=can_send,
        embed_links=can_send,
        attach_files=can_send,
        read_message_history=True,
        reason=f"Added to ticket",
    )
    logger.info(f"Added {user} to ticket channel {channel.name}")


async def remove_user_from_ticket(
    channel: TextChannel,
    user: Member | User,
) -> None:
    """Remove a user from a ticket channel.

    Args:
        channel: The ticket channel
        user: The user to remove

    """
    await channel.set_permissions(
        user,
        overwrite=None,
        reason=f"Removed from ticket",
    )
    logger.info(f"Removed {user} from ticket channel {channel.name}")


async def close_ticket_channel(
    channel: TextChannel,
    creator: Member | User | None = None,
) -> None:
    """Close a ticket channel by removing send permissions.

    The channel remains visible but users can no longer send messages.
    Staff retain full access.

    Args:
        channel: The ticket channel
        creator: The ticket creator (to restrict their permissions)

    """
    guild = channel.guild

    # Update creator permissions to read-only
    if creator:
        await channel.set_permissions(
            creator,
            read_messages=True,
            send_messages=False,
            embed_links=False,
            attach_files=False,
            read_message_history=True,
            reason="Ticket closed",
        )

    # Optionally move to archive category
    if TICKET_ARCHIVE_CATEGORY_ID:
        archive_category = guild.get_channel(TICKET_ARCHIVE_CATEGORY_ID)
        if archive_category:
            await channel.edit(
                category=archive_category,
                reason="Ticket closed - moved to archive",
            )

    # Update channel name to indicate closed status
    if not channel.name.startswith("closed-"):
        try:
            await channel.edit(
                name=f"closed-{channel.name}",
                reason="Ticket closed",
            )
        except discord.HTTPException:
            pass  # Channel name might be too long

    logger.info(f"Closed ticket channel {channel.name}")


async def reopen_ticket_channel(
    channel: TextChannel,
    creator: Member | User | None = None,
) -> None:
    """Reopen a closed ticket channel.

    Args:
        channel: The ticket channel
        creator: The ticket creator (to restore their permissions)

    """
    guild = channel.guild

    # Restore creator permissions
    if creator:
        await channel.set_permissions(
            creator,
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            reason="Ticket reopened",
        )

    # Move back to main ticket category if it was archived
    if TICKET_CATEGORY_ID:
        ticket_category = guild.get_channel(TICKET_CATEGORY_ID)
        if ticket_category and channel.category_id == TICKET_ARCHIVE_CATEGORY_ID:
            await channel.edit(
                category=ticket_category,
                reason="Ticket reopened - moved from archive",
            )

    # Update channel name to remove closed prefix
    if channel.name.startswith("closed-"):
        try:
            await channel.edit(
                name=channel.name[7:],  # Remove "closed-" prefix
                reason="Ticket reopened",
            )
        except discord.HTTPException:
            pass

    logger.info(f"Reopened ticket channel {channel.name}")


async def delete_ticket_channel(
    channel: TextChannel,
    reason: str = "Ticket deleted",
) -> None:
    """Delete a ticket channel permanently.

    Args:
        channel: The ticket channel to delete
        reason: Reason for deletion (for audit log)

    """
    logger.info(f"Deleting ticket channel {channel.name}")
    await channel.delete(reason=reason)


def is_ticket_channel(channel: TextChannel) -> bool:
    """Check if a channel is a ticket channel.

    Args:
        channel: The channel to check

    Returns:
        True if the channel is a ticket channel

    """
    # Check if it's in the ticket category
    if TICKET_CATEGORY_ID and channel.category_id == TICKET_CATEGORY_ID:
        return True

    # Check if it's in the archive category
    if TICKET_ARCHIVE_CATEGORY_ID and channel.category_id == TICKET_ARCHIVE_CATEGORY_ID:
        return True

    # Check channel name pattern
    name = channel.name
    if name.startswith(TICKET_CHANNEL_PREFIX + "-"):
        return True
    if name.startswith("closed-" + TICKET_CHANNEL_PREFIX + "-"):
        return True

    return False


def user_is_staff(member: Member) -> bool:
    """Check if a member has a staff role.

    Args:
        member: The member to check

    Returns:
        True if the member has any staff role

    """
    return any(role.id in TICKET_STAFF_ROLE_IDS for role in member.roles)


async def get_channel_creator_id(channel: TextChannel) -> int | None:
    """Extract the creator's Discord ID from a ticket channel.

    This parses the channel topic to find the ticket creator.

    Args:
        channel: The ticket channel

    Returns:
        Discord user ID of the creator, or None if not found

    """
    # The creator is tracked in the larpmanager API via the ticket
    # We can't reliably get it from the channel alone
    # This would need to query the API or check permission overwrites
    
    # Check permission overwrites for non-role user overwrites
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, (discord.Member, discord.User)):
            # Skip the bot itself
            if target.id == channel.guild.me.id:
                continue
            # If they have read access but it's not a role, likely the creator
            if overwrite.read_messages:
                return target.id

    return None
