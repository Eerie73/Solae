# Solae (Sol A.I. - Sun AI)

An open-source Discord bot powered by **Ollama**.

Solae can chat with users when mentioned or replied to, and can independently monitor Discord messages for potentially inappropriate content using a local Ollama model.

## Features

- Chat with an AI directly through Discord
- Supports **any Ollama model** that works with the bot
- Local AI processing through Ollama
- Monitors server messages for inappropriate content
- Saves flagged messages as local `.txt` reports
- Reports are organized by user and date
- Optional message actions such as deleting flagged messages
- Configurable channel exclusions
- Configurable moderation categories and keywords
- Chat and moderation AI are completely separated

## Requirements

- Python 3
- A Discord bot
- [Ollama](https://ollama.com/)
- An Ollama-compatible model

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

Install an Ollama model:
Suggested chat model: llama3.2:3b
suggested monitoring model: mistral:7b
```bash
ollama pull <model>
```

For example:

```bash
ollama pull llama3.2:3b
```

```bash
Suggested model for chat & monitor: lama3.2:3b
```

Start Ollama:

```bash
ollama serve
```

By default, Solae connects to:

```text
http://127.0.0.1:11434
```

If Ollama and Solae are running in different environments, configure the Ollama address in `bot/config.py`.

## Discord Bot Setup

1. Create a Discord application and bot from the [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable **Message Content Intent** under the bot's privileged intents.
3. Add your bot token to `bot/config.py`.
4. Invite the bot with the permissions required by your configuration.

The default permissions are:

- View Channels
- Send Messages
- Read Message History

Permission integer:

```text
68608
```

```text
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot&permissions=68608
```

## Run

```bash
python main.py
```

When started, Solae can scan previous messages according to the configured backfill period and then continue monitoring new messages.

## Project Structure

```text
main.py                    Entry point

bot/
  config.py                Main bot configuration
  moderation_rules.py      Moderation categories and rules
  ai.py                    Ollama AI logic
  client.py                Discord events and chat handling
  monitor.py               Message monitoring and moderation
  reports.py               Report file handling
  state.py                 Processing state

json/
  state.json               Runtime state data

reports/
  report-<user_id>-<month>-<day>.txt
```

## Configuration

Most of Solae's behavior can be configured through:

```text
bot/config.py
bot/moderation_rules.py
```

### `bot/config.py`

Controls things such as:

- Ollama URL
- Ollama model
- Discord bot token
- Chat triggers
- DM chat
- Monitoring
- Channel exclusions
- Backfill period
- Moderation actions
- Report settings

### `bot/moderation_rules.py`

Controls what the moderation system is instructed to look for.

Moderation categories can be enabled, disabled, or customized.

## Reports

When a message is flagged, Solae creates a report for that user and day:

```text
reports/report-<user_id>-<month>-<day>.txt
```

Example:

```text
reports/report-82928733729-sept-18.txt
```

If the same user has multiple flagged messages on the same day, new entries are appended to the same file instead of creating multiple files.

Example:

```text
============================================================
Display Name: SomeUser
User ID: 82928733729
Message ID: 1234567890123456789
Message Link: https://discord.com/channels/.../.../...
Message Content: <actual message>
Message Attachments: image.png, document.pdf
Flags: slur, threat
Reason: Contains a targeted threat against another user
Message Sent Time: September 18, 2026, 03:42 PM
Message Edited: False
============================================================
```

## Limitations

- The AI currently receives attachment **file names**, not the actual image or video content.
- Moderation accuracy depends on the Ollama model being used.
- The startup/backfill message scanner is currently not working correctly and may be fixed or removed in a future update.
- Only active threads are scanned when thread monitoring is enabled.
- The bot does not require the Members intent for its current functionality.
- Startup history scanning is now available.
- Use one model for both chat and monitor for consistency.

## Local & Open Source

Solae is designed to run locally using Ollama. AI processing can therefore be handled by the host machine instead of relying on a third-party AI API.

The project is open source, so you can inspect, modify, and self-host the bot yourself.
