"""Guaranteed-teardown context for the coordinator.

Wraps an ``ExitStack`` plus signal handlers (SIGINT/SIGTERM on POSIX,
SIGINT only on Windows — Windows doesn't deliver SIGTERM to console apps).
The coordinator pushes a ``compose down -v`` callback for each stack as
soon as ``compose up`` is invoked. Any of:

  - normal completion (queue drained, all workers exited 0)
  - user Ctrl-C
  - uncaught exception in coordinator
  - SIGTERM from outside

…triggers ``ExitStack.close()`` which fires every registered teardown in
LIFO order. This is the single place that guarantees no orphaned
containers, networks, or volumes after a parallel run.

The Ctrl-C path raises ``KeyboardInterrupt`` after first calling the
default signal handler, so subsequent Ctrl-C presses still work normally
(double Ctrl-C escape hatch — useful if a teardown itself hangs).
"""

from __future__ import annotations

import contextlib
import signal
import sys
from typing import Callable


class CoordinatorLifecycle:
    """Context manager: ``with CoordinatorLifecycle() as lc: lc.on_teardown(fn)``.

    Restores the previous signal handlers on exit so this doesn't leak signal
    state to a calling process.
    """

    def __init__(self):
        self._stack = contextlib.ExitStack()
        self._previous_handlers: dict[int, Callable | int | None] = {}
        self._signals_to_handle: list[int] = [signal.SIGINT]
        if hasattr(signal, "SIGTERM") and sys.platform != "win32":
            # Windows console apps don't receive SIGTERM in the usual sense;
            # skip it to avoid OSError on signal.signal().
            self._signals_to_handle.append(signal.SIGTERM)

    def __enter__(self) -> "CoordinatorLifecycle":
        self._stack.__enter__()
        for sig in self._signals_to_handle:
            try:
                self._previous_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                # signal.signal raises ValueError off the main thread; OSError
                # for unsupported signals on the platform. Either way, skip.
                pass
        return self

    def __exit__(self, exc_type, exc, tb):
        # Restore signal handlers BEFORE running teardown so a Ctrl-C during
        # teardown gets handled by the default Python interrupt logic.
        for sig, prev in self._previous_handlers.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError):
                pass
        return self._stack.__exit__(exc_type, exc, tb)

    def on_teardown(self, fn: Callable[[], None]) -> None:
        """Register a callback to run on context exit (LIFO order)."""
        self._stack.callback(self._safe_call, fn)

    @staticmethod
    def _safe_call(fn: Callable[[], None]) -> None:
        """Run a teardown callback; never let an exception escape and abort
        subsequent teardowns. Logs to stderr instead."""
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — teardown must be best-effort
            print(f"steppy teardown error (continuing): {e}", file=sys.stderr)

    def _on_signal(self, signum, frame):
        """Convert SIGINT/SIGTERM into KeyboardInterrupt so the coordinator's
        outer try/except can react and ExitStack runs teardowns. Restores
        the previous handler immediately so a second Ctrl-C uses the default
        behavior (panic-exit if a teardown hangs)."""
        try:
            signal.signal(signum, self._previous_handlers.get(signum, signal.SIG_DFL))
        except (ValueError, OSError):
            pass
        raise KeyboardInterrupt()
