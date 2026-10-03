"""Картинки, описания и ссылки лейблов для discocs — разовая синхронизация.

    python label_sync.py                     # все лейблы discocs, уже отправленные пропускаются
    python label_sync.py --only "Ninja Tune" # один лейбл (без учёта регистра)
    python label_sync.py --limit 20          # первые N лейблов (по числу релизов)
    python label_sync.py --force             # отправить заново и уже отправленные
    python label_sync.py --dry-run           # ничего не отправлять, только отчёт

Лейбл ищется через релиз, а не по названию (по названию Discogs на «Trip» отдаёт чужой TRIP):
штрихкоды файлов этого лейбла (tools/library-tags/cache/scan.json) → релиз на Beatport / Discogs →
его лейбл. Нет штрихкодов — поиск по названию, но только с подтверждением: у кандидата должен
найтись релиз из библиотеки discocs.

Картинка: Beatport → Discogs (заглушку Beatport не берём). Описание: ru Wikipedia → en Wikipedia
(через Wikidata по id лейбла Discogs) → Discogs profile → Beatport bio. Ссылки — из Discogs.
Доступ к Beatport/Discogs и кэш их ответов — из ../music-fill (config.json там же).
"""
from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "music-fill"))

import beatport  # noqa: E402  (music-fill: вход в Beatport API v4 и кэш ответов)
import discogs  # noqa: E402  (music-fill: ключ Discogs, лимит 60/мин и кэш ответов)

CONFIG = ROOT / "config.json"
SCAN = ROOT.parent / "library-tags" / "cache" / "scan.json"
CACHE = ROOT / "cache"
WEB_CACHE = CACHE / "web.json"
IMAGES = CACHE / "images"
STATE = ROOT / "out" / "state.json"
REPORT = ROOT / "out" / "report.txt"

# Серый квадрат со значком Beatport вместо логотипа — «картинки нет».
BEATPORT_PLACEHOLDER = "cda9862c-cf92-4d13-ac65-7e9277181f51"
# Штрихкод Deezer совпадает с Beatport/Discogs не всегда (у Suara — у одного релиза из ~10: другой
# дистрибьютор), поэтому перебираем до MAX_BARCODES, но останавливаемся, как только лейбл подтверждён.
MAX_BARCODES = 40
CONFIRM_PAGES = 3        # страниц релизов кандидата при подтверждении поиска по названию
WEB_TTL = 30 * 86400

_session = requests.Session()
_session.headers["User-Agent"] = "discocs-label-sync/0.1 (personal library tool)"


# ---------- общее ----------

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def norm(text: str | None) -> str:
    """Как normalize_text в discocs: пробелы схлопнуты, без учёта регистра."""
    return " ".join(str(text or "").split()).casefold()


_web_cache: dict = read_json(WEB_CACHE, {})


def web_json(url: str, **params) -> dict:
    """GET JSON с кэшем на диске (Wikidata, Wikipedia)."""
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    hit = _web_cache.get(key)
    if hit and time.time() - hit["ts"] < WEB_TTL:
        return hit["data"]
    r = _session.get(url, params=params, timeout=30)
    data = r.json() if r.status_code == 200 else {}
    _web_cache[key] = {"ts": time.time(), "data": data}
    write_json(WEB_CACHE, _web_cache)
    time.sleep(0.2)
    return data


def download(url: str) -> bytes | None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    path = IMAGES / (hashlib.sha1(url.encode()).hexdigest() + ".img")
    if path.exists():
        return path.read_bytes()
    r = _session.get(url, timeout=60)
    if r.status_code != 200 or not r.content:
        return None
    path.write_bytes(r.content)
    return r.content


# ---------- discocs ----------

class Discocs:
    def __init__(self, url: str, token: str | None):
        self.url = url.rstrip("/")
        self.headers = {"X-Discocs-Service-Token": token} if token else {}

    def labels(self) -> list[dict]:
        out, offset = [], 0
        while offset is not None:
            r = _session.get(f"{self.url}/api/v1/labels", params={"limit": 100, "offset": offset},
                             headers=self.headers, timeout=60)
            r.raise_for_status()
            page = r.json()
            out += page["items"]
            offset = page["next_offset"]
        return out

    def release_titles(self, label_id: int) -> list[str]:
        r = _session.get(f"{self.url}/api/v1/labels/{label_id}/releases", headers=self.headers, timeout=60)
        r.raise_for_status()
        return [item["title"] for item in r.json()["items"]]

    def put(self, payload: dict) -> None:
        r = _session.put(f"{self.url}/api/v1/labels/metadata", json=payload, headers=self.headers, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"discocs {r.status_code}: {r.text[:300]}")


# ---------- штрихкоды из тегов ----------

def barcodes_by_label() -> dict[str, list[str]]:
    """Лейбл (нормализованный) → штрихкоды его релизов, сначала релизы с меньшим числом файлов.

    Большие сборники (много файлов на штрихкод) чаще выходят через дистрибьютора с другим UPC,
    EP и синглы находятся лучше.
    """
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for tags in read_json(SCAN, {}).values():
        if not isinstance(tags, dict):
            continue
        label, code = norm(tags.get("label")), str(tags.get("barcode") or "").strip()
        if label and code.isdigit():
            counts[label][code] += 1
    return {label: sorted(c, key=lambda code: (c[code], code)) for label, c in counts.items()}


class Votes:
    """Голоса штрихкодов за лейбл; подтверждён — совпало название или два голоса."""

    def __init__(self, name: str):
        self.name = norm(name)
        self.counter: collections.Counter = collections.Counter()
        self.confirmed: int | None = None

    def add(self, label_id: int, label_name: str) -> None:
        self.counter[label_id] += 1
        if norm(discogs.clean_name(label_name)) == self.name or self.counter[label_id] >= 2:
            self.confirmed = label_id

    def best(self) -> int | None:
        if self.confirmed is not None:
            return self.confirmed
        return self.counter.most_common(1)[0][0] if self.counter else None


def upc_variants(code: str) -> list[str]:
    bare = code.lstrip("0")
    return list(dict.fromkeys([code, bare, bare.zfill(12), bare.zfill(13)]))


def titles_overlap(ours: list[str], theirs: list[str]) -> bool:
    keys = set()
    for title in ours:
        keys |= discogs.title_keys(title)
    return any(discogs.title_keys(title) & keys for title in theirs)


# ---------- Beatport ----------

def beatport_by_barcodes(name: str, codes: list[str]) -> int | None:
    votes = Votes(name)
    for code in codes[:MAX_BARCODES]:
        for variant in upc_variants(code):
            results = beatport.get("/catalog/releases/", upc=variant).get("results", [])
            if results:
                for release in results[:1]:
                    label = release.get("label") or {}
                    if label.get("id"):
                        votes.add(int(label["id"]), label.get("name", ""))
                break
        if votes.confirmed is not None:
            break
    return votes.best()


def beatport_by_name(name: str, titles: list[str]) -> int | None:
    for candidate in beatport.search(name, "labels", 10):
        if norm(candidate.get("name")) != norm(name):
            continue
        theirs = []
        for page in range(1, CONFIRM_PAGES + 1):
            data = beatport.get(f"/catalog/labels/{candidate['id']}/releases/", per_page=100, page=page)
            theirs += [r.get("name", "") for r in data.get("results", [])]
            if not data.get("next"):
                break
        if titles_overlap(titles, theirs):
            return int(candidate["id"])
    return None


def beatport_image_url(label: dict) -> str | None:
    image = label.get("image") or {}
    uri = image.get("dynamic_uri") or ""
    url = uri.replace("{w}x{h}", "500x500") if "{w}x{h}" in uri else image.get("uri")
    if not url or BEATPORT_PLACEHOLDER in url:
        return None
    return url


# ---------- Discogs ----------

def discogs_by_barcodes(name: str, codes: list[str]) -> int | None:
    votes = Votes(name)
    for code in codes[:MAX_BARCODES]:
        results = discogs.get("/database/search", barcode=code, type="release", per_page=5).get("results", [])
        if not results:
            continue
        release = discogs.get(f"/releases/{results[0]['id']}")
        labels = [entry for entry in release.get("labels", []) if entry.get("id")]
        # У релиза бывает несколько лейблов (и дистрибьютор) — берём совпавший с тегом, иначе первый.
        same = [entry for entry in labels if norm(discogs.clean_name(entry.get("name"))) == norm(name)]
        for entry in (same or labels)[:1]:
            votes.add(int(entry["id"]), entry.get("name", ""))
        if votes.confirmed is not None:
            break
    return votes.best()


def discogs_by_name(name: str, titles: list[str]) -> int | None:
    results = discogs.get("/database/search", q=name, type="label", per_page=10).get("results", [])
    for candidate in results:
        if norm(discogs.clean_name(candidate.get("title"))) != norm(name):
            continue
        theirs = []
        for page in range(1, CONFIRM_PAGES + 1):
            data = discogs.get(f"/labels/{candidate['id']}/releases", per_page=100, page=page)
            theirs += [r.get("title", "") for r in data.get("releases", [])]
            if page >= (data.get("pagination") or {}).get("pages", 1):
                break
        if titles_overlap(titles, theirs):
            return int(candidate["id"])
    return None


_DISCOGS_REF = re.compile(r"\[(a|l|r|m)=?(\d+)\]", re.I)
_DISCOGS_NAMED = re.compile(r"\[(a|l)=([^\]]+)\]", re.I)
_DISCOGS_URL = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.I | re.S)
_DISCOGS_BARE_URL = re.compile(r"\[url\](.*?)\[/url\]", re.I | re.S)
_DISCOGS_TAGS = re.compile(r"\[/?(?:b|i|u|s)\]|\[g[^\]]*\]", re.I)


def _discogs_ref_name(kind: str, ref_id: str) -> str:
    kind = kind.lower()
    if kind == "a":
        name = discogs.clean_name(discogs.get(f"/artists/{ref_id}").get("name", ""))
        return f"[a={name}]" if name else ""
    if kind == "l":
        return discogs.clean_name(discogs.get(f"/labels/{ref_id}").get("name", ""))
    path = "releases" if kind == "r" else "masters"
    return discogs.get(f"/{path}/{ref_id}").get("title", "")


def discogs_profile_text(profile: str) -> str:
    """Разметка Discogs → текст; упоминания артистов остаются как «[a=Имя]» (discocs делает из них ссылки)."""
    text = _DISCOGS_REF.sub(lambda m: _discogs_ref_name(m.group(1), m.group(2)), profile or "")
    text = _DISCOGS_NAMED.sub(
        lambda m: f"[a={discogs.clean_name(m.group(2))}]" if m.group(1).lower() == "a" else discogs.clean_name(m.group(2)),
        text,
    )
    text = _DISCOGS_URL.sub(lambda m: m.group(2), text)
    text = _DISCOGS_BARE_URL.sub(lambda m: m.group(1), text)
    return _DISCOGS_TAGS.sub("", text).strip()


# ---------- Wikidata / Wikipedia ----------

def wikipedia_intro(discogs_label_id: int) -> tuple[str, str, str] | None:
    """(текст, источник, Q-id) по id лейбла Discogs: русская статья, иначе английская."""
    search = web_json("https://www.wikidata.org/w/api.php", action="query", list="search",
                      srsearch=f"haswbstatement:P1955={discogs_label_id}", format="json")
    hits = [hit["title"] for hit in (search.get("query") or {}).get("search", [])]
    if len(hits) != 1:
        return None
    qid = hits[0]
    entity = web_json("https://www.wikidata.org/w/api.php", action="wbgetentities", ids=qid,
                      props="sitelinks", sitefilter="ruwiki|enwiki", format="json")
    sitelinks = ((entity.get("entities") or {}).get(qid) or {}).get("sitelinks") or {}
    for wiki, lang in (("ruwiki", "ru"), ("enwiki", "en")):
        title = (sitelinks.get(wiki) or {}).get("title")
        if not title:
            continue
        data = web_json(f"https://{lang}.wikipedia.org/w/api.php", action="query", prop="extracts",
                        exintro=1, explaintext=1, redirects=1, titles=title, format="json")
        pages = ((data.get("query") or {}).get("pages") or {}).values()
        extract = next((p.get("extract") for p in pages if p.get("extract")), "")
        if extract.strip():
            return extract.strip(), f"wikipedia_{lang}", qid
    return None


# ---------- один лейбл ----------

def resolve(label: dict, codes: list[str], discocs: Discocs) -> dict:
    name = label["name"]
    titles: list[str] | None = None

    def our_titles() -> list[str]:
        nonlocal titles
        if titles is None:
            titles = discocs.release_titles(label["id"])
        return titles

    bp_id = beatport_by_barcodes(name, codes) if codes else None
    bp_how = "barcode" if bp_id else None
    if bp_id is None:
        bp_id = beatport_by_name(name, our_titles())
        bp_how = "name" if bp_id else None
    dc_id = discogs_by_barcodes(name, codes) if codes else None
    dc_how = "barcode" if dc_id else None
    if dc_id is None:
        dc_id = discogs_by_name(name, our_titles())
        dc_how = "name" if dc_id else None

    bp = beatport.label(bp_id) if bp_id else {}
    dc = discogs.get(f"/labels/{dc_id}") if dc_id else {}

    image_url, image_source = beatport_image_url(bp), "beatport"
    if not image_url:
        primary = sorted(dc.get("images") or [], key=lambda i: i.get("type") != "primary")
        image_url, image_source = (primary[0].get("uri") if primary else None), "discogs"

    description, description_source, qid = None, None, None
    wiki = wikipedia_intro(dc_id) if dc_id else None
    if wiki:
        description, description_source, qid = wiki
    elif dc.get("profile") and discogs_profile_text(dc["profile"]):
        description, description_source = discogs_profile_text(dc["profile"]), "discogs"
    elif (bp.get("bio") or "").strip():
        description, description_source = bp["bio"].strip(), "beatport"

    links = [{"url": url} for url in dc.get("urls") or [] if str(url).startswith(("http://", "https://"))]
    if dc_id:
        links.append({"url": f"https://www.discogs.com/label/{dc_id}", "title": "Discogs"})
    if bp_id:
        links.append({"url": f"https://www.beatport.com/label/{bp.get('slug') or 'x'}/{bp_id}", "title": "Beatport"})
    seen, unique_links = set(), []
    for link in links:
        key = link["url"].rstrip("/").lower().replace("://www.", "://")
        if key not in seen:
            seen.add(key)
            unique_links.append(link)

    external_ids = {k: str(v) for k, v in (("beatport", bp_id), ("discogs", dc_id), ("wikidata", qid)) if v}
    return {
        "name": name,
        "image_url": image_url,
        "image_source": image_source if image_url else None,
        "description": description,
        "description_source": description_source,
        "links": unique_links[:50],
        "external_ids": external_ids,
        "how": {"beatport": bp_how, "discogs": dc_how},
    }


def payload_for(result: dict) -> dict:
    payload = {key: result[key] for key in ("name", "description", "description_source", "links", "external_ids")}
    image = download(result["image_url"]) if result["image_url"] else None
    if image:
        payload["image_base64"] = base64.b64encode(image).decode("ascii")
        payload["image_source"] = result["image_source"]
    return payload


# ---------- запуск ----------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="один лейбл по названию")
    parser.add_argument("--limit", type=int, help="первые N лейблов")
    parser.add_argument("--force", action="store_true", help="отправить и уже отправленные")
    parser.add_argument("--dry-run", action="store_true", help="не отправлять в discocs")
    args = parser.parse_args()

    cfg = read_json(CONFIG, {})
    discocs = Discocs(cfg.get("discocs_url") or "http://192.168.1.41:8711", cfg.get("service_token") or None)
    state = read_json(STATE, {})
    codes_by_label = barcodes_by_label()

    labels = discocs.labels()
    if args.only:
        labels = [label for label in labels if norm(label["name"]) == norm(args.only)]
    if args.limit:
        labels = labels[: args.limit]
    todo = [label for label in labels if args.force or args.dry_run or norm(label["name"]) not in state]
    print(f"лейблов в discocs: {len(labels)}, к обработке: {len(todo)}")

    started = time.time()
    try:
        for index, label in enumerate(todo, 1):
            key = norm(label["name"])
            try:
                result = resolve(label, codes_by_label.get(key, []), discocs)
                if not args.dry_run:
                    discocs.put(payload_for(result))
                    state[key] = {k: v for k, v in result.items() if k != "description"} | {"synced_at": time.time()}
                    write_json(STATE, state)
                print(f"[{index}/{len(todo)}] {label['name']}: картинка={result['image_source'] or '—'} "
                      f"описание={result['description_source'] or '—'} {result['how']}")
            except Exception as exc:  # один упавший лейбл не должен останавливать весь прогон
                print(f"[{index}/{len(todo)}] {label['name']}: ОШИБКА {exc}")
            if index % 20 == 0:
                beatport.flush()
                discogs.flush()
                print(f"  … {index} за {int(time.time() - started)} с")
    finally:
        beatport.flush()
        discogs.flush()
        write_report(state)


def write_report(state: dict) -> None:
    image = collections.Counter(entry.get("image_source") or "нет" for entry in state.values())
    description = collections.Counter(entry.get("description_source") or "нет" for entry in state.values())
    unmatched = sorted(entry["name"] for entry in state.values() if not entry.get("external_ids"))
    lines = [
        f"отправлено лейблов: {len(state)}",
        "картинки: " + ", ".join(f"{k}={v}" for k, v in image.most_common()),
        "описания: " + ", ".join(f"{k}={v}" for k, v in description.most_common()),
        f"не найдены ни на Beatport, ни на Discogs ({len(unmatched)}):",
        *(f"  {name}" for name in unmatched),
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:3]))


if __name__ == "__main__":
    main()
