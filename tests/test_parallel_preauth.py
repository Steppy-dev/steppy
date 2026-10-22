"""Tests for the parallel-mode `parallel.pre_auth` hook.

Two concerns, no Docker:
  1. Override rendering — a `steppy-preauth` service + the worker gate appear
     ONLY when pre_auth is configured, and are absent (byte-identical) otherwise.
  2. The in-container lock-wrapper (`run_preauth`) — one winner runs the login
     command; losers wait for its sentinel; failure propagates.
"""

import sys
from pathlib import Path

import yaml

from steppy._parallel.compose import ComposeIntrospection, render_override
from steppy._parallel.preauth import run_preauth, LOCK_NAME, DONE_NAME, FAILED_NAME


# A ports-free, healthchecked introspection so the rendered YAML has no `!reset`
# tag and can be parsed by PyYAML.
_PARSEABLE_INTRO = ComposeIntrospection(
    services_with_ports=[],
    networks=["default"],
    services_with_healthcheck=["app-frontend", "krakend"],
)


def _render(tmp_path: Path, **overrides) -> str:
    introspection = overrides.pop("introspection", _PARSEABLE_INTRO)
    return render_override(
        introspection=introspection,
        worker_image="steppy-worker",
        image_tag="0.3.0",
        worker_index=1,
        run_id="abc12345",
        project_root=tmp_path / "proj",
        tmp_dir=tmp_path / "run",
        base_url_override='{"editor": "http://app-frontend:80"}',
        locales="",
        layouts="",
        guide_mode="",
        no_asserts="",
        screenshots="",
        **overrides,
    )


# ── Rendering ────────────────────────────────────────────────────────────────


def test_no_preauth_service_when_unset(tmp_path):
    yml = _render(tmp_path)  # pre_auth_command defaults to None
    assert "steppy-preauth" not in yml
    doc = yaml.safe_load(yml)
    assert "depends_on" not in doc["services"]["steppy-worker"]


def test_preauth_service_rendered_when_set(tmp_path):
    yml = _render(tmp_path, pre_auth_command="python auth_login.py --internal")
    doc = yaml.safe_load(yml)
    svcs = doc["services"]
    assert "steppy-preauth" in svcs
    pa = svcs["steppy-preauth"]
    assert pa["entrypoint"] == ["python", "-m", "steppy", "_preauth-run"]
    # string command → wrapped as `sh -c`
    assert pa["command"] == [
        "--timeout",
        "600",
        "--",
        "sh",
        "-c",
        "python auth_login.py --internal",
    ]
    assert pa["image"] == "steppy-worker:0.3.0"


def test_preauth_gate_waits_on_healthchecked_services(tmp_path):
    yml = _render(tmp_path, pre_auth_command="do-login")
    pa = yaml.safe_load(yml)["services"]["steppy-preauth"]
    assert pa["depends_on"] == {
        "app-frontend": {"condition": "service_healthy"},
        "krakend": {"condition": "service_healthy"},
    }


def test_worker_gated_on_preauth_completion(tmp_path):
    yml = _render(tmp_path, pre_auth_command="do-login")
    worker = yaml.safe_load(yml)["services"]["steppy-worker"]
    assert worker["depends_on"] == {
        "steppy-preauth": {"condition": "service_completed_successfully"},
    }


def test_preauth_list_command_not_shell_wrapped(tmp_path):
    yml = _render(tmp_path, pre_auth_command=["python", "auth_login.py", "--internal"])
    pa = yaml.safe_load(yml)["services"]["steppy-preauth"]
    assert pa["command"] == ["--timeout", "600", "--", "python", "auth_login.py", "--internal"]


def test_preauth_custom_image_and_timeout(tmp_path):
    yml = _render(
        tmp_path,
        pre_auth_command="do-login",
        pre_auth_image="my/custom:tag",
        pre_auth_timeout=1200,
    )
    pa = yaml.safe_load(yml)["services"]["steppy-preauth"]
    assert pa["image"] == "my/custom:tag"
    assert pa["command"][:3] == ["--timeout", "1200", "--"]


def test_preauth_no_depends_when_no_healthchecks(tmp_path):
    intro = ComposeIntrospection(services_with_ports=[], networks=["default"], services_with_healthcheck=[])
    yml = _render(tmp_path, introspection=intro, pre_auth_command="do-login")
    pa = yaml.safe_load(yml)["services"]["steppy-preauth"]
    assert "depends_on" not in pa  # nothing to gate on


def test_preauth_receives_base_url_override_and_coord_dir(tmp_path):
    yml = _render(tmp_path, pre_auth_command="do-login")
    env = yaml.safe_load(yml)["services"]["steppy-preauth"]["environment"]
    assert env["STEPPY_BASE_URL_OVERRIDE"] == '{"editor": "http://app-frontend:80"}'
    assert env["STEPPY_COORD_DIR"] == "/steppy/coord"


def test_preauth_mounts_project_and_auth(tmp_path):
    yml = _render(tmp_path, pre_auth_command="do-login")
    vols = yaml.safe_load(yml)["services"]["steppy-preauth"]["volumes"]
    joined = "\n".join(vols)
    assert "/steppy/project:ro" in joined
    assert "/steppy/project/.auth" in joined
    assert "/steppy/coord" in joined


# ── Lock-wrapper (run_preauth) ───────────────────────────────────────────────


def _ok_cmd():
    return [sys.executable, "-c", "print('login ok')"]


def _fail_cmd(code=3):
    return [sys.executable, "-c", f"raise SystemExit({code})"]


def test_winner_runs_command_and_marks_done(tmp_path):
    rc = run_preauth(_ok_cmd(), timeout=10, coord_dir=tmp_path)
    assert rc == 0
    assert (tmp_path / DONE_NAME).exists()


def test_short_circuits_on_existing_done(tmp_path):
    (tmp_path / DONE_NAME).write_text("ok")
    # command would fail if run — proves it's not run
    rc = run_preauth(_fail_cmd(), timeout=10, coord_dir=tmp_path)
    assert rc == 0


def test_winner_failure_marks_failed_and_returns_code(tmp_path):
    rc = run_preauth(_fail_cmd(3), timeout=10, coord_dir=tmp_path)
    assert rc == 3
    assert (tmp_path / FAILED_NAME).exists()


def test_loser_returns_zero_when_winner_done(tmp_path):
    # Simulate another stack holding the lock and having succeeded.
    (tmp_path / LOCK_NAME).write_text("999")
    (tmp_path / DONE_NAME).write_text("ok")
    rc = run_preauth(_fail_cmd(), timeout=10, coord_dir=tmp_path)
    assert rc == 0


def test_loser_returns_one_when_winner_failed(tmp_path):
    (tmp_path / LOCK_NAME).write_text("999")
    (tmp_path / FAILED_NAME).write_text("2")
    rc = run_preauth(_ok_cmd(), timeout=10, coord_dir=tmp_path)
    assert rc == 1


def test_loser_times_out_without_sentinel(tmp_path):
    # Lock held, but no done/failed ever appears → the loser gives up.
    (tmp_path / LOCK_NAME).write_text("999")
    rc = run_preauth(_ok_cmd(), timeout=1, coord_dir=tmp_path)
    assert rc == 1


def test_empty_command_returns_two(tmp_path):
    assert run_preauth([], timeout=5, coord_dir=tmp_path) == 2


def test_default_coord_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STEPPY_COORD_DIR", str(tmp_path))
    rc = run_preauth(_ok_cmd(), timeout=10)
    assert rc == 0
    assert (tmp_path / DONE_NAME).exists()
