# bot/state.py
#
# All persistent, non-report data lives as JSON under JSON_DIR - never
# hardcoded in the .py files. Right now this is just "how far have we
# already scanned in each channel", which is what makes the 12h
# backfill + live monitoring work correctly across restarts without
# ever re-flagging the same message twice.

import json
import os

from . import config

os.makedirs(config.JSON_DIR, exist_ok=True)
STATE_PATH = os.path.join(config.JSON_DIR, "state.json")

_state = None


def _load_from_disk():
    if not os.path.exists(STATE_PATH):
        return {"channels": {}}

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"channels": {}}

    data.setdefault("channels", {})
    return data


def get_state():
    global _state
    if _state is None:
        _state = _load_from_disk()
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
