# bot/ai.py
#
# All AI functioning lives here. There are two completely separate
# capabilities that never share context/history:
#
#   1. chat()            -> talks to users when mentioned/replied to.
#                            Uses CHAT_SYSTEM_PROMPT, which knows
#                            nothing about monitoring or reports.
#
#   2. analyze_message()  -> silent background moderation classifier.
#                            Uses the prompt built from
#                            moderation_rules.py. Never sees, and is
#                            never seen by, the chat conversation.

import json
import re

import aiohttp

from . import config
from . import moderation_rules

THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _strip_think(text):
    """Remove deepseek-r1 style <think>...</think> reasoning blocks."""
    return THINK_TAG_RE.sub("", text or "").strip()


def _build_moderation_prompt():
    lines = []
    for name, rule in moderation_rules.CATEGORIES.items():
        if not rule.get("enabled", True):
            continue
        lines.append(f"- {name}: {rule['description']}")

    categories_block = "\n".join(lines) if lines else "- (no categories enabled)"

    server_rules = (config.SERVER_RULES or "").strip()
    if server_rules:
        server_rules_block = f"\nThis server's rules:\n{server_rules}\n"
    else:
        server_rules_block = ""

    return moderation_rules.MODERATION_SYSTEM_PROMPT_TEMPLATE.format(
        categories=categories_block,
        server_rules=server_rules_block,
    )


DEFAULT_ANALYSIS = {"flagged": False, "category": None, "flags": [], "reason": ""}


class ModerationUnavailable(Exception):
    """Raised when the moderation AI backend (Ollama) could not be
    reached or errored out, as opposed to it successfully responding
    with "not flagged". monitor.py deliberately treats this
    differently from a real "not flagged" result: a message must NOT
    be marked as scanned if we never actually got to analyze it, or
    it would silently slip through moderation forever (this was the
    cause of the "past 12h scan doesn't catch anything" bug - every
    message was being marked done even when Ollama was unreachable)."""


# ---------------------------------------------------------------
# Word whitelist (config.WHITELIST_WORDS) - never flag these
# ---------------------------------------------------------------
def _normalize(text):
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


def _strip_whitelisted_words(text):
    remaining = text or ""
    for raw_word in config.WHITELIST_WORDS:
        word = (raw_word or "").strip()
        if not word:
            continue
        remaining = re.sub(r"(?i)\b" + re.escape(word) + r"\b", "", remaining)
    return remaining


def _message_is_fully_whitelisted(text):
    """True if, once every whitelisted word/phrase is stripped out,
    nothing meaningful is left - i.e. the message is made up entirely
    of whitelisted words (+ punctuation/whitespace). Lets us skip the
    AI call entirely for the common case (someone just says a
    whitelisted word on its own)."""
    if not config.WHITELIST_WORDS:
        return False
    if not _normalize(text):
        return False
    return _normalize(_strip_whitelisted_words(text)) == ""


def _flag_is_whitelisted(flag_text):
    normalized = _normalize(flag_text)
    if not normalized:
        return False
    return any(
        normalized == _normalize(word)
        for word in config.WHITELIST_WORDS
        if _normalize(word)
    )


def _apply_whitelist(result):
    """Second safety net: even for mixed-content messages that do go
    to the AI, if every trigger word/phrase the AI itself flagged is
    whitelisted, suppress the flag."""
    if not result.get("flagged"):
        return result

    flags = result.get("flags") or []
    if flags and all(_flag_is_whitelisted(f) for f in flags):
        print(f"[ai] flag suppressed - all trigger words are whitelisted: {flags}")
        return dict(DEFAULT_ANALYSIS)

    return result


class AI:
    def __init__(self):
        self.conversations = {}
        self._moderation_prompt = _build_moderation_prompt()

    # ---------------------------------------------------------------
    # Chat conversation memory helpers
    # ---------------------------------------------------------------
    def get_conversation(self, conversation_id):
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = []
        return self.conversations[conversation_id]

    def clear_conversation(self, conversation_id):
        self.conversations.pop(conversation_id, None)

    # ---------------------------------------------------------------
    # Low level Ollama call
    # ---------------------------------------------------------------
    async def _call_ollama(self, model, messages):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
        }

        timeout = aiohttp.ClientTimeout(total=config.OLLAMA_REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{config.OLLAMA_URL}/api/chat",
                json=payload,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        return data["message"]["content"]

    # ---------------------------------------------------------------
    # 1. Chat (mention / reply-to-bot only, handled by bot.py)
    # ---------------------------------------------------------------
    async def chat(self, conversation_id, user_message):
        history = self.get_conversation(conversation_id)

        messages = [{"role": "system", "content": config.CHAT_SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        raw = await self._call_ollama(config.CHAT_MODEL, messages)
        answer = _strip_think(raw) or raw.strip() or "..."

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": answer})

        if len(history) > config.MAX_HISTORY * 2:
            del history[: -config.MAX_HISTORY * 2]

        return answer

    # ---------------------------------------------------------------
    # 2. Moderation / monitoring analysis (silent, no memory, no chat)
    # ---------------------------------------------------------------
    async def analyze_message(self, content, attachment_names=None):
        """
        Returns a dict:
          {"flagged": bool, "category": str|None, "flags": [...], "reason": str}
        Never raises for PARSING issues (bad/garbled model output) -
        those fall back to "not flagged", same as before.

        DOES raise ModerationUnavailable if the AI backend itself
        couldn't be reached/errored, so the caller can retry the
        message later instead of treating "couldn't check" the same
        as "checked, and it's clean".
        """
        text = content or ""
        if attachment_names:
            text += "\n[Attachments: " + ", ".join(attachment_names) + "]"
        if not text.strip():
            return dict(DEFAULT_ANALYSIS)

        # Fast path: message is nothing but whitelisted word(s) - skip
        # the AI call entirely (attachments still always get analyzed).
        if not attachment_names and _message_is_fully_whitelisted(content or ""):
            return dict(DEFAULT_ANALYSIS)

        messages = [
            {"role": "system", "content": self._moderation_prompt},
            {"role": "user", "content": text},
        ]

        try:
            raw = await self._call_ollama(config.MODERATION_MODEL, messages)
        except Exception as e:
            # IMPORTANT: this must propagate, not swallow-and-return
            # "not flagged" - otherwise an offline/unreachable Ollama
            # server causes every message to be silently marked as
            # scanned without ever really being checked.
            raise ModerationUnavailable(str(e)) from e

        cleaned = _strip_think(raw)
        match = JSON_OBJECT_RE.search(cleaned)
        if not match:
            return dict(DEFAULT_ANALYSIS)

        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError:
            return dict(DEFAULT_ANALYSIS)

        result = {
            "flagged": bool(result.get("flagged", False)),
            "category": result.get("category"),
            "flags": result.get("flags") or [],
            "reason": result.get("reason") or "",
        }

        return _apply_whitelist(result)

    # ---------------------------------------------------------------
    # Startup diagnostics only - checks whether Ollama is reachable
    # and what models it has, used for the terminal banner.
    # ---------------------------------------------------------------
    async def check_ollama_connection(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{config.OLLAMA_URL}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
            models = sorted(m.get("name", "?") for m in data.get("models", []))
            return True, models
        except Exception as e:
            return False, str(e)
