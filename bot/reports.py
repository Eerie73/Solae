# bot/reports.py
#
# Writes the human-readable per-user, per-day report files described
# in the spec. Filename: report-<user_id>-<month_abbr>-<day>.txt
# If that file already exists (same user flagged again the same
# local day), the new violation is appended at the bottom, separated
# by a "=====" divider - never overwritten.

import os

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config

os.makedirs(config.REPORTS_DIR, exist_ok=True)

SEPARATOR = "=" * 60


def _get_timezone():
    try:
        return ZoneInfo(config.TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _report_path(user_id, local_dt):
    month = config.MONTH_ABBR.get(local_dt.month, local_dt.strftime("%b").lower())
    filename = f"report-{user_id}-{month}-{local_dt.day}.txt"
    return os.path.join(config.REPORTS_DIR, filename)


def _format_entry(message, analysis, local_dt):
    display_name = getattr(message.author, "display_name", None) or str(message.author)
    attachments = (
        ", ".join(a.filename for a in message.attachments)
        if message.attachments else "None"
    )
    flags = ", ".join(analysis.get("flags") or []) or "None"
    reason = analysis.get("reason") or "Not specified"
    edited = "True" if getattr(message, "edited_at", None) else "False"
    content = message.content if message.content else "(no text content)"
    sent_time = local_dt.strftime("%B %d, %Y, %I:%M %p")

    lines = [
        SEPARATOR,
        f"Display Name: {display_name}",
        f"User ID: {message.author.id}",
        f"Message ID: {message.id}",
        f"Message Link: {message.jump_url}",
        f"Message Content: {content}",
        f"Message Attachments: {attachments}",
        f"Flags: {flags}",
        f"Reason: {reason}",
        f"Message Sent Time: {sent_time}",
        f"Message Edited: {edited}",
        SEPARATOR,
        "",
    ]
    return "\n".join(lines)


def save_report(message, analysis):
    """
    Creates today's report file for this user if it doesn't exist yet,
    otherwise appends the new violation to the bottom of it.
    Returns the file path written to.
    """
    tz = _get_timezone()
    sent_utc = message.created_at  # aware UTC datetime from discord.py
    local_dt = sent_utc.astimezone(tz)

    path = _report_path(message.author.id, local_dt)
    entry = _format_entry(message, analysis, local_dt)

    file_exists = os.path.exists(path)
    mode = "a" if file_exists else "w"

    with open(path, mode, encoding="utf-8") as f:
        if file_exists:
            f.write("\n")
        f.write(entry)

    return path
