"""Click hotspots: the target box each capture's sidecar recorded.

Every step capture has a JSON sidecar beside it (``step-03.json``, written by the
generated conftest) with the page it was taken in and the element the step acted
on:

- ``viewport`` ``{width, height}`` and ``device_scale_factor`` (absent means 1);
- ``target``: ``null``, ``{kind: "single", bbox}``, or ``{kind: "multi", fields:
  [{bbox}]}`` for a ``fill-form``.

A ``bbox`` is ``{x, y, width, height}`` in CSS pixels of the captured image, or
``null`` when the element was not found.

**A sidecar is never an error.** One that is missing, too large, not JSON or shaped
in any other way gives its screenshot no box, and the image shows on its own. A
bundle may come from anywhere, so nothing it holds may stop the report: hence the
size limit, and an exception list that covers what ``json`` really raises (a deeply
nested document is a ``RecursionError``, a 400-digit number an ``OverflowError``).

Only the run's own sidecars get here: the manifest lists no capture the run did not
take (``bundle/manifest.walk_candidates``). A sidecar is joined to its screenshot
by the attribution ``bundle/classify.py`` gave both, so nothing parses a path.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..contracts.bundle import BundleFile, BundleManifest

#: A sidecar is a few hundred bytes; one larger than this is not one.
MAX_SIDECAR_BYTES = 64 * 1024
#: Boxes kept per capture: a fill-form has one per field.
MAX_BOXES = 64


@dataclass(frozen=True)
class Hotspot:
    """What a viewer needs of one sidecar."""

    device_scale_factor: float = 1.0
    viewport: Optional[dict[str, float]] = None
    boxes: tuple[dict[str, float], ...] = ()

    def fields(self) -> dict[str, Any]:
        """The contract's fields, as :class:`~steppy.contracts.report.ReportCapture` takes them."""
        return {
            "viewport": self.viewport,
            "device_scale_factor": self.device_scale_factor,
            "boxes": list(self.boxes),
        }


def parse(data: bytes) -> Optional[Hotspot]:
    """One sidecar's bytes, or ``None`` when they are not a sidecar.

    A target kind this version does not know gives no box. A scale factor that is
    not a positive number makes the whole sidecar unusable: the box would be drawn
    in the wrong place.
    """
    if len(data) > MAX_SIDECAR_BYTES:
        return None
    try:
        payload = json.loads(data)
        return _hotspot(payload)
    except (ValueError, RecursionError, OverflowError):
        # Not JSON, not UTF-8, nested past the interpreter's limit, or a number no
        # float can hold.
        return None


def _hotspot(payload: Any) -> Optional[Hotspot]:
    if not isinstance(payload, dict):
        return None
    raw_scale = payload.get("device_scale_factor")
    scale = 1.0 if raw_scale is None else _number(raw_scale)
    if scale is None or scale <= 0:
        return None
    target = payload.get("target")
    raw_boxes: list[Any] = []
    if target is not None:
        if not isinstance(target, dict):
            return None
        if target.get("kind") == "single":
            raw_boxes = [target.get("bbox")]
        elif target.get("kind") == "multi":
            fields = target.get("fields")
            if not isinstance(fields, list):
                return None
            raw_boxes = [f.get("bbox") if isinstance(f, dict) else None for f in fields]
    boxes = [box for box in map(_box, raw_boxes[:MAX_BOXES]) if box]
    return Hotspot(device_scale_factor=scale, viewport=_viewport(payload.get("viewport")), boxes=tuple(boxes))


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _box(raw: Any) -> Optional[dict[str, float]]:
    """A box a viewer can draw, rounded to a hundredth of a pixel."""
    if not isinstance(raw, dict):
        return None
    x, y, width, height = (_number(raw.get(name)) for name in ("x", "y", "width", "height"))
    if x is None or y is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    return {"x": round(x, 2), "y": round(y, 2), "width": round(width, 2), "height": round(height, 2)}


def _viewport(raw: Any) -> Optional[dict[str, float]]:
    if not isinstance(raw, dict):
        return None
    width, height = _number(raw.get("width")), _number(raw.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return {"width": width, "height": height}


def _key(entry: BundleFile) -> Optional[tuple]:
    if entry.scenario is None or entry.step is None:
        return None
    return (entry.scenario, entry.locale, entry.layout, entry.step, entry.frame)


def pair(manifest: BundleManifest, shots: Iterable[str]) -> dict[str, str]:
    """``{screenshot path: sidecar path}`` for the screenshots named in *shots*."""
    wanted = set(shots)
    sidecars: dict[tuple, str] = {}
    for entry in manifest.files:
        if entry.kind == "sidecar" and (key := _key(entry)) is not None:
            sidecars.setdefault(key, entry.path)
    found: dict[str, str] = {}
    for entry in manifest.files:
        if entry.kind != "screenshot" or entry.path not in wanted:
            continue
        sidecar = sidecars.get(_key(entry))
        if sidecar is not None:
            found[entry.path] = sidecar
    return found


def read(source, shots: Iterable[str]) -> dict[str, Hotspot]:
    """``{screenshot path: Hotspot}`` for the screenshots named in *shots*.

    The fetch's warnings are dropped on purpose: a sidecar the manifest lists and
    the bundle lacks is a screenshot without a box, not something to act on.
    """
    paired = pair(source.manifest, shots)
    if not paired:
        return {}
    fetched, _warnings = source.fetch(texts=set(paired.values()), max_bytes=MAX_SIDECAR_BYTES)
    parsed = {path: parse(data) for path, data in fetched.items()}
    return {shot: hotspot for shot, path in paired.items() if (hotspot := parsed.get(path)) is not None}
