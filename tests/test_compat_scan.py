"""Tests for the `steppy update --check` compatibility scan (FLAWS §7.7).

The matchers are raw line scans (not the parser), because models.py normalizes
the deprecated spellings away at parse time — the original text is the only place
the deprecated form survives.
"""

import pytest
import typer

from steppy.update_cli import (
    _exit_code_for,
    _run_update,
    _scan_deprecations,
)


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_scenario_auth0_login_is_flagged(tmp_path):
    scen = _write(
        tmp_path,
        "scenarios/login.md",
        "# Login\n\n## Step 1\n- auth0-login: '#submit'\n",
    )
    hits = _scan_deprecations(tmp_path)
    action_hits = [h for h in hits if h["deprecation"].id == "action-auth0-login-to-login"]
    assert len(action_hits) == 1
    assert action_hits[0]["file"] == scen
    assert action_hits[0]["line"] == 4


def test_config_auth_mode_auth0_is_flagged(tmp_path):
    _write(
        tmp_path,
        "config.yaml",
        "apps:\n  admin:\n    url: http://localhost:3000\n    auth:\n      mode: auth0\n",
    )
    hits = _scan_deprecations(tmp_path)
    mode_hits = [h for h in hits if h["deprecation"].id == "auth-mode-auth0-to-form"]
    assert len(mode_hits) == 1
    assert mode_hits[0]["line"] == 5


def test_config_global_auth0_block_is_flagged(tmp_path):
    _write(
        tmp_path,
        "config.yaml",
        "auth:\n  auth0:\n    submit_selector: '#kc-login'\n",
    )
    hits = _scan_deprecations(tmp_path)
    block_hits = [h for h in hits if h["deprecation"].id == "auth-global-key-auth0-to-form"]
    assert len(block_hits) == 1
    assert block_hits[0]["line"] == 2


def test_clean_project_has_no_hits(tmp_path):
    _write(tmp_path, "scenarios/ok.md", "# Ok\n\n## Step 1\n- login: '#submit'\n")
    _write(
        tmp_path,
        "config.yaml",
        "apps:\n  admin:\n    auth:\n      mode: form\nauth:\n  form:\n    x: y\n",
    )
    assert _scan_deprecations(tmp_path) == []


def test_check_mode_exits_zero_when_only_info(tmp_path):
    _write(
        tmp_path,
        "scenarios/login.md",
        "# Login\n\n## Step 1\n- auth0-login: '#submit'\n",
    )
    # All live deprecations are severity 'info' → CI-clean (exit 0).
    with pytest.raises(typer.Exit) as exc:
        _run_update(tmp_path, check=True)
    assert exc.value.exit_code == 0


def test_exit_code_maps_error_severity_to_one():
    class _Dep:
        severity = "error"

    assert _exit_code_for([{"deprecation": _Dep()}]) == 1
    assert _exit_code_for([]) == 0

    class _Info:
        severity = "info"

    assert _exit_code_for([{"deprecation": _Info()}]) == 0
