"""Журнал: что происходило и почему — чтобы после сбоя не восстанавливать картину по косвенным
признакам (так было с ребутом 01.10: оборванные отправки, упавшие на диске релизы, записи задним числом).

logs/events.jsonl — события, только дописывание: время, событие, подробности (одна строка — одно событие).
logs/server.log   — лог сервера: меняющие запросы и полный текст ошибок (ротация 5 × 5 МБ).
backups/ГГГГ-ММ-ДД/ — ежедневные копии decisions.json, downloads.json, plan_state.json (14 дней)."""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
EVENTS = LOG_DIR / "events.jsonl"
BACKUP_DIR = ROOT / "backups"
BACKUP_FILES = ("decisions.json", "downloads.json", "plan_state.json")
BACKUP_DAYS = 14
# события-проблемы: их видно фильтром «только проблемы» и они дублируются в server.log как warning
PROBLEMS = {"error", "send_failed", "deemix_failed", "deemix_down", "deemix_stuck", "deemix_stalled", "retry_failed",
            "slsk_dl_failed", "slsk_place_failed", "tags_failed"}

LOG_DIR.mkdir(exist_ok=True)
log = logging.getLogger("music-fill")
if not log.handlers:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(LOG_DIR / "server.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)

_lock = threading.Lock()


def event(name: str, /, **data) -> None:
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": name, **data}
    line = json.dumps(rec, ensure_ascii=False)
    with _lock:
        with open(EVENTS, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    (log.warning if name in PROBLEMS else log.info)("%s %s", name, json.dumps(data, ensure_ascii=False)[:500])


def read_events(limit: int = 500, text: str = "", problems: bool = False) -> list[dict]:
    """Последние события (новые первыми). text — подстрока где угодно в событии (задача, релиз, ошибка)."""
    if not EVENTS.exists():
        return []
    text = text.lower()
    out: deque = deque(maxlen=limit)
    with open(EVENTS, encoding="utf-8") as f:
        for line in f:
            if text and text not in line.lower():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if problems and rec.get("event") not in PROBLEMS:
                continue
            out.append(rec)
    return list(reversed(out))


def daily_backup() -> None:
    """Раз в день — копия состояния; старше BACKUP_DAYS — удаляется. Любую порчу можно откатить."""
    day = BACKUP_DIR / time.strftime("%Y-%m-%d")
    if not day.exists():
        day.mkdir(parents=True)
        for name in BACKUP_FILES:
            if (ROOT / name).exists():
                shutil.copy2(ROOT / name, day / name)
        event("backup", dir=str(day.relative_to(ROOT)))
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - BACKUP_DAYS * 86400))
    for d in BACKUP_DIR.iterdir():
        if d.is_dir() and d.name < cutoff:
            shutil.rmtree(d, ignore_errors=True)
