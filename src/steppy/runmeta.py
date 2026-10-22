"""Run identity and the ``--bundle`` artifact layout.

A **leaf**: stdlib plus ``steppy.__version__`` only. Three modules need to agree
on what is written here and where — ``cli`` (single-worker runs),
``coordinator`` (parallel runs) and ``bundle`` (the reader) — and two of those
are lazy-imported from the third, so a shared home avoids an import cycle as
well as three copies of the same path strings.

Two things live here:

**Run identity.** :func:`new_run_id` is the project's single id generator.
Sortability is load-bearing rather than cosmetic: both
``coverage.merge.latest_run_id`` and ``steppy bundle create`` resolve "the
newest recorded run" by taking the lexicographically last directory name, so a
random id would make them pick arbitrarily. Historically the parallel
coordinator minted a bare ``uuid4().hex[:8]`` for exactly this variable, which
is why that resolution was only ever correct on the single-worker path.

**The sidecar.** ``steppy bundle create`` runs as a separate process, minutes
or a CI step after the run itself, and cannot otherwise know what the run was
asked to do. Re-deriving the spec from flags the user retypes is guaranteed to
drift, and drift here means a results bundle that misreports its own
dimensions. So a ``--bundle`` run records them.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import __version__

#: Per-run directories under ``output/``, mirroring
#: ``output/coverage/raw/<run_id>/``. Keying on the run means a previous run's
#: files can never be mistaken for this one's, so nothing has to be reset
#: between runs — the failure mode that would otherwise ship yesterday's
#: results inside today's bundle.
EVENTS_SUBDIR = "events"
JUNIT_SUBDIR = "junit"

#: Where ``steppy report`` writes its static site by default. Lives here rather
#: than in ``report/`` because the *bundler* needs it too: without skipping this
#: directory, ``steppy bundle create`` would archive the whole rendered site —
#: every screenshot it copied included — and roughly double the bundle's payload.
#: (``classify`` maps ``output/reports``, plural, to a pytest-report kind; this
#: singular directory would otherwise fall through to ``other`` and ship.)
REPORT_SUBDIR = "report"

#: Leading dot so it can never collide with a scenario slug. Under ``output/``
#: because that is the only writable mount inside a worker container.
RUN_SIDECAR_NAME = ".steppy-run.json"

#: Schema version of the sidecar. Additive fields are always allowed and
#: readers default what is absent — the same forward-compat rule the screenshot
#: sidecars follow.
RUN_SIDECAR_VERSION = 1


def new_run_id() -> str:
    """Sortable unique id for one run: ``20260806T101500Z-a1b2c3``."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]


def bundle_dirs(project_root: Path, run_id: str) -> tuple[Path, Path]:
    """``(events_dir, junit_dir)`` for a ``--bundle`` run."""
    output = Path(project_root) / "output"
    return output / EVENTS_SUBDIR / run_id, output / JUNIT_SUBDIR / run_id


def sidecar_path(project_root: Path) -> Path:
    return Path(project_root) / "output" / RUN_SIDECAR_NAME


def write_run_sidecar(
    project_root: Path,
    *,
    run_id: str,
    spec: dict,
    executor: str = "local",
    slugs: Optional[list[str]] = None,
) -> Optional[Path]:
    """Write ``output/.steppy-run.json``. Best-effort — returns ``None`` and
    never raises, because failing a whole test run over a metadata file would
    be a poor trade. ``steppy bundle create`` says so loudly when it is absent.
    """
    payload: dict[str, Any] = {
        "steppy_run": RUN_SIDECAR_VERSION,
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steppy_version": __version__,
        "executor": executor,
        "spec": spec,
    }
    if slugs is not None:
        payload["slugs"] = list(slugs)
    try:
        path = sidecar_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return path
    except OSError:
        return None


def read_run_sidecar(project_root: Path) -> Optional[dict]:
    """Load ``output/.steppy-run.json``. ``None`` when absent or unreadable."""
    try:
        raw = json.loads(sidecar_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
