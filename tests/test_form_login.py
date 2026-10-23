"""Form login: the post-submit wait, the post-login URL, and the app's own login page.

Three things kept automatic form login from working on an app with a live
connection and its own login page in front of the provider:

- every login path waited for ``networkidle`` after submitting, which a page
  that holds an EventSource or a WebSocket open never reaches;
- ``auth.form.post_login_url_pattern`` was parsed and never read, and the
  ``<app url>/**`` fallback already matches a provider served on the app's own
  origin (Keycloak behind the same proxy), so the wait returned before the
  login had finished;
- login always started at ``/`` and waited for the username field, which never
  shows on an app whose own page has a "Log in" button in front of the provider.

Both code paths are covered: the generated conftest (exec'd out of the canonical
``templates/conftest.py.j2`` on fake pages) and ``steppy.auth``. The real-browser
cases, against a stdlib server whose home page keeps an EventSource open, run
behind ``STEPPY_AUTH_E2E=1`` (they need ``playwright install chromium``).
"""

from __future__ import annotations

import ast
import base64
import functools
import hashlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from steppy import auth as auth_mod
from steppy.config import _check_post_login_placeholder
from steppy.generator import SteppyGenerator
from steppy.presets import IDP_PRESETS
from steppy.models import (
    AppAuthConfig,
    AppConfig,
    AuthCredentials,
    FormAuthConfig,
    GlobalAuthConfig,
    SteppyConfig,
    TimingConfig,
)

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"
_AUTH_SRC = Path(__file__).resolve().parents[1] / "src" / "steppy" / "auth.py"

_FORM = {
    "username_selector": "#username",
    "password_selector": "#password",
    "submit_selector": "#kc-login",
}


def _slice(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


@functools.lru_cache(maxsize=None)
def _rendered_conftest() -> str:
    """The conftest the generator writes, rendered from the canonical ``templates/``."""
    config = SteppyConfig(
        apps={"editor": AppConfig(name="Editor", url="http://app.test", auth=AppAuthConfig(mode="form"))}
    )
    return SteppyGenerator(config, project_root=_CONFTEST.parents[1]).generate_conftest([])


def _load_conftest_login(config: dict, auth_dir: Path, new_context=None) -> dict:
    """Exec the generated conftest's login helpers out of the rendered conftest.

    Three slices: the form defaults and the IdP preset table, the login helpers
    (``_totp_now`` through the ``fill_auth0_credentials`` alias), and
    ``_authenticate_auth0``. ``_new_role_context`` is stubbed — it is the one
    collaborator that needs the whole layout/video/locale machinery.
    """
    text = _rendered_conftest()
    source = "\n".join(
        (
            _slice(text, "_FORM_DEFAULTS = {", "def _get_viewport()"),
            _slice(text, "def _totp_now(", "def _new_role_context("),
            _slice(text, "def _authenticate_auth0(", "def get_authenticated_context("),
        )
    )
    ns: dict = {
        "CONFIG": config,
        "AUTH_DIR": auth_dir,
        "Page": object,
        "Browser": object,
        "BrowserContext": object,
        "base64": base64,
        "hashlib": hashlib,
        "hmac": hmac,
        "time": time,
        "_new_role_context": new_context or (lambda browser, role: browser.new_context()),
    }
    exec(compile(source, "<conftest login>", "exec"), ns)
    return ns


def _config(form: dict | None = None, navigate_wait: str | None = None) -> dict:
    cfg: dict = {
        "apps": {
            "editor": {
                "url": "http://app.test",
                "auth": {"mode": "form", "credentials": {"username": "u", "password": "p"}},
            }
        },
        "auth": {"form": {**_FORM, **(form or {})}},
    }
    if navigate_wait is not None:
        cfg["timing"] = {"navigate_wait": navigate_wait}
    return cfg


# ── Fakes ───────────────────────────────────────────────────────────────────


class _Locator:
    def __init__(self, page, selector, alternatives=()):
        self.page = page
        self.selector = selector
        self.alternatives = alternatives

    def or_(self, other):
        return _Locator(self.page, self.selector, (*self.alternatives, other.selector))

    @property
    def first(self):
        return self

    def wait_for(self, timeout=None):
        self.page.calls.append(("wait_for", (self.selector, *self.alternatives)))

    def is_visible(self):
        return self.selector in self.page.visible

    def click(self):
        self.page.calls.append(("click", self.selector))


class _Page:
    """Records every call; ``visible`` says which selectors are showing."""

    def __init__(self, visible=("#username", "#password")):
        self.visible = set(visible)
        self.calls: list[tuple] = []

    def locator(self, selector):
        return _Locator(self, selector)

    def goto(self, url, **kwargs):
        self.calls.append(("goto", url, kwargs))

    def wait_for_selector(self, selector, timeout=None):
        self.calls.append(("wait_for_selector", selector))

    def fill(self, selector, value):
        self.calls.append(("fill", selector))

    def click(self, selector):
        self.calls.append(("click", selector))

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append(("load_state", state))

    def wait_for_url(self, pattern, timeout=None):
        self.calls.append(("wait_for_url", pattern))

    def close(self):
        pass

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


class _Context:
    def __init__(self, page):
        self.page = page
        self.saved_to = None
        self.closed = False

    def new_page(self):
        return self.page

    def storage_state(self, path):
        self.saved_to = path
        Path(path).write_text("{}", encoding="utf-8")

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self, page):
        self.context = _Context(page)
        self.kwargs = None

    def new_context(self, **kwargs):
        self.kwargs = kwargs
        return self.context


# ── Generated conftest: the post-submit wait ────────────────────────────────


@pytest.mark.parametrize(
    "navigate_wait, expected",
    [(None, ["networkidle"]), ("load", ["load"]), ("domcontentloaded", ["domcontentloaded"]), ("none", [])],
)
def test_the_post_submit_wait_follows_navigate_wait(tmp_path, navigate_wait, expected):
    ns = _load_conftest_login(_config(navigate_wait=navigate_wait), tmp_path)
    page = _Page()
    ns["fill_login_credentials"](page, "editor")
    assert [c[1] for c in page.of("load_state")] == expected


def test_no_login_path_hard_codes_networkidle():
    text = _CONFTEST.read_text(encoding="utf-8")
    login = _slice(text, "def _totp_now(", "def get_authenticated_context(")
    assert 'wait_for_load_state("networkidle"' not in login
    assert 'wait_for_load_state("networkidle"' not in _AUTH_SRC.read_text(encoding="utf-8")


def test_the_totp_step_settles_the_same_way(tmp_path):
    cfg = _config(form={"totp_selector": "#otp"}, navigate_wait="none")
    cfg["apps"]["editor"]["auth"]["credentials"]["totp_secret"] = "JBSWY3DPEHPK3PXP"
    ns = _load_conftest_login(cfg, tmp_path)
    page = _Page()
    ns["fill_login_credentials"](page, "editor")
    assert ("fill", "#otp") in page.calls
    assert page.of("load_state") == []


# ── Generated conftest: the post-login URL ──────────────────────────────────


def test_the_login_step_waits_for_post_login_url_pattern_when_set(tmp_path):
    ns = _load_conftest_login(_config(form={"post_login_url_pattern": "**/home"}), tmp_path)
    page = _Page()
    ns["fill_login_credentials"](page, "editor")
    assert page.of("wait_for_url") == [("wait_for_url", "**/home")]


def test_the_login_step_waits_for_no_url_without_a_pattern(tmp_path):
    ns = _load_conftest_login(_config(), tmp_path)
    page = _Page()
    ns["fill_login_credentials"](page, "editor")
    assert page.of("wait_for_url") == []


def test_auto_login_falls_back_to_the_app_url(tmp_path):
    ns = _load_conftest_login(_config(), tmp_path)
    page = _Page()
    ns["_authenticate_auth0"](_Browser(page), "editor")
    assert page.of("wait_for_url") == [("wait_for_url", "http://app.test/**")]
    assert (tmp_path / "editor.json").is_file()


def test_auto_login_waits_for_post_login_url_pattern(tmp_path):
    ns = _load_conftest_login(_config(form={"post_login_url_pattern": "**/home"}), tmp_path)
    page = _Page()
    ns["_authenticate_auth0"](_Browser(page), "editor")
    assert {c[1] for c in page.of("wait_for_url")} == {"**/home"}


# ── Generated conftest: where login starts ──────────────────────────────────


@pytest.mark.parametrize(
    "navigate_wait, kwargs",
    [
        (None, {}),
        ("load", {}),
        ("domcontentloaded", {"wait_until": "domcontentloaded"}),
        ("none", {"wait_until": "commit"}),
    ],
)
def test_auto_login_starts_at_login_path(tmp_path, navigate_wait, kwargs):
    ns = _load_conftest_login(_config(form={"login_path": "/auth"}, navigate_wait=navigate_wait), tmp_path)
    page = _Page()
    ns["_authenticate_auth0"](_Browser(page), "editor")
    assert page.of("goto") == [("goto", "/auth", kwargs)]


def test_auto_login_starts_at_the_root_by_default(tmp_path):
    ns = _load_conftest_login(_config(), tmp_path)
    page = _Page()
    ns["_authenticate_auth0"](_Browser(page), "editor")
    assert page.of("goto") == [("goto", "/", {})]


def test_start_selector_is_clicked_when_the_apps_own_page_shows(tmp_path):
    ns = _load_conftest_login(_config(form={"start_selector": "#start-login"}), tmp_path)
    page = _Page(visible=("#start-login", "#password"))
    ns["fill_login_credentials"](page, "editor")
    assert ("wait_for", ("#username", "#start-login")) in page.calls
    assert ("click", "#start-login") in page.calls
    # The click comes before the username is filled.
    assert page.calls.index(("click", "#start-login")) < page.calls.index(("fill", "#username"))


def test_start_selector_is_not_clicked_when_the_provider_form_is_up(tmp_path):
    ns = _load_conftest_login(_config(form={"start_selector": "#start-login"}), tmp_path)
    page = _Page()
    ns["fill_login_credentials"](page, "editor")
    assert ("click", "#start-login") not in page.calls


def test_without_start_selector_nothing_extra_happens(tmp_path):
    ns = _load_conftest_login(_config(), tmp_path)
    page = _Page()
    ns["fill_login_credentials"](page, "editor")
    assert page.of("wait_for") == []
    assert page.calls[0] == ("wait_for_selector", "#username")


# ── Generated conftest: IdP presets ─────────────────────────────────────────
#
# The conftest reads config.yaml raw, so `preset: keycloak` used to reach it as
# one more key: every selector stayed on the Auth0 default, and the login waited
# 15 s for an `input[name='username']` that Keycloak never renders.


def _selectors(form: dict, key: str = "form") -> dict:
    ns = _load_conftest_login({"auth": {key: form}}, Path("."))
    return ns["_form_selectors"]()


def test_a_preset_alone_fills_the_selectors():
    selectors = _selectors({"preset": "keycloak"})
    assert selectors["username_selector"] == "#username"
    assert selectors["password_selector"] == "#password"
    assert selectors["submit_selector"] == "#kc-login"
    assert selectors["totp_selector"] == "#otp"
    assert "preset" not in selectors


def test_an_explicit_selector_wins_over_the_preset():
    selectors = _selectors({"preset": "keycloak", "username_selector": "#email", "totp_selector": None})
    assert selectors["username_selector"] == "#email"
    assert selectors["password_selector"] == "#password"
    # An explicit null is explicit too, as it is for FormAuthConfig.
    assert selectors["totp_selector"] is None


def test_the_legacy_auth0_key_takes_a_preset_too():
    assert _selectors({"preset": "keycloak"}, key="auth0")["submit_selector"] == "#kc-login"


def test_without_a_preset_the_defaults_are_unchanged():
    assert _selectors({"username_selector": "#u"}) == {
        "username_selector": "#u",
        "password_selector": "input[name='password']",
        "submit_selector": "button[data-action-button-primary='true']",
    }


def test_an_unknown_preset_says_which_exist():
    with pytest.raises(ValueError, match="keycloak"):
        _selectors({"preset": "okta"})


def test_the_rendered_table_is_the_registry():
    """Rendered from IDP_PRESETS at generate time: a preset added there reaches
    generated suites with no second copy to keep in step."""
    tree = ast.parse(_rendered_conftest())
    (table,) = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "_IDP_FORM_PRESETS"
    ]
    assert ast.literal_eval(table) == {name: kit["form"] for name, kit in IDP_PRESETS.items()}


def test_a_preset_login_fills_the_presets_fields(tmp_path):
    cfg = _config()
    cfg["auth"]["form"] = {"preset": "keycloak"}
    ns = _load_conftest_login(cfg, tmp_path)
    page = _Page()
    ns["fill_login_credentials"](page, "editor")
    assert ("fill", "#username") in page.calls
    assert ("fill", "#password") in page.calls


# ── steppy.auth ─────────────────────────────────────────────────────────────


def _steppy_config(tmp_path, navigate_wait="networkidle", **form) -> SteppyConfig:
    app = AppConfig(
        name="Editor",
        url="http://app.test",
        auth=AppAuthConfig(mode="form", credentials=AuthCredentials(username="u", password="p")),
    )
    return SteppyConfig(
        apps={"editor": app},
        auth=GlobalAuthConfig(storage_state_dir=str(tmp_path), form=FormAuthConfig(**_FORM, **form)),
        timing=TimingConfig(navigate_wait=navigate_wait),
    )


def test_library_login_honours_every_form_setting(tmp_path):
    config = _steppy_config(
        tmp_path,
        navigate_wait="domcontentloaded",
        login_path="/auth",
        start_selector="#start-login",
        post_login_url_pattern="**/home",
    )
    page = _Page(visible=("#start-login",))
    browser = _Browser(page)
    auth_mod.authenticate_form(browser, "editor", config.apps["editor"], config)
    assert page.of("goto") == [("goto", "/auth", {"timeout": 30000, "wait_until": "domcontentloaded"})]
    assert ("click", "#start-login") in page.calls
    assert page.of("wait_for_url") == [("wait_for_url", "**/home")]
    assert page.of("load_state") == [("load_state", "domcontentloaded")]
    assert Path(browser.context.saved_to).is_file()


def test_library_login_skips_the_settle_for_none(tmp_path):
    config = _steppy_config(tmp_path, navigate_wait="none")
    page = _Page()
    auth_mod.authenticate_form(_Browser(page), "editor", config.apps["editor"], config)
    assert page.of("goto") == [("goto", "/", {"timeout": 30000, "wait_until": "commit"})]
    assert page.of("wait_for_url") == [("wait_for_url", "http://app.test/**")]
    assert page.of("load_state") == []


def test_use_cache_false_logs_in_despite_a_fresh_cache(tmp_path):
    config = _steppy_config(tmp_path)
    (tmp_path / "editor.json").write_text("{}", encoding="utf-8")

    cached = _Page()
    auth_mod.authenticate_form(_Browser(cached), "editor", config.apps["editor"], config)
    assert cached.calls == []  # served from the cache

    fresh = _Page()
    auth_mod.authenticate_form(_Browser(fresh), "editor", config.apps["editor"], config, use_cache=False)
    assert ("fill", "#username") in fresh.calls


# ── The old scaffold placeholder ────────────────────────────────────────────


@pytest.mark.parametrize("key", ["form", "auth0"])
def test_the_old_scaffold_placeholder_warns(key):
    warnings: list = []
    _check_post_login_placeholder({"auth": {key: {"post_login_url_pattern": "**/callback**"}}}, warnings)
    assert len(warnings) == 1
    assert f"auth.{key}.post_login_url_pattern" in warnings[0]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"auth": None},
        {"auth": {"form": None}},
        {"auth": {"form": {}}},
        {"auth": {"form": {"post_login_url_pattern": "**/home**"}}},
    ],
)
def test_any_other_value_is_silent(raw):
    warnings: list = []
    _check_post_login_placeholder(raw, warnings)
    assert warnings == []


def test_the_scaffold_no_longer_writes_the_placeholder():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "templates" / "init" / "config.yaml", root / "config.yaml"):
        warnings: list = []
        import yaml

        _check_post_login_placeholder(yaml.safe_load(path.read_text(encoding="utf-8")), warnings)
        assert warnings == [], path


# ── Real Chromium ───────────────────────────────────────────────────────────
#
# The app that prompted this, in miniature: `/` sends you to the app's own login page,
# whose button leads to a provider on the same origin; logging in lands on a
# home page that keeps an EventSource open, so the network never goes idle.

_e2e = pytest.mark.skipif(
    os.environ.get("STEPPY_AUTH_E2E", "") in ("", "0"),
    reason="set STEPPY_AUTH_E2E=1 to log in through headless Chromium",
)

_PAGES = {
    "/auth": '<button id="start-login" onclick="location.href=\'/idp/login\'">Log in</button>',
    "/idp/login": (
        '<form method="post" action="/idp/login">'
        '<input id="username" name="username"><input id="password" name="password" type="password">'
        '<button id="kc-login" type="submit">Sign in</button></form>'
    ),
    "/home": "<h1>Home</h1><script>new EventSource('/events')</script>",
}


class _App(BaseHTTPRequestHandler):
    stop: threading.Event

    def log_message(self, *args):
        pass

    def _redirect(self, to, cookie=None):
        self.send_response(302)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def do_GET(self):
        if self.path == "/":
            return self._redirect("/auth")
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while not self.stop.is_set():
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    self.stop.wait(0.3)
            except OSError:
                pass  # the browser went away
            return
        body = _PAGES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = f"<!doctype html><html><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._redirect("/home", cookie="session=ok; Path=/")


@pytest.fixture
def live_app():
    stop = threading.Event()
    handler = type("_LiveApp", (_App,), {"stop": stop})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


@pytest.fixture
def chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def _live_config(url: str, navigate_wait: str) -> dict:
    cfg = _config(navigate_wait=navigate_wait)
    cfg["apps"]["editor"]["url"] = url
    # The provider's form is Keycloak's, so the preset alone names its fields.
    cfg["auth"]["form"] = {
        "preset": "keycloak",
        "login_path": "/auth",
        "start_selector": "#start-login",
        "post_login_url_pattern": "**/home",
    }
    return cfg


def _session_cookie(state_file: Path) -> list:
    return [c for c in json.loads(state_file.read_text(encoding="utf-8"))["cookies"] if c["name"] == "session"]


@_e2e
def test_generated_conftest_logs_in_past_the_apps_page_with_a_live_connection(tmp_path, live_app, chromium):
    ns = _load_conftest_login(
        _live_config(live_app, "domcontentloaded"),
        tmp_path,
        new_context=lambda browser, role: browser.new_context(base_url=live_app),
    )
    context = ns["_authenticate_auth0"](chromium, "editor")
    try:
        assert _session_cookie(tmp_path / "editor.json")
    finally:
        context.close()


@_e2e
def test_networkidle_never_comes_on_that_page(tmp_path, live_app, chromium):
    """The control: the same login under ``networkidle`` times out, which is
    what proves the page above really holds the network busy."""
    ns = _load_conftest_login(
        _live_config(live_app, "networkidle"),
        tmp_path,
        new_context=lambda browser, role: browser.new_context(base_url=live_app),
    )
    with pytest.raises(Exception, match="Timeout"):
        ns["_authenticate_auth0"](chromium, "editor")


@_e2e
def test_library_login_does_the_same(tmp_path, live_app, chromium):
    config = _steppy_config(
        tmp_path,
        navigate_wait="domcontentloaded",
        login_path="/auth",
        start_selector="#start-login",
        post_login_url_pattern="**/home",
    )
    config.apps["editor"].url = live_app
    context = auth_mod.authenticate_form(chromium, "editor", config.apps["editor"], config, use_cache=False)
    try:
        assert _session_cookie(tmp_path / "editor.json")
    finally:
        context.close()
