import logging
import os

import discord

# General Channels & Categories
IN_GAME_DIRECT_MESSAGES_CATEGORY_ID = 1284699255205793934
CPU_GUILD_ID = 540903470484553729
PRIORITY_NEWS_CHANNEL_ID = 1249227555907833866

# Indy Channels
INDY_ITEM_CARD_CHANNEL_ID = 1219779997670310008
INDY_JOB_QUEUE_CATEGORY_ID = 1446333298497425578
INDY_JOB_QUEUE_CHANNEL_ID = 1446340456328986828
INDY_LOGISTICS_CATEGORY_ID = 1201382418976866385
INDY_NEWS_CHANNEL_ID = 879095183529107496
INDY_OOC_CHANNEL_ID = 1025815723878125599
INDY_RUMORS_CHANNEL_ID = 1156687003547607091
INDY_STAFF_AFTER_SIM_CATEGORY_ID = 1187136728994156595
INDY_STAFF_TO_DO_CATEGORY_ID = 1187136789635412161

# Seattle Channels
SEATTLE_ITEM_CARD_CHANNEL_ID = 1450222391702720512
SEATTLE_JOB_QUEUE_CATEGORY_ID = 1450220915777605683
SEATTLE_JOB_QUEUE_CHANNEL_ID = 1450221131993714872
SEATTLE_LOGISTICS_CATEGORY_ID = 1450220693911240744
SEATTLE_NEWS_CHANNEL_ID = 982709436881723442
SEATTLE_OOC_CHANNEL_ID = 1025815748880371792
SEATTLE_RUMORS_CHANNEL_ID = 1156687105901207562
SEATTLE_STAFF_AFTER_SIM_CATEGORY_ID = 1185538682892062870
SEATTLE_STAFF_TO_DO_CATEGORY_ID = 1185537846250065950

# Role ids
ADMIN_INDY_ROLE_ID = 752234266871726111
PLAYER_INDY_ROLE_ID = 1013586618529099787
ADMIN_SEATTLE_ROLE_ID = 928109603919634432
PLAYER_SEATTLE_ROLE_ID = 1013587313026146446
NPC_ROLE_ID = 1343417313017466991
LOGISTICS_ROLE_ID = 1196337700878426292

# Ticket System Categories
# NOTE: Create a "Tickets" category in Discord and put the ID here
# To get the ID: Enable Developer Mode > Right-click category > Copy ID
TICKET_CATEGORY_ID = None  # TODO: Set this to your ticket category ID
TICKET_ARCHIVE_CATEGORY_ID = None  # Optional: Category for archived/closed tickets

# User city
CHAPTER_INDY = "Indy"
CHAPTER_SEATTLE = "Seattle"
USER_CITY_FILE = os.path.join(os.getcwd(), "Shared", "Utilities", "user_city_overrides.json")


async def check_admin_role(interaction: discord.Interaction, send_response: bool = True):
    role_names = [role.name for role in interaction.user.roles]
    logging.info(f"Checking roles for {role_names}")
    admin_roles = {ADMIN_INDY_ROLE_ID, ADMIN_SEATTLE_ROLE_ID}
    if any(role.id in admin_roles for role in interaction.user.roles):
        return True
    else:
        if send_response:
            await _send_access_denied_response(interaction)
        return False


async def check_npc_role(interaction: discord.Interaction, send_response: bool = True):
    role_names = [role.name for role in interaction.user.roles]
    print(f"Checking roles for {role_names}")
    if "npc" in role_names:
        return True
    else:
        if send_response:
            await _send_access_denied_response(interaction)
        return False


async def user_has_only_indy_role(interaction: discord.Interaction):
    return (
            await user_has_indy_role(interaction)
            and not await user_has_seattle_role(interaction)
    )


async def user_has_indy_role(interaction: discord.Interaction):
    indy_roles = {ADMIN_INDY_ROLE_ID, PLAYER_INDY_ROLE_ID}
    return any(role.id in indy_roles for role in interaction.user.roles)


async def user_has_only_seattle_role(interaction: discord.Interaction):
    return (
            await user_has_seattle_role(interaction)
            and not await user_has_indy_role(interaction)
    )


async def user_has_seattle_role(interaction: discord.Interaction):
    seattle_roles = {ADMIN_SEATTLE_ROLE_ID, PLAYER_SEATTLE_ROLE_ID}
    return any(role.id in seattle_roles for role in interaction.user.roles)


async def send_message_safe(interaction: discord.Interaction, message: str, ephemeral: bool = False,
                            view: discord.ui.View | None = None):
    logging.info(f"_send_message_safe attempting to send message: {message}")
    kwargs = {"content": message, "ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view

    try:
        if hasattr(interaction, "response") and not interaction.response.is_done():
            await interaction.response.send_message(**kwargs)
        elif hasattr(interaction, "response"):
            await interaction.followup.send(**kwargs)
        else:
            # fallback if interaction object doesn't have response
            await interaction.channel.send(**kwargs)
    except Exception as e:
        logging.error(f"_send_message_safe failed: {e}")


async def skip_writing_strings(selected_entry: str):
    logging.info(f"skip_writing_strings: {selected_entry}")
    if selected_entry == "Unfortunately, it's nothing of value" or selected_entry == "Nothing":
        logging.info(f"skip_writing_strings: returning True")
        return True
    logging.info(f"skip_writing_strings: returning False")
    return False


async def defer_safe(interaction: discord.Interaction, ephemeral: bool = False):
    try:
        if hasattr(interaction, "response") and not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
    except Exception as e:
        logging.error(f"_defer_safe failed: {e}")


async def move_ticket(interaction: discord.Interaction, category):
    channel = interaction.channel

    if category is None:
        await send_message_safe(interaction,
                                "Target category not found.",
                                ephemeral=True
                                )
        return

    await channel.edit(category=category)
    await send_message_safe(interaction,
                            f"Channel moved.",
                            ephemeral=True
                            )


async def _send_access_denied_response(interaction: discord.Interaction):
    await send_message_safe(interaction, "You do not have the required role to run this command.")


def split_into_chunks(text: str, chunk_size: int = 2000, safety_margin: int = 50) -> list[str]:
    if not text:
        return []

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        # Ideal end index
        end = start + chunk_size
        if end >= text_len:
            # Last chunk
            chunks.append(text[start:])
            break

        # Look for the last space within the safety margin before hitting chunk_size
        split_at = text.rfind(" ", start + chunk_size - safety_margin, end)
        if split_at == -1:
            # No space found in range: fallback to hard split at chunk_size
            split_at = end

        chunks.append(text[start:split_at].rstrip())
        start = split_at + 1  # Skip the space

    return chunks


async def send_channel_message_safe(channel: discord.TextChannel, message: str):
    for idx, chunk in enumerate(split_into_chunks(message), 1):
        logging.info(f"send_channel_message_safe: sending chunk {idx} ({len(chunk)} chars) to {channel.id}")
        try:
            await channel.send(chunk)
        except Exception as e:
            logging.error(f"send_channel_message_safe failed on chunk {idx}: {e}")
