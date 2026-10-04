"""Клиенты Beatport API v4, Discogs API и Wikidata/Wikipedia для синхронизации лейблов.

Все ответы кэшируются (``HttpCache``); запросы к одному сервису идут по одному, на 429 —
пауза и повтор. ``http`` и ``sleep`` подменяются в тестах.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from collections.abc import Callable

import httpx

from app.services.label_sync.http_cache import HttpCache, cache_key

USER_AGENT = "discocs-label-sync/1.0 (personal music library)"
BEATPORT_API = "https://api.beatport.com/v4"
# Клиент страницы документации Beatport API: логин → код авторизации → токен.
BEATPORT_CLIENT_ID = "0GIvkCltVIuPkkwSJHp6NDb3s0potTjLBQr388Dd"
BEATPORT_REDIRECT = f"{BEATPORT_API}/auth/o/post-message/"
DISCOGS_API = "https://api.discogs.com"

API_TTL = 30 * 86400
_RETRIES = 6


class ServiceAuthError(RuntimeError):
    """Вход во внешний сервис не работает — нужно войти заново в админке."""


def new_http_client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=False)


# ---------- Beatport ----------

def beatport_login(http: httpx.Client, username: str, password: str) -> dict[str, object]:
    """Логин и пароль → токены. Пароль никуда не сохраняется."""
    r = http.post(f"{BEATPORT_API}/auth/login/", json={"username": username, "password": password})
    if r.status_code != 200:
        raise ServiceAuthError(f"Beatport rejected the login ({r.status_code})")
    r = http.get(
        f"{BEATPORT_API}/auth/o/authorize/",
        params={"response_type": "code", "client_id": BEATPORT_CLIENT_ID, "redirect_uri": BEATPORT_REDIRECT},
    )
    location = r.headers.get("Location", "")
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code", [None])[0]
    if not code:
        raise ServiceAuthError(f"Beatport did not return an authorization code ({r.status_code})")
    r = http.post(
        f"{BEATPORT_API}/auth/o/token/",
        data={"grant_type": "authorization_code", "code": code,
              "client_id": BEATPORT_CLIENT_ID, "redirect_uri": BEATPORT_REDIRECT},
    )
    if r.status_code != 200:
        raise ServiceAuthError(f"Beatport did not issue a token ({r.status_code})")
    return _beatport_token(r.json(), previous_refresh=None)


def _beatport_token(payload: dict, previous_refresh: object) -> dict[str, object]:
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or previous_refresh,
        "expires_at": time.time() + float(payload.get("expires_in", 36000)) - 60,
    }


class BeatportClient:
    def __init__(
        self,
        token: dict[str, object],
        on_token: Callable[[dict[str, object]], None],
        cache: HttpCache,
        http: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = dict(token)
        self._on_token = on_token  # новый refresh-токен надо сохранить: старый Beatport больше не примет
        self._cache = cache
        self._http = http
        self._sleep = sleep
        self._lock = threading.Lock()

    def _access_token(self, force_refresh: bool) -> str:
        if force_refresh or time.time() > float(self._token.get("expires_at") or 0):
            r = self._http.post(
                f"{BEATPORT_API}/auth/o/token/",
                data={"grant_type": "refresh_token", "client_id": BEATPORT_CLIENT_ID,
                      "refresh_token": self._token.get("refresh_token")},
            )
            if r.status_code != 200:
                raise ServiceAuthError("Beatport login expired — sign in again in the admin")
            self._token = _beatport_token(r.json(), self._token.get("refresh_token"))
            self._on_token(dict(self._token))
        return str(self._token["access_token"])

    def get(self, path: str, **params: object) -> dict:
        key = cache_key("beatport", path, params)
        hit = self._cache.get(key, API_TTL)
        if hit is not None:
            return hit
        with self._lock:  # лимитов Beatport не публикует — по одному запросу
            refreshed = False
            r = None
            for attempt in range(_RETRIES):
                r = self._http.get(f"{BEATPORT_API}{path}", params=params,
                                   headers={"Authorization": f"Bearer {self._access_token(refreshed)}"})
                if r.status_code == 401 and not refreshed:
                    refreshed = True
                    continue
                if r.status_code == 429:
                    self._sleep(5 + 5 * attempt)
                    continue
                break
            self._sleep(0.2)
        if r is None:  # pragma: no cover — цикл выше всегда делает хотя бы один запрос
            raise RuntimeError("Beatport request was not sent")
        if r.status_code == 401:
            raise ServiceAuthError("Beatport login expired — sign in again in the admin")
        if r.status_code == 404:
            data: dict = {}
        else:
            r.raise_for_status()
            data = r.json()
        self._cache.put(key, data)
        return data

    def label(self, label_id: int) -> dict:
        return self.get(f"/catalog/labels/{label_id}/")

    def search(self, query: str, type_: str, limit: int = 10) -> list[dict]:
        return list(self.get("/catalog/search/", q=query, type=type_, per_page=limit).get(type_, []))


# ---------- Discogs ----------

class DiscogsClient:
    def __init__(
        self,
        token: str,
        cache: HttpCache,
        http: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = token
        self._cache = cache
        self._http = http
        self._sleep = sleep
        self._lock = threading.Lock()

    def get(self, path: str, **params: object) -> dict:
        key = cache_key("discogs", path, params)
        hit = self._cache.get(key, API_TTL)
        if hit is not None:
            return hit
        with self._lock:  # 60 запросов в минуту на внешний IP — по одному
            r = None
            for attempt in range(_RETRIES):
                r = self._http.get(f"{DISCOGS_API}{path}", params=params,
                                   headers={"Authorization": f"Discogs token={self._token}"})
                if r.status_code == 429:
                    self._sleep(5 + 5 * attempt)
                    continue
                break
            if r is None:  # pragma: no cover — цикл выше всегда делает хотя бы один запрос
                raise RuntimeError("Discogs request was not sent")
            if r.status_code == 401:
                raise ServiceAuthError("Discogs rejected the token — check it in the admin")
            if int(r.headers.get("X-Discogs-Ratelimit-Remaining", 60)) <= 3:
                self._sleep(3)
        if r.status_code == 404:
            data: dict = {}
        else:
            r.raise_for_status()
            data = r.json()
        self._cache.put(key, data)
        return data


# ---------- Wikidata / Wikipedia ----------

class WebClient:
    def __init__(self, cache: HttpCache, http: httpx.Client, sleep: Callable[[float], None] = time.sleep):
        self._cache = cache
        self._http = http
        self._sleep = sleep

    def get_json(self, url: str, **params: object) -> dict:
        key = cache_key("web", url, params)
        hit = self._cache.get(key, API_TTL)
        if hit is not None:
            return hit
        r = self._http.get(url, params=params)
        data = r.json() if r.status_code == 200 else {}
        self._cache.put(key, data)
        self._sleep(0.2)
        return data

    def download(self, url: str) -> bytes | None:
        r = self._http.get(url, timeout=60)
        if r.status_code != 200 or not r.content:
            return None
        return r.content
