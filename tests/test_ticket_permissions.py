"""Tests for ticket channel permissions management."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord


class TestSlugify:
    """Tests for the slugify function."""

    def test_slugify_basic(self):
        """Test basic text to slug conversion."""
        from Tickets.ticket_permissions import slugify

        result = slugify("Hello World")
        assert result == "hello-world"

    def test_slugify_special_chars(self):
        """Test that special characters are removed."""
        from Tickets.ticket_permissions import slugify

        result = slugify("Test! @#$% Issue")
        assert result == "test-issue"

    def test_slugify_max_length(self):
        """Test that slugify truncates to max_length."""
        from Tickets.ticket_permissions import slugify

        result = slugify("This is a very long subject line", max_length=10)
        assert len(result) <= 10
        # The function truncates after slugifying, so trailing dash may remain
        assert result.startswith("this-is-a")

    def test_slugify_empty(self):
        """Test that empty string returns 'ticket'."""
        from Tickets.ticket_permissions import slugify

        result = slugify("")
        assert result == "ticket"

    def test_slugify_only_special_chars(self):
        """Test that string with only special chars returns 'ticket'."""
        from Tickets.ticket_permissions import slugify

        result = slugify("!@#$%^&*()")
        assert result == "ticket"

    def test_slugify_multiple_spaces(self):
        """Test that multiple spaces become single dash."""
        from Tickets.ticket_permissions import slugify

        result = slugify("Hello    World")
        assert result == "hello-world"

    def test_slugify_leading_trailing_dashes(self):
        """Test that leading/trailing dashes are removed."""
        from Tickets.ticket_permissions import slugify

        result = slugify("-test-string-")
        assert result == "test-string"


class TestGenerateChannelName:
    """Tests for channel name generation."""

    def test_generate_channel_name(self):
        """Test that channel name is generated correctly."""
        from Tickets.ticket_permissions import generate_channel_name

        with patch("Tickets.ticket_permissions.TICKET_CHANNEL_PREFIX", "ticket"):
            result = generate_channel_name("Test Issue", "abc12345-uuid")

        assert result.startswith("ticket-")
        assert "test-issue" in result
        assert "abc12345" in result

    def test_generate_channel_name_no_subject(self):
        """Test channel name with empty subject."""
        from Tickets.ticket_permissions import generate_channel_name

        with patch("Tickets.ticket_permissions.TICKET_CHANNEL_PREFIX", "ticket"):
            result = generate_channel_name("", "abc12345-uuid")

        assert "support" in result
        assert "abc12345" in result

    def test_generate_channel_name_short_uuid(self):
        """Test that only first 8 chars of UUID are used."""
        from Tickets.ticket_permissions import generate_channel_name

        with patch("Tickets.ticket_permissions.TICKET_CHANNEL_PREFIX", "ticket"):
            result = generate_channel_name("Test", "12345678901234567890")

        assert "12345678" in result
        assert "901234567890" not in result


class TestCreateTicketChannel:
    """Tests for ticket channel creation."""

    @pytest.mark.asyncio
    async def test_create_ticket_channel_success(self, mock_guild):
        """Test that channel is created with correct permissions."""
        from Tickets.ticket_permissions import create_ticket_channel

        creator = MagicMock()
        creator.id = 123456789

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", 123456789):
            with patch("Tickets.ticket_permissions.TICKET_STAFF_ROLE_IDS", [752234266871726111]):
                with patch("Tickets.ticket_permissions.TICKET_VIEWER_ROLE_IDS", []):
                    channel = await create_ticket_channel(
                        guild=mock_guild,
                        creator=creator,
                        subject="Test Subject",
                        ticket_uuid="abc-123",
                        association_name="Test Org",
                    )

        mock_guild.create_text_channel.assert_called_once()
        assert channel is not None

    @pytest.mark.asyncio
    async def test_create_ticket_channel_with_none_creator(self, mock_guild):
        """Test that a None creator yields no creator overwrite and no raise."""
        from Tickets.ticket_permissions import create_ticket_channel

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", 123456789):
            with patch("Tickets.ticket_permissions.TICKET_STAFF_ROLE_IDS", [752234266871726111]):
                with patch("Tickets.ticket_permissions.TICKET_VIEWER_ROLE_IDS", []):
                    channel = await create_ticket_channel(
                        guild=mock_guild,
                        creator=None,
                        subject="External ticket",
                        ticket_uuid="abc-456",
                    )

        assert channel is not None
        overwrites = mock_guild.create_text_channel.call_args.kwargs["overwrites"]
        assert None not in overwrites
        # The bot and default role still get overwrites.
        assert mock_guild.me in overwrites
        assert mock_guild.default_role in overwrites

    @pytest.mark.asyncio
    async def test_create_ticket_channel_no_category_configured(self, mock_guild):
        """Test that ValueError is raised when category not configured."""
        from Tickets.ticket_permissions import create_ticket_channel

        creator = MagicMock()

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", None):
            with pytest.raises(ValueError) as exc_info:
                await create_ticket_channel(
                    guild=mock_guild,
                    creator=creator,
                    subject="Test",
                    ticket_uuid="abc-123",
                )

        assert "not configured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_ticket_channel_category_not_found(self, mock_guild):
        """Test that ValueError is raised when category not found in guild."""
        from Tickets.ticket_permissions import create_ticket_channel

        mock_guild.get_channel.return_value = None
        creator = MagicMock()

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", 999999999):
            with pytest.raises(ValueError) as exc_info:
                await create_ticket_channel(
                    guild=mock_guild,
                    creator=creator,
                    subject="Test",
                    ticket_uuid="abc-123",
                )

        assert "not found" in str(exc_info.value)


class TestAddRemoveUsers:
    """Tests for adding/removing users from tickets."""

    @pytest.mark.asyncio
    async def test_add_user_to_ticket(self, mock_channel, mock_member):
        """Test that user is added with correct permission overwrite."""
        from Tickets.ticket_permissions import add_user_to_ticket

        await add_user_to_ticket(mock_channel, mock_member)

        mock_channel.set_permissions.assert_called_once()
        call_args = mock_channel.set_permissions.call_args
        assert call_args[0][0] == mock_member
        assert call_args[1]["read_messages"] is True
        assert call_args[1]["send_messages"] is True

    @pytest.mark.asyncio
    async def test_add_user_to_ticket_read_only(self, mock_channel, mock_member):
        """Test adding user with read-only access."""
        from Tickets.ticket_permissions import add_user_to_ticket

        await add_user_to_ticket(mock_channel, mock_member, can_send=False)

        call_args = mock_channel.set_permissions.call_args
        assert call_args[1]["send_messages"] is False

    @pytest.mark.asyncio
    async def test_remove_user_from_ticket(self, mock_channel, mock_member):
        """Test that user permission overwrite is removed."""
        from Tickets.ticket_permissions import remove_user_from_ticket

        await remove_user_from_ticket(mock_channel, mock_member)

        mock_channel.set_permissions.assert_called_once_with(
            mock_member,
            overwrite=None,
            reason="Removed from ticket",
        )


class TestCloseReopenChannel:
    """Tests for closing and reopening ticket channels."""

    @pytest.mark.asyncio
    async def test_close_ticket_channel_removes_send(self, mock_channel, mock_member):
        """Test that creator's send permission is removed on close."""
        from Tickets.ticket_permissions import close_ticket_channel

        with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
            await close_ticket_channel(mock_channel, mock_member)

        # Should set permissions to read-only
        call_args = mock_channel.set_permissions.call_args
        assert call_args[1]["send_messages"] is False

    @pytest.mark.asyncio
    async def test_close_ticket_channel_moves_to_archive(self, mock_channel, mock_member):
        """Test that channel is moved to archive category if configured."""
        from Tickets.ticket_permissions import close_ticket_channel

        archive_category = MagicMock()
        archive_category.id = 987654321
        mock_channel.guild.get_channel.return_value = archive_category

        with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", 987654321):
            await close_ticket_channel(mock_channel, mock_member)

        mock_channel.edit.assert_called()

    @pytest.mark.asyncio
    async def test_close_ticket_channel_renames(self, mock_channel):
        """Test that channel is renamed with 'closed-' prefix."""
        from Tickets.ticket_permissions import close_ticket_channel

        mock_channel.name = "ticket-test-abc123"

        with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
            await close_ticket_channel(mock_channel, creator=None)

        # Check that edit was called to rename
        edit_calls = [call for call in mock_channel.edit.call_args_list]
        assert any("closed-" in str(call) for call in edit_calls)

    @pytest.mark.asyncio
    async def test_reopen_ticket_channel_restores_send(self, mock_channel, mock_member):
        """Test that creator's send permission is restored on reopen."""
        from Tickets.ticket_permissions import reopen_ticket_channel

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", None):
            with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
                await reopen_ticket_channel(mock_channel, mock_member)

        call_args = mock_channel.set_permissions.call_args
        assert call_args[1]["send_messages"] is True

    @pytest.mark.asyncio
    async def test_reopen_ticket_channel_removes_prefix(self, mock_channel):
        """Test that 'closed-' prefix is removed on reopen."""
        from Tickets.ticket_permissions import reopen_ticket_channel

        mock_channel.name = "closed-ticket-test-abc123"
        mock_channel.category_id = None

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", None):
            with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
                await reopen_ticket_channel(mock_channel, creator=None)

        mock_channel.edit.assert_called()

    @pytest.mark.asyncio
    async def test_close_strips_all_user_overwrites(self):
        """Test that close removes all non-staff user overwrites."""
        from Tickets.ticket_permissions import close_ticket_channel

        channel = AsyncMock()
        channel.name = "ticket-test-abc123"
        channel.overwrites = {}
        channel.guild = MagicMock()
        channel.guild.me = MagicMock(id=111111111)
        channel.guild.get_member.return_value = None

        creator = MagicMock()
        creator.id = 123456789

        added_user = MagicMock()
        added_user.id = 555666777

        staff_role = MagicMock()
        staff_role.id = 752234266871726111
        staff_role.__class__ = discord.Role

        channel.overwrites = {
            creator: MagicMock(),
            added_user: MagicMock(),
            staff_role: MagicMock(),
        }

        with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
            await close_ticket_channel(channel, creator)

        calls = channel.set_permissions.call_args_list

        # Creator is denied read/send access.
        creator_calls = [c for c in calls if c.args[0] is creator]
        assert creator_calls
        assert creator_calls[0].kwargs["read_messages"] is False
        assert creator_calls[0].kwargs["send_messages"] is False

        # /ticketadd user overwrite is stripped.
        added_calls = [c for c in calls if c.args[0] is added_user]
        assert added_calls
        assert added_calls[0].kwargs["overwrite"] is None

        # Staff role overwrite is retained.
        assert not [c for c in calls if c.args[0] is staff_role]

    @pytest.mark.asyncio
    async def test_close_removes_creator_when_member_missing(self):
        """Test that close denies creator access even when the member left."""
        from Tickets.ticket_permissions import close_ticket_channel

        channel = AsyncMock()
        channel.name = "ticket-test-abc123"
        channel.overwrites = {}
        channel.guild = MagicMock()
        channel.guild.me = MagicMock(id=111111111)
        channel.guild.get_member.return_value = None

        with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
            await close_ticket_channel(
                channel,
                creator=None,
                discord_creator_id=123456789,
            )

        # The member has left the guild, so the deny overwrite must be applied
        # via channel.edit (set_permissions only accepts Member/Role).
        overwrite_calls = [
            c for c in channel.edit.call_args_list if "overwrites" in c.kwargs
        ]
        assert overwrite_calls
        overwrites = overwrite_calls[0].kwargs["overwrites"]
        creator_key = discord.Object(id=123456789, type=discord.User)
        assert creator_key in overwrites
        allow, deny = overwrites[creator_key].pair()
        assert deny.read_messages is True
        assert deny.send_messages is True

        # set_permissions must never be called with a bare Object for the creator.
        assert not [
            c
            for c in channel.set_permissions.call_args_list
            if c.args and isinstance(c.args[0], discord.Object)
        ]

    @pytest.mark.asyncio
    async def test_reopen_uses_db_creator_id(self):
        """Test that reopen restores access using the DB discord_creator_id."""
        from Tickets.ticket_permissions import reopen_ticket_channel

        channel = AsyncMock()
        channel.name = "ticket-test-abc123"
        channel.category_id = None
        channel.overwrites = {}
        channel.guild = MagicMock()
        channel.guild.get_member.return_value = None

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", None):
            with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
                await reopen_ticket_channel(
                    channel,
                    creator=None,
                    discord_creator_id=123456789,
                )

        # The member has left the guild, so the allow overwrite must be applied
        # via channel.edit (set_permissions only accepts Member/Role).
        overwrite_calls = [
            c for c in channel.edit.call_args_list if "overwrites" in c.kwargs
        ]
        assert overwrite_calls
        overwrites = overwrite_calls[0].kwargs["overwrites"]
        creator_key = discord.Object(id=123456789, type=discord.User)
        assert creator_key in overwrites
        allow, deny = overwrites[creator_key].pair()
        assert allow.read_messages is True
        assert allow.send_messages is True

        assert not [
            c
            for c in channel.set_permissions.call_args_list
            if c.args and isinstance(c.args[0], discord.Object)
        ]

    @pytest.mark.asyncio
    async def test_close_strips_object_user_overwrites(self):
        """Test that user-level Object overwrites are dropped via channel.edit."""
        from Tickets.ticket_permissions import close_ticket_channel

        channel = AsyncMock()
        channel.name = "ticket-test-abc123"
        channel.guild = MagicMock()
        channel.guild.me = MagicMock(id=111111111)
        channel.guild.get_member.return_value = None

        # A /ticketadd user who left the guild appears as a bare Object key.
        left_user = discord.Object(id=555666777, type=discord.User)
        channel.overwrites = {left_user: MagicMock()}

        with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
            await close_ticket_channel(
                channel,
                creator=None,
                discord_creator_id=123456789,
            )

        # The left user's overwrite is removed via channel.edit, not set_permissions.
        assert not [
            c
            for c in channel.set_permissions.call_args_list
            if c.args and isinstance(c.args[0], discord.Object)
        ]
        edit_overwrite_calls = [
            c for c in channel.edit.call_args_list if "overwrites" in c.kwargs
        ]
        assert edit_overwrite_calls
        assert any(
            left_user not in c.kwargs["overwrites"] for c in edit_overwrite_calls
        )


class TestIsTicketChannel:
    """Tests for ticket channel detection."""

    def test_is_ticket_channel_by_category(self, mock_channel):
        """Test that channel in ticket category is detected."""
        from Tickets.ticket_permissions import is_ticket_channel

        mock_channel.category_id = 123456789

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", 123456789):
            with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
                with patch("Tickets.ticket_permissions.TICKET_CHANNEL_PREFIX", "ticket"):
                    result = is_ticket_channel(mock_channel)

        assert result is True

    def test_is_ticket_channel_by_name(self, mock_channel):
        """Test that channel with ticket- prefix is detected."""
        from Tickets.ticket_permissions import is_ticket_channel

        mock_channel.name = "ticket-support-abc123"
        mock_channel.category_id = 999999999  # Not ticket category

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", 123456789):
            with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", None):
                with patch("Tickets.ticket_permissions.TICKET_CHANNEL_PREFIX", "ticket"):
                    result = is_ticket_channel(mock_channel)

        assert result is True

    def test_is_ticket_channel_false(self, mock_channel):
        """Test that non-ticket channel returns False."""
        from Tickets.ticket_permissions import is_ticket_channel

        mock_channel.name = "general"
        mock_channel.category_id = 999999999

        with patch("Tickets.ticket_permissions.TICKET_CATEGORY_ID", 123456789):
            with patch("Tickets.ticket_permissions.TICKET_ARCHIVE_CATEGORY_ID", 987654321):
                with patch("Tickets.ticket_permissions.TICKET_CHANNEL_PREFIX", "ticket"):
                    result = is_ticket_channel(mock_channel)

        assert result is False


class TestUserIsStaff:
    """Tests for staff role detection."""

    def test_user_is_staff_true(self, mock_staff_member):
        """Test that member with staff role returns True."""
        from Tickets.ticket_permissions import user_is_staff

        with patch("Tickets.ticket_permissions.TICKET_STAFF_ROLE_IDS", [752234266871726111]):
            result = user_is_staff(mock_staff_member)

        assert result is True

    def test_user_is_staff_false(self, mock_member):
        """Test that member without staff role returns False."""
        from Tickets.ticket_permissions import user_is_staff

        mock_member.roles = []  # No roles

        with patch("Tickets.ticket_permissions.TICKET_STAFF_ROLE_IDS", [752234266871726111]):
            result = user_is_staff(mock_member)

        assert result is False

    def test_user_is_staff_wrong_role(self, mock_member):
        """Test that member with different role returns False."""
        from Tickets.ticket_permissions import user_is_staff

        wrong_role = MagicMock()
        wrong_role.id = 999999999
        mock_member.roles = [wrong_role]

        with patch("Tickets.ticket_permissions.TICKET_STAFF_ROLE_IDS", [752234266871726111]):
            result = user_is_staff(mock_member)

        assert result is False
