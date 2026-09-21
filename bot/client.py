# bot/client.py
#
# Discord client wiring:
#  - Monitoring runs on EVERY eligible message, silently, regardless
#    of mentions.
#  - Chat only ever sends a message when the bot is @mentioned or
#    someone replies to one of its own messages (per config.py).

import re

import discord

from . import config
from . import monitor
from .ai import AI

intents = discord.Intents.default()
intents.message_content = True  # must also be enabled in the Dev Portal

client = discord.Client(intents=intents)
ai = AI()


def strip_mentions(content, bot_user_id):
    return re.sub(rf"<@!?{bot_user_id}>", "", content).strip()


def chunk_for_discord(text, limit=None):
    limit = limit or config.DISCORD_MSG_LIMIT

    if len(text) <= limit:
        yield text
        return

    while text:
        if len(text) <= limit:
            yield text
            return

        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit

        yield text[:split_at]
        text = text[split_at:].lstrip()


def get_conversation_id(message):
    if message.guild is None:
        return f"dm:{message.author.id}" if config.SEPARATE_DMS else "dm"
    return f"guild:{message.guild.id}" if config.SEPARATE_BY_GUILD else "guild"


async def _is_reply_to_bot(message):
    if not message.reference:
        return False

    resolved = message.reference.resolved

    if isinstance(resolved, discord.DeletedReferencedMessage):
        return False

    if resolved is None:
        try:
            resolved = await message.channel.fetch_message(
                message.reference.message_id
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    return resolved is not None and resolved.author.id == client.user.id


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    print(f"[monitor] running startup backfill (last {config.BACKFILL_HOURS}h)...")

    await monitor.run_backfill(client, ai)
    client.loop.create_task(monitor.live_rescan_loop(client, ai))

    print("Discord monitor bot is ready.")


@client.event
async def on_message(message):
    if client.user is not None and message.author.id == client.user.id:
        return

    # ---------------------------------------------------------------
    # Monitoring - runs independently of chat, on every eligible
    # guild message, never sends anything to the channel by itself.
    # ---------------------------------------------------------------
    if message.guild is not None:
        await monitor.process_message(ai, message)

    # ---------------------------------------------------------------
    # Chat - only ever triggers on mention / reply-to-bot (or DMs)
    # ---------------------------------------------------------------
    if message.author.bot:
        return

    is_dm = message.guild is None

    if is_dm:
        if not config.ALLOW_DM_CHAT:
            return
        trigger = True
    else:
        mentioned = client.user in message.mentions
        replied_to_bot = (
            await _is_reply_to_bot(message)
            if config.RESPOND_ON_REPLY_TO_BOT else False
        )
        trigger = (mentioned and config.RESPOND_ON_MENTION) or replied_to_bot

    if not trigger:
        return

    question = strip_mentions(message.content, client.user.id)
    if not question:
        return

    conversation_id = get_conversation_id(message)

    async with message.channel.typing():
        try:
            answer = await ai.chat(conversation_id, question)
        except Exception as e:
            await message.reply(f"⚠ AI error: {e}", mention_author=False)
            return

    first = True
    for chunk in chunk_for_discord(answer):
        if first:
            await message.reply(chunk, mention_author=False)
            first = False
        else:
            await message.channel.send(chunk)


def run():
    if not config.DISCORD_BOT_TOKEN or config.DISCORD_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is not configured. Edit bot/config.py first."
        )

    client.run(config.DISCORD_BOT_TOKEN)
