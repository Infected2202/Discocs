from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class PrepTimer:
    """Collect per-step durations for track preparation."""

    label: str
    _started_at: float = field(default_factory=time.perf_counter)
    steps: list[tuple[str, float]] = field(default_factory=list)
    details: dict[str, str | int | float] = field(default_factory=dict)

    @asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.steps.append((name, time.perf_counter() - t0))

    def add_detail(self, key: str, value: str | int | float) -> None:
        self.details[key] = value

    @property
    def total_seconds(self) -> float:
        return time.perf_counter() - self._started_at

    def summary_parts(self) -> list[str]:
        parts = [f"{name}={elapsed:.2f}s" for name, elapsed in self.steps]
        if self.details:
            parts.extend(f"{key}={value}" for key, value in self.details.items())
        return parts

    def build_payload(self) -> dict[str, object]:
        total = self.total_seconds
        steps_total = sum(elapsed for _, elapsed in self.steps)
        payload: dict[str, object] = {
            "label": self.label,
            "total_s": round(total, 3),
            "steps_s": round(steps_total, 3),
            "steps": {name: round(elapsed, 3) for name, elapsed in self.steps},
            "details": dict(self.details),
        }
        if self.steps:
            slowest_name, slowest_elapsed = max(self.steps, key=lambda item: item[1])
            payload["slowest"] = slowest_name
            payload["slowest_s"] = round(slowest_elapsed, 3)
        return payload

    def log_summary(self) -> dict[str, object]:
        payload = self.build_payload()
        logger.info(
            "Prep timing [%s] total=%.2fs | %s",
            self.label,
            payload["total_s"],
            " | ".join(self.summary_parts()),
        )
        return payload

    def to_event_context(self) -> str:
        return json.dumps(self.build_payload(), ensure_ascii=False)
