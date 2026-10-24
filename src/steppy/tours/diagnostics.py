"""Compile diagnostics: stable kebab-case codes, one rendered line each.

Errors stop emission (``steppy validate`` exits 1); warnings never change
an exit code; infos only print with ``steppy tours --explain``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Severity = Literal["error", "warning", "info"]

#: Every code the compiler emits, with its severity. Tests pin that the compiler
#: never emits a code missing here, that every code here is emitted somewhere
#: (``tests/test_tours_diagnostics.py``), and that ``docs/tours.md`` lists each
#: one with this severity (``tests/test_tours_docs_sync.py``).
CODES: dict[str, Severity] = {
    # errors
    "scenario-unreadable": "error",
    "tour-role-required": "error",
    "tour-role-unknown": "error",
    "tour-no-role": "error",
    "tour-id-invalid": "error",
    "tour-id-duplicate": "error",
    "entry-invalid": "error",
    "entry-no-match": "error",
    "no-entry": "error",  # downgraded to a warning when tours.replay is off
    "range-duplicate": "error",
    "range-reversed": "error",
    "start-on-navigation": "error",
    "target-not-test-id": "error",
    "no-kept-steps": "error",
    "layouts-unmapped": "error",
    "annotation-outside-tour": "error",
    "annotation-on-skipped-step": "error",
    "skip-and-annotations": "error",
    "keep-not-allowed": "error",
    "advance-needs-target": "error",
    "advance-not-replayable": "error",  # downgraded to a warning when tours.replay is off
    "limit-exceeded": "error",
    "layout-media-mismatch": "error",
    "build-id": "error",
    "target-path": "error",
    # warnings
    "unguided-action": "warning",
    "navigate-in-tour": "warning",
    "reloads-page": "warning",
    "escape-in-tour": "warning",
    "may-not-change": "warning",
    "upload-target-may-be-hidden": "warning",
    "entry-query-dropped": "warning",
    "entry-id-segment": "warning",
    "entry-unknown": "warning",
    "app-url-unresolved": "warning",
    "fixture-leak": "warning",
    "generic-text": "warning",
    "no-authored-text": "warning",
    "title-fallback": "warning",
    "placement-ignored": "warning",
    "tour-text-locale-untargeted": "warning",
    "locale-not-emitted": "warning",
    "chrome-strings-missing": "warning",
    "text-link-dropped": "warning",
    "layout-media-overlap": "warning",
    "app-assets-skipped": "warning",
    "no-tours": "warning",
    # infos
    "step-skipped": "info",
    "gate-dropped": "info",
    "gate-stale": "info",
    "reduced-to-host": "info",
    "conditional-skipped": "info",
    "layout-media-unchecked": "info",
}


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str
    message: str
    scenario: Optional[str] = None
    tour: Optional[str] = None
    step_number: Optional[int] = None
    step_title: Optional[str] = None
    step_label: Optional[str] = None
    hint: Optional[str] = None

    def render(self) -> str:
        where = ""
        if self.tour and self.scenario and self.tour != self.scenario:
            where = f"tour '{self.tour}' ({self.scenario})"
        elif self.tour or self.scenario:
            where = f"tour '{self.tour or self.scenario}'"
        if self.step_number is not None:
            where += f", step {self.step_number}"
            title = self.step_title
            # The position counts headings; the label is what the author wrote ("### 16.").
            if self.step_label and self.step_label != str(self.step_number):
                where += f" (### {self.step_label})"
                if title and title.startswith(f"{self.step_label}. "):
                    title = title[len(self.step_label) + 2 :]
            if title:
                where += f" '{title}'"
        text = f"{where}: {self.message}" if where else self.message
        if self.hint:
            text += f" — {self.hint}"
        return f"[{self.code}] {text}"

    def sort_key(self):
        return (self.scenario or "", self.step_number or 0, self.code, self.message)


class DiagSink:
    """Collects diagnostics for one scenario (or the whole run when ``scenario`` is None)."""

    def __init__(self, scenario: Optional[str] = None, tour: Optional[str] = None, *, replay: bool = True):
        self.scenario = scenario
        self.tour = tour
        self.replay = replay
        self.items: list[Diagnostic] = []

    def add(self, code: str, message: str, *, step=None, hint: Optional[str] = None) -> None:
        severity = CODES[code]
        if code in ("no-entry", "advance-not-replayable") and not self.replay:
            severity = "warning"
        self.items.append(
            Diagnostic(
                severity=severity,
                code=code,
                message=message,
                scenario=self.scenario,
                tour=self.tour,
                step_number=getattr(step, "step_number", None),
                step_title=getattr(step, "step_title", None),
                step_label=getattr(step, "step_label", None),
                hint=hint,
            )
        )

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.items)


def sort_diagnostics(items: list[Diagnostic]) -> list[Diagnostic]:
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(items, key=lambda d: (order[d.severity], *d.sort_key()))
