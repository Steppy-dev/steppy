"""``parallel.isolate_auth`` — a private ``.auth`` dir per worker.

By default the project's ``.auth`` is bind-mounted **read-write into every
worker**, so N workers read and rewrite one storage-state file concurrently.
That is a race by construction, and it is a prime suspect whenever a big
parallel run goes intermittently red. Isolating trades one shared login for N
private ones.

The mutual exclusion with ``pre_auth`` is the part worth guarding: ``pre_auth``
exists to populate ONE shared cache from a single in-network login, which a
private per-worker dir would never receive. Silently preferring one would leave
workers unauthenticated with no visible cause.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from rich.console import Console

from steppy import coordinator
from steppy.models import ParallelConfig, PreAuthConfig


def _console():
    return Console(force_terminal=False, no_color=True)


# ── config validation ───────────────────────────────────────────────────────


def test_isolate_auth_defaults_off():
    assert ParallelConfig().isolate_auth is False


def test_isolate_auth_rejects_pre_auth():
    with pytest.raises(ValidationError) as exc:
        ParallelConfig(isolate_auth=True, pre_auth=PreAuthConfig(command="python auth_login.py"))
    assert "mutually exclusive" in str(exc.value)


def test_pre_auth_alone_is_fine():
    assert ParallelConfig(pre_auth=PreAuthConfig(command="x")).pre_auth is not None


def test_isolate_auth_alone_is_fine():
    assert ParallelConfig(isolate_auth=True).isolate_auth is True


# ── dir preparation ─────────────────────────────────────────────────────────


def test_disabled_produces_no_mounts(tmp_path):
    assert (
        coordinator._prepare_isolated_auth(
            enabled=False,
            worker_count=3,
            project_root=tmp_path,
            tmp_dir=tmp_path / "run",
            console=_console(),
        )
        == []
    )


def test_creates_one_dir_per_worker(tmp_path):
    tmp_dir = tmp_path / "run"
    tmp_dir.mkdir()
    mounts = coordinator._prepare_isolated_auth(
        enabled=True,
        worker_count=3,
        project_root=tmp_path,
        tmp_dir=tmp_dir,
        console=_console(),
    )
    assert len(mounts) == 3
    for i in range(1, 4):
        assert (tmp_dir / "auth" / f"w{i}").is_dir()
    assert len(set(mounts)) == 3, "each worker needs its OWN dir, not a shared one"


def test_seeds_each_worker_from_the_shared_cache(tmp_path):
    """Seeding means a still-valid cached session is reused instead of forcing
    N cold logins on every run."""
    shared = tmp_path / ".auth"
    shared.mkdir()
    (shared / "editor.json").write_text('{"cookies": []}', encoding="utf-8")
    tmp_dir = tmp_path / "run"
    tmp_dir.mkdir()

    coordinator._prepare_isolated_auth(
        enabled=True,
        worker_count=2,
        project_root=tmp_path,
        tmp_dir=tmp_dir,
        console=_console(),
    )
    for i in (1, 2):
        copied = tmp_dir / "auth" / f"w{i}" / "editor.json"
        assert copied.is_file()
        assert copied.read_text(encoding="utf-8") == '{"cookies": []}'


def test_works_without_an_existing_shared_dir(tmp_path):
    tmp_dir = tmp_path / "run"
    tmp_dir.mkdir()
    mounts = coordinator._prepare_isolated_auth(
        enabled=True,
        worker_count=1,
        project_root=tmp_path,
        tmp_dir=tmp_dir,
        console=_console(),
    )
    assert len(mounts) == 1
    assert Path(mounts[0]).is_dir()


def test_mount_paths_are_posix_absolute(tmp_path):
    """Docker Desktop path translation needs absolute posix-style sources."""
    tmp_dir = tmp_path / "run"
    tmp_dir.mkdir()
    mounts = coordinator._prepare_isolated_auth(
        enabled=True,
        worker_count=1,
        project_root=tmp_path,
        tmp_dir=tmp_dir,
        console=_console(),
    )
    assert "\\" not in mounts[0]
    assert mounts[0].endswith("/auth/w1")
