"""Opt-in browser integration test for the coverage recorder.

Skipped unless STEPPY_IT=1 (needs Playwright Chromium installed). Runs the
FULL real loop against a tiny pushState+fetch SPA served by http.server:

    steppy generate -> steppy run --coverage --scenario demo--smoke
    -> generated conftest records (framenavigated + history hook + CDP
       Profiler + inline source map persistence)
    -> auto-merge into .steppy/coverage.db
    -> steppy coverage refresh + report

and asserts the record shape, the SPA route trail, the API request capture,
the source-module decoding, and the declared-unreached bucket.

Run it:  STEPPY_IT=1 python -m pytest tests/integration/test_coverage_recorder_it.py -q
(PowerShell:  $env:STEPPY_IT='1'; python -m pytest tests/integration/... -q)
"""

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    os.environ.get("STEPPY_IT") != "1",
    reason="browser integration test — set STEPPY_IT=1 to run",
)

_MAP = {"version": 3, "sources": ["src/app-entry.ts"], "names": [], "mappings": "AAAA"}
_MAP_B64 = base64.b64encode(json.dumps(_MAP).encode()).decode()

_APP_JS = (
    "function go() {\n"
    '  history.pushState({}, "", "/orders/123");\n'
    '  fetch("/api/orders/123");\n'
    '  document.getElementById("done").textContent = "ok";\n'
    "}\n"
    'document.getElementById("go").addEventListener("click", go);\n'
    f"//# sourceMappingURL=data:application/json;base64,{_MAP_B64}\n"
)

_INDEX_HTML = (
    "<!DOCTYPE html><html><body>"
    '<button id="go">Go</button><div id="done"></div>'
    '<script src="/app.js"></script>'
    "</body></html>"
)


class _SpaHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/app.js":
            body, ctype = _APP_JS.encode(), "application/javascript"
        elif self.path.startswith("/api/"):
            body, ctype = b'{"ok": true}', "application/json"
        else:
            body, ctype = _INDEX_HTML.encode(), "text/html"  # SPA fallback
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence per-request stderr noise
        pass


@pytest.fixture
def spa_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SpaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def project(tmp_path: Path, spa_server: str, monkeypatch) -> Path:
    (tmp_path / "config.yaml").write_text(
        dedent(f"""\
            apps:
              demo:
                name: "Demo"
                url: "{spa_server}"
                auth:
                  mode: bypass

            coverage:
              apis:
                - name: demo-api
                  openapi: "openapi.json"
                  base_urls: ["{spa_server}"]
            """),
        encoding="utf-8",
    )
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    "/api/orders/{orderId}": {"get": {"operationId": "getOrder"}},
                    "/api/admin/jobs": {"post": {"operationId": "runJobs"}},
                },
            }
        ),
        encoding="utf-8",
    )
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "demo--smoke.md").write_text(
        dedent("""\
            ---
            title: Coverage smoke
            auth: false
            ---

            ### 1. Open the app
            - role: demo
            - action: navigate
            - target: /

            ### 2. Trigger SPA navigation and API call
            - role: demo
            - action: click
            - selector: #go

            ### 3. Let the fetch settle
            - role: demo
            - action: wait
            - duration: 1s
            """),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_full_coverage_loop_against_real_browser(project: Path):
    from steppy.cli import app

    runner = CliRunner()

    generated = runner.invoke(app, ["generate"])
    assert generated.exit_code == 0, generated.output

    run = runner.invoke(app, ["run", "--coverage", "--scenario", "demo--smoke"])
    assert run.exit_code == 0, run.output
    assert "Coverage: merged 1 scenario(s)" in run.output

    # ── Raw record shape ────────────────────────────────────────────────
    raw_root = project / "output" / "coverage" / "raw"
    run_dirs = [p for p in raw_root.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    records = list((run_dirs[0] / "demo--smoke").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["steppy_coverage_record"] == 1
    assert record["scenario"] == "demo--smoke"
    assert record["outcome"] == "passed"

    # Route trail saw the SPA pushState — both via framenavigated and the hook.
    nav_urls = {e["u"] for e in record["nav_events"]}
    assert any(u.endswith("/orders/123") for u in nav_urls), nav_urls
    spa_pushes = [e for e in record["spa_events"] if e.get("k") == "push"]
    assert any(e["u"].endswith("/orders/123") for e in spa_pushes), record["spa_events"]

    # The fetch was captured.
    api_calls = [r for r in record["requests"] if "/api/orders/123" in r["u"]]
    assert api_calls and api_calls[0]["m"] == "GET"

    # JS coverage worked and the inline map blob was persisted.
    assert record["js_coverage"]["ok"] is True, record["js_coverage"]
    app_scripts = [s for s in record["js_coverage"]["scripts"] if s["url"].endswith("/app.js")]
    assert app_scripts and app_scripts[0]["map"]["kind"] == "inline"
    blob = run_dirs[0] / "_maps" / f"{app_scripts[0]['map']['hash']}.json.gz"
    assert blob.exists()

    # ── Map + gap report ────────────────────────────────────────────────
    assert (project / ".steppy" / "coverage.db").exists()
    refresh = runner.invoke(app, ["coverage", "refresh"])
    assert refresh.exit_code == 0, refresh.output

    report = runner.invoke(app, ["coverage", "report", "--json", "gaps.json"])
    assert report.exit_code == 0, report.output
    gaps = json.loads((project / "gaps.json").read_text(encoding="utf-8"))

    tested = {(e["method"], e["key"]) for e in gaps["endpoints"]["tested"]}
    assert ("GET", "/api/orders/{orderId}") in tested
    unreached = {(e["method"], e["key"]) for e in gaps["endpoints"]["declared_unreached"]}
    assert unreached == {("POST", "/api/admin/jobs")}

    routes = {e["key"] for e in gaps["routes"]["discovered_tested"] + gaps["routes"]["discovered_uncovered"]}
    assert {"/", "/orders/:id"} <= routes

    modules = {m["key"] for m in gaps["modules"]["covered"]}
    assert "src/app-entry.ts" in modules, modules
