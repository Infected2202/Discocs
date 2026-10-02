"""Вход в Beatport API своим аккаунтом: python beatport_login.py [--show]

Логин и пароль вводятся здесь, в терминале, и никуда не сохраняются. В config.json
пишутся только токены (beatport_token): access живёт ~10 часов, дальше beatport.py
продлевает его refresh-токеном без пароля. Если и refresh протухнет — запустить снова.

Клиент — тот же, что у страницы документации API (api.beatport.com/v4/docs):
логин → код авторизации → токен."""
import getpass
import json
import sys
import time
import urllib.parse
from pathlib import Path

import requests

API = "https://api.beatport.com/v4"
CLIENT_ID = "0GIvkCltVIuPkkwSJHp6NDb3s0potTjLBQr388Dd"
REDIRECT = f"{API}/auth/o/post-message/"
CONFIG = Path(__file__).parent / "config.json"


def main() -> None:
    s = requests.Session()
    s.headers["User-Agent"] = "music-fill/0.1 (local library tool)"
    user = input("Beatport логин: ").strip()
    if "--show" in sys.argv:  # в терминале Windows getpass не принимает вставку из буфера
        password = input("Beatport пароль (виден на экране): ")
    else:
        password = getpass.getpass("Beatport пароль (не отображается; если не вставляется — запусти с --show): ")
    r = s.post(f"{API}/auth/login/", json={"username": user, "password": password})
    del password
    if r.status_code != 200:
        raise SystemExit(f"вход не удался: {r.status_code} {r.text[:200]}")
    r = s.get(f"{API}/auth/o/authorize/", allow_redirects=False,
              params={"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT})
    code = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers.get("Location", "")).query).get("code", [None])[0]
    if not code:
        raise SystemExit(f"не получил код авторизации: {r.status_code} {r.headers.get('Location')}")
    r = s.post(f"{API}/auth/o/token/", data={"grant_type": "authorization_code", "code": code,
                                             "client_id": CLIENT_ID, "redirect_uri": REDIRECT})
    if r.status_code != 200:
        raise SystemExit(f"не получил токен: {r.status_code} {r.text[:200]}")
    tok = r.json()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["beatport_token"] = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                             "expires_at": time.time() + tok.get("expires_in", 36000) - 60}
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"готово: токен сохранён в config.json (действует ~{tok.get('expires_in', 0) // 3600} ч, дальше продлевается сам)")


if __name__ == "__main__":
    main()
