"""Собирает browser.json для ytmusicapi из curl.txt.

curl.txt: в DevTools правый клик по POST-запросу browse на music.youtube.com →
Copy → Copy as cURL (bash), вставить в curl.txt и сохранить.
Скрипт печатает только имена найденных заголовков, не значения.
"""

import re
import shlex
from pathlib import Path

from ytmusicapi import setup

ROOT = Path(__file__).parent

text = (ROOT / "curl.txt").read_text(encoding="utf-8")

# Куки, вставленные таблицей из DevTools → Application → Cookies:
# Name<TAB>Value<TAB>Domain<TAB>... Берём только домен youtube.com
# (те же имена есть и для .google.com); строка без домена — тоже youtube.
cookies = {}
curl_lines = []
for line in text.splitlines():
    fields = line.strip().split("\t")
    if len(fields) < 2:
        curl_lines.append(line)
        continue
    name, value = fields[0], fields[1]
    domain = fields[2] if len(fields) > 2 else ".youtube.com"
    if domain.endswith("youtube.com") and value:
        cookies[name] = value
text = "\n".join(curl_lines)

text = re.sub(r"\\\r?\n", " ", text)  # склеить перенос строк через "\"
tokens = shlex.split(text)

headers = {}
for flag, value in zip(tokens, tokens[1:]):
    if flag in ("-H", "--header") and ":" in value:
        key, val = value.split(":", 1)
        headers[key.strip().lower()] = val.strip()
    elif flag in ("-b", "--cookie"):
        headers["cookie"] = value

if cookies and "cookie" not in headers:
    headers["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    print("cookies:", ", ".join(cookies))

print("headers:",", ".join(sorted(headers)))
missing = {"cookie", "authorization"} - headers.keys()
if missing:
    raise SystemExit(f"нет обязательных заголовков: {missing}")

raw = "\n".join(f"{k}: {v}" for k, v in headers.items())
setup(str(ROOT / "browser.json"), raw)
print("browser.json готов")
