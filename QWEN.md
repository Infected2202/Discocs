# Qwen Assistant Manual (discocs Music Recommendation System)

## Project Overview

Discocs is a local music recommendation MVP for a personal audio collection. The system provides music recommendations using Discogs-EffNet embeddings and supports distributed analysis for large libraries. It features a FastAPI/React web interface, a command-line interface, and integration with Navidrome for music streaming.

The project also includes a Telegram bot component that allows users to access music from their Navidrome library directly through Telegram chats, with support for sharing tracks and creating radio stations based on recommendations.

### Architecture and Components

The system follows this processing pipeline:
```
music folder -> scan files -> decode audio -> Discogs-EffNet embedding
-> normalize / aggregate -> save embeddings -> build HNSW cosine index
-> REST API / browser UI similar tracks
```

Main technologies used:
- Python 3.11+
- FastAPI web framework
- SQLite database
- hnswlib for approximate nearest neighbor search
- Essentia TensorFlow library for audio embeddings
- MuQ-MuLan for audio features (optional)

## Building and Running

### Setting up Environment

Create and install the environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,essentia]"
```

For MuQ-MuLan inference on the same worker, install the optional PyTorch/MuQ dependencies too:
```bash
python -m pip install -e ".[dev,essentia,muq]"
```

### Model Files Required

Place the default model file here:
```
models/discogs_multi_embeddings-effnet-bs64-1.pb
```

Download Discogs-EffNet model files from the Essentia model catalog:
```
https://essentia.upf.edu/models/feature-extractors/discogs-effnet/
```

### Running the Application

The easiest way to run the main application is using the provided script:
```bash
./run_app.sh  # On Windows: run this in Git Bash or WSL
```

The application will be accessible at:
```
http://localhost:8711
```

Note: On Windows, you might need to enable execution of shell scripts:
```bash
chmod +x run_app.sh
```

#### Alternative Manual Start
If you prefer to run the server manually:
```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8711
```

## Telegram Bot Component

Discocs includes a Telegram bot component that provides access to your personal Navidrome music library through Telegram. The bot adds functionality that allows users to search for tracks, receive them as audio files, and create "radio stations" based on recommendations from discocs.

### Bot Requirements

- Python 3.11+
- ffmpeg and ffprobe in PATH
- Navidrome Subsonic API
- Discocs API running on `:8711` with completed `navidrome-sync` and built index

### Bot Installation

```bash
cd discocs_bot
cp .env.example .env
# fill .env with required credentials
run.bat
```

Or manually:

```bash
python -m venv .venv
.venv\Scripts\activate  # On Unix use: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### Bot Configuration

Fill `.env` with variables:

| Variable | Description | Default |
|---|---|---|
| `BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) ||
| `ALLOWED_TELEGRAM_USER_IDS` | Comma-separated list of Telegram user IDs ||
| `NAVIDROME_BASE_URL` | Full URL to Navidrome instance ||
| `NAVIDROME_USERNAME` | Username for Navidrome account ||
| `NAVIDROME_PASSWORD` | Password for Navidrome account ||
| `NAVIDROME_CLIENT_NAME` | Client name for Navidrome API requests | discocs-bot |
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

Use a Telegram bot like [@userinfobot](https://t.me/userinfobot) to find your Telegram ID.

### Bot Commands

- `/start`, `/help` — show help
- `/search <query>` — search in Navidrome
- `/random` — random track
- `/menu` — show navigation menu
- `/settings` — user settings

Under each track you'll find inline buttons:
- Send - sends MP3 to chat using cached Telegram `file_id` or by transcoding the original
- Radio - creates Discocs-based similar tracks via the `/navidrome/similar` API call

### Bot Handler Structure

The bot is organized in several components:
- `handlers/start.py` - Handles `/start` and `/help` commands
- `handlers/search.py` - Handles search requests and manages search results pagination
- `handlers/random.py` - Provides random track selection
- `handlers/callbacks.py` - Processes all button callbacks like send, radio, album
- `handlers/menu.py` - Implements menu navigation
- `handlers/settings.py` - Manages user preferences and configurations

### Bot Services

Key services the bot uses:
- `services/navidrome.py` - Implements Navidrome Subsonic API client
- `services/discocs.py` - Interfaces with Discocs API for recommendations
- `services/transcoder.py` - Handles audio transcoding (FLAC/WAV to MP3)
- `services/delivery.py` - Manages delivery of audio files to Telegram clients

### Bot API Integration

The bot uses the same Discocs API contract as the Navidrome plugin:

```http
GET /navidrome/similar?item_id=<navidrome_song_id>&count=10
```

The recommendation model, filters and other parameters come from Discocs settings; the bot simply passes the track ID and requested count.

### Bot File Cache & Delivery

The bot uses the following delivery logic:
1. Gets track metadata from Navidrome
2. Checks if a telegram `file_id` exists for this track in SQLite storage
3. If exists, sends cached audio directly using Telegram's file_id reference
4. If not, retrieves file from Navidrome, transcodes to MP3 if needed, uploads to Telegram, saves the new `file_id`

The caching system avoids repeating transcoding operations and significantly speeds up subsequent accesses.

### Running the Service

```bash
cd discocs_bot
run.bat # On Windows or Git bash
# Or: bash run.sh on Unix systems
```

### Testing and Diagnostic Tools for Bot

The bot includes scripts for testing and verification:
```bash
cd discocs_bot
python scripts/smoke_test.py  # Validates Navidrome ping, Discocs `/health`, search, `/navidrome/similar`, download, transcoding
```

This performs a complete validation of all the integration points.

### Bot Dependencies

The bot has minimal dependencies:
- `python-telegram-bot>=21.0` - Async Telegram Bot API framework
- `httpx>=0.27` - HTTP client for API requests to Navidrome/Discocs
- `pydantic-settings>=2.0` - Settings management with .env file support
- `aiosqlite>=0.20` - Async SQLite bindings for caching

These are found in: `discocs_bot/requirements.txt`

### Error Handling and Logging

The bot implements error handling and logging for:
- Invalid user access (restricts unauthorized users)
- Navidrome connectivity issues
- Discocs recommendation failures
- Audio transcoding problems
- Large file size exceeding Telegram limits
- Telegram API failures due to timeouts/network issues

The script creates `.venv`, installs dependencies on first launch, then starts the bot.

To stop the bot:
- Use `stop.bat` on Windows
- Or `Ctrl+C` to terminate the process

## Development Conventions

### Python Style Guide
- Follow PEP 8 guidelines for Python code.
- Use type hints for all function signatures.
- Use descriptive variable names.

### Testing and Commands
- Run tests with: `python -m pytest` or for the bot: `python -m pytest discocs_bot`
- Check syntax: `python -m compileall app tests` or `python -m compileall discocs_bot`
- Show CLI commands: `recs --help`

### Typical Development Workflow
1. Set the music path in the Web UI or via CLI
2. Scan the music folder: `recs scan /path/to/music`
3. Analyze tracks: `recs analyze --limit 500`
4. Build index: `recs build-index`
5. Test recommendations: `recs similar --track-id 1 --k 30`

## CLI Commands

Common CLI operations:

- `recs scan <music_dir>` - Scan a music folder and upsert track metadata
- `recs analyze --model discogs_multi --limit 500` - Extract embeddings for tracks missing the model
- `recs build-index --model discogs_multi` - Build search index for the model
- `recs similar --track-id 1 --k 30` - Find similar tracks to the given track
- `recs navidrome-sync` - Sync with Navidrome catalog
- `recs analyze-heads --limit 20` - Analyze Discogs-EffNet heads
- `recs analyze-audio-features --limit 20` - Analyze audio features
- `recs normalize-library` - Backfill normalized artist/release tables

## Configuration

Environment variables used:

```text
DISCOCS_DB_PATH=data/app.db          # Path to SQLite database
DISCOCS_DATA_DIR=data                # Directory for data files
DISCOCS_MODEL_DIR=models             # Directory containing model files
DISCOCS_INDEX_DIR=data               # Directory for index files
DISCOCS_DEFAULT_MODEL=discogs_multi  # Default model to use
DISCOCS_AUDIO_LOADER=ffmpeg          # Audio loader (ffmpeg or av)
DISDISCOCS_HOST=0.0.0.0              # Host to bind server to
DISCOCS_PORT=8711                   # Port to serve API
```

For the Telegram bot, see the discocs_bot/.env configuration requirements.

## Advanced Features

### Distributed Analysis
The system supports remote workers to speed up analysis. Configure workers by setting environment variables and running:
- Linux/Mac: `python -c 'from app.cli import cli; cli(["worker"])' ...`  
- Windows: `python -m app.cli worker ...`

### Navidrome Integration
The application can integrate with Navidrome and replace its default Instant Mix recommendations with Discogs-based recommendations.

### Telegram Access
The Discocs Bot provides an additional interface for accessing your music library directly through Telegram channels.

## Deployment Notes

### Runtime Artifacts
Runtime state files (database, models, evaluations) are intentionally ignored by git:
- `data/app.db` (and related WAL files)
- `data/index_*_hnsw.bin`
- `models/*.pb`, `models/*.json`, `models/*.onnx`
- `eval/results/`
- `discocs_bot/data/bot.sqlite`

### Docker Support
The repository includes Dockerfiles for deployment in different environments:
- Main application: `Dockerfile`
- Worker application: `Dockerfile.worker`

To build and run the main container:
```bash
docker build -t discocs .
docker run --rm \
  -p 8711:8711 \
  -v "$PWD/data:/app/data" \
  -v "$PWD/models:/app/models" \
  -v "/path/to/music:/music:ro" \
  discocs
```

## Project Files Layout

- `app/` - Main application code including API endpoints, analysis, storage
- `tests/` - Unit and integration tests for main application
- `docs/` - Documentation on analysis pipeline, operations, and Navidrome integration
- `scripts/` - Utility scripts for downloads and benchmarks
- `models/` - Downloaded model files
- `data/` - Runtime data (ignored by Git)
- `plugins/` - Custom plugins for integrating with other systems (Navidrome plugin)
- `pyproject.toml` - Python project configuration with dependencies
- `discocs_bot/` - Telegram bot source code with all related modules

## Key Modules in the App Package

- `app/main.py` - Main FastAPI app with API routes
- `app/cli.py` - Command-line interface entrypoint
- `app/store.py` - Database operations and models
- `app/recommender.py` - Music recommendation engine
- `app/embedder.py` - Audio embedding extraction
- `app/scanner.py` - Audio file scanning and metadata extraction
- `app/autoplay.py` - Autoplay queue management
- `app/mixes.py` - Generated mix creation and management
- `app/navidrome.py` - Navidrome API client
- `app/head_pack.py` - Additional feature analysis (tags, genres, mood, etc.)

## Key Modules in the Bot Package

- `discocs_bot/bot/main.py` - Main bot entrypoint and initialization
- `discocs_bot/bot/config.py` - Configuration loading from .env
- `discocs_bot/bot/handlers/` - Handlers for different bot commands and callbacks
- `discocs_bot/bot/services/navidrome.py` - Navidrome API client
- `discocs_bot/bot/services/discocs.py` - Discocs recommendations interface
- `discocs_bot/bot/services/transcoder.py` - Audio transcoding utilities
- `discocs_bot/bot/services/delivery.py` - Audio delivery to Chat logic
- `discocs_bot/bot/utils/` - Utility functions for audio, tracks, captions and more
- `discocs_bot/bot/storage/` - SQLite database storage for cached file_ids and user settings
  - `db.py` - Database connection and schema management
  - `models.py` - Data classes for Track, SimilarTrack, and Album
  - `user_prefs.py` - Audio preferences and user settings management

## Running the Bot

You can start the bot in two ways:
1. Using the convenience script: `cd discocs_bot && run.bat` (Windows) or `bash run.sh` (Unix)
2. Using the CLI command: `python -m bot.main` from the discocs_bot directory

The bot must be configured with the appropriate .env settings before starting.

## Bot Database Schema

The Telegram bot uses SQLite for caching and user preferences via the following three tables:

- `telegram_audio_cache` - Stores Telegram file_id references mapped to Navidrome song IDs and audio profiles (bitrates):
  - Includes columns for navidrome_song_id, telegram_file_id, bitrate, file_size, duration, title, artist, album
  - Primary key composed of (navidrome_song_id, bitrate) allowing multiple cached versions of the same song at different qualities

- `users` - Stores user preferences and access control information:
  - Includes telegram_user_id, username, first_name, role, audio_profile, creation and last seen timestamps

- `events` - Records bot usage for analytics and monitoring:
  - Logs user ID, song ID, event type (search, send, radio), context and timestamps

## Testing

Unit tests exist for multiple components:
- Main discocs application: Run with `python -m pytest` from the root
- Bot application: Run with `python -m pytest` from the `discocs_bot/` directory
- Specific bot tests: `discocs_bot/tests/` includes tests for:
  - Audio quality logic: Determines when to transcode vs send original files based on user preferences
  - User preferences: Managing audio profiles and delivery preferences of users

## Best Practices

1. Keep the MVP focused on proven recommendation quality before adding complex features
2. Use SQLite for simplicity but consider limitations for heavy concurrent usage
3. Preserve heavy dependencies as optional using conditional imports
4. When a file changes, invalidate its cached embeddings automatically
5. Maintain resume capability in analysis jobs to avoid unnecessary reprocessing
6. For the Telegram bot, ensure all audio files are properly transcoded to fit Telegram's size and format limits
7. Cache Telegram file_id values to minimize repeated transcoding and uploading
8. Implement rate limiting and access controls for the Telegram bot
9. Regularly clean up temporary audio files and unused cached entries in the database
10. Monitor the size of bot databases as long-term usage can accumulate significant cached data