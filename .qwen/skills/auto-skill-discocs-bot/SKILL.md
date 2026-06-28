---
name: discocs-bot
description: Installation, configuration and usage of the Discocs Telegram bot for Navidrome library access
source: auto-skill
extracted_at: '2026-06-25T18:24:22.225Z'
---

# Discocs Telegram Bot Setup and Configuration

## Installation

1. Navigate to the discocs_bot directory:
   ```
   cd discocs_bot
   ```

2. Copy the example environment file:
   ```
   cp .env.example .env
   ```

3. Install dependencies using one of these methods:
   - Automatic setup with script:
     ```
     run.bat  # On Windows
     # Or: bash run.sh on Unix systems  
     ```
   - Manual setup:
     ```
     python -m venv .venv
     .venv\Scripts\activate  # On Unix: source .venv/bin/activate
     pip install -r requirements.txt
     pip install -e .
     ```

## Configuration Variables

All necessary configurations go in the `.env` file you created from the template:

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Token received from [@BotFather](https://t.me/BotFather) | |
| `ALLOWED_TELEGRAM_USER_IDS` | Comma-separated list of authorized Telegram user IDs | |
| `NAVIDROME_BASE_URL` | Full URL to your Navidrome instance | |
| `NAVIDROME_USERNAME` | Username for Navidrome account | |
| `NAVIDROME_PASSWORD` | Password for Navidrome account | |
| `NAVIDROME_CLIENT_NAME` | Client identifier for Navidrome API requests | discocs-bot |
| `NAVIDROME_API_VERSION` | Subsonic API version | 1.16.1 |
| `DISCOCS_BASE_URL` | Complete URL to discocs server | |
| `DISCOCS_COUNT` | Number of recommendations for radio | 10 |
| `SQLITE_PATH` | Path for bot SQLite database | data/bot.sqlite |
| `TEMP_DIR` | Path for temporary files during transcoding | data/tmp |
| `TRANSCODE_BITRATE` | Audio bitrate for transcoding | 320k |
| `TRANSCODE_WORKERS` | Number of concurrent transcoding workers | 4 |
| `TRANSCODE_FAST` | Enable faster transcoding with lower quality | True |
| `NAVIDROME_STREAM_MAX_BITRATE` | Maximum bitrate for Navidrome stream | 320 |
| `PREP_TIMING_LOG_EVENTS` | Log timing of operations | True |
| `MAX_TELEGRAM_AUDIO_MB` | Maximum audio file size for Telegram in MB | 50 |

Get your Telegram user ID by messaging [@userinfobot](https://t.me/userinfobot).

## Usage

### Bot Commands
- `/start` - Shows introduction and basic info
- `/help` - Shows help information
- `/search <query>` - Search for tracks in Navidrome
- `/random` - Get a random track
- `/menu` - Show navigation menu
- `/settings` - User settings

Under each track displayed in the bot, you'll find these inline buttons:
- Send - Sends MP3 to chat using cached Telegram `file_id` or transcodes fresh
- Radio - Creates Discocs-based similar tracks via the `/navidrome/similar` API call

### Startup
Run either:
1. Using convenience script: `run.bat` (Windows) or `bash run.sh` (Unix)
2. Using CLI: `python -m bot.main` (after activating virtual environment)

### Testing and Validation
Before using the bot fully, validate your setup:
```
cd discocs_bot
python scripts/smoke_test.py
```

This validates that Navidrome ping succeeds, Discocs `/health` returns OK, search works, `/navidrome/similar` functions, and transcoding works correctly.

## Core Services and Architecture

The bot uses several core services:

- `services/navidrome.py` - Implements Navidrome Subsonic API client
- `services/discocs.py` - Interfaces with Discocs API for recommendations
- `services/transcoder.py` - Handles audio transcoding (FLAC/WAV to MP3)
- `services/delivery.py` - Manages delivery of audio files to Telegram clients

The delivery system uses caching for improved performance:
1. Gets track metadata from Navidrome
2. Checks if a telegram `file_id` exists for this track in SQLite storage
3. If exists, sends cached audio directly using Telegram's file_id reference
4. If not, retrieves file from Navidrome, transcodes to MP3 if needed, uploads to Telegram, saves the new `file_id`