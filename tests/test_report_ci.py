"""Tests for CI reporting wiring (FLAWS §6.1): JUnit/HTML/retry args + trace/video env."""

from pathlib import Path

from steppy.cli import _apply_report_args, _build_run_env
from steppy.models import ReportConfig, SteppyConfig, AppConfig, AppAuthConfig
from rich.console import Console

_CONSOLE = Console()


def _cmd_for(report: ReportConfig, tmp_path) -> list:
    cmd = ["pytest"]
    # Point relative paths at tmp so mkdir doesn't touch the repo.
    _apply_report_args(cmd, report, _CONSOLE)
    return cmd


def test_junit_arg_appended(tmp_path):
    report = ReportConfig(junit_xml=str(tmp_path / "reports" / "junit.xml"))
    cmd = _cmd_for(report, tmp_path)
    assert any(a.startswith("--junitxml=") for a in cmd)
    # Parent dir is created.
    assert (tmp_path / "reports").is_dir()


def test_no_report_no_args(tmp_path):
    cmd = _cmd_for(ReportConfig(), tmp_path)
    assert cmd == ["pytest"]


def test_html_missing_plugin_warns_not_fails(tmp_path, monkeypatch):
    """report.html without pytest-html warns and skips (never appends --html)."""
    import importlib.util as _il

    real_find = _il.find_spec

    def fake_find(name, *a, **k):
        if name == "pytest_html":
            return None
        return real_find(name, *a, **k)

    monkeypatch.setattr(_il, "find_spec", fake_find)
    cmd = ["pytest"]
    _apply_report_args(cmd, ReportConfig(html=str(tmp_path / "r.html")), _CONSOLE)
    assert not any("--html" in a for a in cmd)


def test_trace_env_set_when_on():
    env = _build_run_env(False, False, [], [], False, False, _CONSOLE, trace_mode="on-failure")
    assert env.get("STEPPY_TRACE") == "on-failure"


def test_trace_env_absent_when_off():
    env = _build_run_env(False, False, [], [], False, False, _CONSOLE, trace_mode="off")
    assert "STEPPY_TRACE" not in env


# ── Video (FLAWS §6.1) ───────────────────────────────────────────────────────


def test_video_env_set_when_on():
    env = _build_run_env(False, False, [], [], False, False, _CONSOLE, video_mode="on-failure")
    assert env.get("STEPPY_VIDEO") == "on-failure"


def test_video_env_absent_when_off():
    env = _build_run_env(False, False, [], [], False, False, _CONSOLE, video_mode="off")
    assert "STEPPY_VIDEO" not in env


# ── Flaky-retry (FLAWS §6.1) ─────────────────────────────────────────────────


def test_retries_arg_appended_when_plugin_present(monkeypatch):
    """report.retries appends --reruns when pytest-rerunfailures is importable."""
    import importlib.util as _il

    real_find = _il.find_spec

    def fake_find(name, *a, **k):
        if name == "pytest_rerunfailures":
            return object()  # pretend the plugin is installed
        return real_find(name, *a, **k)

    monkeypatch.setattr(_il, "find_spec", fake_find)
    cmd = ["pytest"]
    _apply_report_args(cmd, ReportConfig(retries=3), _CONSOLE)
    assert "--reruns=3" in cmd


def test_retries_missing_plugin_warns_not_fails(monkeypatch):
    """report.retries without pytest-rerunfailures warns and skips (no --reruns)."""
    import importlib.util as _il

    real_find = _il.find_spec

    def fake_find(name, *a, **k):
        if name == "pytest_rerunfailures":
            return None
        return real_find(name, *a, **k)

    monkeypatch.setattr(_il, "find_spec", fake_find)
    cmd = ["pytest"]
    _apply_report_args(cmd, ReportConfig(retries=3), _CONSOLE)
    assert not any("--reruns" in a for a in cmd)


def test_retries_zero_appends_nothing():
    cmd = ["pytest"]
    _apply_report_args(cmd, ReportConfig(retries=0), _CONSOLE)
    assert cmd == ["pytest"]


def test_report_config_defaults_off():
    app = AppConfig(name="A", url="http://x", auth=AppAuthConfig(mode="form"))
    cfg = SteppyConfig(apps={"a": app})
    assert cfg.report.junit_xml is None
    assert cfg.report.html is None
    assert cfg.report.trace == "off"
    assert cfg.report.video == "off"
    assert cfg.report.retries == 0


def test_conftest_template_has_trace_helpers():
    src = (Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2").read_text(encoding="utf-8")
    assert "_maybe_start_trace" in src
    assert "_maybe_stop_trace" in src
    assert "TRACE_MODE = os.environ.get" in src


def test_conftest_template_has_video_helpers():
    src = (Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2").read_text(encoding="utf-8")
    assert "_video_context_kwargs" in src
    assert "_maybe_save_video" in src
    assert "VIDEO_MODE = os.environ.get" in src


def test_every_browser_context_goes_through_the_shared_builder():
    """Every context must get the video kwargs — and viewport, DSF, locale,
    timezone and the insecure-origin polyfill with them.

    This used to be spelled ``src.count("**_video_context_kwargs()") == 6``,
    counting the six hand-maintained copies of the kwargs block. All six now
    call ``_new_role_context``, so the property is structural: there is exactly
    one ``new_context(`` in the template and it is the one inside the builder.
    That makes the invariant hold for a seventh call site automatically,
    instead of requiring this number to be bumped.
    """
    src = (Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2").read_text(encoding="utf-8")

    assert src.count("_new_role_context(") > 1, "call sites should route through the shared builder"
    assert src.count(".new_context(") == 1, (
        "a raw browser.new_context(...) was reintroduced — build contexts via "
        "_new_role_context so they inherit viewport/DSF/locale/timezone/video/polyfill"
    )
    # …and that single call carries the video kwargs.
    builder = src[src.index("def _new_role_context(") : src.index("def _authenticate_auth0(")]
    assert "**_video_context_kwargs()" in builder
    assert "_apply_insecure_origin_polyfill(context)" in builder
