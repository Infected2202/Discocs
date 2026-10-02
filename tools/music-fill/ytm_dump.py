"""Выгрузка лайков и библиотеки YouTube Music в raw/ytm/.

Авторизация: browser.json рядом со скриптом, создаётся командой
    ytmusicapi browser
(вставить заголовки POST-запроса /browse с music.youtube.com из DevTools).
Файл содержит куки сессии — не коммитить и никуда не передавать.
"""

import json
import sys
from pathlib import Path

from ytmusicapi import YTMusic

ROOT = Path(__file__).parent
OUT = ROOT / "raw" / "ytm"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    yt = YTMusic(str(ROOT / "browser.json"))

    sections = {
        "liked_songs": lambda: yt.get_liked_songs(limit=None),
        "library_artists": lambda: yt.get_library_artists(limit=None),
        "library_subscriptions": lambda: yt.get_library_subscriptions(limit=None),
        "library_albums": lambda: yt.get_library_albums(limit=None),
        "library_songs": lambda: yt.get_library_songs(limit=None),
        "library_playlists": lambda: yt.get_library_playlists(limit=None),
        "history": lambda: yt.get_history(),
    }
    for name, fetch in sections.items():
        try:
            data = fetch()
        except Exception as exc:  # одна упавшая секция не должна ронять остальные
            print(f"{name}: FAILED {exc!r}", file=sys.stderr)
            continue
        (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        n = len(data["tracks"]) if isinstance(data, dict) and "tracks" in data else len(data)
        print(f"{name}: {n}")


if __name__ == "__main__":
    main()
