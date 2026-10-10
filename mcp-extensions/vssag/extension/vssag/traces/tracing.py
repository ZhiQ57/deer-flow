from __future__ import annotations

import time
import uuid

from .trace_models import TraceEvent


class TraceRecorder:
    def __init__(self):
        self.events: list[TraceEvent] = []
        self._started: dict[str, float] = {}

    def start(self, stage: str, input_summary: dict | None = None) -> str:
        trace_id = str(uuid.uuid4())
        event = TraceEvent(
            trace_id=trace_id,
            stage=stage,
            input_summary=input_summary or {},
        )
        self.events.append(event)
        self._started[trace_id] = time.monotonic()
        return trace_id

    def end(self, trace_id: str, output_summary: dict | None = None, error: str | None = None, started: float | None = None) -> None:
        for event in reversed(self.events):
            if event.trace_id == trace_id:
                marker = self._started.pop(trace_id, None)
                event.duration_ms = int(
                    (time.monotonic() - (started or marker)) * 1000
                ) if (started or marker) else event.duration_ms
                event.output_summary = output_summary or {}
                event.error = error
                return
