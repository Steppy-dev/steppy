"""In-network pre-authentication lock-wrapper for parallel mode.

Runs INSIDE a one-off ``steppy-preauth`` container. The coordinator renders one
such service per worker stack (so it can be gated, per-stack, before that
stack's ``steppy-worker`` claims a scenario), which means N containers would
otherwise all run the user's login command at once and race on the shared
``.auth`` cache. Instead they share ``/steppy/coord`` (the coordinator tmp dir,
bind-mounted into every stack) and use the SAME O_EXCL claim primitive as the
work queue (`_parallel/queue.py`) to elect exactly ONE winner:

  * winner  — runs the user's ``pre_auth.command``; on success drops a
              ``preauth.done`` sentinel, on failure a ``preauth.failed`` one.
  * losers  — block until they see the winner's sentinel, then exit with the
              matching code (0 on done, 1 on failed / timeout).

So the login happens once per run even though every stack starts a preauth
container. The sentinels live in the per-run coord dir, so a *new* run re-runs
the command (inter-run freshness is the command's own concern — e.g. the
capture script skips roles whose session is still fresh).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


LOCK_NAME = "preauth.lock"
DONE_NAME = "preauth.done"
FAILED_NAME = "preauth.failed"
_POLL_INTERVAL = 0.5


def run_preauth(
    command: list[str],
    *,
    timeout: int,
    coord_dir: Path | None = None,
) -> int:
    """Elect one winner (O_EXCL) to run ``command``; losers wait for its
    sentinel. Returns a process exit code (0 = auth ready for this stack).

    ``coord_dir`` defaults to ``$STEPPY_COORD_DIR`` (``/steppy/coord`` in the
    container). ``command`` is the argv to execute (a plain shell string is
    wrapped as ``sh -c`` by the caller before it reaches here).
    """
    if not command:
        print("[preauth] no command given — nothing to do", file=sys.stderr, flush=True)
        return 2

    coord = Path(coord_dir) if coord_dir is not None else Path(os.environ.get("STEPPY_COORD_DIR", "/steppy/coord"))
    coord.mkdir(parents=True, exist_ok=True)
    lock_path = coord / LOCK_NAME
    done_path = coord / DONE_NAME
    failed_path = coord / FAILED_NAME

    # A previous stack already settled auth this run — short-circuit.
    if done_path.exists():
        return 0
    if failed_path.exists():
        return 1

    try:
        fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        # Another stack is (or was) the winner — wait for its verdict.
        return _wait_for_sentinel(done_path, failed_path, timeout)

    # We won the claim: run the command exactly once for this run.
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
    finally:
        os.close(fd)

    print(f"[preauth] winner — running: {' '.join(command)}", flush=True)
    try:
        code = subprocess.run(command, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"[preauth] command timed out after {timeout}s", file=sys.stderr, flush=True)
        code = 124
    except FileNotFoundError as e:
        print(f"[preauth] command not found: {e}", file=sys.stderr, flush=True)
        code = 127

    if code == 0:
        done_path.write_text("ok", encoding="utf-8")
        print("[preauth] done — auth cache is ready", flush=True)
    else:
        failed_path.write_text(str(code), encoding="utf-8")
        print(f"[preauth] FAILED (exit {code})", file=sys.stderr, flush=True)
    return code


def _wait_for_sentinel(done_path: Path, failed_path: Path, timeout: int) -> int:
    """Loser path: block until the winner signals done/failed, or timeout."""
    print("[preauth] another stack is authenticating; waiting…", flush=True)
    deadline = time.monotonic() + max(1, timeout)
    while time.monotonic() < deadline:
        if done_path.exists():
            return 0
        if failed_path.exists():
            print("[preauth] auth winner reported failure", file=sys.stderr, flush=True)
            return 1
        time.sleep(_POLL_INTERVAL)
    print("[preauth] timed out waiting for the auth winner", file=sys.stderr, flush=True)
    return 1
