# bot/monitor.py
#
# The monitoring side of the bot. Completely independent from the
# chat side - it never sends a message to the server on its own
# (only silent report files, unless you explicitly enable the
# delete_message / notify_mod_channel actions in config.py).

import asyncio
import datetime

import discord

from . import config
from . import moderation_rules
from . import reports
from . import state as state_store


def channel_is_monitorable(channel, me):
    """Only monitor channels the bot can actually view/read, and that
    aren't in the exclusion list."""
    if channel.id in config.CHANNEL_EXCLUSIONS:
        return False

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return False

    perms = channel.permissions_for(me)
    return perms.view_channel and perms.read_message_history


def _should_analyze(content, attachment_names):
    if attachment_names:
        return True

    stripped = (content or "").strip()
    if len(stripped) >= config.MIN_MESSAGE_LENGTH_TO_ANALYZE:
        return True

    lowered = stripped.lower()
    return any(
        keyword.lower() in lowered
        for keyword in moderation_rules.ALWAYS_CHECK_KEYWORDS
        if keyword
    )


async def _handle_flagged(message, result):
    if config.ACTIONS.get("create_report"):
        path = reports.save_report(message, result)
        print(
            f"[monitor] flagged message from {message.author} "
            f"in #{message.channel}: {result.get('category')} -> {path}"
        )

    if config.ACTIONS.get("delete_message"):
        try:
            await message.delete()
        except discord.Forbidden:
            print(
                "[monitor] delete_message is enabled but the bot lacks the "
                "'Manage Messages' permission - message was NOT deleted."
            )
        except discord.HTTPException as e:
            print(f"[monitor] failed to delete flagged message: {e}")

    if config.ACTIONS.get("notify_mod_channel") and config.MOD_LOG_CHANNEL_ID:
        channel = message.guild.get_channel(config.MOD_LOG_CHANNEL_ID)
        if channel is not None:
            try:
                await channel.send(
                    f"🚩 Flagged message from {message.author.mention} "
                    f"in {message.channel.mention} - "
                    f"**{result.get('category')}**: {result.get('reason')}"
                )
            except discord.HTTPException:
                pass


async def process_message(ai, message, mark_processed=True):
    """Runs a single message through moderation analysis (if eligible)
    and acts on it if flagged. Used for both live messages and
    backfilled history."""
    try:
        eligible = (
            config.MONITORING_ENABLED
            and message.guild is not None
            and not (config.IGNORE_BOTS and message.author.bot)
            and message.channel.id not in config.CHANNEL_EXCLUSIONS
        )

        if eligible:
            content = message.content or ""
            attachment_names = (
                [a.filename for a in message.attachments]
                if config.ANALYZE_ATTACHMENT_NAMES else None
            )

            if _should_analyze(content, attachment_names):
                result = await ai.analyze_message(content, attachment_names)
                if result.get("flagged"):
                    await _handle_flagged(message, result)

    except Exception as e:
        print(f"[monitor] error processing message {message.id}: {e}")

    finally:
        if mark_processed and message.guild is not None:
            state_store.set_last_processed(message.channel.id, message.id)


async def _channel_after_bound(channel, cutoff):
    """Returns (after, last_id) - 'after' is what to pass to
    channel.history(), respecting both the saved position and the
    BACKFILL_HOURS cap."""
    last_id = state_store.get_last_processed_id(channel.id)

    if last_id is not None:
        last_time = discord.utils.snowflake_time(last_id)
        if last_time > cutoff:
            return discord.Object(id=last_id), last_id

    return cutoff, last_id


async def backfill_guild(ai, guild):
    me = guild.me
    cutoff = discord.utils.utcnow() - datetime.timedelta(hours=config.BACKFILL_HOURS)

    channels = list(guild.text_channels)
    if config.MONITOR_THREADS:
        channels += list(guild.threads)

    for channel in channels:
        if not channel_is_monitorable(channel, me):
            continue

        after, last_id = await _channel_after_bound(channel, cutoff)

        try:
            async for message in channel.history(
                limit=None, after=after, oldest_first=True
            ):
                if last_id is not None and message.id == last_id:
                    continue
                await process_message(ai, message)

        except discord.Forbidden:
            continue
        except discord.HTTPException as e:
            print(f"[monitor] could not read history for #{channel}: {e}")
            continue


async def run_backfill(client, ai):
    for guild in client.guilds:
        await backfill_guild(ai, guild)
    print("[monitor] scan complete")


async def live_rescan_loop(client, ai):
    """Safety net in case a brief disconnect causes on_message to miss
    something. Already-processed messages are always skipped, so this
    never creates duplicate reports."""
    await client.wait_until_ready()

    while not client.is_closed():
        await asyncio.sleep(config.LIVE_RESCAN_INTERVAL_SECONDS)
        try:
            await run_backfill(client, ai)
        except Exception as e:
            print(f"[monitor] rescan error: {e}")
