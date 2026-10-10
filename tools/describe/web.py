"""Поиск (SearXNG) и чтение страниц.

Адреса для чтения приходят только из выдачи поиска и известных источников — модель выбирает
из них по номеру, своих адресов не пишет. Чтение не ходит в локальную сеть: страница с
инъекцией не заставит открыть Jenkins или админку discocs.
"""
from __future__ import annotations

import codecs
import ipaddress
import socket
import threading
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
import trafilatura

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_BYTES = 3_000_000
MAX_TEXT = 14_000
TIMEOUT = 15

# Здесь фактов о лейбле нет или страница не читается без браузера.
SKIP_DOMAINS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com", "threads.net", "vk.com",
    "youtube.com", "youtu.be", "spotify.com", "music.apple.com", "deezer.com", "music.yandex",
    "tidal.com", "shazam.com", "soundcloud.com", "linkedin.com", "pinterest.", "reddit.com",
    "rutube.ru", "vimeo.com", "twitch.tv",
    # Каталоги, магазины и агрегаторы: таблицы без текста, факты из них — строки «X выпустил Y».
    "linktr.ee", "songstats.com", "1001tracklists.com", "junodownload.com", "deejay.de", "traxsource.com",
    "musicbrainz.org", "chosic.com", "last.fm", "allmusic.com/album",
)
# Страницы треков, релизов и артистов на Beatport — сетки релизов; страница самого лейбла читается по ID.
SKIP_PATHS = ("beatport.com/artist/", "beatport.com/release/", "beatport.com/track/", "beatport.com/label/")


@dataclass(frozen=True)
class Hit:
    url: str
    title: str
    snippet: str

    @property
    def domain(self) -> str:
        return urlparse(self.url).hostname or ""


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    # Весь видимый текст, со списками и сетками релизов, — для проверки «тот ли это лейбл».
    full: str = ""


class Web:
    def __init__(self, searxng_url: str, engines: str = "", flaresolverr_url: str = "") -> None:
        self.searxng_url = searxng_url.rstrip("/")
        self.engines = engines
        # Запасной путь для страниц за проверкой «включите JavaScript» (Bandcamp Daily и т.п.).
        self.flaresolverr_url = flaresolverr_url.rstrip("/")
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT

    def search(self, query: str, limit: int = 10) -> list[Hit]:
        response = self.http.get(
            f"{self.searxng_url}/search", params={"q": query, "format": "json", **({"engines": self.engines} if self.engines else {})},
            timeout=30,
        )
        response.raise_for_status()
        hits = []
        for item in response.json().get("results", []):
            url = str(item.get("url") or "")
            if not url.startswith(("http://", "https://")) or _skipped(url):
                continue
            hits.append(Hit(url, str(item.get("title") or ""), str(item.get("content") or "")))
            if len(hits) >= limit:
                break
        return hits

    def read(self, url: str) -> Page | None:
        """Текст страницы без меню и рекламы; None — не открылась, не HTML или пустая.

        Через FlareSolverr — только если сайт закрылся защитой от ботов: ответил 403/429/503 или прислал
        страницу-проверку («Just a moment», «Enable JavaScript»). На 404, обрыв, PDF или короткую страницу
        браузер не поднимается: каждый вызов — это Chrome на сервере, и за ночной прогон такие «запасные»
        вызовы (5.7 тыс.) съели память homelab вместе с соседями.
        """
        try:
            html, final_url = self._get(url)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            return self._via_flaresolverr(url) if status in _BOT_WALL_STATUSES else None
        except (requests.RequestException, ValueError):
            return None
        page = _page(html, final_url)
        if page is None and _bot_wall(html):
            return self._via_flaresolverr(url)
        return page

    def _via_flaresolverr(self, url: str) -> Page | None:
        """Один запрос к FlareSolverr за раз на весь процесс (лейблы идут параллельно, а Chrome на сервере —
        дорогой); сайт, где FlareSolverr не помог, больше через него не открывается."""
        domain = urlparse(url).hostname or ""
        path = urlparse(url).path.casefold()
        if not self.flaresolverr_url or domain in _FLARESOLVERR_FAILED or path.endswith(_NOT_PAGES):
            return None
        with _FLARESOLVERR_LOCK:
            try:
                html, final_url = self._get_via_flaresolverr(url)
            except (requests.RequestException, ValueError, KeyError):
                html, final_url = "", url
        # Сайт плохой для FlareSolverr, только если тот упал или сам упёрся в проверку; пустая или
        # несуществующая статья — не повод больше не ходить на сайт.
        if not html or _bot_wall(html):
            _FLARESOLVERR_FAILED.add(domain)
            return None
        return _page(html, final_url)

    def _get_via_flaresolverr(self, url: str) -> tuple[str, str]:
        _check_public(url)
        response = requests.post(
            f"{self.flaresolverr_url}/v1",
            json={"cmd": "request.get", "url": url, "maxTimeout": 30000},
            timeout=60,
        )
        response.raise_for_status()
        solution = response.json()["solution"]
        final_url = solution.get("url") or url
        _check_public(final_url)  # браузер FlareSolverr мог уйти редиректом во внутреннюю сеть
        if int(solution.get("status") or 0) >= 400:
            raise ValueError(f"status {solution.get('status')}")
        return str(solution.get("response") or ""), final_url

    def _get(self, url: str) -> tuple[str, str]:
        for _ in range(5):
            _check_public(url)
            response = self.http.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False)
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                response.close()
                continue
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", "") and "text" not in response.headers.get("content-type", ""):
                raise ValueError("not html")
            body = b""
            for chunk in response.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ValueError("too big")
            response.encoding = response.encoding or response.apparent_encoding
            return body.decode(_codec(response.encoding), errors="replace"), url
        raise ValueError("too many redirects")


def _codec(encoding: str | None) -> str:
    """Кодировка из заголовка, если Python её знает: бывает «charset=empty» (Bones Brigade) — тогда utf-8."""
    try:
        return codecs.lookup(encoding).name if encoding else "utf-8"
    except LookupError:
        return "utf-8"


_CHALLENGE = ("enable javascript", "javascript is disabled", "checking your browser", "just a moment",
              "verify you are human", "client challenge")
# Ответы защиты от ботов (Cloudflare и т.п.) — только их стоит пробовать через FlareSolverr.
_BOT_WALL_STATUSES = {403, 429, 503}
_FLARESOLVERR_LOCK = threading.Lock()
_FLARESOLVERR_FAILED: set[str] = set()
# Не страницы — через браузер не открываются.
_NOT_PAGES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".zip", ".mp3", ".flac", ".wav", ".mp4")


def _bot_wall(html: str) -> bool:
    """Страница-проверка вместо содержимого: короткая и с её маркерами."""
    head = html[:20000].casefold()
    return len(html) < 60000 and any(marker in head for marker in _CHALLENGE + ("cf-chl", "challenge-platform"))


def _page(html: str, final_url: str) -> Page | None:
    text = trafilatura.extract(html, url=final_url, include_comments=False, favor_recall=True) or ""
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "") or final_url
    if len(text) < 200 or (len(text) < 1500 and any(m in (title + " " + text).casefold() for m in _CHALLENGE)):
        return None
    full = trafilatura.html2txt(html) or text
    return Page(final_url, title, text[:MAX_TEXT], full)


def _skipped(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if any(p in url for p in SKIP_PATHS):
        return True
    return any(host == d or host.endswith("." + d) or (d in host and "/" not in d) or ("/" in d and d in url)
               for d in SKIP_DOMAINS)


def _check_public(url: str) -> None:
    """Только http(s) и только публичные адреса — иначе ValueError."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"bad url {url!r}")
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError(f"private address for {parsed.hostname}")
