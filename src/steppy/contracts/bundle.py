"""Results-bundle manifest — contract v1.

A bundle is a ``.tar.zst`` (or ``.tar.gz``) of a subset of a run's ``output/``
tree **exactly as steppy writes it** — no path rewriting, so an unpacked bundle
feeds ``steppy guide`` and ``steppy build`` unchanged, and a laptop, a CI runner
and a remote agent produce identical structures by construction — plus three
generated members at the archive root::

    manifest.json                 this contract
    junit.xml                     merged pytest --junitxml (belt and braces)
    events/
      <scenario>__<locale>-<layout>.jsonl
    output/
      screenshots/<scenario>/[<locale>/][<layout>/]step-NN[-<frame>].png
      screenshots/.../step-NN[-<frame>].json      sidecars: viewport, DSF, bbox
      screenshots/_failures/...
      coverage/raw/<run_id>/<scenario>/<fixture>__<locale-layout>.json
      coverage/raw/<run_id>/_maps/<sha256>.json.gz
      failures/<slug>/pytest.log
      guides/...                                  optional

Producer: ``steppy bundle create``. See :mod:`steppy.contracts` for the
compatibility rules and why these models are ``extra="allow"``.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

#: Bump only on a breaking change. Additive fields never require it.
BUNDLE_SCHEMA_VERSION = 1

#: What a consumer at this version will read. The N and N−1 rule expressed as
#: data, so the error message and the test read the same tuple.
ACCEPTED_BUNDLE_VERSIONS: tuple[int, ...] = (1,)


class _Wire(BaseModel):
    """Base for every wire model: unknown fields are kept, never rejected."""

    model_config = ConfigDict(extra="allow")


class BundleGit(_Wire):
    """Where the run's code came from. Every field optional — a laptop run
    outside a repository has none of it, and that is not an error."""

    sha: Optional[str] = None
    branch: Optional[str] = None
    pr: Optional[int] = None


class BundleSpec(_Wire):
    """What the run was asked to do — the ``steppy run`` flags, resolved.

    Recorded by the run itself rather than reconstructed by the bundler: they
    are separate processes, and re-deriving this from flags a user retypes
    drifts silently.
    """

    guide: bool = False
    no_guide: bool = False
    no_asserts: bool = False
    screenshots: bool = False
    coverage: bool = False
    locales: list[str] = Field(default_factory=list)
    layouts: list[str] = Field(default_factory=list)
    workers: int = 1
    tag: Optional[Any] = None
    scenario: Optional[str] = None


class BundleRun(_Wire):
    """Run identity and provenance."""

    run_id: str
    created_at: str
    steppy_version: str
    executor: str = "local"
    git: BundleGit = Field(default_factory=BundleGit)
    spec: BundleSpec = Field(default_factory=BundleSpec)


class BundleTest(_Wire):
    """One test result — one (scenario, locale, layout) parametrization.

    ``outcome`` uses the ``STEPPY-EVT`` vocabulary: ``passed|failed|skipped|
    error`` from a worker, plus ``interrupted`` and ``worker_died``, which a
    coordinator synthesises for tests that were still in flight. Deliberately
    a plain ``str`` rather than an enum — rule 2 in :mod:`steppy.contracts`.
    """

    #: Required, not optional: a Steppy test belongs to a scenario by
    #: construction. Producers that lack one on a given path derive it from the
    #: node id's module name (``test_<slug>.py``) rather than emitting null —
    #: an older consumer types this as a plain ``str`` and rejects the whole
    #: manifest otherwise.
    scenario: str
    nodeid: str
    outcome: str
    duration_ms: Optional[int] = None
    locale: Optional[str] = None
    layout: Optional[str] = None
    fail_tail_b64: Optional[str] = None
    #: 1-based index of the step that was in flight when the test died — the
    #: scenario's own step number, so it survives steps skipped by ``only-in:``
    #: or ``layouts:``. Recorded by the env-gated ``STEPPY_STEP_EVENTS`` emitter
    #: that ``steppy run --bundle`` turns on.
    #:
    #: ``None`` is a real answer, not just "unknown": a passing test has no
    #: failed step, and a test that died in fixture teardown closed every step
    #: it ran. A producer that was not recording step events also reports
    #: ``None``, so a consumer that needs to tell the two apart should look for
    #: ``step-start`` records in ``events/``.
    failed_step: Optional[int] = None


class BundleFile(_Wire):
    """One archived file, content-addressed.

    ``sha256`` is what makes uploads cheap: with locales × layouts × device
    scale factors, unchanged screenshots dominate a bundle, so a consumer can
    ask only for digests it does not already hold.
    """

    path: str
    kind: str
    sha256: str
    size: int
    scenario: Optional[str] = None
    locale: Optional[str] = None
    layout: Optional[str] = None
    step: Optional[int] = None
    #: Multi-frame captures (a tall ``fill-form``) emit ``step-NN-a``, ``-b``, …
    frame: Optional[str] = None


class BundleCoverage(_Wire):
    """Pointer to the coverage records carried in this bundle.

    ``run_id`` may legitimately differ from ``run.run_id``: a bundle created
    with an explicit ``--run-id`` names itself after the identity its consumer
    assigned, while the records on disk keep whatever the run recorded them
    under.
    """

    run_id: Optional[str] = None
    records: int = 0


class BundleManifest(_Wire):
    """The contract."""

    steppy_bundle: int = Field(description="Contract version; see ACCEPTED_BUNDLE_VERSIONS.")
    run: BundleRun
    tests: list[BundleTest] = Field(default_factory=list)
    files: list[BundleFile] = Field(default_factory=list)
    coverage: Optional[BundleCoverage] = None
    #: True **only** when a file that belonged in the bundle was dropped to fit
    #: a size budget. Not set for content the user excluded by flag, and not for
    #: content that was never produced. The distinction is what lets a consumer
    #: tell "step 7 never ran" from "we dropped step 7's screenshot".
    truncated: bool = False
    #: Human-readable notes from the producer (dropped files, degraded sources).
    warnings: list[str] = Field(default_factory=list)

    def digests(self) -> set[str]:
        return {f.sha256 for f in self.files}

    def files_by_kind(self, kind: str) -> list[BundleFile]:
        return [f for f in self.files if f.kind == kind]


SCHEMA_TITLE = "Steppy results bundle manifest"


def generate_bundle_schema() -> dict:
    """JSON Schema for ``manifest.json``, for consumers that are not Python.

    Mirrors :mod:`steppy.config_schema`: generated from the model, committed to
    the repo, and byte-compared by a drift guard so the published contract can
    never fall out of sync with what the producer emits.
    """
    schema = BundleManifest.model_json_schema()
    schema["title"] = SCHEMA_TITLE
    schema["x-steppy-bundle-version"] = BUNDLE_SCHEMA_VERSION
    schema["x-steppy-bundle-accepted"] = list(ACCEPTED_BUNDLE_VERSIONS)
    return schema


def schema_json() -> str:
    """The canonical serialized form written to disk and compared in tests."""
    import json

    return json.dumps(generate_bundle_schema(), indent=2, sort_keys=True) + "\n"


def accepts(version: int) -> bool:
    return version in ACCEPTED_BUNDLE_VERSIONS


def parse_manifest(payload: dict[str, Any]) -> BundleManifest:
    """Validate a ``manifest.json`` payload, with actionable version errors."""
    version = payload.get("steppy_bundle")
    if not isinstance(version, int):
        raise ValueError("manifest.json is missing the integer 'steppy_bundle' contract version")
    if not accepts(version):
        supported = ", ".join(f"v{v}" for v in sorted(ACCEPTED_BUNDLE_VERSIONS, reverse=True))
        raise ValueError(
            f"bundle contract v{version} is not accepted by this version of steppy (supports {supported}). "
            "Upgrade the steppy that is reading this bundle, or re-create it with the steppy that wrote it."
        )
    return BundleManifest.model_validate(payload)
