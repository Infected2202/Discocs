"""Поиск (SearXNG) и чтение страниц.

Адреса для чтения приходят только из выдачи поиска и известных источников — модель выбирает
из них по номеру, своих адресов не пишет. Чтение не ходит в локальную сеть: страница с
инъекцией не заставит открыть Jenkins или админку discocs.
"""
from __future__ import annotations

import ipaddress
import socket
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

        Не открылась напрямую или вместо текста заглушка защиты от ботов — ещё раз через FlareSolverr.
        """
        try:
            page = _page(*self._get(url))
        except (requests.RequestException, ValueError):
            page = None
        if page is None and self.flaresolverr_url:
            try:
                page = _page(*self._get_via_flaresolverr(url))
            except (requests.RequestException, ValueError, KeyError):
                page = None
        return page

    def _get_via_flaresolverr(self, url: str) -> tuple[str, str]:
        _check_public(url)
        response = requests.post(
            f"{self.flaresolverr_url}/v1",
            json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
            timeout=90,
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
            return body.decode(response.encoding or "utf-8", errors="replace"), url
        raise ValueError("too many redirects")


_CHALLENGE = ("enable javascript", "javascript is disabled", "checking your browser", "just a moment",
              "verify you are human", "client challenge")


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
