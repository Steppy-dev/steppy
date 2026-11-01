"""`auth.mode: bypass` writes `auth.bypass.local_storage` in the generated suite.

`steppy.auth.authenticate_bypass` always handled `local_storage`, but the
generated conftest's `get_authenticated_context` only added the cookie, so a
project authenticating through a localStorage token ran every test logged out.

The helpers are exec'd out of the rendered conftest on a fake context; the
script itself is run under Node with a fake `window` when Node is installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from steppy.generator import SteppyGenerator
from steppy.models import AppAuthConfig, AppConfig, SteppyConfig

_ROOT = Path(__file__).resolve().parents[1]


def _rendered_conftest() -> str:
    config = SteppyConfig(
        apps={"editor": AppConfig(name="Editor", url="http://app.test", auth=AppAuthConfig(mode="form"))}
    )
    return SteppyGenerator(config, project_root=_ROOT).generate_conftest([])


def _slice(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


class _FakeContext:
    def __init__(self):
        self.cookies: list = []
        self.init_scripts: list[str] = []

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    def add_init_script(self, script):
        self.init_scripts.append(script)


def _load(bypass: dict, url: str = "http://app.test:4200/app") -> tuple[dict, _FakeContext]:
    src = _rendered_conftest()
    context = _FakeContext()
    ns: dict = {
        "json": json,
        "Browser": object,
        "BrowserContext": object,
        "CONFIG": {"apps": {"editor": {"url": url, "auth": {"mode": "bypass", "bypass": bypass}}}},
        "_new_role_context": lambda browser, role, **extra: context,
    }
    code = _slice(src, "def get_authenticated_context(", "def _authenticate_token(")
    exec(compile(code, "<conftest>", "exec"), ns)
    return ns, context


def test_bypass_local_storage_becomes_an_init_script():
    ns, context = _load({"local_storage": {"auth_token": "abc", "tenant": "t1"}})
    assert ns["get_authenticated_context"](None, "editor") is context
    assert len(context.init_scripts) == 1
    script = context.init_scripts[0]
    assert '"auth_token", "abc"' in script
    assert '"tenant", "t1"' in script
    # Written on the app's origin only, never on an IdP page.
    assert 'window.location.origin === "http://app.test:4200"' in script


def test_bypass_cookie_and_local_storage_together():
    cookie = {"name": "session", "value": "s", "domain": "app.test", "path": "/"}
    ns, context = _load({"cookie": cookie, "local_storage": {"k": "v"}})
    ns["get_authenticated_context"](None, "editor")
    assert context.cookies == [cookie]
    assert len(context.init_scripts) == 1


def test_bypass_without_local_storage_adds_no_script():
    ns, context = _load({"cookie": {"name": "session", "value": "s", "domain": "app.test", "path": "/"}})
    ns["get_authenticated_context"](None, "editor")
    assert context.init_scripts == []
    assert len(context.cookies) == 1


def test_hostile_value_stays_a_string_literal():
    ns, _ = _load({})
    hostile = 'x"); alert(1); ("\'</script>'
    script = ns["_bypass_local_storage_script"]("http://app.test", {"k": hostile})
    assert json.dumps(hostile) in script
    assert 'alert(1); ("' not in script.replace(json.dumps(hostile), "")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.parametrize(
    ("origin", "expected"),
    [("http://app.test", {"k": 'va"l', "n": "1"}), ("https://idp.example", {})],
)
def test_script_sets_values_on_the_app_origin_only(origin, expected):
    ns, _ = _load({})
    script = ns["_bypass_local_storage_script"]("http://app.test/", {"k": 'va"l', "n": 1})
    harness = (
        "const store = {};"
        f"const window = {{ location: {{ origin: {json.dumps(origin)} }},"
        " localStorage: { setItem: (k, v) => { store[k] = v; } } };"
        f"{script}\nprocess.stdout.write(JSON.stringify(store));"
    )
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == expected
