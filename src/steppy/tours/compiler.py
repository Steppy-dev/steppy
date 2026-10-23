"""All opted-in scenarios → compiled tours, replay plans and diagnostics."""

from __future__ import annotations

from ..models import Scenario
from .diagnostics import Diagnostic, DiagSink, sort_diagnostics
from .inference import infer_scenario
from .model import CompiledTour, ReplayPlan, ScenarioInference, TourCompileResult
from .settings import ToursSettings
from .text import check_texts, content_for, emitted_locales, guide_url, resolve_texts, tour_title


def opted_in(scenarios: list[Scenario], settings: ToursSettings) -> list[Scenario]:
    return [s for s in scenarios if s.tour_opted_in(settings.default_opt_in)]


def compile_tours(scenarios: list[Scenario], settings: ToursSettings) -> TourCompileResult:
    """Compile every opted-in scenario (the caller passes the whole directory)."""
    diagnostics: list[Diagnostic] = []
    tours: list[CompiledTour] = []
    plans: dict[str, ReplayPlan] = {}
    ids: dict[str, str] = {}
    untitled: list[str] = []  # multi-locale tours whose title is the (one-language) scenario title

    for scenario in opted_in(scenarios, settings):
        slug = scenario.slug or (scenario.file_path.stem if scenario.file_path else "scenario")
        tour_id = scenario.tour.id if scenario.tour and scenario.tour.id else slug
        sink = DiagSink(slug, tour_id, replay=settings.replay)
        from ..tour_spec import is_tour_id

        if not is_tour_id(tour_id):
            sink.add(
                "tour-id-invalid",
                f"{tour_id!r} cannot be a tour id (1-64 characters [A-Za-z0-9_-], no device names)",
                hint="set `tour: {id: <id>}`",
            )
            diagnostics.extend(sink.items)
            continue
        key = tour_id.casefold()
        if key in ids:
            sink.add("tour-id-duplicate", f"tour id {tour_id!r} is also used by {ids[key]}", hint="set `tour.id`")
            diagnostics.extend(sink.items)
            continue
        ids[key] = slug

        inference = infer_scenario(scenario, settings, sink)
        if inference is None:
            diagnostics.extend(sink.items)
            continue
        tour = _compile_one(scenario, slug, tour_id, inference, settings, sink)
        diagnostics.extend(sink.items)
        if sink.has_errors:
            continue
        tours.append(tour)
        if (scenario.tour is None or scenario.tour.title is None) and len(tour.locales) > 1:
            untitled.append(tour_id)
        plans[slug] = ReplayPlan(
            scenario_slug=slug,
            tour_id=tour_id,
            role=inference.role,
            auth=scenario.auth,
            default_locale=settings.default_locale,
            locales=tour.locales,
            entry_route=inference.entry_route,
            entry_params=inference.entry_params,
            entry_step_number=(
                scenario.steps[inference.entry_step_index].step_number
                if inference.entry_step_index is not None
                else None
            ),
            start_index=inference.start_index,
            entries=tuple(inference.entries),
            tour_step_count=len(inference.steps),
            layouts=tuple(
                (name, query)
                for name, query in settings.layouts
                if any(p.layouts and name in p.layouts for p in inference.steps)
            ),
            test_id_attribute=settings.test_id_attribute,
            end_index=inference.end_index,
            start_in_place=inference.start_in_place,
        )

    if untitled:
        shown = ", ".join(sorted(untitled)[:5]) + (", …" if len(untitled) > 5 else "")
        diagnostics.append(
            Diagnostic(
                "warning",
                "title-fallback",
                f"{len(untitled)} multi-language tour(s) have no `tour.title` ({shown}); every language shows "
                "the scenario title, which apps listing tours display as is",
                hint="set `tour: {title: {en: …, fr: …}}`",
            )
        )
    tours.sort(key=lambda t: t.id)
    return TourCompileResult(tours=tours, replay_plans=plans, diagnostics=sort_diagnostics(diagnostics))


def _compile_one(
    scenario: Scenario,
    slug: str,
    tour_id: str,
    inference: ScenarioInference,
    settings: ToursSettings,
    sink: DiagSink,
) -> CompiledTour:
    locales = emitted_locales(scenario, inference.steps, settings)
    dropped = [loc for loc in settings.locales if loc not in locales]
    if dropped:
        sink.add(
            "locale-not-emitted",
            f"no kept step has text in {', '.join(dropped)}, so no file is written for it; those users get "
            f"the tour in their base language if it has a file, else in {settings.default_locale}",
            hint="add `tour-text` or `guide-text` in that language",
        )
    if not any(content_for(scenario.steps[p.step_index], settings.default_locale) for p in inference.steps):
        sink.add(
            "no-authored-text",
            f"no step has text in the default locale {settings.default_locale!r}; every step shows a generic instruction",
        )
    titles = {}
    fell_back = []
    for locale in locales:
        titles[locale], fb = tour_title(scenario, locale, settings.default_locale)
        if fb:
            fell_back.append(locale)
    if fell_back:
        sink.add(
            "title-fallback",
            f"tour.title has no entry for {', '.join(fell_back)}; the {settings.default_locale} title "
            "(else the scenario title) is used there",
        )
    texts = resolve_texts(scenario, inference.steps, locales)
    check_texts(scenario, inference.steps, texts, settings, sink)
    urls = {loc: u for loc in locales if (u := guide_url(scenario, settings, loc))}
    return CompiledTour(
        id=tour_id,
        scenario_slug=slug,
        role=inference.role,
        entry_route=inference.entry_route,
        steps=inference.steps,
        locales=locales,
        titles=titles,
        step_texts=texts,
        guide_urls=urls,
    )
