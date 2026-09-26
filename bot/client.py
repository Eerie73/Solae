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


class _C:
    """Minimal ANSI helpers. If a terminal doesn't support color the
    codes just render as a no-op-ish sequence; nothing here is load
    bearing for functionality."""
    R = "\033[0m"
    B = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"


def _rule(char="─", width=64):
    print(f"{_C.DIM}{char * width}{_C.R}")


def _count_monitorable_channels(guild):
    me = guild.me
    if me is None:
        return 0, 0

    text_count = sum(
        1 for c in guild.text_channels if monitor.channel_is_monitorable(c, me)
    )
    thread_count = 0
    if config.MONITOR_THREADS:
        thread_count = sum(
            1 for t in guild.threads if monitor.channel_is_monitorable(t, me)
        )
    return text_count, thread_count


@client.event
async def on_ready():
    _rule("═")
    print(f"{_C.B}{_C.CYAN}Solae{_C.R} — Discord monitor + chat bot")
    _rule("═")
    print(f"  Logged in as   : {_C.B}{client.user}{_C.R} (id: {client.user.id})")
    print(f"  Guilds         : {len(client.guilds)}")

    total_text, total_threads = 0, 0
    for guild in client.guilds:
        t, th = _count_monitorable_channels(guild)
        total_text += t
        total_threads += th
    print(
        f"  Monitorable    : {total_text} text channel(s), "
        f"{total_threads} thread(s)"
    )

    mod_ok, mod_detail = await ai.check_ollama_connection()
    if mod_ok:
        models = ", ".join(mod_detail) if mod_detail else "(no models pulled)"
        print(f"  Ollama         : {_C.GREEN}reachable{_C.R} @ {config.OLLAMA_URL}")
        print(f"  Models on host : {models}")
        if config.MODERATION_MODEL not in (mod_detail or []):
            print(
                f"  {_C.YELLOW}⚠ moderation model '{config.MODERATION_MODEL}' "
                f"not found on the Ollama host - pull it or moderation "
                f"calls will fail{_C.R}"
            )
    else:
        print(f"  Ollama         : {_C.RED}unreachable{_C.R} ({mod_detail})")
        print(
            f"  {_C.YELLOW}⚠ moderation is effectively paused until Ollama "
            f"is reachable - affected messages will retry automatically, "
            f"nothing is lost or skipped{_C.R}"
        )

    print(
        f"  Monitoring     : "
        f"{_C.GREEN + 'enabled' if config.MONITORING_ENABLED else _C.RED + 'disabled'}{_C.R}"
        f"  |  backfill window: last {config.BACKFILL_HOURS}h"
        f"  |  rescan every {config.LIVE_RESCAN_INTERVAL_SECONDS}s"
    )
    print(
        f"  Actions        : create_report={config.ACTIONS.get('create_report')}"
        f"  delete_message={config.ACTIONS.get('delete_message')}"
        f"  notify_mod_channel={config.ACTIONS.get('notify_mod_channel')}"
    )
    whitelist_count = len([w for w in config.WHITELIST_WORDS if w and w.strip()])
    print(f"  Whitelist words: {whitelist_count} configured")
    print(
        f"  Chat triggers  : mention={config.RESPOND_ON_MENTION}"
        f"  reply-to-bot={config.RESPOND_ON_REPLY_TO_BOT}"
        f"  dm={config.ALLOW_DM_CHAT}"
    )
    _rule("═")

    print(f"  Running startup backfill (last {config.BACKFILL_HOURS}h)...")
    totals = await monitor.run_backfill(client, ai)
    summary = (
        f"  {_C.GREEN}✔{_C.R} Backfill complete — "
        f"{totals['scanned']} scanned, {totals['flagged']} flagged"
    )
    if totals.get("duplicate"):
        summary += f", {totals['duplicate']} duplicate flag(s) skipped"
    if totals.get("retry"):
        summary += (
            f", {_C.YELLOW}{totals['retry']} pending retry "
            f"(AI was unreachable){_C.R}"
        )
    print(summary)

    client.loop.create_task(monitor.live_rescan_loop(client, ai))

    _rule("═")
    print(f"  {_C.B}{_C.GREEN}Solae is online and monitoring.{_C.R}")
    _rule("═")


@client.event
async def on_message(message):
    if client.user is not None and message.author.id == client.user.id:
        return

    # ---------------------------------------------------------------
    # Monitoring - runs independently of chat, on every eligible
    # guild message, never sends anything to the channel by itself.
    #
    # Fired as a background task instead of awaited: process_message
    # already catches and logs all of its own exceptions internally,
    # so it's safe to fire-and-forget. Previously this was awaited
    # here, which meant EVERY chat reply had to wait for a full
    # moderation-model call (a separate, much larger model) to finish
    # first - doubling latency and forcing Ollama to swap models back
    # and forth on every single message.
    # ---------------------------------------------------------------
    if message.guild is not None:
        client.loop.create_task(monitor.process_message(ai, message))

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
