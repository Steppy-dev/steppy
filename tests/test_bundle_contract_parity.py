"""Parity: a manifest this repo produces must parse with the control plane's own consumer.

The control plane (`../steppy-cloud`) implemented the consumer half of the
bundle contract before this producer existed, so the two are independent
implementations of one spec — which is the only situation where a parity test
proves anything. It has already earned its place once: the producer emitted
``scenario: null`` for JUnit-only rows against a consumer that types it as a
required ``str``, and the failure mode is not a dropped row but a **rejected
bundle**.

Skipped when the sibling repo is absent. It is private, and a fresh clone of
this repo must still run its full suite — so this is a bonus check for the
machine that has both, never a dependency. The version-independent half of the
same protection lives in ``test_bundle_schema.py``
(``test_required_fields_are_not_quietly_widened``), which needs nothing external.
"""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

_PLANE_SRC = Path(__file__).resolve().parents[2] / "steppy-cloud" / "api" / "src"

pytestmark = pytest.mark.skipif(
    not (_PLANE_SRC / "steppy_cloud" / "contracts" / "bundle.py").is_file(),
    reason="control plane checkout not present at ../steppy-cloud",
)


@pytest.fixture
def plane_parse_manifest():
    """The control plane's own ``parse_manifest``, imported from its checkout."""
    sys.path.insert(0, str(_PLANE_SRC))
    try:
        from steppy_cloud.contracts.bundle import parse_manifest  # type: ignore

        yield parse_manifest
    finally:
        sys.path.remove(str(_PLANE_SRC))
        for name in [n for n in sys.modules if n.startswith("steppy_cloud")]:
            del sys.modules[name]


def test_produced_manifest_is_accepted_by_the_control_plane(plane_parse_manifest, tmp_path: Path):
    from steppy.bundle import create_bundle

    from .test_bundle import build_project  # the hard-shape synthetic tree

    project = build_project(tmp_path)
    result = create_bundle(project_root=project, fmt="gz", target=tmp_path / "b.tar.gz")

    with tarfile.open(result.path, "r:gz") as tar:
        payload = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))

    parsed = plane_parse_manifest(payload)

    assert parsed.run.run_id == result.manifest.run.run_id
    assert len(parsed.tests) == len(result.manifest.tests)
    assert len(parsed.files) == len(result.manifest.files)
    assert parsed.digests() == {f.sha256 for f in result.manifest.files}
    assert parsed.files_by_kind("screenshot"), "the plane sees no screenshots in a bundle that has them"


def test_plane_accepts_the_version_we_emit(plane_parse_manifest):
    """A producer that raced ahead of every deployed consumer is a broken
    producer, whatever the spec says.

    Either spelling of the constant is accepted: the plane vendors this repo's
    contract module and renamed it once already. What this test is for is
    *version* drift, and hard-coding one name turns an unrelated rename over
    there into a red suite over here — which teaches people to ignore it.
    """
    from steppy_cloud.contracts import bundle as plane_bundle  # type: ignore

    from steppy.contracts.bundle import BUNDLE_SCHEMA_VERSION

    accepted = getattr(plane_bundle, "ACCEPTED_BUNDLE_VERSIONS", None) or getattr(
        plane_bundle, "ACCEPTED_VERSIONS", None
    )
    assert accepted is not None, (
        "the control plane's bundle contract exposes neither ACCEPTED_BUNDLE_VERSIONS nor "
        "ACCEPTED_VERSIONS — it can no longer state which versions it reads"
    )
    assert BUNDLE_SCHEMA_VERSION in accepted, (
        f"this repo emits bundle contract v{BUNDLE_SCHEMA_VERSION}, which the control plane "
        f"does not accept (it takes {sorted(accepted)}). Ship the consumer first."
    )
