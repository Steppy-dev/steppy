"""User guide assembler for Steppy - creates Markdown and HTML guides from test screenshots."""

import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

import jinja2
from markdown_it import MarkdownIt

from ._player_bundle import get_legacy_cursor_iife
from ._templating import make_template_env
from .captures import _has_captures, step_stem
from .models import Scenario, Step, SteppyConfig
from .parser import parse_scenario_file

# ``_has_captures`` / ``step_stem`` are imported (not defined) here: they moved to
# the stdlib-only ``captures`` leaf, which also owns their inverse, because the
# results bundler has to parse the same grammar and cannot afford this module's
# jinja2 + markdown_it import cost on the default CLI path. Importing rather than
# re-declaring keeps ``from .guide import step_stem`` (build.py:28) working and
# keeps one definition, per the FLAWS §4.1 single-source rule.


_md = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": True})


def _markdown_inline(text: Optional[str]) -> str:
    """Render inline markdown to HTML. Strips the wrapping <p> so the result
    is safe to embed inside block elements like <div class="step-content">."""
    if text is None:
        return ""
    return _md.renderInline(str(text))


def register_jinja_filters(env: jinja2.Environment) -> None:
    """Register Steppy's shared Jinja2 filters on an environment."""
    env.filters["markdown"] = _markdown_inline


# Actions whose steps render without a cursor in the guide. Non-visual work
# (asserts, navigation, waits, auth) has no meaningful cursor behavior.
_HIDDEN_CURSOR_ACTIONS = {
    "navigate",
    "assert",
    "wait",
    "wait-for",
    "wait-for-text",
    "login",
    "keyboard",
    "capture",
}


def _bbox_from_sidecar(sidecar: Optional[dict]) -> Optional[dict]:
    """Extract the single-target bbox from a sidecar. None if missing/malformed."""
    if not sidecar:
        return None
    target = sidecar.get("target")
    if not target or target.get("kind") != "single":
        return None
    return target.get("bbox")


def _multi_bboxes_from_sidecar(sidecar: Optional[dict]) -> list[Optional[dict]]:
    """Extract per-field bboxes from a multi-target sidecar, preserving order."""
    if not sidecar:
        return []
    target = sidecar.get("target")
    if not target or target.get("kind") != "multi":
        return []
    return [f.get("bbox") for f in target.get("fields", [])]


# Actions whose cursor is fully described by "one kind, one selector, maybe one
# value": action -> (cursor kind, the Step attribute holding the typed value, or
# None when this behavior emits no ``value`` key at all).
#
# Adding an action that animates like a click/type/hover is a row here; only
# genuinely bespoke shapes (fill-form's multi-target frames) need real code.
_SIMPLE_CURSOR: dict[str, tuple[str, Optional[str]]] = {
    # select-option opens a component dropdown; the cursor lands on the trigger
    # selector, same as a click. toggle clicks a switch/segment; pick-date and
    # pick-time click the field to open a calendar/clock widget; expand clicks a
    # fieldset legend. All land on (or within) the host selector like a click,
    # positioned by the runtime sidecar bbox — no manual guide-cursor needed.
    "click": ("click", None),
    "select": ("click", None),
    "select-option": ("click", None),
    "confirm": ("click", None),
    "toggle": ("click", None),
    "pick-date": ("click", None),
    "pick-time": ("click", None),
    "expand": ("click", None),
    "upload": ("click", None),
    "hover": ("hover", None),
    "scroll": ("hover", None),
    "fill": ("type", "value"),
    # fill-date's literal is resolved at test run time, so the guide shows the
    # date expression rather than a baked-in date.
    "fill-date": ("type", "date"),
}

# The confirm action resolves its target button imperatively (the dialog
# accept/reject button has no stable selector), so its cursor relies on the
# runtime-captured sidecar bbox rather than a CSS selector.
_NO_SELECTOR_CURSOR = {"confirm"}


def _cursor_from_override(override, sidecar: Optional[dict]) -> dict:
    """Cursor dict for a scenario-authored ``guide-cursor:`` block.

    ``override.behavior`` is already ``Literal["click","type","hover"]`` (see
    ``GuideCursorOverride`` in models.py), i.e. it *is* the cursor kind.
    """
    return {
        "kind": override.behavior,
        "target": {
            "selector": override.target,
            "bbox": _bbox_from_sidecar(sidecar),
        },
        "value": override.value,
    }


def _fillform_cursor(step: Step, sidecar: Optional[dict]) -> Optional[dict]:
    """Cursor for a fill-form step — the one genuinely multi-target shape.

    Progressive capture emits one frame per field with a single-target sidecar
    pointing at that field, plus a final settled frame with ``target: null``.
    Detect the shape by inspecting the sidecar so each frame gets the right
    cursor.
    """
    target = (sidecar or {}).get("target") if sidecar else None
    if step.interactive_progressive:
        if target is None:
            # Settled "all filled" frame — no cursor.
            return None
        if target.get("kind") == "single":
            sel = target.get("selector")
            value = (step.fields or {}).get(sel) if sel else None
            return {
                "kind": "type",
                "target": {
                    "selector": sel,
                    "bbox": target.get("bbox"),
                },
                "value": value,
            }
        # Sidecar exists but isn't single-target — fall through to multi
        # so we never end up with a frame whose cursor silently disappears.
    fields = step.fields or {}
    bboxes = _multi_bboxes_from_sidecar(sidecar)
    targets = []
    for i, (sel, val) in enumerate(fields.items()):
        targets.append(
            {
                "selector": sel,
                "bbox": bboxes[i] if i < len(bboxes) else None,
                "value": val,
            }
        )
    return {"kind": "type-multi", "targets": targets}


def _compute_cursor_behavior(step: Step, sidecar: Optional[dict]) -> Optional[dict]:
    """Map a scenario step + its sidecar metadata to a cursor behavior dict.

    Returns None when the cursor must stay hidden for this step. Otherwise a
    JSON-serializable dict consumed by the guide HTML template's JS driver.
    """
    action = step.action

    if action in _HIDDEN_CURSOR_ACTIONS:
        return None

    # Scenario-authored override. `custom` has no native cursor at all, so the
    # override is its only source. `upload` accepts one because hidden file
    # inputs are common (visible trigger + chosen behavior), but falls back to a
    # click on the input selector itself.
    if step.guide_cursor and action in ("custom", "call", "upload"):
        return _cursor_from_override(step.guide_cursor, sidecar)
    if action in ("custom", "call"):
        return None

    if action == "fill-form":
        return _fillform_cursor(step, sidecar)

    spec = _SIMPLE_CURSOR.get(action)
    if spec is None:
        return None
    kind, value_attr = spec

    behavior = {
        "kind": kind,
        "target": {
            "selector": None if action in _NO_SELECTOR_CURSOR else step.selector,
            "bbox": _bbox_from_sidecar(sidecar),
        },
    }
    # Absent value_attr means this behavior emits no "value" key at all, which
    # is distinct from emitting "value": None.
    if value_attr is not None:
        behavior["value"] = getattr(step, value_attr)
    return behavior


# 1.1 added the optional per-capture ``content`` override (layout-varying guide
# text). The player gates on the MAJOR only, so a 1.0 manifest still loads —
# but the reverse is not true: ``Capture`` is a strict Zod object, so a 1.1
# manifest fed to a 1.0 player bundle fails validation outright. Emitter and
# vendored bundle must always ship together.
INTERACTIVE_MANIFEST_VERSION = "1.1"

# Layout key used in the manifest when the build has no layouts configured.
# Mirrors the synthetic key the player's Zod schema expects.
_LAYOUTLESS_KEY = "_"


def _normalize_cursor_for_manifest(cursor: Optional[dict]) -> Optional[dict]:
    """Reshape ``_compute_cursor_behavior`` output into the strict manifest
    cursor shape. Drops None-valued optional keys so the JSON we emit matches
    the Zod schema's ``.strict()`` + ``.optional()`` contract (Zod's
    ``optional()`` rejects ``null``, only ``undefined``/absent keys).

    Returns None when ``cursor`` is None (non-visual step / no cursor)."""
    if cursor is None:
        return None
    kind = cursor.get("kind")
    if kind == "type-multi":
        out_targets: list[dict] = []
        for t in cursor.get("targets") or []:
            entry: dict = {"bbox": t.get("bbox")}
            sel = t.get("selector")
            if sel is not None:
                entry["selector"] = sel
            val = t.get("value")
            if val is not None:
                entry["value"] = val
            out_targets.append(entry)
        return {"kind": "type-multi", "targets": out_targets}

    target = cursor.get("target") or {}
    out_target: dict = {"bbox": target.get("bbox")}
    sel = target.get("selector")
    if sel is not None:
        out_target["selector"] = sel
    out: dict = {"kind": kind, "target": out_target}
    if kind == "type":
        val = cursor.get("value")
        if val is not None:
            out["value"] = val
    return out


def _cursor_has_complete_bboxes(cursor: Optional[dict]) -> bool:
    """True when every bbox referenced by the cursor dict is non-empty."""
    if cursor is None:
        return True
    if cursor.get("kind") == "type-multi":
        targets = cursor.get("targets") or []
        return bool(targets) and all(t.get("bbox") for t in targets)
    target = cursor.get("target") or {}
    return bool(target.get("bbox"))


def _filter_type_multi_for_frame(
    behavior: Optional[dict],
    viewport: Optional[dict],
    already_animated: set,
) -> Optional[dict]:
    """Return a copy of a ``type-multi`` cursor behavior keeping only targets
    that (a) are in-viewport for this frame and (b) have not yet been animated
    in an earlier frame.

    ``already_animated`` is mutated in place: every selector kept for this
    frame is added to it so subsequent frames skip it. This ensures a form
    field visible in the overlap zone between two frames is animated exactly
    once — in the first frame it appears in.
    """
    if not behavior or behavior.get("kind") != "type-multi":
        return behavior
    if not viewport:
        return behavior
    vh = viewport.get("height")
    vw = viewport.get("width")
    if not vh or not vw:
        return behavior
    kept = []
    for t in behavior.get("targets") or []:
        bbox = t.get("bbox")
        if not bbox:
            continue
        sel = t.get("selector")
        if sel is not None and sel in already_animated:
            continue
        cy = bbox.get("y", 0) + bbox.get("height", 0) / 2
        cx = bbox.get("x", 0) + bbox.get("width", 0) / 2
        if 0 <= cy <= vh and 0 <= cx <= vw:
            kept.append(t)
            if sel is not None:
                already_animated.add(sel)
    if not kept:
        return None
    return {"kind": "type-multi", "targets": kept}


class GuideAssembler:
    """Assembles user guides from scenario files and captured screenshots."""

    def __init__(
        self,
        config: SteppyConfig,
        templates_dir: Optional[Path] = None,
        project_root: Optional[Path] = None,
    ):
        """
        Initialize the guide assembler.

        Args:
            config: Validated Steppy configuration
            templates_dir: Explicit template directory (error if missing).
            project_root: Root whose ``templates/`` overrides the bundled ones.
                          Defaults to the current working directory.

        See ``_templating.template_search_paths`` for the precedence rule,
        which is shared with the generator and build assembler.
        """
        self.config = config

        # Memo for resolve_capture_segments. Instance-scoped, and positive
        # answers only — see resolve_capture_segments / clear_capture_cache.
        self._scenario_dir_cache: dict[
            tuple[str, str, Optional[str], Optional[str]],
            tuple[Optional[str], Optional[str]],
        ] = {}

        # Memo for _parse_scenarios_dir, same lifetime/rationale.
        self._scenarios_cache: dict[str, list[Scenario]] = {}

        self.env = make_template_env(templates_dir, project_root)
        register_jinja_filters(self.env)

    def _filter_steps_by_role(
        self,
        scenario: Scenario,
        role: str,
        layout: Optional[str] = None,
    ) -> list[tuple[int, Step]]:
        """
        Filter steps to only include those with screenshots for the given role.

        When ``layout`` is provided, additionally drops steps whose ``layouts``
        whitelist excludes it. Steps marked ``only-in: test`` are always
        dropped (guides only ever render the guide-mode artifact set).

        Args:
            scenario: Parsed scenario
            role: Role name (e.g., "editor", "reviewer")
            layout: Optional layout name (e.g., "desktop", "mobile"). When None,
                no layout filtering is applied.

        Returns:
            ``(original_index, step)`` pairs for the steps that have screenshots
            for this role and (if specified) run in the requested layout.
            ``original_index`` is 1-based and refers to the step's position in
            the unfiltered ``scenario.steps`` — callers need it to locate
            screenshots on disk, and recovering it afterwards would mean a
            ``list.index()`` scan whose ``==`` is a full pydantic deep compare.
        """
        filtered = []
        for original_index, step in enumerate(scenario.steps, start=1):
            # Skip steps without screenshots
            if step.screenshot is False or step.screenshot is None:
                continue

            # Include if screenshot is "all" or matches the role
            if step.screenshot != "all" and step.screenshot != role:
                continue

            # Drop test-only steps (guide assembly is the guide-mode artifact set)
            if step.only_in == "test":
                continue

            # Drop layout-restricted steps that don't match the requested layout
            if layout and step.layouts and layout not in step.layouts:
                continue

            filtered.append((original_index, step))

        return filtered

    def _discover_layouts(
        self,
        scenario_slug: str,
        screenshots_dir: Path,
        locale: Optional[str] = None,
    ) -> list[str]:
        """Discover layout subdirs under ``<slug>/<locale>/`` (or ``<slug>/``
        when ``locale`` is None). A "layout" is any subdir that itself contains
        ``step-*.png`` files.

        Returns ``[]`` when no per-layout subdirs exist — the project either
        captured flat (``<slug>/step-NN.png``) or per-locale only
        (``<slug>/<locale>/step-NN.png``). The caller treats ``[]`` as the
        single-layout / back-compat path.

        Used by ``generate_guides_for_tag`` to render one guide per
        (locale, layout) when ``steppy run --guide`` was driven with
        ``screenshot.layouts`` configured, so screenshots in
        ``<slug>/<locale>/<layout>/`` get picked up automatically.
        """
        if locale:
            base = screenshots_dir / scenario_slug / locale
            # Under a locale dir every capture-bearing subdir is a layout.
            not_layouts: set[str] = set()
        else:
            base = screenshots_dir / scenario_slug
            # At the flat base a per-locale capture dir and a per-layout one are
            # structurally identical (`<slug>/fr/` vs `<slug>/desktop/`), so name
            # is the only signal: anything named like a configured locale is a
            # locale dir, not a layout.
            not_layouts = set(self.config.locales or ())
        if not base.is_dir():
            return []
        return sorted(child.name for child in base.iterdir() if child.name not in not_layouts and _has_captures(child))

    def resolve_capture_segments(
        self,
        scenario_slug: str,
        screenshots_dir: Path,
        locale: Optional[str] = None,
        layout: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Which ``(locale, layout)`` path segments actually exist on disk.

        **Single source of truth for capture-directory shape.** Three consumers
        have to agree on this, or a build silently produces broken images:

          * the READ side — :meth:`_resolve_scenario_dir`, i.e. which PNGs and
            bbox sidecars the guide HTML is actually built from;
          * the COPY side — ``build.py``'s ``_copy_screenshots``, i.e. where
            those files land in the build's assets tree;
          * the URL side — ``build.py``'s ``make_url_fn``, i.e. what the
            ``<img src>`` says.

        They used to answer it independently and disagreed in three ways, most
        damagingly when ``layouts:`` was configured but the screenshots on disk
        had no per-layout subdirectory: the URL gained a ``/<layout>/`` segment
        that the copier never created, so every screenshot 404'd while the build
        reported success. Returning the *segments* (rather than a resolved Path)
        is what lets the copier mirror them into the assets tree and the URL
        builder join them into a URL, from one decision.

        Resolution order, most specific first — **locale beats layout**, because
        the locale determines the UI language actually visible in the capture,
        and showing the wrong language is worse than showing the wrong viewport:

          1. ``<slug>/<locale>/<layout>/`` → ``(locale, layout)``
          2. ``<slug>/<locale>/``          → ``(locale, None)``
          3. ``<slug>/<layout>/``          → ``(None, layout)``
          4. otherwise                     → ``(None, None)``  (flat/legacy)

        Falling back to a coarser shape is deliberate: single-locale and
        single-layout projects keep building without re-capturing.

        A rung matches only when the directory actually holds captures (see
        :func:`_has_captures`) — merely existing is not enough. Two shapes
        depend on that: an empty ``<slug>/<locale>/`` left behind by a crashed
        capture must not shadow a working coarser one, and a
        ``<slug>/<locale>/`` that holds only a ``<layout>/`` subdir holds no
        captures of its own.

        Memoized — the answer depends only on the four arguments, which are
        constant for a whole ``prepare_guide_steps`` call, yet the read-side
        callers (screenshot path, frame path, sidecar) each ask per step. A
        *negative* answer is never memoized: nothing stops a caller from holding
        an assembler across a capture, and pinning "nothing on disk" for the
        object's lifetime would make it blind to screenshots written afterwards.
        Use :meth:`clear_capture_cache` to drop positive answers too.
        """
        cache_key = (scenario_slug, str(screenshots_dir), locale, layout)
        cached = self._scenario_dir_cache.get(cache_key)
        if cached is not None:
            return cached

        base = screenshots_dir / scenario_slug
        if locale and layout and _has_captures(base / locale / layout):
            segments = (locale, layout)
        elif locale and _has_captures(base / locale):
            segments = (locale, None)
        elif layout and _has_captures(base / layout):
            segments = (None, layout)
        else:
            # Flat/legacy, or nothing captured yet. Deliberately not memoized —
            # see the docstring.
            return (None, None)

        self._scenario_dir_cache[cache_key] = segments
        return segments

    def clear_capture_cache(self) -> None:
        """Forget memoized capture-directory resolutions.

        Call after writing screenshots if this assembler was built beforehand.
        """
        self._scenario_dir_cache.clear()

    def _resolve_scenario_dir(
        self,
        scenario_slug: str,
        screenshots_dir: Path,
        locale: Optional[str] = None,
        layout: Optional[str] = None,
    ) -> Path:
        """The directory holding this scenario's screenshots — read side.

        Thin wrapper over :meth:`resolve_capture_segments`, which owns the rules.
        """
        segments = self.resolve_capture_segments(scenario_slug, screenshots_dir, locale, layout)
        path = screenshots_dir / scenario_slug
        for segment in segments:
            if segment:
                path = path / segment
        return path

    def _get_screenshot_path(
        self,
        scenario_slug: str,
        step_index: int,
        screenshots_dir: Path,
        locale: Optional[str] = None,
        layout: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Get the path to a screenshot file if it exists.

        Args:
            scenario_slug: Scenario slug (e.g., "cross--order-lifecycle")
            step_index: Step index (1-based)
            screenshots_dir: Base screenshots directory
            locale: Locale code; resolves the per-locale subdir if it exists.
            layout: Layout name; resolves the per-layout subdir if it exists.

        Returns:
            Path to screenshot if it exists, None otherwise
        """
        screenshot_dir = self._resolve_scenario_dir(scenario_slug, screenshots_dir, locale, layout)
        screenshot_path = screenshot_dir / f"{step_stem(step_index)}.png"

        if screenshot_path.exists():
            return screenshot_path
        return None

    def _load_sidecar(
        self,
        scenario_slug: str,
        step_index: int,
        screenshots_dir: Path,
        frame: Optional[str] = None,
        locale: Optional[str] = None,
        layout: Optional[str] = None,
    ) -> Optional[dict]:
        """Load a sidecar JSON if present. None on any failure.

        When ``frame`` is None, loads ``step-NN.json``. When ``frame`` is set
        (e.g. ``"a"``, ``"b"``), loads ``step-NN-{frame}.json``. ``locale``
        and ``layout`` pick the appropriate subdirs when they exist.
        """
        stem = step_stem(step_index, frame)
        path = self._resolve_scenario_dir(scenario_slug, screenshots_dir, locale, layout) / f"{stem}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _frame_screenshot_path(
        self,
        scenario_slug: str,
        step_index: int,
        screenshots_dir: Path,
        frame: Optional[str] = None,
        locale: Optional[str] = None,
        layout: Optional[str] = None,
    ) -> Optional[Path]:
        """Return the PNG path for a (step, frame) pair if it exists."""
        stem = step_stem(step_index, frame)
        p = self._resolve_scenario_dir(scenario_slug, screenshots_dir, locale, layout) / f"{stem}.png"
        return p if p.exists() else None

    def prepare_guide_steps(
        self,
        scenario: Scenario,
        role: str,
        locale: str,
        screenshots_dir: Path,
        screenshot_url_fn: "Callable[[str, int, Optional[str]], str] | None" = None,
        layout: Optional[str] = None,
    ) -> list[dict]:
        """Prepare guide step dicts for template rendering.

        This is the shared logic used by both ``generate_guide`` (standalone HTML)
        and the ``steppy build`` pipeline (Angular fragments).

        Args:
            scenario: Parsed scenario
            role: Role name (e.g., "editor", "reviewer")
            locale: Locale code (e.g., "fr", "en")
            screenshots_dir: Base screenshots directory
            screenshot_url_fn: Custom screenshot URL resolver. Called with
                ``(scenario_slug, step_index, frame_key)`` and must return
                the URL/path string to use in the template. If ``None``,
                uses the default relative-path convention.
            layout: Optional layout name (e.g., "desktop", "mobile"). When
                provided, only steps that run in this layout are included and
                screenshots are loaded from the per-layout subdirectory.

        Returns:
            List of step dicts ready for Jinja2 template rendering.

        Raises:
            ValueError: If screenshots are missing or guide-text is missing
                for the requested locale.
        """
        filtered_steps = self._filter_steps_by_role(scenario, role, layout=layout)

        if not filtered_steps:
            layout_suffix = f" / layout '{layout}'" if layout else ""
            raise ValueError(
                f"No steps with screenshots found for role '{role}'{layout_suffix} in scenario '{scenario.slug}'"
            )

        # Check that all steps have guide-text for this locale
        missing_guide_text_indices = []
        for original_index, step in filtered_steps:
            if not step.guide_text or not step.guide_text.get(locale):
                missing_guide_text_indices.append(original_index)

        if missing_guide_text_indices:
            raise ValueError(f"Missing guide-text for locale '{locale}' in steps: {missing_guide_text_indices}")

        def _default_screenshot_url(slug: str, step_index: int, frame_key: Optional[str]) -> str:
            stem = step_stem(step_index, frame_key)
            return f"../../../screenshots/{slug}/{stem}.png"

        url_fn = screenshot_url_fn or _default_screenshot_url

        guide_steps: list[dict] = []
        for original_index, filtered_step in filtered_steps:
            # Detect multi-frame capture (fill-form: step-NN-a, -b, -c, …)
            frame_keys: list[Optional[str]] = []
            for letter in "abcdefghijklmnopqrstuvwxyz":
                if self._frame_screenshot_path(
                    scenario.slug,
                    original_index,
                    screenshots_dir,
                    frame=letter,
                    locale=locale,
                    layout=layout,
                ):
                    frame_keys.append(letter)
                else:
                    break
            if not frame_keys:
                frame_keys = [None]
                if not self._get_screenshot_path(
                    scenario.slug,
                    original_index,
                    screenshots_dir,
                    locale=locale,
                    layout=layout,
                ):
                    layout_suffix = f" / layout '{layout}'" if layout else ""
                    raise ValueError(
                        f"Missing screenshot for step {original_index}{layout_suffix} in scenario '{scenario.slug}'"
                    )

            frames = []
            already_animated: set = set()
            for fk in frame_keys:
                screenshot_ref = url_fn(scenario.slug, original_index, fk)
                sidecar = self._load_sidecar(
                    scenario.slug,
                    original_index,
                    screenshots_dir,
                    frame=fk,
                    locale=locale,
                    layout=layout,
                )
                cursor_behavior = _compute_cursor_behavior(filtered_step, sidecar)
                viewport = (sidecar or {}).get("viewport")
                if len(frame_keys) > 1:
                    cursor_behavior = _filter_type_multi_for_frame(cursor_behavior, viewport, already_animated)
                frames.append(
                    {
                        "screenshot": screenshot_ref,
                        "viewport": viewport,
                        "cursor": cursor_behavior,
                    }
                )

            first = frames[0]
            guide_content = filtered_step.guide_text.get(locale)
            guide_steps.append(
                {
                    "index": len(guide_steps) + 1,
                    "title": guide_content.title if guide_content else "",
                    "subtitle": guide_content.subtitle if guide_content else None,
                    "text": guide_content.text if guide_content else None,
                    "screenshot": first["screenshot"],
                    "viewport": first["viewport"],
                    "cursor": first["cursor"],
                    "frames": frames,
                }
            )

        return guide_steps

    def prepare_interactive_manifest(
        self,
        scenario: Scenario,
        role: str,
        screenshots_dir: Path,
        locales: list[str],
        layouts: list[str],
        default_locale: str,
        default_layout: Optional[str],
        title: dict[str, str],
        cursor_timings: dict,
        screenshot_url_fn: "Callable[[str, int, Optional[str], str, Optional[str]], str]",
        manifest_version: str = INTERACTIVE_MANIFEST_VERSION,
        cursor_mode: Optional[str] = None,
        default_template: str = "overlay",
    ) -> dict:
        """Build an interactive guide manifest from a scenario.

        Reuses ``prepare_guide_steps`` per (locale, layout) pair and merges the
        results into the manifest shape consumed by ``<steppy-guide>`` and
        validated by [packages/player/src/manifest/schema.ts].

        Pairs are merged by list POSITION, not by scenario step number, because
        the twin-branch authoring pattern writes one logical step twice — once
        per layout — when the DOM path differs (a sidebar link on desktop, a
        hamburger drawer on mobile). Position ``i`` is therefore "step i of this
        layout's path"; toggling layout mid-guide keeps the step index, which in
        a complementary-whitelist scenario may land on a different logical
        moment. That is the intended semantics and the only one a single
        ``steps[]`` array can express. What the manifest cannot express is a
        divergent step *count* per layout, which is fatal.

        Args:
            scenario: Parsed scenario (must have ``guide.enabled`` set true upstream).
            role: Role name (must be one of ``config.apps``).
            screenshots_dir: Base screenshots directory.
            locales: Locales to include in the manifest. Order is preserved.
            layouts: Layouts to include. Empty list signals a layout-less build;
                the manifest emits a synthetic ``"_"`` layout key.
            default_locale: Must appear in ``locales``.
            default_layout: Must appear in ``layouts`` when non-empty, else None.
            title: LocalizedText for the guide title (locale → string).
            cursor_timings: Cursor timing block (typically
                ``scenario.guide.cursor.model_dump()``).
            screenshot_url_fn: Callable that resolves a screenshot URL for a
                given (slug, step_index, frame_key, locale, layout). The
                ``layout`` arg is None when ``layouts`` is empty.
            manifest_version: Manifest format version. Defaults to the supported
                version; bumped when the schema changes incompatibly.
            cursor_mode: Optional cursor presentation override merged into the
                manifest's ``cursor`` block when set.
            default_template: Per-guide default presentation template
                (``sidebar`` | ``overlay`` | ``inline``). Emitted as the
                top-level ``defaultTemplate``; the player resolves attr →
                persisted → this → ``overlay``.

        Returns:
            A JSON-serializable manifest dict.

        Raises:
            ValueError: On any inconsistency that the schema would reject
                (empty locales, default_locale not in locales, divergent step
                counts across (locale, layout), missing bbox on an interactive
                step, etc.). The error message names the failing step so the
                author can fix the scenario.
        """
        if not locales:
            raise ValueError("prepare_interactive_manifest: locales must be non-empty")
        if default_locale not in locales:
            raise ValueError(
                f"prepare_interactive_manifest: default_locale '{default_locale}' is not in locales {locales}"
            )
        if layouts:
            if default_layout is None:
                raise ValueError("prepare_interactive_manifest: default_layout must be set when layouts is non-empty")
            if default_layout not in layouts:
                raise ValueError(
                    f"prepare_interactive_manifest: default_layout '{default_layout}' is not in layouts {layouts}"
                )
        elif default_layout is not None:
            raise ValueError("prepare_interactive_manifest: default_layout must be None when layouts is empty")

        # Title must cover every declared locale (matches schema rule).
        missing_titles = [loc for loc in locales if loc not in title]
        if missing_titles:
            raise ValueError(
                f"prepare_interactive_manifest: title is missing locales {missing_titles}; declared locales: {locales}"
            )

        # Layout dimension: iterate None when layouts is empty (passes through
        # to prepare_guide_steps unchanged), key the manifest under "_" instead.
        layout_iter: list[Optional[str]] = list(layouts) if layouts else [None]

        def layout_key_for(layout: Optional[str]) -> str:
            return layout if layout is not None else _LAYOUTLESS_KEY

        # Call prepare_guide_steps once per (locale, layout). Wrap the 5-arg
        # URL fn into the 3-arg signature prepare_guide_steps expects.
        per_pair: dict[tuple[str, Optional[str]], list[dict]] = {}
        for locale in locales:
            for layout in layout_iter:

                def _bound(
                    slug: str,
                    step_idx: int,
                    frame_key: Optional[str],
                    _locale: str = locale,
                    _layout: Optional[str] = layout,
                ) -> str:
                    return screenshot_url_fn(slug, step_idx, frame_key, _locale, _layout)

                pair_steps = self.prepare_guide_steps(
                    scenario=scenario,
                    role=role,
                    locale=locale,
                    screenshots_dir=screenshots_dir,
                    screenshot_url_fn=_bound,
                    layout=layout,
                )
                per_pair[(locale, layout)] = pair_steps

        # Per-layout filtered step lists — the join table for the loop below.
        # Position `i` is the same MANIFEST step in every layout, but NOT
        # necessarily the same SCENARIO step: the desktop/mobile twin-branch
        # pattern deliberately authors one logical step twice (a sidebar link on
        # desktop, a hamburger drawer on mobile), so the layouts cover
        # complementary `original_index` values by design. Divergent indices are
        # therefore expected, not an error; what the manifest cannot express is
        # a divergent step COUNT (see the guard below).
        #
        # Layout is the only filter dimension — locale never affects
        # `_filter_steps_by_role` — so one call per layout covers every
        # (locale, layout) pair, and `canonical_filtered` reuses this map rather
        # than filtering a third time.
        per_layout_filtered: dict[Optional[str], list[tuple[int, Step]]] = {
            layout: self._filter_steps_by_role(scenario, role, layout=layout) for layout in layout_iter
        }

        # Step counts must agree across every (locale, layout) pair: the manifest
        # is ONE step list with a layout toggle, and the loop below indexes every
        # pair positionally, so an extra capture in one layout has no step slot
        # to live in. Locale does not affect step filtering, so a mismatch on
        # that axis means something else went wrong (e.g. a capture shape that
        # resolved differently per locale) — fatal either way.
        counts = {pair: len(steps) for pair, steps in per_pair.items()}
        if len(set(counts.values())) > 1:
            breakdown = ", ".join(f"({loc},{lay or '_'})={n}" for (loc, lay), n in counts.items())
            coverage = "; ".join(
                f"{lay or '_'} covers scenario steps {[i for i, _ in steps]}"
                for lay, steps in per_layout_filtered.items()
            )
            raise ValueError(
                f"Interactive manifest for '{scenario.slug}': non-uniform step "
                f"coverage across (locale, layout) pairs ({breakdown}). {coverage}. "
                f"Interactive mode renders one step list with a layout toggle, so "
                f"every layout must contribute the same number of captured steps. "
                f"A step restricted with 'layouts: [desktop]' needs a matching "
                f"'layouts: [mobile]' twin so the counts balance."
            )
        total = next(iter(set(counts.values())))

        # Cursor 'interactive' shape must agree across pairs. Per-frame bboxes
        # are allowed to differ (different viewport, different element rect)
        # but whether the step is interactive at all must not.
        canonical_pair = (default_locale, default_layout)
        canonical_steps = per_pair[canonical_pair]
        canonical_filtered = per_layout_filtered[default_layout]

        # Positional-join smell test. Layout twins always share an action, so a
        # divergence here means the join lined up two unrelated steps and the
        # layout-invariant fields (action, interactive, screenshotWhen,
        # waitAfter — all sourced from the canonical layout) are wrong for the
        # other layouts. Warn rather than raise: none of those fields drives
        # player behaviour (`action` only rides the `steppy:step` event
        # payload), and the one that does matter — whether the step waits for a
        # click — is separately and fatally guarded by the cursor-presence check
        # further down. Raising here would re-create the build breakage that
        # over-strict cross-layout validation caused in the first place.
        if len(layout_iter) > 1:
            for i in range(total):
                actions = {lay or "_": steps[i][1].action for lay, steps in per_layout_filtered.items()}
                if len(set(actions.values())) > 1:
                    indices = {lay or "_": steps[i][0] for lay, steps in per_layout_filtered.items()}
                    print(
                        f"WARNING: interactive manifest for '{scenario.slug}' step {i + 1}: "
                        f"layouts disagree on action ({actions}) at scenario steps {indices}. "
                        f"Check that the layout twins are paired in source order.",
                        file=sys.stderr,
                    )

        def _content_for(step_obj: Step, loc: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
            """``(title, text, subtitle)`` for one step in one locale.

            ``title`` is the short per-step heading (sidebar timeline); ``text``
            is the instruction body. Both are nullable where guide-text is
            absent (like ``subtitle``); the player falls back to ``text``, and
            ``text`` itself falls back to ``title`` — which is why this cannot
            read ``prepare_guide_steps`` output, where that second fallback does
            not exist.
            """
            gc = step_obj.guide_text.get(loc) if step_obj.guide_text else None
            if gc is None:
                return (None, None, None)
            return (gc.title, gc.text if gc.text is not None else gc.title, gc.subtitle)

        manifest_steps: list[dict] = []
        for i in range(total):
            canonical_step = canonical_steps[i]
            original_index, scenario_step = canonical_filtered[i]
            canonical_cursor = canonical_step.get("cursor")

            # Step-level text is the canonical layout's, resolved at build time
            # (not at runtime — the spec's "one manifest, all locales inline"
            # rule). Layouts whose wording differs override it per capture,
            # below.
            title_per_locale: dict[str, Optional[str]] = {}
            text_per_locale: dict[str, Optional[str]] = {}
            subtitle_per_locale: dict[str, Optional[str]] = {}
            for locale in locales:
                (
                    title_per_locale[locale],
                    text_per_locale[locale],
                    subtitle_per_locale[locale],
                ) = _content_for(scenario_step, locale)

            # Reject empty/missing text for any locale — the schema would too.
            empty_text = [loc for loc, t in text_per_locale.items() if not t]
            if empty_text:
                raise ValueError(
                    f"Interactive manifest for '{scenario.slug}' step "
                    f"{original_index}: empty text for locales {empty_text}"
                )

            # captures[locale][layout].frames[]
            captures: dict[str, dict[str, dict]] = {}
            step_is_interactive = canonical_cursor is not None
            for locale in locales:
                captures[locale] = {}
                for layout in layout_iter:
                    pair_steps = per_pair[(locale, layout)]
                    src = pair_steps[i]
                    src_cursor_for_step = src.get("cursor")

                    # Cursor "shape" (None vs not-None) must be uniform across
                    # pairs for a given step.
                    if (src_cursor_for_step is None) != (canonical_cursor is None):
                        raise ValueError(
                            f"Interactive manifest for '{scenario.slug}' step "
                            f"{original_index}: cursor presence differs between "
                            f"({default_locale},{default_layout or '_'}) and "
                            f"({locale},{layout or '_'}). All pairs must agree."
                        )

                    manifest_frames: list[dict] = []
                    for src_frame in src["frames"]:
                        viewport = src_frame.get("viewport") or {}
                        raw_cursor = src_frame.get("cursor")
                        norm_cursor = _normalize_cursor_for_manifest(raw_cursor)

                        if step_is_interactive and norm_cursor is not None:
                            if not _cursor_has_complete_bboxes(norm_cursor):
                                raise ValueError(
                                    f"Interactive manifest for "
                                    f"'{scenario.slug}' step {original_index} "
                                    f"({locale}/{layout or '_'}): cursor target "
                                    f"is missing a bbox. The sidecar JSON may "
                                    f"be missing or the selector failed at "
                                    f"capture time."
                                )

                        capture_w = viewport.get("width")
                        capture_h = viewport.get("height")
                        if not capture_w or not capture_h:
                            raise ValueError(
                                f"Interactive manifest for '{scenario.slug}' "
                                f"step {original_index} ({locale}/"
                                f"{layout or '_'}): missing viewport "
                                f"dimensions in sidecar."
                            )

                        manifest_frames.append(
                            {
                                "screenshot": src_frame["screenshot"],
                                "captureSize": {
                                    "width": capture_w,
                                    "height": capture_h,
                                },
                                "cursor": norm_cursor,
                            }
                        )
                    capture: dict = {"frames": manifest_frames}

                    # Layout-varying guide text rides in the layout-scoped bag,
                    # beside the frames it describes. The twin-branch pattern
                    # authors one logical step twice — "click the row of the
                    # table" on desktop, "tap the card" on mobile — and only the
                    # canonical branch's wording reaches the step level, so
                    # without this the mobile viewer reads desktop's
                    # instructions under a mobile screenshot.
                    #
                    # Emitted ONLY when it differs from the step-level triple:
                    # every guide with no layout twins (and every single-layout
                    # or layout-less build, where canonical IS the only layout)
                    # then stays byte-identical, which is also what keeps the
                    # video render cache from churning.
                    layout_content = _content_for(per_layout_filtered[layout][i][1], locale)
                    if layout_content != (
                        title_per_locale[locale],
                        text_per_locale[locale],
                        subtitle_per_locale[locale],
                    ):
                        if not layout_content[1]:
                            raise ValueError(
                                f"Interactive manifest for '{scenario.slug}' step "
                                f"{per_layout_filtered[layout][i][0]} ({locale}/{layout or '_'}): "
                                f"empty text. Every layout twin needs its own guide-text."
                            )
                        capture["content"] = {
                            "title": layout_content[0],
                            "text": layout_content[1],
                            "subtitle": layout_content[2],
                        }
                    captures[locale][layout_key_for(layout)] = capture

            manifest_steps.append(
                {
                    "id": f"step-{i + 1}",
                    "index": i + 1,
                    "action": scenario_step.action,
                    "interactive": step_is_interactive,
                    "screenshotWhen": scenario_step.screenshot_when or "after",
                    "waitAfter": int(scenario_step.wait_after or 0),
                    "title": title_per_locale,
                    "text": text_per_locale,
                    "subtitle": subtitle_per_locale,
                    "captures": captures,
                }
            )

        # Cursor block: pass through the timings unchanged and merge the
        # optional ``mode`` only when set. Absent ``mode`` keeps the manifest
        # byte-identical to pre-feature output so existing snapshots survive.
        cursor_block: dict = dict(cursor_timings)
        if cursor_mode is not None:
            cursor_block["mode"] = cursor_mode

        return {
            "version": manifest_version,
            "slug": scenario.slug,
            "mode": "interactive",
            "role": role,
            "title": dict(title),
            "locales": list(locales),
            "defaultLocale": default_locale,
            "layouts": list(layouts),
            "defaultLayout": default_layout,
            "cursor": cursor_block,
            "defaultTemplate": default_template,
            "steps": manifest_steps,
        }

    @staticmethod
    def _relative_url_fn(capture_dir: Path, target_dir: Path) -> Callable[[str, int, Optional[str]], str]:
        """Screenshot-URL builder for a page written to ``target_dir`` reading
        captures from ``capture_dir``.

        ``--output-dir`` and ``--screenshots-dir`` are independent CLI options,
        so no fixed ``../../..`` prefix can be correct: it depends on both paths
        AND on which capture shape resolved (flat / locale / locale+layout).
        Shared by the per-guide pages and the handbook, which sit in the same
        directory and therefore need the identical URLs.
        """

        def fn(slug: str, step_index: int, frame_key: Optional[str]) -> str:
            stem = step_stem(step_index, frame_key)
            try:
                relative = os.path.relpath(capture_dir.resolve(), target_dir.resolve())
            except ValueError:
                # Windows: no relative path exists across drives. Emit a real
                # file:// URL rather than a bare `D:/...` path — the latter is
                # not a URL, so a browser resolves it against the document and
                # every image comes out blank (the video renderer loads its
                # harness over file://, where this bites hardest).
                return (capture_dir / f"{stem}.png").resolve().as_uri()
            return f"{Path(relative).as_posix()}/{stem}.png"

        return fn

    def scenario_by_slug(self, scenarios_dir: Path, slug: str) -> Scenario:
        """Look a parsed scenario up by slug, reusing the per-assembler cache.

        ``generate_guides_for_tag`` returns results keyed by slug but not the
        scenarios themselves; callers that need to go back to the source (the
        video pass, which builds its own manifest) would otherwise re-parse the
        whole directory.
        """
        for scenario in self._parse_scenarios_dir(scenarios_dir):
            if scenario.slug == slug:
                return scenario
        raise ValueError(f"no scenario with slug '{slug}' in {scenarios_dir}")

    def prepare_video_manifest(
        self,
        scenario: Scenario,
        role: str,
        locale: str,
        output_dir: Path,
        screenshots_dir: Path,
        layout: Optional[str] = None,
        title: Optional[str] = None,
    ) -> dict:
        """Manifest for one (locale, layout), for the ``steppy guide --video`` path.

        ``steppy build`` has a build config to derive URLs from; ``steppy guide``
        has only ``--output-dir`` and ``--screenshots-dir``, and no ``guide.json``
        on disk at all. So the manifest is built here, in the class that owns the
        capture-shape resolution — routing through the same ``_relative_url_fn``
        the HTML pages use, so the video harness (which sits in that same
        directory) resolves exactly the screenshots the page would.

        Note this validates more strictly than :meth:`generate_guide`: a step
        missing a bbox or a locale missing ``guide-text`` raises here while the
        HTML guide renders happily. Callers should treat a failure as "skip this
        video", not "abort the run".
        """
        target_dir = output_dir / role / locale
        if layout:
            target_dir = target_dir / layout
        capture_dir = self._resolve_scenario_dir(scenario.slug, screenshots_dir, locale, layout)
        relative_url = self._relative_url_fn(capture_dir, target_dir)

        def screenshot_url(slug, step_index, frame_key, _locale, _layout):
            return relative_url(slug, step_index, frame_key)

        return self.prepare_interactive_manifest(
            scenario=scenario,
            role=role,
            screenshots_dir=screenshots_dir,
            locales=[locale],
            layouts=[layout] if layout else [],
            default_locale=locale,
            default_layout=layout,
            title={locale: title or scenario.title or scenario.slug},
            cursor_timings=scenario.guide.cursor.model_dump() if scenario.guide else {},
            screenshot_url_fn=screenshot_url,
        )

    def generate_guide(
        self,
        scenario: Scenario,
        role: str,
        locale: str,
        output_dir: Path,
        screenshots_dir: Path,
        layout: Optional[str] = None,
    ) -> dict[str, str]:
        """
        Generate Markdown and HTML guides for a scenario, role, and locale.

        Args:
            scenario: Parsed scenario
            role: Role name (e.g., "editor", "reviewer")
            locale: Locale code (e.g., "fr", "en")
            output_dir: Base output directory (e.g., output/guides)
            screenshots_dir: Base screenshots directory (e.g., output/screenshots)
            layout: Optional layout name (e.g., "desktop", "mobile"). When
                provided, screenshots are loaded from
                ``<slug>/<locale>/<layout>/`` and the output path includes a
                ``<layout>`` segment so per-layout guides don't overwrite each
                other. When None, behavior matches the pre-layout era.

        Returns:
            Dict with keys "markdown" and "html" containing file paths

        Raises:
            ValueError: If screenshots are missing or guide-text is missing for locale
        """
        # Output paths must be known BEFORE rendering the steps: the image URLs
        # are relative to the page's own directory. Per-layout output gets its
        # own subdir so each (locale, layout) combination ships an independent
        # .md + .html pair.
        role_locale_dir = output_dir / role / locale
        if layout:
            target_dir = role_locale_dir / layout
        else:
            target_dir = role_locale_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        # Screenshot URLs are computed from the page's real location to the
        # capture directory that will actually be read. ``--output-dir`` and
        # ``--screenshots-dir`` are independent CLI options, so no fixed
        # ``../../..`` prefix can be correct: it depends on both paths AND on
        # which capture shape resolved (flat / locale / locale+layout).
        capture_dir = self._resolve_scenario_dir(scenario.slug, screenshots_dir, locale, layout)
        _relative_screenshot_url = self._relative_url_fn(capture_dir, target_dir)

        guide_steps = self.prepare_guide_steps(
            scenario,
            role,
            locale,
            screenshots_dir,
            screenshot_url_fn=_relative_screenshot_url,
            layout=layout,
        )

        # Get the app config for the role to extract the display name
        app_config = self.config.apps.get(role)
        role_display_name = app_config.name if app_config else role.title()

        markdown_path = target_dir / f"{scenario.slug}.md"
        html_path = target_dir / f"{scenario.slug}.html"

        # Render Markdown guide
        md_template = self.env.get_template("guide.md.j2")
        markdown_content = md_template.render(
            title=scenario.title,
            role=role_display_name,
            steps=guide_steps,
        )
        markdown_path.write_text(markdown_content, encoding="utf-8")

        # Render HTML guide
        html_template = self.env.get_template("guide.html.j2")
        html_content = html_template.render(
            title=scenario.title,
            role=role_display_name,
            locale=locale,
            steps=guide_steps,
            cursor_timings=scenario.guide.cursor.model_dump(),
            cursor_iife=get_legacy_cursor_iife(),
        )
        html_path.write_text(html_content, encoding="utf-8")

        return {
            "markdown": str(markdown_path),
            "html": str(html_path),
        }

    def _parse_scenarios_dir(self, scenarios_dir: Path) -> list[Scenario]:
        """Parse every scenario in a directory, once per assembler.

        ``generate_guides_for_tag`` is called once per ``user-guide-*`` tag and
        used to re-glob and re-parse the whole directory each time, discarding
        all but the tagged scenarios. Parsing is not cheap (frontmatter, a
        PyYAML pass per nested block, full ``Step`` validation), so an N-tag
        project paid it N times over.

        Cached like ``_resolve_scenario_dir``: an assembler is built after the
        scenarios on disk are final and only reads them.
        """
        key = str(scenarios_dir)
        cached = self._scenarios_cache.get(key)
        if cached is None:
            cached = [parse_scenario_file(f) for f in scenarios_dir.glob("*.md")]
            self._scenarios_cache[key] = cached
        return cached

    def _role_for_tag(self, tag: str) -> str:
        """``user-guide-editor`` → ``editor``, validated against configured roles."""
        if not tag.startswith("user-guide-"):
            raise ValueError(f"Tag must be in format 'user-guide-{{role}}', got: {tag}")
        role = tag[len("user-guide-") :]
        if role not in self.config.apps:
            raise ValueError(f"Unknown role '{role}'. Available roles: {list(self.config.apps.keys())}")
        return role

    def generate_guides_for_tag(
        self,
        tag: str,
        locales: list[str],
        scenarios_dir: Path,
        output_dir: Path,
        screenshots_dir: Path,
    ) -> dict[str, dict]:
        """
        Generate guides for all scenarios matching a user-guide tag.

        Args:
            tag: Tag like "user-guide-editor" or "user-guide-reviewer"
            locales: List of locale codes to generate
            scenarios_dir: Directory containing scenario files
            output_dir: Base output directory for guides
            screenshots_dir: Base screenshots directory

        Returns:
            Dict mapping scenario slugs to generation results

        Raises:
            ValueError: If tag doesn't match user-guide-{role} pattern
        """
        role = self._role_for_tag(tag)

        # Find all scenarios with this tag
        results = {}
        for scenario in self._parse_scenarios_dir(scenarios_dir):
            if tag not in scenario.tags:
                continue

            scenario_results = {}
            for locale in locales:
                # Auto-discover per-layout subdirs under <slug>/<locale>/. When
                # screenshots were captured with ``screenshot.layouts``
                # configured, this returns the layout names; otherwise it
                # returns [] and we render a single guide for the locale.
                #
                # Fall back to the locale-less probe: `steppy run --screenshots
                # --layouts desktop,mobile` (no --locales) writes
                # <slug>/<layout>/ with no locale segment, and this command
                # always has a locale (the CLI defaults it to ["fr"]). Without
                # the fallback that whole capture shape is invisible and every
                # scenario reports "Missing screenshot" for PNGs on disk.
                layouts = self._discover_layouts(
                    scenario.slug,
                    screenshots_dir,
                    locale=locale,
                ) or self._discover_layouts(
                    scenario.slug,
                    screenshots_dir,
                    locale=None,
                )
                # Iterate at least once: [None] when no layouts so the
                # back-compat single-guide-per-locale path stays unchanged.
                for layout in layouts or [None]:
                    # Result key encodes both dimensions when layouts exist so
                    # the CLI can show "scenario (fr/desktop)" without losing
                    # per-layout granularity.
                    result_key = f"{locale}/{layout}" if layout else locale
                    try:
                        paths = self.generate_guide(
                            scenario,
                            role,
                            locale,
                            output_dir,
                            screenshots_dir,
                            layout=layout,
                        )
                        scenario_results[result_key] = {
                            "status": "success",
                            "markdown": paths["markdown"],
                            "html": paths["html"],
                            # Carried for the PDF footer (`--pdf`), which has
                            # only this dict to work from.
                            "title": scenario.title,
                        }
                    except Exception as e:
                        scenario_results[result_key] = {
                            "status": "error",
                            "error": str(e),
                        }

            results[scenario.slug] = scenario_results

        return results

    def generate_handbook(
        self,
        tag: str,
        locale: str,
        scenarios_dir: Path,
        output_dir: Path,
        screenshots_dir: Path,
        layout: Optional[str] = None,
        *,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        toc_label: str = "Contents",
        on_skip: Optional[Callable[[str, str], None]] = None,
    ) -> Optional[Path]:
        """Assemble every guide for one (tag, locale, layout) into a single
        "handbook" HTML page — a cover, a table of contents, then each guide.

        Written next to the per-guide pages so the screenshot URLs are the same
        relative paths (see :meth:`_relative_url_fn`). The result is a real
        artifact, not a temp file: it is a legitimate single-page read *and*
        what ``--handbook`` prints to PDF.

        Guides are ordered by slug — deterministic, unlike ``Path.glob``.
        A scenario that can't be assembled (missing capture / guide-text) is
        skipped rather than losing the whole handbook; ``on_skip(slug, reason)``
        reports it. Returns the written path, or ``None`` when nothing could be
        assembled.
        """
        role = self._role_for_tag(tag)

        target_dir = output_dir / role / locale
        if layout:
            target_dir = target_dir / layout
        target_dir.mkdir(parents=True, exist_ok=True)

        fragment_tpl = self.env.get_template("guide.fragment.html.j2")
        entries: list[dict] = []
        for scenario in sorted(self._parse_scenarios_dir(scenarios_dir), key=lambda s: s.slug or ""):
            if tag not in scenario.tags:
                continue
            capture_dir = self._resolve_scenario_dir(scenario.slug, screenshots_dir, locale, layout)
            try:
                guide_steps = self.prepare_guide_steps(
                    scenario,
                    role,
                    locale,
                    screenshots_dir,
                    screenshot_url_fn=self._relative_url_fn(capture_dir, target_dir),
                    layout=layout,
                )
            except Exception as e:
                if on_skip:
                    on_skip(scenario.slug or str(scenario.file_path), str(e))
                continue
            entries.append(
                {
                    "slug": scenario.slug,
                    "title": scenario.title,
                    # role=None: the cover carries the role once; a badge above
                    # every chapter would just be noise.
                    "html": fragment_tpl.render(
                        title=scenario.title,
                        role=None,
                        steps=guide_steps,
                        include_styles=False,
                    ),
                }
            )

        if not entries:
            return None

        app_config = self.config.apps.get(role)
        role_display_name = app_config.name if app_config else role.title()

        book_tpl = self.env.get_template("guide.book.html.j2")
        html_content = book_tpl.render(
            title=title or role_display_name,
            subtitle=subtitle,
            # The cover title already names the role; only badge it when the
            # caller overrode the title with something else.
            role=role_display_name if title else None,
            locale=locale,
            guides=entries,
            toc_label=toc_label,
            # Same loader the static target uses, so a project's own
            # templates/static/guides.css override styles the handbook too.
            guides_css=self.env.get_template("static/guides.css").render(),
        )
        handbook_path = target_dir / f"{role}-handbook.html"
        handbook_path.write_text(html_content, encoding="utf-8")
        return handbook_path
