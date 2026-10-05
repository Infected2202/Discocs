"""Поиск одного лейбла на Beatport и Discogs и сбор его картинки, описания и ссылок.

Лейбл ищется через релизы, а не по названию (по названию Discogs на «Trip» отдаёт чужой TRIP):
штрихкод → релиз → его лейбл, ISRC → трек → релиз → лейбл (только Beatport). Лейбл релиза
принимается, только если его название совпадает с нашим (``label_key``): у релиза бывает
дистрибьютор или цифровой саб-лейбл, а ISRC трека встречается и на чужих сборниках. Нет ни
штрихкодов, ни ISRC — поиск по названию, но кандидат принимается, только если у него нашёлся
релиз из библиотеки: в первых страницах его каталога или поиском релиза по названию внутри
лейбла (у мейджоров тысячи релизов). Служебные записи Discogs (бутлеги, контрафакт, копирайтные
строки) не принимаются никогда.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.services.label_sync.clients import BeatportClient, DiscogsClient, WebClient

# Серый квадрат со значком Beatport вместо логотипа — «картинки нет».
BEATPORT_PLACEHOLDER = "cda9862c-cf92-4d13-ac65-7e9277181f51"
# Штрихкод из тегов совпадает с Beatport/Discogs не всегда (другой дистрибьютор), поэтому
# перебираем до MAX_CODES, но останавливаемся, как только лейбл подтверждён.
MAX_CODES = 40
CONFIRM_PAGES = 3
# Не нашёлся в первых страницах — ищем внутри лейбла по названиям стольких наших релизов.
CONFIRM_TITLES = 5
# Хвосты, которыми одно и то же название различается в тегах и в каталогах.
_LABEL_SUFFIXES = frozenset({
    "records", "recordings", "recording", "music", "discs", "ltd", "limited", "inc", "llc", "label",
})
# Юридическая форма перед названием: Discogs пишет «ООО "Союз Мьюзик"», теги — «Союз Мьюзик».
_LABEL_PREFIXES = frozenset({"ооо", "оао", "зао", "пао", "llc"})
# Служебные записи Discogs с названием настоящего лейбла (часто с «(2)»): для бутлегов
# («This is [b]NOT[/b] a real label»), для контрафакта («This page catalogues counterfeit
# editions…») и для копирайтных строк («For the copyright or licensing entries of label…»).
_PLACEHOLDER_PROFILE = re.compile(
    r"\bnot\s+a\s+real\s+label\b|\bcatalogues\s+counterfeit\b|\bfor\s+the\s+copyright\s+or\s+licensing\s+entries\b",
    re.I,
)


def norm(text: object) -> str:
    """Как normalize_text в discocs: пробелы схлопнуты, без учёта регистра."""
    return " ".join(str(text or "").split()).casefold()


def clean_name(name: object) -> str:
    """Discogs: 'Hermeth (2)' → 'Hermeth', 'Nina Kraviz*' → 'Nina Kraviz'."""
    return re.sub(r"\s*\(\d+\)$", "", str(name or "").strip()).rstrip("*").strip()


def label_key(name: object) -> str:
    """Ключ сравнения названий лейблов: без «(2)», пунктуации, «ООО» и хвоста Records/Discs/Ltd/LLC."""
    words = re.sub(r"[^\w]+", " ", norm(clean_name(name))).split()
    while len(words) > 1 and words[0] in _LABEL_PREFIXES:
        words.pop(0)
    while len(words) > 1 and words[-1] in _LABEL_SUFFIXES:
        words.pop()
    return " ".join(words)


def is_placeholder_label(label: dict) -> bool:
    """Служебная запись Discogs для неофициальных релизов, а не лейбл."""
    profile = re.sub(r"\[/?[a-z]+\]", "", str(label.get("profile") or ""), flags=re.I)
    return bool(_PLACEHOLDER_PROFILE.search(profile))


def title_key(title: object) -> str:
    """Ключ сравнения названий релизов: без скобок, пунктуации и хвоста EP/LP/Single."""
    text = re.sub(r"[\(\[].*?[\)\]]", " ", str(title or "").casefold())
    text = " ".join(re.sub(r"[^\w]+", " ", text).split())
    return re.sub(r"\s+(?:ep|lp|single|album|mini album)$", "", text)


def titles_overlap(ours: Sequence[str], theirs: Sequence[str]) -> bool:
    keys = {title_key(t) for t in ours} - {""}
    return any(title_key(t) in keys for t in theirs)


def search_titles(titles: Sequence[str]) -> list[str]:
    """Названия для поиска релиза внутри лейбла: без скобок, без повторов, не больше CONFIRM_TITLES."""
    out: dict[str, str] = {}
    for title in titles:
        query = " ".join(re.sub(r"[\(\[].*?[\)\]]", " ", str(title or "")).split())
        key = title_key(query)
        if key and key not in out:
            out[key] = query
    return list(out.values())[:CONFIRM_TITLES]


def upc_variants(code: str) -> list[str]:
    bare = code.lstrip("0")
    return list(dict.fromkeys([code, bare, bare.zfill(12), bare.zfill(13)]))


@dataclass
class LabelResult:
    image_url: str | None = None
    image_source: str | None = None
    description: str | None = None
    description_source: str | None = None
    links: list[dict[str, str]] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)
    how: dict[str, str | None] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return bool(self.external_ids.get("beatport") or self.external_ids.get("discogs"))


class LabelResolver:
    def __init__(self, beatport: BeatportClient, discogs: DiscogsClient, web: WebClient):
        self.beatport = beatport
        self.discogs = discogs
        self.web = web

    # ---------- Beatport ----------

    def beatport_by_barcodes(self, name: str, codes: Sequence[str]) -> int | None:
        key = label_key(name)
        for code in codes[:MAX_CODES]:
            for variant in upc_variants(code):
                results = self.beatport.get("/catalog/releases/", upc=variant).get("results", [])
                if results:
                    found = _matching_label(key, (release.get("label") or {} for release in results))
                    if found is not None:
                        return found
                    break
        return None

    def beatport_by_isrcs(self, name: str, isrcs: Sequence[str]) -> int | None:
        key = label_key(name)
        for isrc in isrcs[:MAX_CODES]:
            results = self.beatport.get("/catalog/tracks/", isrc=isrc).get("results", [])
            # Один ISRC — на оригинальном релизе и на сборниках других лейблов: ищем свой.
            found = _matching_label(key, ((track.get("release") or {}).get("label") or {} for track in results))
            if found is not None:
                return found
        return None

    def beatport_by_name(self, name: str, titles: Sequence[str]) -> int | None:
        for candidate in self.beatport.search(name, "labels", 10):
            if label_key(candidate.get("name")) != label_key(name):
                continue
            theirs: list[str] = []
            for page in range(1, CONFIRM_PAGES + 1):
                data = self.beatport.get(f"/catalog/labels/{candidate['id']}/releases/", per_page=100, page=page)
                theirs += [r.get("name", "") for r in data.get("results", [])]
                if not data.get("next"):
                    break
            if titles_overlap(titles, theirs) or self._beatport_has_release(candidate["id"], titles):
                return int(candidate["id"])
        return None

    def _beatport_has_release(self, label_id: object, titles: Sequence[str]) -> bool:
        """Наш релиз в каталоге лейбла Beatport — поиском по названию, а не листанием страниц."""
        for query in search_titles(titles):
            found = self.beatport.get("/catalog/releases/", label_id=label_id, name=query, per_page=5)
            if titles_overlap(titles, [r.get("name", "") for r in found.get("results", [])]):
                return True
        return False

    # ---------- Discogs ----------

    def discogs_by_barcodes(self, name: str, codes: Sequence[str]) -> int | None:
        key = label_key(name)
        for code in codes[:MAX_CODES]:
            results = self.discogs.get("/database/search", barcode=code, type="release", per_page=5).get("results", [])
            if not results:
                continue
            release = self.discogs.get(f"/releases/{results[0]['id']}")
            # У релиза бывает несколько лейблов (и дистрибьютор) — берём только совпавший по названию.
            for entry in release.get("labels", []):
                if entry.get("id") and label_key(entry.get("name")) == key and self._discogs_real(entry["id"]):
                    return int(entry["id"])
        return None

    def discogs_by_name(self, name: str, titles: Sequence[str]) -> int | None:
        results = self.discogs.get("/database/search", q=name, type="label", per_page=10).get("results", [])
        for candidate in results:
            if label_key(candidate.get("title")) != label_key(name) or not self._discogs_real(candidate["id"]):
                continue
            theirs: list[str] = []
            for page in range(1, CONFIRM_PAGES + 1):
                data = self.discogs.get(f"/labels/{candidate['id']}/releases", per_page=100, page=page)
                theirs += [r.get("title", "") for r in data.get("releases", [])]
                if page >= (data.get("pagination") or {}).get("pages", 1):
                    break
            if titles_overlap(titles, theirs) or self._discogs_has_release(candidate, titles):
                return int(candidate["id"])
        return None

    def _discogs_has_release(self, candidate: dict, titles: Sequence[str]) -> bool:
        """Наш релиз у лейбла Discogs: поиск по названиям лейбла и релиза, затем лейбл релиза по id.

        Поиск по названию лейбла отдаёт и релизы однофамильцев («Crammed Discs (2)»), поэтому
        найденный релиз засчитывается, только если среди его лейблов есть именно этот.
        """
        for query in search_titles(titles):
            results = self.discogs.get("/database/search", type="release", label=clean_name(candidate.get("title")),
                                       release_title=query, per_page=5).get("results", [])
            for found in results[:3]:
                # В поиске Discogs заголовок релиза — «Артист - Название».
                if not titles_overlap(titles, [str(found.get("title") or "").split(" - ", 1)[-1]]):
                    continue
                release = self.discogs.get(f"/releases/{found['id']}")
                if any(str(entry.get("id")) == str(candidate["id"]) for entry in release.get("labels", [])):
                    return True
        return False

    def _discogs_real(self, label_id: object) -> bool:
        return not is_placeholder_label(self.discogs.get(f"/labels/{label_id}"))

    def mismatch(self, name: str, beatport_id: object, discogs_id: object) -> str | None:
        """Какая из уже сохранённых привязок неверна: служебная запись Discogs или чужое название."""
        key = label_key(name)
        if discogs_id:
            dc = self.discogs.get(f"/labels/{discogs_id}")
            if dc and (is_placeholder_label(dc) or label_key(dc.get("name")) != key):
                return "discogs"
        if beatport_id:
            bp = self.beatport.label(int(str(beatport_id)))
            if bp and label_key(bp.get("name")) != key:
                return "beatport"
        return None

    # ---------- описание ----------

    def wikipedia_intro(self, discogs_label_id: int) -> tuple[str, str, str] | None:
        """(текст, источник, Q-id) по id лейбла Discogs: русская статья, иначе английская."""
        search = self.web.get_json("https://www.wikidata.org/w/api.php", action="query", list="search",
                                   srsearch=f"haswbstatement:P1955={discogs_label_id}", format="json")
        hits = [hit["title"] for hit in (search.get("query") or {}).get("search", [])]
        if len(hits) != 1:
            return None
        qid = hits[0]
        entity = self.web.get_json("https://www.wikidata.org/w/api.php", action="wbgetentities", ids=qid,
                                   props="sitelinks", sitefilter="ruwiki|enwiki", format="json")
        sitelinks = ((entity.get("entities") or {}).get(qid) or {}).get("sitelinks") or {}
        for wiki, lang in (("ruwiki", "ru"), ("enwiki", "en")):
            title = (sitelinks.get(wiki) or {}).get("title")
            if not title:
                continue
            data = self.web.get_json(f"https://{lang}.wikipedia.org/w/api.php", action="query", prop="extracts",
                                     exintro=1, explaintext=1, redirects=1, titles=title, format="json")
            pages = ((data.get("query") or {}).get("pages") or {}).values()
            extract = next((p.get("extract") for p in pages if p.get("extract")), "")
            if extract.strip():
                return extract.strip(), f"wikipedia_{lang}", qid
        return None

    def discogs_profile_text(self, profile: str) -> str:
        """Разметка Discogs → текст; артисты остаются «[a=Имя]» (discocs делает из них ссылки)."""
        text = _DISCOGS_REF.sub(lambda m: self._ref_name(m.group(1), m.group(2)), profile or "")
        text = _DISCOGS_NAMED.sub(
            lambda m: f"[a={clean_name(m.group(2))}]" if m.group(1).lower() == "a" else clean_name(m.group(2)),
            text,
        )
        text = _DISCOGS_URL.sub(lambda m: m.group(2), text)
        text = _DISCOGS_BARE_URL.sub(lambda m: m.group(1), text)
        return _DISCOGS_TAGS.sub("", text).strip()

    def _ref_name(self, kind: str, ref_id: str) -> str:
        kind = kind.lower()
        if kind == "a":
            name = clean_name(self.discogs.get(f"/artists/{ref_id}").get("name", ""))
            return f"[a={name}]" if name else ""
        if kind == "l":
            return clean_name(self.discogs.get(f"/labels/{ref_id}").get("name", ""))
        path = "releases" if kind == "r" else "masters"
        return str(self.discogs.get(f"/{path}/{ref_id}").get("title", ""))

    # ---------- один лейбл ----------

    def resolve(
        self,
        name: str,
        barcodes: Sequence[str],
        isrcs: Sequence[str],
        release_titles: Callable[[], list[str]],
    ) -> LabelResult:
        bp_id, bp_how = self._first((
            ("barcode", lambda: self.beatport_by_barcodes(name, barcodes) if barcodes else None),
            ("isrc", lambda: self.beatport_by_isrcs(name, isrcs) if isrcs else None),
            ("name", lambda: self.beatport_by_name(name, release_titles())),
        ))
        dc_id, dc_how = self._first((
            ("barcode", lambda: self.discogs_by_barcodes(name, barcodes) if barcodes else None),
            ("name", lambda: self.discogs_by_name(name, release_titles())),
        ))
        bp = self.beatport.label(bp_id) if bp_id else {}
        dc = self.discogs.get(f"/labels/{dc_id}") if dc_id else {}

        result = LabelResult(how={"beatport": bp_how, "discogs": dc_how})
        result.image_url, result.image_source = _beatport_image_url(bp), "beatport"
        if not result.image_url:
            images = sorted(dc.get("images") or [], key=lambda i: i.get("type") != "primary")
            result.image_url, result.image_source = (images[0].get("uri") if images else None), "discogs"
        if not result.image_url:
            result.image_source = None

        wiki = self.wikipedia_intro(dc_id) if dc_id else None
        profile = self.discogs_profile_text(dc["profile"]) if dc.get("profile") else ""
        qid = None
        if wiki:
            result.description, result.description_source, qid = wiki
        elif profile:
            result.description, result.description_source = profile, "discogs"
        elif str(bp.get("bio") or "").strip():
            result.description, result.description_source = str(bp["bio"]).strip(), "beatport"

        result.links = _links(dc, dc_id, bp, bp_id)
        result.external_ids = {k: str(v) for k, v in (("beatport", bp_id), ("discogs", dc_id), ("wikidata", qid)) if v}
        return result

    @staticmethod
    def _first(steps) -> tuple[int | None, str | None]:
        for how, step in steps:
            found = step()
            if found:
                return found, how
        return None, None


_DISCOGS_REF = re.compile(r"\[(a|l|r|m)=?(\d+)\]", re.I)
_DISCOGS_NAMED = re.compile(r"\[(a|l)=([^\]]+)\]", re.I)
_DISCOGS_URL = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.I | re.S)
_DISCOGS_BARE_URL = re.compile(r"\[url\](.*?)\[/url\]", re.I | re.S)
_DISCOGS_TAGS = re.compile(r"\[/?(?:b|i|u|s)\]|\[g[^\]]*\]", re.I)


def _matching_label(key: str, labels) -> int | None:
    for label in labels:
        if label.get("id") and label_key(label.get("name")) == key:
            return int(label["id"])
    return None


def _beatport_image_url(label: dict) -> str | None:
    image = label.get("image") or {}
    uri = image.get("dynamic_uri") or ""
    url = uri.replace("{w}x{h}", "500x500") if "{w}x{h}" in uri else image.get("uri")
    if not url or BEATPORT_PLACEHOLDER in url:
        return None
    return str(url)


def _links(dc: dict, dc_id: int | None, bp: dict, bp_id: int | None) -> list[dict[str, str]]:
    links = [{"url": str(url)} for url in dc.get("urls") or [] if str(url).startswith(("http://", "https://"))]
    if dc_id:
        links.append({"url": f"https://www.discogs.com/label/{dc_id}", "title": "Discogs"})
    if bp_id:
        links.append({"url": f"https://www.beatport.com/label/{bp.get('slug') or 'x'}/{bp_id}", "title": "Beatport"})
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for link in links:
        key = link["url"].rstrip("/").lower().replace("://www.", "://")
        if key not in seen:
            seen.add(key)
            unique.append(link)
    return unique[:50]
