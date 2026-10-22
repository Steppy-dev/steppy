"""The tour demos' server (``fixtures/tour-demo/serve.py``): the CSP it sends.

The plain demo is served under a strict policy — no inline script or style —
because that is what the overlay is built to live with. The Angular demo needs
inline styles (Angular and PrimeNG inject ``<style>`` elements), so the server
takes extra ``style-src`` sources; the default must stay strict.
"""

import importlib.util
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "tour-demo"

_spec = importlib.util.spec_from_file_location("tour_demo_serve_under_test", DEMO / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


def _csp(**kwargs) -> dict[str, str]:
    server = serve.DemoServer(**kwargs)
    try:
        return dict(part.split(" ", 1) for part in server.csp.split("; "))
    finally:
        server.stop()


def test_the_default_policy_is_strict():
    csp = _csp()
    assert csp["script-src"] == "'self'"
    assert csp["style-src"] == "'self'"
    assert "trusted-types" not in csp


def test_extra_style_sources_are_appended_and_scripts_stay_strict():
    csp = _csp(style_src=("'unsafe-inline'",))
    assert csp["style-src"] == "'self' 'unsafe-inline'"
    assert csp["script-src"] == "'self'"


def test_the_cli_takes_an_app_dir_and_style_sources(tmp_path, monkeypatch):
    seen = {}

    class Recorder:
        bundle = Path(__file__)

        def __init__(self, app_dir, **kwargs):
            seen.update(app_dir=app_dir, **kwargs)

        @property
        def url(self):
            return "http://127.0.0.1:0"

        def stop(self):
            pass

    monkeypatch.setattr(serve, "DemoServer", Recorder)
    monkeypatch.setattr(serve.threading.Event, "wait", lambda self, timeout=None: True)
    serve.main(["--app-dir", str(tmp_path), "--style-src", "'unsafe-inline'", "--port", "0"])
    assert seen["app_dir"] == tmp_path
    assert seen["style_src"] == ("'unsafe-inline'",)
