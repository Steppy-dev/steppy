"""Per-locale tour text.

The ladder for a step and locale L is ``tour-text[L]`` → ``guide-text[L]``
(each: exact tag, then base language) → nothing. "Nothing" is legitimate: the
player then shows a generic, localised instruction chosen by the advance kind
("Click the highlighted element"). Languages are never mixed — a French tour
never borrows the English step text — and the compiler warns per step.

A locale is emitted for a tour when at least one kept step has authored text in
it; the default locale is always emitted.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from ..locales import base_language
from ..models import GuideText, GuideTextContent, Scenario, Step
from ..tour_spec import MAX_TEXT_CHARS, MAX_TITLE_CHARS
from .diagnostics import DiagSink
from .model import TourStepPlan
from .settings import ToursSettings, origin_of

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def content_for(step: Step, locale: str) -> Optional[GuideTextContent]:
    for text in (step.tour_text, step.guide_text):
        if text is not None:
            found = text.get(locale)
            if found is not None:
                return found
    return None


def title_text(content: Optional[GuideTextContent]) -> tuple[Optional[str], Optional[str]]:
    """``(title, body)`` for a popover.

    A legacy string ``guide-text: "Do this. Then that."`` stores the first
    sentence as the title *and* the whole string as the text; showing both would
    repeat the sentence, so the derived title is dropped. A block with a title
    and no text shows the title as the body.
    """
    if content is None:
        return None, None
    title = (content.title or "").strip() or None
    text = (content.text or "").strip() or None
    if text is None:
        return None, title
    if title is not None:
        legacy = text.split(".")[0].strip() if "." in text else text.strip()
        if title == legacy:
            title = None
    return title, text


def tour_title(scenario: Scenario, locale: str, default_locale: str) -> tuple[str, bool]:
    """``(title, fell_back)`` — ``tour.title`` per locale, else the scenario title.

    A per-locale map is read exact tag → base language → the default locale (and
    its base) — the rung ``pick_locale`` gives guide titles — and only then falls
    back to the scenario title. ``fell_back`` is True whenever the title is not
    in ``locale``'s own language; without a ``tour.title`` it is False (the caller
    reports that case once for the whole project).
    """
    wanted = scenario.tour.title if scenario.tour else None
    if isinstance(wanted, str):
        return wanted.strip(), False
    if isinstance(wanted, dict):
        own = (locale, base_language(locale))
        for key in (*own, default_locale, base_language(default_locale)):
            if key in wanted:
                return wanted[key].strip(), key not in own
        return scenario.title.strip(), True
    return scenario.title.strip(), False


def emitted_locales(scenario: Scenario, steps: list[TourStepPlan], settings: ToursSettings) -> tuple[str, ...]:
    out = []
    for locale in settings.locales:
        if locale == settings.default_locale or any(
            content_for(scenario.steps[p.step_index], locale) is not None for p in steps
        ):
            out.append(locale)
    return tuple(out)


def guide_url(scenario: Scenario, settings: ToursSettings, locale: str) -> Optional[str]:
    if not settings.guide_base_url:
        return None
    slug = scenario.tour.guide_slug if scenario.tour else None
    if slug is None and scenario.guide.enabled:
        slug = scenario.slug
    if not slug:
        return None
    return urljoin(settings.guide_base_url, f"{slug}/{locale}.html")


def leak_values(scenario: Scenario, steps: list[TourStepPlan]) -> dict[int, list[str]]:
    """Literal fixture values per kept scenario step (``${…}``/``{{…}}`` are not knowable)."""
    out: dict[int, list[str]] = {}
    for idx in sorted({p.step_index for p in steps}):
        step = scenario.steps[idx]
        values: list[Optional[str]] = []
        if step.action in ("fill", "select"):
            values.append(step.value)
        if step.action == "fill-form" and step.fields:
            values.extend(step.fields.values())
        if step.action == "select-option":
            values.append(step.search)
            if step.option and step.option.startswith("text:"):
                values.append(step.option[5:].strip().strip('"').strip("'"))
            values.extend(step.multiple or [])
        clean = [
            str(v).strip()
            for v in values
            if v is not None and "${" not in str(v) and "{{" not in str(v) and len(str(v).strip()) >= 2
        ]
        if clean:
            out[idx] = clean
    return out


def check_texts(
    scenario: Scenario,
    steps: list[TourStepPlan],
    texts: dict[str, list[tuple[Optional[str], Optional[str]]]],
    settings: ToursSettings,
    sink: DiagSink,
) -> None:
    """Leaks, links, limits, untargeted ``tour-text`` locales and generic-text warnings."""
    values = leak_values(scenario, steps)
    reported: set[tuple[int, str]] = set()
    for n, plan in enumerate(steps):
        step = scenario.steps[plan.step_index]
        for locale, rows in texts.items():
            title, body = rows[n]
            for label, value, limit in (("title", title, MAX_TITLE_CHARS), ("text", body, MAX_TEXT_CHARS)):
                if value is not None and len(value) > limit:
                    sink.add(
                        "limit-exceeded", f"the {locale} step {label} is longer than {limit} characters", step=step
                    )
            joined = " ".join(v for v in (title, body) if v)
            for value in values.get(plan.step_index, []):
                if (plan.step_index, value) in reported:
                    continue
                if re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", joined, re.IGNORECASE):
                    reported.add((plan.step_index, value))
                    sink.add(
                        "fixture-leak",
                        f"the {locale} text mentions the test value {value!r}; the tour would ask real users to type test data",
                        step=step,
                        hint="give the step a `tour-text` without it",
                    )
            for _label, url in _LINK.findall(joined):
                if not _link_allowed(url, settings):
                    sink.add(
                        "text-link-dropped",
                        f"the link {url!r} in the {locale} text will render as plain text "
                        "(only app routes and the guide site are linked)",
                        step=step,
                    )
    for idx in sorted({p.step_index for p in steps}):
        step = scenario.steps[idx]
        missing = [loc for loc in texts if content_for(step, loc) is None]
        if missing:
            sink.add(
                "generic-text",
                f"no text for {', '.join(missing)}; the player will show a generic instruction",
                step=step,
                hint="add `guide-text` or `tour-text`",
            )
        for label, text in (("tour-text", step.tour_text), ("guide-text", step.guide_text)):
            for key in _text_keys(text):
                if key in settings.locales or any(base_language(loc) == key for loc in settings.locales):
                    continue
                # Keys are read exactly (`GuideText.get`), while the overlay ignores case and
                # -/_: a near miss silently becomes the generic hint in the compiled tour.
                folded = key.casefold().replace("_", "-")
                near = next(
                    (c for loc in settings.locales for c in (loc, base_language(loc)) if c.casefold() == folded),
                    None,
                )
                if near is not None:
                    sink.add(
                        "tour-text-locale-untargeted",
                        f"{label} has a {key!r} entry; locale keys are read exactly — did you mean {near!r}?",
                        step=step,
                    )
                elif label == "tour-text":
                    sink.add(
                        "tour-text-locale-untargeted",
                        f"tour-text has a {key!r} entry, but tours do not target that locale",
                        step=step,
                    )


def _text_keys(text: Optional[GuideText]) -> list[str]:
    """The locale keys a ``guide-text``/``tour-text`` block sets, in authored order."""
    if text is None:
        return []
    keys = [n for n in ("fr", "en", "nl", "de") if getattr(text, n) is not None]
    return keys + list((text.model_extra or {}).keys())


def _resolved_path(path: str) -> str:
    """``path`` after dot-segment removal, as a browser resolves it (``%2e`` is a dot)."""
    out: list[str] = []
    segments = path.split("/")[1:]
    for n, seg in enumerate(segments):
        if seg.lower().replace("%2e", ".") in (".", ".."):
            if seg.lower().replace("%2e", ".") == ".." and out:
                out.pop()
            if n == len(segments) - 1:
                out.append("")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def _link_allowed(url: str, settings: ToursSettings) -> bool:
    if url.startswith("/") and not url.startswith("//") and not url.startswith("/\\"):
        # The overlay checks the *resolved* href: `/.//host` collapses into `//host`.
        if re.search(r"[\\\s\x00-\x1f\x7f]", url):
            return False
        return not _resolved_path(re.split(r"[?#]", url, maxsplit=1)[0]).startswith("//")
    return settings.guide_origin is not None and origin_of(url) == settings.guide_origin


def resolve_texts(
    scenario: Scenario, steps: list[TourStepPlan], locales: tuple[str, ...]
) -> dict[str, list[tuple[Optional[str], Optional[str]]]]:
    return {
        locale: [title_text(content_for(scenario.steps[p.step_index], locale)) for p in steps] for locale in locales
    }
