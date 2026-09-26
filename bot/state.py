# bot/state.py
#
# All persistent, non-report data lives as JSON under JSON_DIR - never
# hardcoded in the .py files. This file tracks two things:
#
#   1. "how far have we already scanned in each channel" - the cursor
#      used by monitor.py's backfill to resume where it left off.
#   2. the set of message IDs that have already been flagged - a
#      second, independent safety net so that a message is NEVER
#      acted on (report/delete/notify) twice, by ID, even across
#      restarts, even if the scan cursor above is ever wrong.

import json
import os

from . import config

os.makedirs(config.JSON_DIR, exist_ok=True)
STATE_PATH = os.path.join(config.JSON_DIR, "state.json")

# Bumped once: pre-fix builds could advance a channel's scan cursor
# even when a message was never actually analyzed (moderation AI
# unreachable). Loading a state file saved by an older version forces
# one honest full re-scan of the backfill window on the next restart -
# see _migrate_if_needed(). Already-known flagged message IDs are kept,
# so that re-scan can never produce a duplicate report.
STATE_VERSION = 2

_state = None


def _load_from_disk():
    if not os.path.exists(STATE_PATH):
        return {"channels": {}, "flagged_message_ids": {}, "version": STATE_VERSION}

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"channels": {}, "flagged_message_ids": {}, "version": STATE_VERSION}

    data.setdefault("channels", {})
    data.setdefault("flagged_message_ids", {})
    data.setdefault("version", 1)  # files from before this field existed
    return data


def _migrate_if_needed(state):
    if state.get("version") == STATE_VERSION:
        return

    if state["channels"]:
        print(
            "[state] state.json predates the moderation-AI-retry fix - "
            "resetting per-channel scan cursors so the next backfill does "
            "one full, honest re-scan of the configured BACKFILL_HOURS "
            "window (already-flagged message history is kept, so this "
            "cannot produce duplicate reports)"
        )

    state["channels"] = {}
    state["version"] = STATE_VERSION
    save_state()


def get_state():
    global _state
    if _state is None:
        _state = _load_from_disk()
        _migrate_if_needed(_state)
    return _state


def save_state():
    state = get_state()
    tmp_path = STATE_PATH + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    os.replace(tmp_path, STATE_PATH)


def get_last_processed_id(channel_id):
    channel_state = get_state()["channels"].get(str(channel_id))
    return channel_state.get("last_processed_id") if channel_state else None


def set_last_processed(channel_id, message_id, persist=True):
    state = get_state()
    channel_state = state["channels"].setdefault(str(channel_id), {})

    current = channel_state.get("last_processed_id")
    if current is None or message_id > current:
        channel_state["last_processed_id"] = message_id
        if persist:
            save_state()


def is_already_flagged(message_id):
    """True if this exact message ID has already triggered a flag
    before (this run or a previous one). Used to guarantee a message
    is never flagged/reported/deleted twice, independent of whatever
    the channel scan cursor says."""
    return str(message_id) in get_state().setdefault("flagged_message_ids", {})


def mark_flagged(message_id, persist=True):
    state = get_state()
    flagged = state.setdefault("flagged_message_ids", {})
    key = str(message_id)

    if key not in flagged:
        flagged[key] = True
        if persist:
            save_state()
