from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

_FALSE_VALUES = {"0", "false", "no", "off"}


def perf_logs_enabled() -> bool:
    raw = os.getenv("PERF_LOGS")
    if raw is None:
        return False
    return raw.strip().lower() not in _FALSE_VALUES


@dataclass(slots=True)
class PerfTracker:
    """Structured performance tracker for request-scoped stage timings."""

    logger: logging.Logger
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    enabled: bool = field(default_factory=perf_logs_enabled)
    start_ns: int = field(default_factory=time.perf_counter_ns)
    steps_ms: dict[str, float] = field(default_factory=dict)

    @property
    def total_ms(self) -> float:
        return self.elapsed_ms()

    def elapsed_ms(self, since_ns: int | None = None) -> float:
        start_ns = self.start_ns if since_ns is None else since_ns
        return (time.perf_counter_ns() - start_ns) / 1_000_000

    def add_step_ms(self, name: str, duration_ms: float) -> None:
        if not self.enabled:
            return
        self.steps_ms[name] = round(self.steps_ms.get(name, 0.0) + duration_ms, 3)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return

        started_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            self.add_step_ms(name, (time.perf_counter_ns() - started_ns) / 1_000_000)

    def mark(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        payload = {
            "request_id": self.request_id,
            "event": event,
            "t_ms": round(self.total_ms, 3),
        }
        payload.update({k: v for k, v in fields.items() if v is not None})
        self.logger.info("PERF_EVENT %s", json.dumps(payload, default=str))

    def summary(self, **fields: Any) -> None:
        if not self.enabled:
            return
        payload = {
            "request_id": self.request_id,
            "total_ms": round(self.total_ms, 3),
            "steps_ms": self.steps_ms,
        }
        payload.update({k: v for k, v in fields.items() if v is not None})
        self.logger.info("PERF_SUMMARY %s", json.dumps(payload, default=str))
