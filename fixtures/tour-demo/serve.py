"""Dev and test server for the in-app tour demo (``fixtures/tour-demo/app``).

Stdlib only. Two servers:

``DemoServer``
    The app's own origin: ``index.html`` for every extension-less path (an SPA),
    ``/app.js`` and ``/app.css``, the overlay bundle at ``/steppy-tour.js``
    (from ``packages/player/dist`` — build it first), the bundled tour files at
    ``/assets/tours/`` and an optional ``/demo-config.json``. A missing path with
    an extension is a 404, never the index page, so a missing JSON file cannot
    come back as HTML. ``/demo-config.json`` is ``{}`` unless a config is given. Pages carry a strict Content-Security-Policy: no inline
    script or style, which is what the overlay is built to live with.

``RemoteServer``
    A second origin playing the published guide site: ``/tours/<build-id>/*``
    with CORS for the app origin. ``mode`` makes it misbehave on purpose —
    ``fail`` (503), ``tamper`` (one byte of every tour file changed, so the
    bundled manifest's SRI hash no longer matches) or ``nocors``.

Run the demo::

    npm --prefix packages/player run build
    python fixtures/tour-demo/serve.py                     # http://127.0.0.1:8770
    python fixtures/tour-demo/serve.py --remote-port 8771  # remote mode

The Angular demo (``fixtures/tour-demo-angular``, built with ``ng build``)
runs on the same server; Angular and PrimeNG inject ``<style>`` elements, so
it needs inline styles (scripts stay ``'self'`` only)::

    python fixtures/tour-demo/serve.py --style-src "'unsafe-inline'" \
        --app-dir fixtures/tour-demo-angular/app/dist/tour-demo-angular/browser

Integration tests load this file with ``importlib`` (``fixtures`` is not a
package) and use the two classes directly.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APP_DIR = HERE / "app"
DEFAULT_TOURS = ROOT / "contracts" / "fixtures" / "tours" / "valid"
DEFAULT_BUNDLE = ROOT / "packages" / "player" / "dist" / "steppy-tour.js"
BUILD_HINT = (
    "steppy-tour.js is not built: run `npm --prefix packages/player install` and "
    "`npm --prefix packages/player run build` (or set STEPPY_TOUR_BUNDLE)."
)
MANIFEST = "tours.manifest.json"


def _safe_join(root: Path, relative: str) -> Path | None:
    """``root/relative`` when it stays inside ``root`` and is a file, else None."""
    base = root.resolve()
    try:
        candidate = (base / relative.lstrip("/")).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(base) or not candidate.is_file():
        return None
    return candidate


def _content_type(path: Path) -> str:
    if path.suffix == ".js":
        return "text/javascript; charset=utf-8"
    if path.suffix in (".json", ".map"):
        return "application/json; charset=utf-8"
    guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"{guessed}; charset=utf-8" if guessed.startswith("text/") else guessed


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Base:
    """Start/stop plumbing shared by both servers."""

    def __init__(self, handler: type[BaseHTTPRequestHandler], host: str, port: int) -> None:
        self.requests: list[str] = []
        self._httpd = _HTTPServer((host, port), handler)
        self._httpd.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class _Handler(BaseHTTPRequestHandler):
    server_version = "tour-demo"

    @property
    def owner(self):
        return self.server.owner  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        return

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _text(self, status: int, message: str, headers: dict[str, str] | None = None) -> None:
        self._send(status, message.encode(), "text/plain; charset=utf-8", headers)


class DemoServer(_Base):
    """The demo app's origin (see the module docstring)."""

    def __init__(
        self,
        app_dir: Path = APP_DIR,
        *,
        bundle: Path | None = None,
        tours_dir: Path = DEFAULT_TOURS,
        config: dict | None = None,
        connect_src: tuple[str, ...] = (),
        style_src: tuple[str, ...] = (),
        trusted_types: bool = False,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.app_dir = Path(app_dir)
        env_bundle = os.environ.get("STEPPY_TOUR_BUNDLE")
        self.bundle = Path(bundle or env_bundle or DEFAULT_BUNDLE)
        self.tours_dir = Path(tours_dir)
        self.config = config
        csp = [
            "default-src 'self'",
            "script-src 'self'",
            "style-src " + " ".join(["'self'", *style_src]),
            "img-src 'self' data:",
            "connect-src " + " ".join(["'self'", *connect_src]),
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
        ]
        if trusted_types:
            csp += ["require-trusted-types-for 'script'", "trusted-types lit-html"]
        self.csp = "; ".join(csp)
        super().__init__(_DemoHandler, host, port)


class _DemoHandler(_Handler):
    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        owner: DemoServer = self.owner
        path = unquote(urlsplit(self.path).path)
        owner.requests.append(path)
        no_store = {"Cache-Control": "no-store"}
        if path in ("/steppy-tour.js", "/steppy-tour.js.map"):
            file = owner.bundle if path.endswith(".js") else owner.bundle.with_name(owner.bundle.name + ".map")
            if not file.is_file():
                return self._text(404, BUILD_HINT, no_store)
            return self._send(200, file.read_bytes(), _content_type(file), no_store)
        if path == "/demo-config.json":
            config = json.dumps(owner.config or {}).encode()
            return self._send(200, config, "application/json; charset=utf-8", no_store)
        if path.startswith("/assets/tours/"):
            file = _safe_join(owner.tours_dir, path[len("/assets/tours/") :])
            if file is None:
                return self._text(404, "not found", no_store)
            return self._send(200, file.read_bytes(), _content_type(file), {"Cache-Control": "no-cache"})
        name = path.rsplit("/", 1)[-1]
        if "." in name:
            file = _safe_join(owner.app_dir, path)
            if file is None or file.name == "index.html":
                return self._text(404, "not found", no_store)
            return self._send(200, file.read_bytes(), _content_type(file), no_store)
        index = (owner.app_dir / "index.html").read_bytes()
        return self._send(200, index, "text/html; charset=utf-8", {**no_store, "Content-Security-Policy": owner.csp})


class RemoteServer(_Base):
    """The published guide site's origin, serving ``/tours/<build-id>/*``.

    ``root`` holds one directory per build id (``output/tours/`` or a static
    guide's ``tours/``), or — with ``build_id`` — the flat files of that one
    build (``contracts/fixtures/tours/valid``).
    """

    MODES = ("ok", "fail", "tamper", "nocors")

    def __init__(
        self,
        root: Path,
        *,
        allow_origin: str,
        mode: str = "ok",
        build_id: str | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        self.root = Path(root)
        self.allow_origin = allow_origin
        self.mode = mode
        self.build_id = build_id
        super().__init__(_RemoteHandler, host, port)

    def file_for(self, path: str) -> Path | None:
        m = re.fullmatch(r"/tours/([^/]+)/([^/]+)", path)
        if not m:
            return None
        build, name = m.groups()
        if self.build_id is not None:
            return _safe_join(self.root, name) if build == self.build_id else None
        return _safe_join(self.root, f"{build}/{name}")


def _tamper(body: bytes) -> bytes:
    """Change one letter inside the first step text: still valid JSON, different bytes."""
    m = re.search(rb'"text": "([a-z])', body)
    if not m:
        return body + b" "
    i = m.start(1)
    return body[:i] + body[i : i + 1].upper() + body[i + 1 :]


class _RemoteHandler(_Handler):
    def _cors(self) -> dict[str, str]:
        owner: RemoteServer = self.owner
        if owner.mode == "nocors":
            return {}
        return {"Access-Control-Allow-Origin": owner.allow_origin, "Vary": "Origin"}

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server API
        self.owner.requests.append(f"OPTIONS {urlsplit(self.path).path}")
        self._send(
            204,
            b"",
            "text/plain",
            {**self._cors(), "Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "60"},
        )

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        owner: RemoteServer = self.owner
        path = unquote(urlsplit(self.path).path)
        owner.requests.append(path)
        if owner.mode == "fail":
            return self._text(503, "unavailable", self._cors())
        file = owner.file_for(path)
        if file is None:
            return self._text(404, "not found", self._cors())
        body = file.read_bytes()
        is_manifest = file.name == MANIFEST
        if owner.mode == "tamper" and not is_manifest:
            body = _tamper(body)
        cache = "no-cache" if is_manifest else "max-age=60"
        self._send(200, body, _content_type(file), {**self._cors(), "Cache-Control": cache})


def _build_id(tours_dir: Path) -> str:
    return json.loads((tours_dir / MANIFEST).read_text(encoding="utf-8"))["buildId"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--app-dir", type=Path, default=APP_DIR, help="the app to serve (default: the plain demo)")
    parser.add_argument(
        "--style-src",
        action="append",
        default=[],
        help="an extra style-src CSP source, e.g. \"'unsafe-inline'\" for the Angular demo (repeatable)",
    )
    parser.add_argument("--tours-dir", type=Path, default=DEFAULT_TOURS, help="bundled tour files (/assets/tours/)")
    parser.add_argument("--bundle", type=Path, default=None, help="the overlay bundle (default: packages/player/dist)")
    parser.add_argument("--config", type=Path, default=None, help="a JSON file served as /demo-config.json")
    parser.add_argument("--remote-port", type=int, default=None, help="also serve the tours from a second origin")
    parser.add_argument("--remote-mode", choices=RemoteServer.MODES, default="ok")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
    remote = None
    connect: tuple[str, ...] = ()
    if args.remote_port is not None:
        origin = f"http://127.0.0.1:{args.port}"
        remote = RemoteServer(
            args.tours_dir,
            allow_origin=origin,
            mode=args.remote_mode,
            build_id=_build_id(args.tours_dir),
            port=args.remote_port,
        )
        connect = (remote.url,)
        config = {
            "manifest": f"{remote.url}/tours/{{buildId}}/{MANIFEST}",
            "fallbackManifest": f"/assets/tours/{MANIFEST}",
            **(config or {}),
        }
    demo = DemoServer(
        args.app_dir,
        bundle=args.bundle,
        tours_dir=args.tours_dir,
        config=config,
        connect_src=connect,
        style_src=tuple(args.style_src),
        port=args.port,
    )
    print(
        f"Invoice demo on {demo.url}" + (f" (remote tours on {remote.url}, mode {args.remote_mode})" if remote else "")
    )
    if not demo.bundle.is_file():
        print(f"warning: {BUILD_HINT}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        demo.stop()
        if remote:
            remote.stop()


if __name__ == "__main__":
    main()
