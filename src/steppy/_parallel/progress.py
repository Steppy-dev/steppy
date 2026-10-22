"""Tail each worker container's stdout and dispatch lines to the bus.

For each Compose project, a thread runs
``docker compose -p <project> logs -f --no-color steppy-worker`` and
feeds every line into the shared :class:`EventBus`. The bus parses
``STEPPY-EVT::*`` lines into typed events; everything else falls
through to the raw-log channel (printed when ``--verbose`` is set,
always written to a per-worker file for post-mortem).

A second thread per worker captures the full-stack logs (every service
in the project, not just steppy-worker) into a separate file so the
user can dig in after a failure.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .event_bus import EventBus


@dataclass
class WorkerLogTailer:
    """Background thread tailing one worker's logs into the EventBus."""

    project: str
    worker_id: str
    bus: EventBus
    app_log_file: Path  # full-stack logs go here for post-mortem
    _proc: Optional[subprocess.Popen] = field(default=None, init=False, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _stop: Optional[threading.Event] = field(default=None, init=False, repr=False)

    def start(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"log-{self.worker_id}", daemon=True)
        self._thread.start()
        # Also kick off a fire-and-forget capture of the entire stack's logs
        # (worker + app) into a per-worker file so users have a post-mortem
        # if the run fails.
        self.app_log_file.parent.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._capture_full_stack, name=f"log-full-{self.worker_id}", daemon=True).start()

    def _run(self) -> None:
        cmd = [
            "docker",
            "compose",
            "-p",
            self.project,
            "logs",
            "-f",
            "--no-color",
            "--no-log-prefix",
            "steppy-worker",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            return
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop is not None and self._stop.is_set():
                break
            self.bus.feed_line(self.worker_id, line)

    def _capture_full_stack(self) -> None:
        """Capture every service's logs into ``self.app_log_file`` for
        post-mortem. Runs ``compose logs -f`` over the whole project (no
        service filter) and writes raw output. Best-effort; if it fails
        we just lose the post-mortem file, never the run."""
        cmd = ["docker", "compose", "-p", self.project, "logs", "-f", "--no-color"]
        try:
            with self.app_log_file.open("w", encoding="utf-8") as f:
                subprocess.run(
                    cmd, stdout=f, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", check=False
                )
        except (FileNotFoundError, OSError):
            pass

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
