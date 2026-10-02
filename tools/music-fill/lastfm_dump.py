"""Выгрузка всей истории скробблов и лайков Last.fm в raw/lastfm/.

Нужен config.json рядом со скриптом:
    {"lastfm_user": "...", "lastfm_api_key": "..."}

Дозагрузка: страницы, которые уже лежат на диске, повторно не качаются.
Верхняя граница времени фиксируется при первом запуске (raw/lastfm/meta.json),
чтобы новые скробблы не сдвигали нумерацию страниц между перезапусками.
"""

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
OUT = ROOT / "raw" / "lastfm"
API = "https://ws.audioscrobbler.com/2.0/"
PAGE_SIZE = 200


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def call(params: dict) -> dict:
    for attempt in range(6):
        try:
            r = requests.get(API, params={**params, "format": "json"}, timeout=30)
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            err = str(exc)
        else:
            if "error" not in data:
                return data
            # 8 = временная ошибка бэкенда, 29 = rate limit, 16 = временно недоступно
            if data["error"] not in (8, 16, 29):
                raise RuntimeError(f"Last.fm error {data['error']}: {data.get('message')}")
            err = data.get("message")
        wait = 2 ** attempt
        print(f"  retry in {wait}s: {err}", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError("Last.fm: too many retries")


def dump_paged(method: str, subdir: str, list_key: str, item_key: str, extra: dict) -> None:
    cfg = load_config()
    out = OUT / subdir
    out.mkdir(parents=True, exist_ok=True)
    base = {"method": method, "user": cfg["lastfm_user"], "api_key": cfg["lastfm_api_key"],
            "limit": PAGE_SIZE, **extra}

    first = call({**base, "page": 1})
    attr = first[list_key]["@attr"]
    total_pages, total = int(attr["totalPages"]), int(attr["total"])
    print(f"{method}: {total} items, {total_pages} pages")
    (out / "page_00001.json").write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")

    for page in range(2, total_pages + 1):
        path = out / f"page_{page:05d}.json"
        if path.exists():
            continue
        data = call({**base, "page": page})
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        n = len(data[list_key].get(item_key, []))
        print(f"  page {page}/{total_pages} ({n})")
        time.sleep(0.25)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        meta = {"to": int(time.time())}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    dump_paged("user.getLovedTracks", "loved", "lovedtracks", "track", {})
    dump_paged("user.getRecentTracks", "scrobbles", "recenttracks", "track",
               {"to": meta["to"], "extended": 1})


if __name__ == "__main__":
    main()
