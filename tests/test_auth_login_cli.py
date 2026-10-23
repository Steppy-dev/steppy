"""``steppy auth login``: no terminal, ``--auto``, and checking the login worked.

The command used to open a headed browser and block on ``input()``. Without a
terminal (CI, a script, an agent) that ended in ``Login failed: EOF when
reading a line``; with one, it saved whatever session existed when Enter was
pressed, logged in or not.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest
from typer.testing import CliRunner

import steppy.auth as auth_mod
from steppy.cli import app
from steppy.models import AppAuthConfig, AppConfig, FormAuthConfig, GlobalAuthConfig, SteppyConfig

runner = CliRunner()

_CONFIG = """\
apps:
  editor:
    name: Editor
    url: http://app.test
    auth:
      mode: form
      credentials:
        username: "${STEPPY_TEST_USER:-u}"
        password: "${STEPPY_TEST_PASSWORD:-p}"
  viewer:
    name: Viewer
    url: http://app.test
    auth:
      mode: storage-state
      storage_state_path: viewer.json
  nocreds:
    name: No credentials
    url: http://app.test
    auth:
      mode: form
auth:
  storage_state_dir: "./.auth"
"""


def _out(result) -> str:
    """The output with Rich's 80-column line wrapping undone."""
    return " ".join(result.output.split())


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(_CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class _FakePlaywright:
    """Stands in for ``sync_playwright()``; records how Chromium was launched."""

    launches: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def chromium(self):
        return self

    def launch(self, headless):
        self.launches.append(headless)
        return _Closable()


class _Closable:
    def close(self):
        pass


@pytest.fixture
def fake_playwright(monkeypatch):
    _FakePlaywright.launches = []
    monkeypatch.setattr("playwright.sync_api.sync_playwright", _FakePlaywright)
    return _FakePlaywright


@pytest.fixture
def no_browser(monkeypatch):
    def _refuse():
        raise AssertionError("no browser should be launched")

    monkeypatch.setattr("playwright.sync_api.sync_playwright", _refuse)


# ── No terminal ─────────────────────────────────────────────────────────────


def test_without_a_terminal_it_fails_before_launching_a_browser(project, no_browser):
    # CliRunner's stdin is not a TTY, which is exactly the CI/agent case.
    result = runner.invoke(app, ["auth", "login", "editor"])
    assert result.exit_code == 1
    assert "no terminal" in _out(result)
    assert "steppy auth login editor --auto" in _out(result)
    assert "EOF" not in _out(result)


# ── --auto ──────────────────────────────────────────────────────────────────


def test_auto_logs_in_headless_with_a_fresh_login(project, fake_playwright, monkeypatch):
    calls = []

    def _authenticate(browser, role, app_config, config, *, use_cache=True):
        calls.append((role, use_cache))
        return _Closable()

    monkeypatch.setattr(auth_mod, "authenticate_form", _authenticate)
    result = runner.invoke(app, ["auth", "login", "editor", "--auto"])
    assert result.exit_code == 0, result.output
    assert calls == [("editor", False)]
    assert fake_playwright.launches == [True]
    assert "Authentication saved for editor" in _out(result)
    assert "editor.json" in _out(result)


def test_auto_refuses_a_role_that_is_not_form_login(project, no_browser):
    result = runner.invoke(app, ["auth", "login", "viewer", "--auto"])
    assert result.exit_code == 1
    assert "auth mode 'storage-state'" in _out(result)


def test_auto_refuses_a_role_without_credentials(project, no_browser):
    result = runner.invoke(app, ["auth", "login", "nocreds", "--auto"])
    assert result.exit_code == 1
    assert "apps.nocreds.auth.credentials" in _out(result)


def test_auto_reports_a_failed_login(project, fake_playwright, monkeypatch):
    def _fail(*args, **kwargs):
        raise auth_mod.AuthenticationError("Form authentication failed for editor: Timeout")

    monkeypatch.setattr(auth_mod, "authenticate_form", _fail)
    result = runner.invoke(app, ["auth", "login", "editor", "--auto"])
    assert result.exit_code == 1
    assert "Login failed" in _out(result)


# ── Has the page left the login? ────────────────────────────────────────────


class _Page:
    def __init__(self, url, visible=()):
        self.url = url
        self.visible = set(visible)

    def locator(self, selector):
        page = self

        class _Loc:
            first = None

            def is_visible(self):
                return selector in page.visible

        loc = _Loc()
        loc.first = loc
        return loc

    def goto(self, url, timeout=None):
        pass

    def close(self):
        pass


_APP = AppConfig(name="Editor", url="http://app.test:4200", auth=AppAuthConfig(mode="form"))
_FORM = FormAuthConfig(username_selector="#username")


def test_a_page_on_the_providers_origin_is_still_the_login():
    reason = auth_mod._still_on_login_page(_Page("https://idp.test/login"), _APP, _FORM)
    assert reason and "idp.test" in reason


def test_a_visible_username_field_is_still_the_login():
    """A provider on the app's own origin (Keycloak behind the same proxy)."""
    page = _Page("http://app.test:4200/keycloak/auth", visible=("#username",))
    assert auth_mod._still_on_login_page(page, _APP, _FORM) == "the login form is still showing"


def test_the_app_without_the_form_is_logged_in():
    assert auth_mod._still_on_login_page(_Page("http://app.test:4200/home"), _APP, _FORM) is None


def test_without_a_form_block_only_the_origin_counts():
    page = _Page("http://app.test:4200/home", visible=("input[name='username']",))
    assert auth_mod._still_on_login_page(page, _APP, None) is None


# ── The interactive loop ────────────────────────────────────────────────────


class _Browser:
    def __init__(self, urls):
        self.page = _Page(urls[0])
        self.urls = urls
        self.saved = None

    def new_context(self, **kwargs):
        browser = self

        class _Context:
            def new_page(self):
                return browser.page

            def storage_state(self, path):
                browser.saved = path
                Path(path).write_text("{}", encoding="utf-8")

            def close(self):
                pass

        return _Context()


def _config(tmp_path) -> SteppyConfig:
    return SteppyConfig(
        apps={"editor": _APP},
        auth=GlobalAuthConfig(storage_state_dir=str(tmp_path), form=_FORM),
    )


def _answers(monkeypatch, browser, script):
    """Feed ``input()`` from ``script``; each entry is (answer, url after it)."""
    script = list(script)

    def _input(prompt=""):
        if not script:
            raise EOFError
        answer, url = script.pop(0)
        browser.page.url = url
        return answer

    monkeypatch.setattr(builtins, "input", _input)


def test_enter_on_the_login_page_does_not_save(tmp_path, monkeypatch, capsys):
    browser = _Browser(["https://idp.test/login"])
    _answers(monkeypatch, browser, [("", "https://idp.test/login"), ("", "http://app.test:4200/home")])
    auth_mod.interactive_login("editor", _config(tmp_path), browser)
    assert "Not saved yet" in capsys.readouterr().out
    assert browser.saved == str(tmp_path / "editor.json")


def test_save_overrides_the_check(tmp_path, monkeypatch):
    browser = _Browser(["https://idp.test/login"])
    _answers(monkeypatch, browser, [("save", "https://idp.test/login")])
    auth_mod.interactive_login("editor", _config(tmp_path), browser)
    assert browser.saved


def test_closed_input_raises_with_the_auto_hint(tmp_path, monkeypatch):
    browser = _Browser(["https://idp.test/login"])
    _answers(monkeypatch, browser, [])
    with pytest.raises(auth_mod.AuthenticationError, match="--auto"):
        auth_mod.interactive_login("editor", _config(tmp_path), browser)
    assert browser.saved is None


def test_the_misleading_comment_is_gone():
    src = (Path(auth_mod.__file__)).read_text(encoding="utf-8")
    assert "user should close the browser when done" not in src
