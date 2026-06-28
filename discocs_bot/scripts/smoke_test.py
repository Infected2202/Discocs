"""Integration smoke test against live Navidrome and Discocs."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from bot.config import get_settings
from bot.services.discocs import DiscocsClient
from bot.services.navidrome import NavidromeClient
from bot.services.transcoder import Transcoder
from bot.storage.db import Database


async def main() -> int:
    settings = get_settings()
    db = Database(settings)
    navidrome = NavidromeClient(settings)
    discocs = DiscocsClient(settings, navidrome)
    transcoder = Transcoder(settings)

    print("1. Navidrome ping...")
    await navidrome.ping()

    print("2. Discocs health...")
    await discocs.ping()

    print("3. Search tracks...")
    tracks, _ = await navidrome.search_tracks("burial", limit=3)
    if not tracks:
        print("FAIL: no search results")
        return 1
    seed = tracks[0]
    print(f"   seed: {seed.display_line} [{seed.id}] suffix={seed.suffix}")

    print("4. Similar tracks...")
    similar, _ = await discocs.get_similar_tracks(seed.id, limit=3)
    if not similar:
        print("WARN: no similar tracks (Discocs index may miss this seed)")
    else:
        print(f"   got {len(similar)}: {similar[0].display_line}")

    print("5. Download + transcode...")
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    source = settings.temp_dir / f"smoke.{seed.suffix or 'audio'}"
    mp3 = settings.temp_dir / "smoke.mp3"
    await navidrome.download_stream(seed.id, source)
    print(f"   downloaded {source.stat().st_size} bytes")
    if (seed.suffix or "") != "mp3":
        await transcoder.transcode_to_mp3(source, mp3, track=seed)
        print(f"   transcoded to {mp3.stat().st_size} bytes")
    else:
        print("   already mp3, skip transcode")

    print("6. SQLite...")
    await db.connect()
    await db.log_event(
        user_id=None,
        song_id=seed.id,
        event_type="smoke_test",
        context="ok",
        created_at="smoke",
    )
    await db.close()

    await navidrome.close()
    await discocs.close()
    source.unlink(missing_ok=True)
    mp3.unlink(missing_ok=True)
    print("OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
