"""Tests for FLAWS §3.4 — `capture from.type=shell` command execution.

Shell capture runs `subprocess.run(..., shell=True)` with values that may be
scraped from the page (DOM) or an HTTP response. Two mitigations:
  1. It is opt-in (`security.allow_shell_capture`, default false).
  2. Interpolated `{{var}}` values are `shlex.quote`d so page/HTTP content can't
     inject shell metacharacters.

The runtime helpers live in `conftest.py.j2`; their bodies are plain Python
(inside a `{% raw %}` block that preserves the `{{ }}` placeholder markers), so
we extract and exec them to unit-test the behavior.
"""

import shlex
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


def _extract(start_marker: str, end_marker: str) -> str:
    text = _CONFTEST.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _load_namespace(config: dict) -> dict:
    """Exec _interp/_interp_shell + capture with an injectable CONFIG global."""
    import time

    ns: dict = {"time": time, "CONFIG": config}
    interp_src = _extract("def _interp(s, _vars):", "{% endraw %}")
    exec(compile(interp_src, "<interp>", "exec"), ns)
    capture_src = _extract("def capture(spec:", "def capture_dom(")
    exec(compile(capture_src, "<capture>", "exec"), ns)
    return ns


# ── _interp_shell quoting ────────────────────────────────────────────────────


def test_interp_shell_quotes_substituted_values():
    ns = _load_namespace({})
    interp_shell = ns["_interp_shell"]
    hostile = "; touch pwned"
    out = interp_shell("echo {{v}}", {"v": hostile})
    assert out == "echo " + shlex.quote(hostile)
    # The raw metacharacter is no longer bare in the command.
    assert "; touch pwned" not in out.replace(shlex.quote(hostile), "")


def test_interp_shell_noop_without_braces():
    ns = _load_namespace({})
    assert ns["_interp_shell"]("echo static", {"v": "x"}) == "echo static"


# ── opt-in gate ──────────────────────────────────────────────────────────────


def test_shell_capture_disabled_by_default():
    ns = _load_namespace({})  # no security block
    with pytest.raises(RuntimeError, match="allow_shell_capture"):
        ns["capture"]({"type": "shell", "command": "echo hi"}, {})


def test_shell_capture_disabled_when_flag_false():
    ns = _load_namespace({"security": {"allow_shell_capture": False}})
    with pytest.raises(RuntimeError, match="allow_shell_capture"):
        ns["capture"]({"type": "shell", "command": "echo hi"}, {})


def test_shell_capture_runs_when_enabled():
    ns = _load_namespace({"security": {"allow_shell_capture": True}})
    # `echo hello` behaves the same on cmd.exe and POSIX sh.
    assert ns["capture"]({"type": "shell", "command": "echo hello"}, {}) == "hello"
