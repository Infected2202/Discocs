"""Кэш ответов внешних API — одна SQLite (cache/cache.db) вместо JSON-файлов на сотни МБ.

JSON переписывался целиком на каждую новую запись: 2,5 с на дискографию (102 МБ), 9 с на пачку
ответов Deezer (390 МБ) — дольше самих запросов, да ещё под общим замком. Здесь запись — одна
строка (миллисекунды), и в памяти ничего не держится.

Каждый модуль — своё пространство (ns): deezer, discography, labels, beatport, discogs, slsk.
Старый JSON при первом открытии переносится в базу и переименовывается в *.json.imported."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

DB = Path(__file__).parent / "cache" / "cache.db"

_local = threading.local()
_import_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    """Соединение на поток: sqlite3 не делит одно между потоками; WAL — читатели не ждут писателя."""
    c = getattr(_local, "c", None)
    if c is None:
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB, timeout=60)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, key TEXT, ts REAL, data TEXT, PRIMARY KEY (ns, key))")
        _local.c = c
    return c


class Store:
    """legacy — старый JSON этого кэша; field — где в его записи лежат данные ({ts, data} у большинства,
    {ts, files} у slsk); None — запись и есть данные (Deezer хранил ответы без времени)."""

    def __init__(self, ns: str, legacy: Path | None = None, field: str | None = "data"):
        self.ns = ns
        if legacy and legacy.exists():
            self._import(legacy, field)

    def _import(self, path: Path, field: str | None) -> None:
        with _import_lock:
            if not path.exists():
                return
            t0 = time.time()
            old = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
            rows = [(self.ns, k, mtime if field is None else v.get("ts", mtime),
                     json.dumps(v if field is None else v.get(field), ensure_ascii=False)) for k, v in old.items()]
            c = _conn()
            with c:
                c.executemany("INSERT OR IGNORE INTO kv VALUES (?, ?, ?, ?)", rows)
            path.replace(path.with_name(path.name + ".imported"))
            print(f"кэш {path.name} перенесён в cache.db: {len(rows)} записей за {time.time() - t0:.0f} с")

    def get(self, key: str) -> tuple[float, object] | None:
        """(время записи, данные) или None."""
        row = _conn().execute("SELECT ts, data FROM kv WHERE ns = ? AND key = ?", (self.ns, key)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def has(self, key: str) -> bool:
        return _conn().execute("SELECT 1 FROM kv WHERE ns = ? AND key = ?", (self.ns, key)).fetchone() is not None

    def put(self, key: str, data, ts: float | None = None) -> None:
        c = _conn()
        with c:
            c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?, ?)",
                      (self.ns, key, time.time() if ts is None else ts, json.dumps(data, ensure_ascii=False)))

    def older_than(self, age: float) -> list[str]:
        """Ключи записей старше age секунд — от самых старых."""
        return [k for (k,) in _conn().execute("SELECT key FROM kv WHERE ns = ? AND ts < ? ORDER BY ts",
                                              (self.ns, time.time() - age))]
