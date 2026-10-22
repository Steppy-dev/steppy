"""Tests for FLAWS §3.3 — string-built JS injection in auth.py.

localStorage injection must pass key/value as `page.evaluate` ARGUMENTS, never
interpolate them into the JS source, so a value containing a quote or backslash
can't break out of or inject into the script.
"""

from pathlib import Path

from steppy.auth import authenticate_bypass
from steppy.models import (
    AppAuthConfig,
    AppConfig,
    BypassConfig,
    SteppyConfig,
)

_AUTH_SRC = Path(__file__).resolve().parents[1] / "src" / "steppy" / "auth.py"

# A value engineered to break naive f-string interpolation.
_HOSTILE_VALUE = 'va"lue\\ "); alert(1); //'


class _FakePage:
    def __init__(self):
        self.evaluate_calls = []

    def goto(self, *a, **k):
        pass

    def evaluate(self, js, arg=None):
        self.evaluate_calls.append((js, arg))

    def close(self):
        pass


class _FakeContext:
    def __init__(self):
        self.page = _FakePage()
        self.cookies = []

    def new_page(self):
        return self.page

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)


class _FakeBrowser:
    def __init__(self):
        self.context = _FakeContext()

    def new_context(self, **kwargs):
        return self.context


def _config_with_bypass(local_storage):
    app = AppConfig(
        name="Editor",
        url="http://localhost:4200",
        auth=AppAuthConfig(mode="bypass", bypass=BypassConfig(local_storage=local_storage)),
    )
    return app, SteppyConfig(apps={"editor": app})


def test_bypass_passes_value_as_argument_not_interpolated():
    app, config = _config_with_bypass({"token": _HOSTILE_VALUE})
    browser = _FakeBrowser()

    authenticate_bypass(browser, app, config)

    calls = browser.context.page.evaluate_calls
    assert len(calls) == 1
    js, arg = calls[0]
    # The hostile value must NOT appear inside the JS source string...
    assert _HOSTILE_VALUE not in js
    # ...it must be passed as the argument, verbatim.
    assert arg == ["token", _HOSTILE_VALUE]
    # JS references the args, not interpolated literals.
    assert "localStorage.setItem(k, v)" in js


def test_bypass_multiple_keys():
    app, config = _config_with_bypass({"a": "1", "b": '2"'})
    browser = _FakeBrowser()
    authenticate_bypass(browser, app, config)
    args = [arg for _, arg in browser.context.page.evaluate_calls]
    assert ["a", "1"] in args
    assert ["b", '2"'] in args


def test_auth_source_has_no_fstring_localstorage_sink():
    """Guard both sinks (bypass + token) against regressing to f-string interpolation."""
    src = _AUTH_SRC.read_text(encoding="utf-8")
    assert "f'localStorage.setItem(\"" not in src
    assert 'f"localStorage.setItem(' not in src
    # Both sinks use the safe arg-passing form.
    assert src.count("([k, v]) => localStorage.setItem(k, v)") == 2
