"""Summarize prep_timing events from SQLite."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict

import aiosqlite

from bot.config import get_settings


async def main() -> int:
    settings = get_settings()
    async with aiosqlite.connect(settings.sqlite_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
            SELECT navidrome_song_id, context, created_at
            FROM events
            WHERE event_type = 'prep_timing'
            ORDER BY created_at DESC
            LIMIT 100
            """
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        print("No prep_timing events yet. Send a track first, then rerun.")
        return 0

    step_totals: dict[str, list[float]] = defaultdict(list)
    slowest_counts: dict[str, int] = defaultdict(int)
    totals: list[float] = []

    print(f"Last {len(rows)} prep runs:\n")
    for row in rows:
        try:
            payload = json.loads(row["context"])
        except json.JSONDecodeError:
            continue
        total = float(payload.get("total_s", 0))
        totals.append(total)
        slowest = payload.get("slowest")
        if isinstance(slowest, str):
            slowest_counts[slowest] += 1
        steps = payload.get("steps", {})
        if isinstance(steps, dict):
            for name, value in steps.items():
                step_totals[name].append(float(value))
        label = payload.get("label", row["navidrome_song_id"])
        details = payload.get("details", {})
        detail_str = ""
        if isinstance(details, dict) and details:
            detail_str = " " + " ".join(f"{k}={v}" for k, v in details.items())
        print(
            f"  {row['created_at']} {label} total={total:.2f}s "
            f"slowest={payload.get('slowest')} ({payload.get('slowest_s')}s){detail_str}"
        )

    if totals:
        avg_total = sum(totals) / len(totals)
        print(f"\nAverage total: {avg_total:.2f}s")

    if step_totals:
        print("\nAverage step duration:")
        for name, values in sorted(step_totals.items(), key=lambda x: -sum(x[1]) / len(x[1])):
            print(f"  {name}: {sum(values) / len(values):.2f}s ({len(values)} samples)")

    if slowest_counts:
        print("\nMost common bottleneck:")
        for name, count in sorted(slowest_counts.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count}x")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
