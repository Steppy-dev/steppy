"""Guard: the shipped version numbers stay in lockstep (FLAWS §7.6).

Steppy ships several artifacts — the Python package (``pyproject.toml`` /
``steppy.__version__``), the npm types package (``packages/steppy``), the player
(``packages/player``), and the framework wrapper packages (``packages/react``,
``packages/vue``, ``packages/angular``). They drifted (0.3.0 / 0.2.0 / 0.2.1) with only the release
workflow's pyproject check to catch it. This test fails the moment they
disagree, so a version bump must touch all of them.
"""

import json
import re
from pathlib import Path

import steppy

_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert m, "could not find version in pyproject.toml"
    return m.group(1)


def _package_json_version(rel: str) -> str:
    return json.loads((_ROOT / rel).read_text(encoding="utf-8"))["version"]


def test_all_versions_match():
    versions = {
        "pyproject.toml": _pyproject_version(),
        "steppy.__version__": steppy.__version__,
        "packages/steppy/package.json": _package_json_version("packages/steppy/package.json"),
        "packages/player/package.json": _package_json_version("packages/player/package.json"),
        "packages/react/package.json": _package_json_version("packages/react/package.json"),
        "packages/vue/package.json": _package_json_version("packages/vue/package.json"),
        "packages/angular/package.json": _package_json_version("packages/angular/package.json"),
    }
    unique = set(versions.values())
    assert len(unique) == 1, f"version drift detected: {versions}"


def test_the_ci_templates_pin_the_shipped_version():
    """The scaffolded pipelines install steppy from a git tag, and that tag has to
    be one a release actually cuts.

    This is the failure nobody sees until an outsider tries it: the templates are
    copied into someone else's repo, where a pin at a version that was never
    tagged is a ``pip install`` error on their first run, not ours. Bumping the
    package version without re-pinning here would silently ship that.
    """
    expected = f"git+https://github.com/Steppy-dev/steppy.git@v{_pyproject_version()}"
    for rel in ("templates/ci/github-actions.yml", "templates/ci/gitlab-ci.yml"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert expected in text, f"{rel} does not pin v{_pyproject_version()}"


def test_the_reusable_workflow_defaults_to_the_shipped_version():
    """The ``uses:`` ref selects the recipe; ``steppy-ref`` selects the Steppy it
    installs. They have to name the same release, or a caller pinning ``@v0.3.0``
    silently runs some other version's CLI against this version's recipe.
    """
    import yaml

    doc = yaml.safe_load((_ROOT / ".github" / "workflows" / "steppy-e2e.yml").read_text(encoding="utf-8"))
    # YAML 1.1 resolves a bare `on` key to the boolean True.
    triggers = doc.get("on", doc.get(True))
    assert triggers["workflow_call"]["inputs"]["steppy-ref"]["default"] == f"v{_pyproject_version()}"


def test_the_publishable_npm_packages_carry_the_license():
    """``@steppy/player`` and ``@steppy/angular`` ship their own copy of the
    repository's LICENSE (npm packs only what is inside the package folder)."""
    root_license = (_ROOT / "LICENSE").read_text(encoding="utf-8")
    for rel in ("packages/player", "packages/angular"):
        assert (_ROOT / rel / "LICENSE").read_text(encoding="utf-8") == root_license, f"{rel}/LICENSE drifted"
        manifest = json.loads((_ROOT / rel / "package.json").read_text(encoding="utf-8"))
        assert not manifest.get("private"), f"{rel} is marked private"
        assert manifest["publishConfig"]["access"] == "public"
    bundled = (_ROOT / "src" / "steppy" / "_player_bundle" / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")
    assert (_ROOT / "packages" / "player" / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8") == bundled
