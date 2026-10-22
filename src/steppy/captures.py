"""Capture-path grammar — the filename and directory shape of ``output/screenshots``.

The *write* side of this grammar lives in the generated conftest
(``_screenshot_folder`` / ``take_screenshot`` in ``templates/conftest.py.j2``):
a capture lands at

    <screenshots_dir>/<slug>/[<locale>/][<layout>/]step-NN[-<frame>].(png|json)

with the locale segment appended before the layout segment, and either omitted
when that dimension is not parametrized.

Three consumers have to agree on that shape, and historically each answered it
independently (see :meth:`steppy.guide.GuideAssembler.resolve_capture_segments`
for what that cost): the guide read side, the build's copy/URL side, and — since
the results bundle — the *inverse* direction, turning a path found on disk back
into ``(scenario, locale, layout, step, frame)``.

This module is the one home for the grammar — including its *root*, which
:func:`resolve_output_dir` derives from ``config.output`` for every consumer
that used to hardcode ``output/screenshots`` of its own (FLAWS §4.6).

It is a **leaf**: stdlib only, no ``jinja2``/``markdown_it``/``pydantic``, so
the bundler can import it without dragging ``guide.py``'s rendering stack onto
the default CLI path. ``guide.py`` re-exports :func:`step_stem` and
:func:`_has_captures` so its own callers are unaffected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


#: ``step-07.png``, ``step-07.json``, ``step-07-b.png``. The step number is
#: written zero-padded to two digits but matched more loosely — a scenario with
#: 100+ steps must not silently stop being recognised.
STEP_FILE_RE = re.compile(r"^step-(\d{2,})(?:-([a-z]+))?\.(png|json)$")

#: Directory holding full-page screenshots of failed tests, keyed by a sanitised
#: pytest nodeid rather than by scenario/step. Written by the generated
#: conftest's ``pytest_runtest_makereport`` hook.
FAILURES_DIRNAME = "_failures"

#: Fallbacks for ``config.output.*``. The grammar above hangs off a *root*, and
#: that root is the last piece of it that used to be re-derived per consumer —
#: four independent copies of the same literal (FLAWS §4.6). These are the only
#: two left, and the generated conftest carries a third by necessity (it is
#: standalone rendered code and cannot import this module).
DEFAULT_SCREENSHOTS_DIR = "output/screenshots"
DEFAULT_GUIDES_DIR = "output/guides"
#: Canonical in-app tour tree (``<tours_dir>/<build-id>/``); see ``steppy.tours``.
DEFAULT_TOURS_DIR = "output/tours"


def resolve_output_dir(project_root: Path, configured: Optional[str], default: str) -> Path:
    """Resolve a ``config.output.*`` value against the project root.

    A relative value (``./output/screenshots``) lands under the project; an
    absolute one wins outright. Both are plain ``Path.__truediv__`` semantics,
    deliberately — the same rule the conftest applies to
    ``coverage.records_dir``, so writer and readers cannot disagree.

    An empty/absent value falls back to *default*, because ``CONFIG`` is raw
    YAML on the writer side: an absent ``output:`` block yields ``None`` there
    and pydantic's ``default_factory`` never runs.
    """
    return (Path(project_root) / (configured or default)).resolve()


def step_stem(step_index: int, frame: Optional[str] = None) -> str:
    """Filename stem for a captured step: ``step-07`` or ``step-07-b``.

    One definition shared by the read side (guide.py) and the build's URL
    builders, so a change to the capture naming scheme can't half-land.
    """
    return f"step-{step_index:02d}" + (f"-{frame}" if frame else "")


def _has_captures(directory: Path) -> bool:
    """True when *directory* actually holds screenshots.

    The single definition of "this is a capture directory". Existence alone is
    not enough: the generated conftest's ``_screenshot_folder`` mkdirs before
    calling ``page.screenshot``, so an interrupted run leaves empty directories
    behind, and a per-locale directory that only holds per-layout subdirs holds
    no captures of its own.
    """
    return directory.is_dir() and any(directory.glob("step-*.png"))


@dataclass(frozen=True)
class StepFile:
    """A parsed capture filename. The inverse of :func:`step_stem`."""

    step: int
    frame: Optional[str]
    ext: str  # "png" | "json"


def parse_step_stem(filename: str) -> Optional[StepFile]:
    """Parse ``step-NN[-<frame>].(png|json)``. ``None`` when it isn't one.

    Round-trips with :func:`step_stem`::

        parse_step_stem(step_stem(7, "b") + ".png") == StepFile(7, "b", "png")
    """
    match = STEP_FILE_RE.match(filename)
    if match is None:
        return None
    number, frame, ext = match.groups()
    return StepFile(step=int(number), frame=frame, ext=ext)


@dataclass(frozen=True)
class CaptureRef:
    """A capture file located in the screenshots tree."""

    scenario: str
    locale: Optional[str]
    layout: Optional[str]
    step: int
    frame: Optional[str]
    ext: str


def classify_capture_path(
    rel_parts: tuple[str, ...] | list[str],
    *,
    locales: frozenset[str] | set[str] = frozenset(),
    layouts: frozenset[str] | set[str] = frozenset(),
) -> Optional[CaptureRef]:
    """Invert the capture path shape. ``rel_parts`` is relative to the
    screenshots dir, e.g. ``("order-lifecycle", "fr", "mobile", "step-01.png")``.

    Returns ``None`` for anything that is not a step capture — including the
    ``_failures/`` tree, whose filenames are sanitised pytest nodeids rather
    than steps.

    The middle segments are what need deciding, and only one case is genuinely
    ambiguous:

      * **0 segments** — flat/legacy capture, ``(None, None)``.
      * **2 segments** — unambiguous. The writer appends locale then layout, in
        that order, and never any other pair.
      * **1 segment** — ``<slug>/fr/`` and ``<slug>/desktop/`` are structurally
        identical, so the name is the only signal. Resolved by membership in the
        configured sets; when it matches neither (a locale or layout removed
        from ``config.yaml`` after the capture), it is read as a **locale**,
        mirroring the "locale beats layout" precedence
        :meth:`~steppy.guide.GuideAssembler.resolve_capture_segments` documents
        — the locale determines the language actually visible in the image, and
        mislabelling that is worse than mislabelling a viewport.
    """
    parts = tuple(rel_parts)
    if len(parts) < 2 or parts[0] == FAILURES_DIRNAME:
        return None

    parsed = parse_step_stem(parts[-1])
    if parsed is None:
        return None

    scenario = parts[0]
    middle = parts[1:-1]

    if len(middle) == 0:
        locale, layout = None, None
    elif len(middle) == 1:
        segment = middle[0]
        if segment in locales:
            locale, layout = segment, None
        elif segment in layouts:
            locale, layout = None, segment
        else:
            locale, layout = segment, None
    elif len(middle) == 2:
        locale, layout = middle[0], middle[1]
    else:
        # Deeper than the writer can produce — not ours to interpret.
        return None

    return CaptureRef(
        scenario=scenario,
        locale=locale,
        layout=layout,
        step=parsed.step,
        frame=parsed.frame,
        ext=parsed.ext,
    )


def sanitise_nodeid(nodeid: str) -> str:
    """Make a pytest nodeid filesystem-safe. Mirror of ``_sanitise_nodeid``.

    **Byte-mirrored** from ``_sanitise_nodeid`` in ``templates/conftest.py.j2``,
    which names the failure screenshots, traces and videos. Guard:
    ``tests/test_nodeid_sanitiser_mirror.py`` — same arrangement as
    ``totp._totp_now`` (CLAUDE.md). Apply any change to both.

    The mapping is **lossy and not invertible**: a slug containing ``_`` is
    indistinguishable from a separator, and ``(fr-mobile)`` cannot be told from
    a literal parenthesis in a test name. So consumers must build the *forward*
    map — sanitise every nodeid they know about and look filenames up in it —
    never attempt to reverse a filename.
    """
    return nodeid.replace("/", "_").replace("\\", "_").replace("::", "__").replace("[", "(").replace("]", ")")
