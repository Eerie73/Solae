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
        }

        async with aiohttp.ClientSession() as session:
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
        Never raises for parsing issues - falls back to "not flagged".
        """
        text = content or ""
        if attachment_names:
            text += "\n[Attachments: " + ", ".join(attachment_names) + "]"
        if not text.strip():
            return dict(DEFAULT_ANALYSIS)

        messages = [
            {"role": "system", "content": self._moderation_prompt},
            {"role": "user", "content": text},
        ]

        try:
            raw = await self._call_ollama(config.MODERATION_MODEL, messages)
        except Exception as e:
            print(f"[ai] moderation call failed: {e}")
            return dict(DEFAULT_ANALYSIS)

        cleaned = _strip_think(raw)
        match = JSON_OBJECT_RE.search(cleaned)
        if not match:
            return dict(DEFAULT_ANALYSIS)

        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError:
            return dict(DEFAULT_ANALYSIS)

        return {
            "flagged": bool(result.get("flagged", False)),
            "category": result.get("category"),
            "flags": result.get("flags") or [],
            "reason": result.get("reason") or "",
        }
