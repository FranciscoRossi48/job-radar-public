"""Single-task background runner with a live, bounded log stream."""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _LiveHandler(logging.Handler):
    def __init__(self, emit_line: Callable[[str], None]):
        super().__init__()
        self.emit_line = emit_line
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emit_line(self.format(record))
        except Exception:  # pragma: no cover - logging must never break a task
            self.handleError(record)


class Runner:
    """Runs one pipeline operation at a time to protect SQLite and provider quotas."""

    def __init__(self, max_log_lines: int = 300):
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._log: deque[str] = deque(maxlen=max_log_lines)
        self._history: deque[dict[str, Any]] = deque(maxlen=20)
        self.id: str | None = None
        self.running = False
        self.task: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.result: Any = None
        self.error: str | None = None

    def start(self, name: str, func: Callable[[], Any]) -> bool:
        with self._lock:
            if self.running:
                return False
            self.id = uuid.uuid4().hex[:12]
            self.running = True
            self.task = name
            self.started_at = _now()
            self.finished_at = None
            self.result = None
            self.error = None
            self._log.clear()
            self._append(f"Starting {name}")
            self._thread = threading.Thread(
                target=self._run, args=(func,), daemon=True, name=f"job-radar-{name}"
            )
            self._thread.start()
            return True

    def _append(self, line: str) -> None:
        with self._lock:
            self._log.append(line)

    def _run(self, func: Callable[[], Any]) -> None:
        handler = _LiveHandler(self._append)
        root = logging.getLogger()
        previous_level = root.level
        if root.level > logging.INFO:
            root.setLevel(logging.INFO)
        root.addHandler(handler)
        try:
            result = func()
            with self._lock:
                self.result = result
            self._append(f"Finished {self.task}")
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).exception("web task %s failed", self.task)
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)
            with self._lock:
                self.finished_at = _now()
                self.running = False
                self._history.appendleft(self._snapshot(include_history=False))

    def _snapshot(self, *, include_history: bool) -> dict[str, Any]:
        data = {
            "id": self.id,
            "running": self.running,
            "task": self.task,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "log": list(self._log),
        }
        if include_history:
            data["history"] = list(self._history)
        return data

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(include_history=True)


runner = Runner()
