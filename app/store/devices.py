"""Store Devices domain: ключи устройств вместо общего DISCOCS_SERVICE_TOKEN (docs/auth.md, «Устройства»).

Инструмент на ПК сам придумывает ключ и просит доступ; запрос висит в админке, пока его не подключат.
Хранится только SHA-256 ключа — сам ключ знает только устройство.
Do not import this module directly; use app.store instead.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.models import utc_now

DEVICE_PENDING = "pending"
DEVICE_APPROVED = "approved"
DEVICE_REJECTED = "rejected"
DEVICE_REVOKED = "revoked"

# Запрос доступа открыт без входа — чужой в сети не должен завалить админку запросами.
MAX_PENDING_DEVICES = 20
# Неподтверждённый запрос забывается через неделю: устройство при следующем запуске попросит снова.
PENDING_TTL = timedelta(days=7)
# last_seen пишется не на каждый запрос: воркер анализа ходит часто, хватит точности в минуту.
SEEN_EVERY = timedelta(seconds=60)


class TooManyPendingDevices(Exception):
    pass


def _device_dict(row: sqlite3.Row) -> dict[str, object]:
    try:
        info = json.loads(row["info_json"]) if row["info_json"] else {}
    except ValueError:
        info = {}
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "kind": row["kind"],
        "info": info,
        "status": row["status"],
        # Чтобы сверить запрос с устройством (агент пишет то же в свой журнал), не показывая ключ.
        "fingerprint": str(row["key_hash"])[:8],
        "requested_ip": row["requested_ip"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "decided_by": row["decided_by"],
        "last_seen_at": row["last_seen_at"],
        "last_ip": row["last_ip"],
    }


class DevicesStoreMixin:
    def _forget_stale_device_requests(self, conn: sqlite3.Connection) -> None:
        cutoff = (datetime.now(UTC) - PENDING_TTL).isoformat()
        conn.execute("DELETE FROM device_keys WHERE status = ? AND created_at < ?", (DEVICE_PENDING, cutoff))

    def request_device(
        self, key_hash: str, *, name: str, kind: str, info: dict[str, object], ip: str | None
    ) -> dict[str, object]:
        """Запрос доступа. Повторный с тем же ключом — тот же запрос (обновляются имя и сведения):
        устройство спрашивает при каждом запуске, а решение остаётся за админкой."""
        now = utc_now()
        info_json = json.dumps(info, ensure_ascii=False)
        with self.connect() as conn:  # type: ignore[attr-defined]
            self._forget_stale_device_requests(conn)
            row = conn.execute("SELECT id FROM device_keys WHERE key_hash = ?", (key_hash,)).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE device_keys SET name = ?, kind = ?, info_json = ?, last_seen_at = ?, last_ip = ? WHERE id = ?",
                    (name, kind, info_json, now, ip, row["id"]),
                )
                device_id = int(row["id"])
            else:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM device_keys WHERE status = ?", (DEVICE_PENDING,)
                ).fetchone()[0]
                if pending >= MAX_PENDING_DEVICES:
                    raise TooManyPendingDevices
                cursor = conn.execute(
                    """
                    INSERT INTO device_keys (key_hash, name, kind, info_json, status, requested_ip, created_at,
                                             last_seen_at, last_ip)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key_hash, name, kind, info_json, DEVICE_PENDING, ip, now, now, ip),
                )
                device_id = int(cursor.lastrowid)
        return self.device(device_id)  # type: ignore[return-value]

    def device(self, device_id: int) -> dict[str, object] | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT * FROM device_keys WHERE id = ?", (device_id,)).fetchone()
        return _device_dict(row) if row else None

    def device_for_key(self, key_hash: str, *, ip: str | None = None) -> dict[str, object] | None:
        """Устройство по ключу (любой статус) — для гейта; подключённому отмечает, что оно на связи."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT * FROM device_keys WHERE key_hash = ?", (key_hash,)).fetchone()
            if row is None:
                return None
            seen = row["last_seen_at"]
            now = datetime.now(UTC)
            stale = True
            if seen:
                try:
                    stale = now - datetime.fromisoformat(seen) >= SEEN_EVERY
                except ValueError:
                    stale = True
            if row["status"] == DEVICE_APPROVED and (stale or row["last_ip"] != ip):
                conn.execute(
                    "UPDATE device_keys SET last_seen_at = ?, last_ip = ? WHERE id = ?",
                    (now.isoformat(), ip, row["id"]),
                )
        return _device_dict(row)

    def devices(self) -> list[dict[str, object]]:
        """Сначала ждущие решения, потом подключённые, потом остальные; внутри — новые первыми."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            self._forget_stale_device_requests(conn)
            rows = conn.execute(
                """
                SELECT * FROM device_keys
                ORDER BY CASE status WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END, id DESC
                """,
                (DEVICE_PENDING, DEVICE_APPROVED),
            ).fetchall()
        return [_device_dict(row) for row in rows]

    def decide_device(self, device_id: int, decision: str, *, by: str | None) -> dict[str, object] | None:
        """approve / reject — для ждущего (и отклонённого/отозванного: передумали); revoke — для подключённого."""
        allowed = {
            "approve": (DEVICE_APPROVED, (DEVICE_PENDING, DEVICE_REJECTED, DEVICE_REVOKED)),
            "reject": (DEVICE_REJECTED, (DEVICE_PENDING,)),
            "revoke": (DEVICE_REVOKED, (DEVICE_APPROVED,)),
        }
        status, from_statuses = allowed[decision]
        marks = ",".join("?" for _ in from_statuses)
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                f"UPDATE device_keys SET status = ?, decided_at = ?, decided_by = ? WHERE id = ? AND status IN ({marks})",
                (status, utc_now(), by, device_id, *from_statuses),
            )
        return self.device(device_id)

    def delete_device(self, device_id: int) -> bool:
        """Забыть запись: устройство при следующем запуске попросит доступ заново."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            cursor = conn.execute("DELETE FROM device_keys WHERE id = ?", (device_id,))
        return cursor.rowcount > 0
