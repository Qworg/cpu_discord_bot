import json
import logging
import os

import discord

from Utilities.discord_utilities import CPU_GUILD_ID
from Utilities.enforce_role_access_task import enforce_role_access
from bot_instance import cpu_discord_bot

intents = discord.Intents.default()
intents.message_content = True  # Allows bot to read message content (needed for commands)

# Files that are imported to bring in Discord / commands
import importlib

importlib.import_module("GameManagement.job_queue_commands")
importlib.import_module("GameManagement.news_commands")
importlib.import_module("GameManagement.player_commands")
importlib.import_module("Rulebook.rulebook_commands")
importlib.import_module("Schematics.item_commands")
importlib.import_module("Tests.live_tests")
importlib.import_module("Tables.Escape.escape_commands")
importlib.import_module("Tables.Loot.loot_commands")
importlib.import_module("Tables.NPC.npc_commands")
importlib.import_module("Tables.Scavenge.scavenge_commands")
# importlib.import_module("Utilities.user_city_commands")

# Ticket system (larpmanager integration)
importlib.import_module("Tickets.ticket_commands")

logging.basicConfig(
    filename='./Shared/bot.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    force=True
)


async def global_log(interaction: discord.Interaction):
    args = interaction.namespace.__dict__ if interaction.namespace else {}
    logging.info(
        f"{interaction.user} (ID: {interaction.user.id}) ran /{interaction.command.name} "
        f"in #{interaction.channel.id} with args: {args}"
    )
    return True


cpu_discord_bot.tree.interaction_check = global_log


@cpu_discord_bot.event
async def on_ready():
    guild = cpu_discord_bot.get_guild(CPU_GUILD_ID)
    await cpu_discord_bot.tree.sync(guild=guild)
    logging.info('Connected to bot: {}'.format(cpu_discord_bot.user.name))
    logging.info('Bot ID: {}'.format(cpu_discord_bot.user.id))
    if not enforce_role_access.is_running():
        enforce_role_access.start()


# Pull in all of the config files
config_dir = os.path.join(os.getcwd(), "Config")
config_path = os.path.join(config_dir, "config.json")

# Actually run the bot
with open(config_path) as f:
    config = json.load(f)

cpu_discord_bot.run(config["token"])
