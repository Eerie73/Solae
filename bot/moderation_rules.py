# bot/moderation_rules.py
#
# Second config file: defines WHAT the AI should treat as
# "inappropriate" while monitoring. Edit this freely - the bot
# rebuilds its moderation instructions from this file on every start.
# Nothing here is ever shown to the chat persona.

CATEGORIES = {
    "harassment_or_bullying": {
        "enabled": True,
        "description": "Insults, threats, targeted harassment, bullying, doxxing attempts.",
    },
    "hate_speech": {
        "enabled": True,
        "description": "Slurs, hate speech or discrimination based on race, religion, gender, "
                        "sexuality, nationality, disability, etc.",
    },
    "nsfw_sexual_content": {
        "enabled": True,
        "description": "Sexual content, explicit content, or sexual solicitation.",
    },
    "violence_or_gore": {
        "enabled": True,
        "description": "Graphic violence, gore, or glorifying/threatening violence.",
    },
    "self_harm": {
        "enabled": True,
        "description": "Content promoting, encouraging, or glorifying self-harm or suicide.",
    },
    "spam_or_scam": {
        "enabled": True,
        "description": "Spam, scam links, phishing, fake giveaways, crypto scams, malware links.",
    },
    "illegal_activity": {
        "enabled": True,
        "description": "Selling drugs/weapons, sharing pirated content, or other illegal activity.",
    },
}

# Optional fast keyword pre-filter. If a message contains one of these
# (case-insensitive substring match), it is ALWAYS sent to the AI for
# a full check even if MIN_MESSAGE_LENGTH_TO_ANALYZE would otherwise
# skip it. This does NOT auto-flag anything by itself - the AI still
# makes the final flagged/not-flagged decision.
ALWAYS_CHECK_KEYWORDS = [
    # "kys", "nsfw", "discord.gg/",
]

# The instruction sent to the moderation model. {categories} is filled
# in automatically from CATEGORIES above. Keep the JSON-only
# instruction intact or report parsing will break.
MODERATION_SYSTEM_PROMPT_TEMPLATE = """You are a silent content-moderation classifier for a Discord server.

You do not talk to anyone and you are not having a conversation. You are
given exactly one message and must decide if it violates the server's
rules below, or any of the general categories listed after them.
{server_rules}
General categories to also check for:
{categories}

Respond with ONLY a single JSON object and nothing else - no markdown,
no explanation, no <think> tags, no extra text before or after it.
Use exactly this format:

{{"flagged": true or false, "category": "rule name above or null", "flags": ["short trigger words/phrases"], "reason": "one short sentence"}}

If the message does not violate anything, respond with:
{{"flagged": false, "category": null, "flags": [], "reason": ""}}
"""
