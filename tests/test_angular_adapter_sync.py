"""The Angular adapter (``packages/angular``) against the overlay it drives.

The adapter names the overlay's tag and events itself — its only runtime
reference to ``@steppy/player/tour`` is a dynamic ``import()``, so nothing
from the overlay can be imported at the top of the package — and writes the
host's properties one by one. A renamed event or a new property would be
silently missed; these checks read both sides as text, without Node.

Also the adapter contract's two prohibitions (docs/tours.md#angular-adapter-contract),
checked over the adapter and its demo: consumers never need
``CUSTOM_ELEMENTS_SCHEMA``, and nothing bypasses Angular's sanitizer or writes
raw HTML.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "packages" / "angular"
PLAYER = ROOT / "packages" / "player"
DEMO = ROOT / "fixtures" / "tour-demo-angular" / "app" / "src"


def _const(path: Path, name: str) -> str:
    match = re.search(rf"^export const {name} = '([^']*)';$", path.read_text(encoding="utf-8"), re.M)
    assert match, f"{path.name}: no one-line `export const {name} = '...'`"
    return match.group(1)


def _tuple(path: Path, name: str) -> tuple[str, ...]:
    match = re.search(rf"^export const {name} = \[([^\]]*)\] as const;$", path.read_text(encoding="utf-8"), re.M)
    assert match, f"{path.name}: no one-line `export const {name} = [...] as const`"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _host_properties() -> set[str]:
    text = (PLAYER / "src" / "tour" / "host.ts").read_text(encoding="utf-8")
    block = re.search(r"static override properties = \{(.*?)\n  \};", text, re.S)
    assert block, "host.ts: static properties block not found"
    return set(re.findall(r"^\s+(\w+): \{", block.group(1), re.M))


def test_the_tag_matches_the_overlay():
    assert _const(ADAPTER / "src" / "overlay.ts", "TOUR_HOST_TAG") == _const(
        PLAYER / "src" / "ui" / "names.ts", "TOUR_HOST_TAG"
    )


def test_the_event_names_match_the_overlay():
    adapter = _tuple(ADAPTER / "src" / "overlay.ts", "TOUR_EVENT_NAMES")
    assert adapter == _tuple(PLAYER / "src" / "tour" / "contract.ts", "TOUR_EVENTS")


def test_apply_props_writes_every_host_property_but_navigate():
    text = (ADAPTER / "src" / "overlay.ts").read_text(encoding="utf-8")
    body = re.search(r"export function applyProps\(.*?\n\}", text, re.S)
    assert body
    written = set(re.findall(r"\bel\.(\w+) = ", body.group(0)))
    # ``navigate`` is set once, at mount (tours.component.ts).
    assert written == _host_properties() - {"navigate"}
    assert "el.navigate = this.navigate" in (ADAPTER / "src" / "tours.component.ts").read_text(encoding="utf-8")


def test_the_fake_overlay_records_every_host_property():
    assert set(_tuple(ADAPTER / "src" / "testing" / "fake-overlay.ts", "HOST_PROPS")) == _host_properties()


def test_the_overlay_is_reached_only_through_the_dynamic_import():
    for path in sorted((ADAPTER / "src").glob("*.ts")):
        if path.name.endswith(".spec.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        for statement in re.findall(r"^import [^;]*'@steppy/player[^']*';$", text, re.M | re.S):
            assert statement.startswith("import type "), f"{path.name}: a runtime import of the overlay: {statement}"
    assert "import('@steppy/player/tour')" in (ADAPTER / "src" / "overlay.ts").read_text(encoding="utf-8")


def _sources() -> list[Path]:
    files = [p for p in (ADAPTER / "src").rglob("*.ts")]
    if DEMO.is_dir():
        files += [p for p in DEMO.rglob("*") if p.suffix in (".ts", ".html")]
    return sorted(files)


@pytest.mark.parametrize("forbidden", ["CUSTOM_ELEMENTS_SCHEMA", "bypassSecurityTrust", "innerHTML"])
def test_the_adapter_contract_prohibitions_hold(forbidden):
    offenders = [str(p.relative_to(ROOT)) for p in _sources() if forbidden in p.read_text(encoding="utf-8")]
    assert offenders == [], f"{forbidden} in {offenders}"


def test_the_player_peer_is_the_shipped_version():
    adapter = json.loads((ADAPTER / "package.json").read_text(encoding="utf-8"))
    player = json.loads((PLAYER / "package.json").read_text(encoding="utf-8"))
    assert adapter["peerDependencies"]["@steppy/player"] == player["version"]
    assert adapter["devDependencies"]["@steppy/player"] == "file:../player"
