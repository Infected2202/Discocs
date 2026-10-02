"""Треки плейлистов из Takeout (raw/takeout/playlists) через ytmusicapi → raw/ytm/playlists/<id>.json.

В Takeout у плейлиста только video ID; get_playlist отдаёт по ним артиста/альбом/тип видео.
Видео, которых нет в ответе (удалены/недоступны), дозапрашиваются по одному через get_song.
Уже скачанные плейлисты повторно не качаются.
"""

import csv
import json
import sys
from pathlib import Path

from ytmusicapi import YTMusic

ROOT = Path(__file__).parent
TAKEOUT = ROOT / "raw" / "takeout" / "playlists"
OUT = ROOT / "raw" / "ytm" / "playlists"


def song_to_track(video_id: str, song: dict) -> dict:
    d = song.get("videoDetails", {})
    return {"videoId": video_id, "title": d.get("title"), "artists": [{"name": d.get("author")}],
            "album": None, "videoType": d.get("musicVideoType"), "_source": "get_song",
            "_playable": song.get("playabilityStatus", {}).get("status")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    yt = YTMusic(str(ROOT / "browser.json"))
    with open(TAKEOUT / "playlists.csv", encoding="utf-8-sig") as f:
        playlists = list(csv.DictReader(f))

    for pl in playlists:
        pid, title = pl["Playlist ID"], pl["Playlist Title (Original)"]
        path = OUT / f"{pid}.json"
        if path.exists():
            continue
        videos_csv = TAKEOUT / f"{title}-videos.csv"
        if not videos_csv.exists():
            print(f"{title}: нет файла {videos_csv.name}", file=sys.stderr)
            continue
        with open(videos_csv, encoding="utf-8-sig") as f:
            takeout_ids = [r["Video ID"].strip() for r in csv.DictReader(f) if r.get("Video ID")]

        try:
            tracks = yt.get_playlist(pid, limit=None).get("tracks", [])
        except Exception as exc:
            print(f"{title}: get_playlist failed {exc!r}", file=sys.stderr)
            tracks = []
        by_id = {t.get("videoId"): t for t in tracks if t.get("videoId")}

        missing = [v for v in takeout_ids if v not in by_id]
        for vid in missing:
            try:
                by_id[vid] = song_to_track(vid, yt.get_song(vid))
            except Exception as exc:
                by_id[vid] = {"videoId": vid, "_error": repr(exc)}

        result = {"id": pid, "title": title, "meta": pl,
                  "tracks": [by_id[v] for v in takeout_ids]}
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        print(f"{title}: {len(takeout_ids)} видео, {len(missing)} дозапрошено по одному")


if __name__ == "__main__":
    main()
