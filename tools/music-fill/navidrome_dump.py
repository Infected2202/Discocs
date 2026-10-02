"""Выгрузка библиотеки Navidrome (Subsonic API) в raw/navidrome/.

В config.json добавить:
    "navidrome_url": "http://192.168.1.41:4533",
    "navidrome_user": "...", "navidrome_password": "..."
"""

import hashlib
import json
import secrets
from pathlib import Path

import requests

ROOT = Path(__file__).parent
OUT = ROOT / "raw" / "navidrome"
PAGE = 500


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    base = cfg["navidrome_url"].rstrip("/") + "/rest/"

    def call(endpoint: str, **params) -> dict:
        salt = secrets.token_hex(8)
        token = hashlib.md5((cfg["navidrome_password"] + salt).encode()).hexdigest()
        r = requests.get(base + endpoint, timeout=60, params={
            "u": cfg["navidrome_user"], "t": token, "s": salt,
            "v": "1.16.1", "c": "music-fill", "f": "json", **params})
        r.raise_for_status()
        resp = r.json()["subsonic-response"]
        if resp["status"] != "ok":
            raise RuntimeError(resp.get("error"))
        return resp

    OUT.mkdir(parents=True, exist_ok=True)

    albums = []
    while True:
        page = call("getAlbumList2", type="alphabeticalByName", size=PAGE, offset=len(albums))
        chunk = page["albumList2"].get("album", [])
        albums += chunk
        if len(chunk) < PAGE:
            break
    (OUT / "albums.json").write_text(json.dumps(albums, ensure_ascii=False), encoding="utf-8")
    print("albums:", len(albums))

    # search3 с пустым запросом в Navidrome отдаёт всю библиотеку постранично
    songs = []
    while True:
        page = call("search3", query="", songCount=PAGE, songOffset=len(songs),
                    albumCount=0, artistCount=0)
        chunk = page["searchResult3"].get("song", [])
        songs += chunk
        if len(chunk) < PAGE:
            break
    (OUT / "songs.json").write_text(json.dumps(songs, ensure_ascii=False), encoding="utf-8")
    print("songs:", len(songs))

    artists = [a for idx in call("getArtists")["artists"].get("index", []) for a in idx["artist"]]
    (OUT / "artists.json").write_text(json.dumps(artists, ensure_ascii=False), encoding="utf-8")
    print("artists:", len(artists))


if __name__ == "__main__":
    main()
