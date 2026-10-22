"""Tests for FLAWS §5 — Robustness fixes.

Covers:
- §5.1 seed subprocess timeout (SeedConfig field + generated fixture)
- §5.2 build.py JS-safe config path (json.dumps, not raw single-quote splice)
- §5.3 pytest-xdist passthrough is rejected (mis-files screenshots otherwise)
- §5.4 auth.py credentials null-check + urlparse-based cookie domain
"""

from pathlib import Path
from textwrap import dedent

import pytest
import typer

import steppy.auth as auth
from steppy._cli_common import reject_xdist_args
from steppy.auth import AuthenticationError, authenticate_auth0, authenticate_token
from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.models import (
    AppAuthConfig,
    AppConfig,
    Auth0Config,
    GlobalAuthConfig,
    SeedConfig,
    SteppyConfig,
    TokenConfig,
)
from steppy.parser import parse_scenario_file

_BUILD_SRC = Path(__file__).resolve().parents[1] / "src" / "steppy" / "build.py"


# ── §5.1 seed timeout ────────────────────────────────────────────────────────


def test_seedconfig_timeout_default_and_bounds():
    assert SeedConfig(script="base").timeout == 120
    assert SeedConfig(script="base", timeout=5).timeout == 5
    with pytest.raises(Exception):  # ge=1
        SeedConfig(script="base", timeout=0)


def _generate_with_seed(tmp_path, seed_block: str) -> str:
    content = f"---\ntitle: Seed timeout\nauth: false\n{seed_block}\n---\n\n" + dedent(
        """
            ### 1. Click
            - role: editor
            - action: click
            - selector: .go
            """
    )
    path = tmp_path / "scenario.md"
    path.write_text(content, encoding="utf-8")
    scenario = parse_scenario_file(path)
    return SteppyGenerator(load_config()).generate_test_file(scenario)


def test_generated_seed_fixture_has_default_timeout(tmp_path):
    src = _generate_with_seed(tmp_path, "seed: base")
    assert "timeout=120" in src
    assert "subprocess.TimeoutExpired" in src
    assert "Seed script timed out after 120s" in src


def test_generated_seed_fixture_honors_custom_timeout(tmp_path):
    src = _generate_with_seed(tmp_path, "seed:\n  script: base\n  timeout: 7")
    assert "timeout=7" in src
    assert "Seed script timed out after 7s" in src


# ── §5.2 build.py JS-safe path ───────────────────────────────────────────────


def test_build_uses_json_dumps_for_config_path():
    """The config path must be JSON-encoded into the JS import (no raw quote splice)."""
    src = _BUILD_SRC.read_text(encoding="utf-8")
    assert "import config from {json.dumps(resolved)}" in src
    # The old unescaped single-quote form must be gone.
    assert "import config from '{resolved}'" not in src


# ── §5.3 reject xdist passthrough ────────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        ["-n", "4"],
        ["-n4"],
        ["--numprocesses", "auto"],
        ["--numprocesses=2"],
        ["--dist", "loadscope"],
        ["-p", "xdist"],
        ["-p", "pytest_xdist"],
        ["--tx", "3*popen"],
        ["-v", "-n", "2"],  # xdist flag not first
    ],
)
def test_reject_xdist_args_blocks(args):
    with pytest.raises(typer.Exit) as exc:
        reject_xdist_args(args)
    assert exc.value.exit_code == 1


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["-v"],
        ["-k", "login"],
        ["--maxfail=1", "-x"],
        ["-p", "no:cacheprovider"],  # -p without xdist is fine
    ],
)
def test_reject_xdist_args_allows_normal(args):
    reject_xdist_args(args)  # must not raise


# ── §5.4 auth robustness ─────────────────────────────────────────────────────


class _FakePage:
    def goto(self, *a, **k):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass

    def click(self, *a, **k):
        pass

    def close(self):
        pass


class _FakeContext:
    def __init__(self):
        self.cookies = []

    def new_page(self):
        return _FakePage()

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)


class _FakeBrowser:
    def __init__(self):
        self.context = _FakeContext()

    def new_context(self, **kwargs):
        return self.context


def _auth0_config(tmp_path, credentials):
    app = AppConfig(
        name="Editor",
        url="http://localhost:4200",
        auth=AppAuthConfig(mode="auth0", credentials=credentials),
    )
    config = SteppyConfig(
        apps={"editor": app},
        auth=GlobalAuthConfig(storage_state_dir=str(tmp_path / ".auth"), auth0=Auth0Config()),
    )
    return app, config


def test_auth0_missing_credentials_raises_clear_error(tmp_path):
    app, config = _auth0_config(tmp_path, credentials=None)
    with pytest.raises(AuthenticationError) as exc:
        authenticate_auth0(_FakeBrowser(), "editor", app, config)
    assert "credentials" in str(exc.value)
    assert "editor" in str(exc.value)


def _token_config(url):
    app = AppConfig(
        name="Editor",
        url=url,
        auth=AppAuthConfig(
            mode="token",
            token=TokenConfig(endpoint="http://token.local/get", inject_as="cookie", key="jwt"),
        ),
    )
    return app, SteppyConfig(apps={"editor": app})


class _FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"token": "abc123"}


def test_token_cookie_domain_from_url_with_port(monkeypatch):
    monkeypatch.setattr(auth.httpx, "request", lambda **k: _FakeResp())
    app, config = _token_config("https://app.example.com:8443")
    browser = _FakeBrowser()
    authenticate_token(browser, "editor", app, config)
    assert browser.context.cookies[0]["domain"] == "app.example.com"


def test_token_cookie_domain_url_without_scheme_raises(monkeypatch):
    monkeypatch.setattr(auth.httpx, "request", lambda **k: _FakeResp())
    app, config = _token_config("localhost:4200")  # no "://"
    with pytest.raises(AuthenticationError) as exc:
        authenticate_token(_FakeBrowser(), "editor", app, config)
    assert "cookie domain" in str(exc.value)
