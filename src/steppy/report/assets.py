"""Which captures travel into the report site, and where they land.

Two jobs, and both are load-bearing for different reasons.

**The policy.** ``all`` on a real corpus is scenarios × locales × layouts ×
frames — hundreds of megabytes pushed to Pages on every run — so the default is
``failed``: the captures of the variants that did not pass, plus every failure
screenshot. A run report's job is diagnosis; the guide site is where every capture
already lives. ``all`` is one flag away for a showcase repo that wants the full
"look what CI verified" strip.

**The naming.** Destinations are computed from *validated components*, never from
a manifest path. That is a security boundary, not tidiness: a downloaded bundle's
``BundleFile.path`` is attacker-controlled, and streaming tar members
one-by-one skips Python's extraction filters — so ``../../evil.png`` must not be
able to decide where anything is written. Every component is reduced to
``[A-Za-z0-9._-]`` and the shape is fixed:

    assets/<scenario>/<locale>-<layout>/step-NN[-frame].png
    assets/_failures/<scenario>/<locale>-<layout>.png

Captures are also deduped by ``sha256``, so a screenshot that is byte-identical
across two layouts is copied once and both URLs point at it. On a guide corpus
where only a handful of steps differ per layout, that is most of the payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..contracts.bundle import BundleFile, BundleManifest

#: Copy every step capture, only the ones belonging to a variant that did not
#: pass, or none at all.
POLICIES = ("failed", "all", "none")
DEFAULT_POLICY = "failed"

ASSETS_DIRNAME = "assets"
FAILURES_DIRNAME = "_failures"

#: Above this, a Pages deploy is heading for trouble — GitHub caps a published
#: site at 1 GB and warns from 1 GB, so half of that is where saying something is
#: still useful rather than merely alarming.
WARN_BYTES = 500 * 1024 * 1024

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_UNSET = "_"


def sanitise(component: str) -> str:
    """One path component, reduced to characters that cannot mean anything else.

    Collapses runs of unsafe characters to ``-`` and strips leading/trailing dots
    and dashes, so ``..``, ``/``, a drive letter or a NUL cannot survive into a
    destination. Stripping the *trailing* dot matters too: Windows rejects a
    filename that ends in one, and a report that renders on Linux and fails to
    unzip on Windows is a bug someone else has to diagnose.
    """
    cleaned = _SAFE.sub("-", component or "").strip(".-")
    return cleaned or _UNSET


def variant_dirname(locale: Optional[str], layout: Optional[str]) -> str:
    """``fr-mobile``, ``fr-_``, ``_-_`` — one directory per (locale, layout).

    The ``_`` placeholder for an unset dimension mirrors the ``default`` sentinel
    the capture path grammar and the event filenames already use: three writers,
    one idea, so a missing dimension never silently merges two variants.
    """
    return f"{sanitise(locale or _UNSET)}-{sanitise(layout or _UNSET)}"


@dataclass
class AssetPlan:
    """What to copy where, and the URL each manifest path resolves to."""

    #: manifest path → destination file (absolute).
    into: dict[str, Path] = field(default_factory=dict)
    #: manifest path → site-relative URL. Includes deduped paths, whose URL points
    #: at the one copy that was actually made.
    urls: dict[str, str] = field(default_factory=dict)
    #: Bytes the copies will occupy, after dedupe.
    bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def copies(self) -> int:
        return len(self.into)


def plan_assets(
    manifest: BundleManifest,
    *,
    site_dir: Path,
    policy: str = DEFAULT_POLICY,
) -> AssetPlan:
    """Decide which captures travel, under *policy*.

    Failure screenshots always travel when the policy is not ``none``: they are the
    single most useful artifact in a red run, and there are at most one per failing
    variant.
    """
    if policy not in POLICIES:
        raise ValueError(f"screenshot policy must be one of {', '.join(POLICIES)} (got {policy!r})")

    plan = AssetPlan()
    if policy == "none":
        return plan

    wanted_variants = _variants_to_include(manifest, policy)
    by_digest: dict[str, str] = {}

    for entry in _sorted_captures(manifest):
        if entry.kind == "screenshot":
            if (entry.scenario, entry.locale, entry.layout) not in wanted_variants:
                continue
            rel = _step_rel(entry)
        else:  # failure_screenshot
            rel = _failure_rel(entry)
        if rel is None:
            continue

        existing = by_digest.get(entry.sha256)
        if existing is not None:
            # Byte-identical capture already copied — point at the one copy.
            plan.urls[entry.path] = existing
            continue
        by_digest[entry.sha256] = rel
        plan.urls[entry.path] = rel
        plan.into[entry.path] = site_dir / Path(rel)
        plan.bytes += entry.size

    if plan.bytes > WARN_BYTES:
        plan.warnings.append(
            f"the report will copy {plan.bytes / (1024 * 1024):.0f} MiB of screenshots; "
            "GitHub Pages caps a published site at 1 GB — consider --screenshots failed"
        )
    return plan


def _sorted_captures(manifest: BundleManifest) -> list[BundleFile]:
    """Captures in a stable order, so dedupe picks the same winner every run.

    Which copy survives has to be deterministic or two renders of one bundle
    produce different URLs, and the report stops being diffable.
    """
    captures = [f for f in manifest.files if f.kind in ("screenshot", "failure_screenshot")]
    return sorted(captures, key=lambda f: f.path)


def _variants_to_include(manifest: BundleManifest, policy: str) -> set[tuple]:
    """The (scenario, locale, layout) keys whose step captures travel.

    Under ``failed`` this is every variant that did not pass — and, when there are
    no test rows at all (a run made without ``--bundle``), nothing: an empty set is
    the honest answer, and the report says so rather than shipping the whole
    corpus by accident.
    """
    if policy == "all":
        return {(f.scenario, f.locale, f.layout) for f in manifest.files if f.kind == "screenshot"}
    return {(t.scenario, t.locale, t.layout) for t in manifest.tests if t.outcome not in ("passed", "skipped")}


def _step_rel(entry: BundleFile) -> Optional[str]:
    if entry.step is None:
        return None
    stem = f"step-{entry.step:02d}" + (f"-{sanitise(entry.frame)}" if entry.frame else "")
    return "/".join(
        (
            ASSETS_DIRNAME,
            sanitise(entry.scenario or _UNSET),
            variant_dirname(entry.locale, entry.layout),
            f"{stem}{_suffix(entry.path)}",
        )
    )


def _failure_rel(entry: BundleFile) -> str:
    # One failure capture per (scenario, variant, fixture); the fixture name is
    # already gone from the manifest, so a second one would collide. Including the
    # digest prefix keeps both rather than silently dropping one.
    return "/".join(
        (
            ASSETS_DIRNAME,
            FAILURES_DIRNAME,
            sanitise(entry.scenario or _UNSET),
            f"{variant_dirname(entry.locale, entry.layout)}-{entry.sha256[:8]}{_suffix(entry.path)}",
        )
    )


def _suffix(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".png"
