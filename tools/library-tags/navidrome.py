"""Navidrome (Subsonic API): запустить сканирование и проверить, что он видит записанные теги.

    python navidrome.py scan [--full]     # запустить сканирование и дождаться конца
    python navidrome.py check             # альбомы из out/apply_log.jsonl: тип релиза в Navidrome

Адрес и учётка — из ../music-fill/config.json (navidrome_url, navidrome_user, navidrome_password).
"""
from __future__ import annotations

import collections
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path

import requests

from dry_run import OUT, read_json

CFG = Path(__file__).resolve().parent.parent / "music-fill" / "config.json"


def call(endpoint: str, **params) -> dict:
    cfg = read_json(CFG, {})
    salt = secrets.token_hex(6)
    token = hashlib.md5((cfg["navidrome_password"] + salt).encode()).hexdigest()
    r = requests.get(cfg["navidrome_url"].rstrip("/") + "/rest/" + endpoint, timeout=60, params={
        "u": cfg["navidrome_user"], "t": token, "s": salt, "v": "1.16.1", "c": "library-tags", "f": "json", **params})
    r.raise_for_status()
    body = r.json()["subsonic-response"]
    if body.get("status") != "ok":
        raise RuntimeError(body.get("error"))
    return body


def scan(full: bool) -> None:
    st = call("startScan", fullScan=str(full).lower())["scanStatus"]
    print("сканирование запущено", st)
    while True:
        time.sleep(5)
        st = call("getScanStatus")["scanStatus"]
        if not st.get("scanning"):
            print("готово:", st)
            return
        print("  …", st.get("count"), flush=True)


def check() -> None:
    log = [json.loads(l) for l in (OUT / "apply_log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    want = {}  # каталог -> записанный тип
    for e in log:
        want.setdefault(str(Path(e["path"]).parent), e["after"].get("RELEASETYPE"))
    releases = {d: r for r in read_json(OUT / "dry_run.json", {})["releases"] for d in r["dirs"]}
    res = collections.Counter()
    for d, rtype in want.items():
        r = releases.get(d)
        hits = call("search3", query=r["album_tag"], albumCount=20, songCount=0, artistCount=0)
        albums = [a for a in (hits.get("searchResult3") or {}).get("album", []) if a.get("name") == r["album_tag"]]
        types = [a.get("releaseTypes") for a in albums]
        expect = rtype or (r["releasetype_tag"] or [None])[0]
        ok = any(expect in (t or []) for t in types)
        res["видит" if ok else "не видит"] += 1
        print(f"  {'OK ' if ok else '-- '} {r['album_tag'][:50]:<50} | записано: {rtype or 'не писали'} | Navidrome: {types}")
    print(dict(res))


if __name__ == "__main__":
    if sys.argv[1:2] == ["scan"]:
        scan("--full" in sys.argv)
    else:
        check()
