"""Worker-image tag computation + build-input resolution.

The bug this file guards: ``steppy run --workers N`` used to derive the
worker Dockerfile as ``<package>/../../Dockerfile``, which only holds for the
editable layout ``<repo>/src/steppy``. On a wheel install the package lives at
``site-packages/steppy``, so the expression produced ``<venv>/Lib/Dockerfile``
and the run died with "worker Dockerfile not found". Worse, the repo
Dockerfile COPYs pyproject.toml/hatch_build.py/src/templates/skills, none of
which exist in a wheel — so simply finding a Dockerfile wouldn't have helped.

No Docker required: tag computation, mode resolution and context synthesis are
all pure filesystem work.
"""

import re
from pathlib import Path

import pytest

from steppy._parallel.image import (
    BAKED_EXTRAS,
    DEFAULT_PLAYWRIGHT_BASE,
    WORKER_IMAGE_CONTRACT,
    BuildInputs,
    compute_image_tag,
    resolve_build_inputs,
    runtime_requirements,
    synthesize_installed_context,
)
from steppy.models import ParallelConfig


REPO_ROOT = Path(__file__).resolve().parent.parent


# ── helpers ─────────────────────────────────────────────────────────────────


def _fake_package(root: Path, *, name: str = "steppy") -> Path:
    """A minimal stand-in for an installed steppy package directory."""
    pkg = root / name
    (pkg / "_parallel").mkdir(parents=True)
    (pkg / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (pkg / "worker.py").write_text("def worker_main():\n    return 0\n", encoding="utf-8")
    (pkg / "_parallel" / "__init__.py").write_text("", encoding="utf-8")
    # Package data that must survive the copy, and cache junk that must not.
    (pkg / "_player_bundle").mkdir()
    (pkg / "_player_bundle" / "steppy-player.js").write_text("// bundle\n", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "worker.cpython-312.pyc").write_bytes(b"\x00cached")
    return pkg


def _repo_layout(root: Path) -> Path:
    """A source checkout: ``<root>/src/steppy`` + Dockerfile + pyproject.toml."""
    pkg = _fake_package(root / "src")
    (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "steppy"\n', encoding="utf-8")
    return pkg


def _installed_layout(root: Path) -> Path:
    """A wheel install: ``<root>/site-packages/steppy`` with no repo above it."""
    site = root / "site-packages"
    pkg = _fake_package(site)
    dist_info = site / "steppy_dev-9.9.9.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: steppy-dev\nVersion: 9.9.9\n", encoding="utf-8")
    # Another distribution's folder that merely starts with the import name.
    other = site / "steppy-0.1.16.dist-info"
    other.mkdir()
    (other / "METADATA").write_text("Name: steppy\nVersion: 0.1.16\n", encoding="utf-8")
    return pkg


# ── image tag ───────────────────────────────────────────────────────────────


def test_tag_always_carries_a_source_hash(tmp_path: Path):
    """Regression: on a wheel install the tag used to collapse to a bare
    version, so reinstalling a different build of the same version silently
    reused a stale worker image."""
    pkg = _installed_layout(tmp_path)
    tag = compute_image_tag(pkg)
    assert re.fullmatch(r".+-[0-9a-f]{12}", tag), tag


def test_tag_is_stable_across_calls(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    assert compute_image_tag(pkg) == compute_image_tag(pkg)


def test_tag_changes_when_source_changes(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    before = compute_image_tag(pkg)
    (pkg / "worker.py").write_text("def worker_main():\n    return 1\n", encoding="utf-8")
    assert compute_image_tag(pkg) != before


def test_tag_ignores_pycache(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    before = compute_image_tag(pkg)
    (pkg / "__pycache__" / "extra.cpython-312.pyc").write_bytes(b"\x00more")
    assert compute_image_tag(pkg) == before


def test_tag_folds_in_base_image_override(tmp_path: Path):
    """Switching parallel.worker_base_image must force a rebuild, and the
    default (empty extra) must leave tags untouched."""
    pkg = _installed_layout(tmp_path)
    plain = compute_image_tag(pkg)
    assert compute_image_tag(pkg, extra="") == plain
    assert compute_image_tag(pkg, extra="my-registry/playwright:v1.60.0") != plain


# ── mode resolution ─────────────────────────────────────────────────────────


def _resolve(pkg: Path, tmp_path: Path, cfg: ParallelConfig, project_root: Path | None = None) -> BuildInputs:
    return resolve_build_inputs(
        package_root=pkg,
        project_root=project_root or tmp_path / "project",
        parallel_cfg=cfg,
        staging_dir=tmp_path / "staging",
    )


def test_repo_layout_resolves_to_repo_mode(tmp_path: Path):
    """Source checkouts keep building exactly as they always have."""
    repo = tmp_path / "repo"
    pkg = _repo_layout(repo)
    inputs = _resolve(pkg, tmp_path, ParallelConfig())
    assert inputs.mode == "repo"
    assert inputs.dockerfile == repo / "Dockerfile"
    assert inputs.context == repo
    assert inputs.build_args == {}


def test_repo_layout_without_pyproject_falls_through_to_installed(tmp_path: Path):
    """A stray Dockerfile two levels up isn't a steppy checkout."""
    repo = tmp_path / "repo"
    pkg = _repo_layout(repo)
    (repo / "pyproject.toml").unlink()
    (tmp_path / "staging").mkdir()
    assert _resolve(pkg, tmp_path, ParallelConfig()).mode == "installed"


def test_installed_layout_resolves_to_installed_mode(tmp_path: Path):
    """The actual bug: no repo above the package → synthesize a context."""
    pkg = _installed_layout(tmp_path)
    (tmp_path / "staging").mkdir()
    inputs = _resolve(pkg, tmp_path, ParallelConfig())
    assert inputs.mode == "installed"
    assert inputs.dockerfile == inputs.context / "Dockerfile"
    assert inputs.dockerfile.is_file()


def test_config_dockerfile_wins_over_repo_layout(tmp_path: Path):
    repo = tmp_path / "repo"
    pkg = _repo_layout(repo)
    project = tmp_path / "project"
    (project / "docker").mkdir(parents=True)
    custom = project / "docker" / "worker.Dockerfile"
    custom.write_text("FROM scratch\n", encoding="utf-8")

    inputs = _resolve(pkg, tmp_path, ParallelConfig(dockerfile="docker/worker.Dockerfile"), project_root=project)
    assert inputs.mode == "config"
    assert inputs.dockerfile == custom
    # Context defaults to the Dockerfile's own directory.
    assert inputs.context == custom.parent


def test_config_build_context_is_honoured(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    project = tmp_path / "project"
    (project / "docker").mkdir(parents=True)
    (project / "docker" / "worker.Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    inputs = _resolve(
        pkg,
        tmp_path,
        ParallelConfig(dockerfile="docker/worker.Dockerfile", build_context="."),
        project_root=project,
    )
    assert inputs.context == project


def test_missing_config_dockerfile_names_the_config_key(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    with pytest.raises(RuntimeError, match=r"parallel\.dockerfile"):
        _resolve(pkg, tmp_path, ParallelConfig(dockerfile="nope/Dockerfile"))


def test_missing_config_build_context_names_the_config_key(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "worker.Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"parallel\.build_context"):
        _resolve(
            pkg,
            tmp_path,
            ParallelConfig(dockerfile="worker.Dockerfile", build_context="nowhere"),
            project_root=project,
        )


def test_build_context_without_dockerfile_is_rejected(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    with pytest.raises(RuntimeError, match=r"parallel\.dockerfile"):
        _resolve(pkg, tmp_path, ParallelConfig(build_context="."))


def test_base_image_override_becomes_a_build_arg(tmp_path: Path):
    repo = tmp_path / "repo"
    pkg = _repo_layout(repo)
    inputs = _resolve(pkg, tmp_path, ParallelConfig(worker_base_image="mirror.local/playwright:v1.60.0-noble"))
    assert inputs.build_args == {"PLAYWRIGHT_BASE": "mirror.local/playwright:v1.60.0-noble"}


# ── synthesized context ─────────────────────────────────────────────────────


def test_synthesized_context_shape(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()

    context = synthesize_installed_context(package_root=pkg, staging_dir=staging)

    assert (context / "Dockerfile").is_file()
    assert (context / "requirements.txt").is_file()
    assert (context / "pkg" / "steppy" / "__init__.py").is_file()
    assert (context / "pkg" / "steppy" / "_parallel" / "__init__.py").is_file()
    # Package data (the vendored player bundle) must come along.
    assert (context / "pkg" / "steppy" / "_player_bundle" / "steppy-player.js").is_file()
    # dist-info keeps importlib.metadata working inside the container.
    # It is found under the distribution's name, not the import package's.
    assert (context / "pkg" / "steppy_dev-9.9.9.dist-info" / "METADATA").is_file()
    assert not (context / "pkg" / "steppy-0.1.16.dist-info").exists()
    # Byte-compiled junk must not be shipped into the image.
    assert not (context / "pkg" / "steppy" / "__pycache__").exists()


def test_synthesized_context_is_idempotent(tmp_path: Path):
    """Re-synthesizing over a stale context must not fail or leave leftovers."""
    pkg = _installed_layout(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()

    context = synthesize_installed_context(package_root=pkg, staging_dir=staging)
    (context / "leftover.txt").write_text("stale\n", encoding="utf-8")
    again = synthesize_installed_context(package_root=pkg, staging_dir=staging)

    assert again == context
    assert not (context / "leftover.txt").exists()


def test_requirements_include_baked_extras_and_drop_the_rest():
    """The two build modes must install the SAME set of packages.

    The repo Dockerfile bakes ``[retry,report]`` in; the synthesized
    requirements.txt therefore has to carry those extras' packages too, with
    the ``; extra == "..."`` marker stripped (inside the container they are
    unconditional). Any extra NOT in ``BAKED_EXTRAS`` stays dropped.
    """
    reqs = runtime_requirements()
    assert any(r.startswith("typer") for r in reqs), reqs
    # Markers must never survive — pip would evaluate `extra == "retry"` as
    # false (no extra is being requested) and silently install nothing.
    assert not any("extra" in r for r in reqs), reqs
    assert any(r.startswith("pytest-rerunfailures") for r in reqs), reqs
    assert any(r.startswith("pytest-html") for r in reqs), reqs


def test_repo_dockerfile_installs_the_baked_extras():
    """Drift guard: the repo image's `pip install` must name exactly the
    extras `runtime_requirements()` folds into the synthesized context."""
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"pip install --no-cache-dir '/tmp/steppy\[([^\]]+)\]'", text)
    assert match, "repo Dockerfile no longer pip-installs steppy with an extras marker"
    assert sorted(e.strip() for e in match.group(1).split(",")) == sorted(BAKED_EXTRAS)


def test_image_contract_is_folded_into_the_tag():
    """A worker-image content change that touches no *.py (Dockerfile edit,
    new baked extra) must still bust warm images. ``WORKER_IMAGE_CONTRACT`` is
    the lever; if it stops reaching the digest, stale images get reused
    silently and the change never lands."""
    from steppy._parallel.image import compute_image_tag

    pkg = Path(__import__("steppy").__file__).parent
    assert compute_image_tag(pkg, extra=f"{WORKER_IMAGE_CONTRACT}\0") != compute_image_tag(pkg, extra="")
    assert compute_image_tag(pkg, extra="1\0") != compute_image_tag(pkg, extra="2\0")


def test_synthesized_dockerfile_matches_the_bundled_one(tmp_path: Path):
    pkg = _installed_layout(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    context = synthesize_installed_context(package_root=pkg, staging_dir=staging)
    bundled = Path(__import__("steppy").__file__).parent / "_docker" / "Dockerfile.installed"
    assert (context / "Dockerfile").read_bytes() == bundled.read_bytes()


# ── base-image drift guard ──────────────────────────────────────────────────


def _arg_default(dockerfile: Path) -> str:
    match = re.search(r"^ARG PLAYWRIGHT_BASE=(.+)$", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{dockerfile} does not declare `ARG PLAYWRIGHT_BASE=`"
    return match.group(1).strip()


def test_base_image_pin_does_not_drift():
    """DEFAULT_PLAYWRIGHT_BASE, the repo Dockerfile and the bundled
    Dockerfile.installed must all name the same Playwright base."""
    repo_dockerfile = REPO_ROOT / "Dockerfile"
    bundled = Path(__import__("steppy").__file__).parent / "_docker" / "Dockerfile.installed"
    assert _arg_default(repo_dockerfile) == DEFAULT_PLAYWRIGHT_BASE
    assert _arg_default(bundled) == DEFAULT_PLAYWRIGHT_BASE
