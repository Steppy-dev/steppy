"""Resolve, probe and build the steppy-worker Docker image.

The image tag is ``<image_prefix>:<package_version>-<src_hash>`` (e.g.
``steppy-worker:0.3.0-ab12cd34ef56``). The hash covers every ``*.py`` under
the installed package root, so any change to steppy's source — a dev edit on
an editable install, or a reinstall of a different build of the same version
— produces a new tag and therefore a rebuild, instead of silently shipping
stale code into the container.

Build inputs (Dockerfile + context) are resolved through three modes, in
priority order — see :func:`resolve_build_inputs`:

``config``
    ``parallel.dockerfile`` / ``parallel.build_context`` are set. Escape hatch
    for a fully custom worker image.
``repo``
    steppy is running out of a source checkout (``<root>/src/steppy`` with a
    sibling ``Dockerfile`` + ``pyproject.toml``). Builds from the repo root,
    exactly as steppy always has.
``installed``
    Everything else — steppy installed as a wheel, with no source tree on the
    machine. Synthesizes a self-contained build context from the installed
    package (see :func:`synthesize_installed_context`) and builds it with the
    bundled ``_docker/Dockerfile.installed``.

Auto-build on first run is the default — :func:`ensure_worker_image` probes
``docker images`` for the tag and builds if missing. The CLI's ``--no-build``
flag flips ``build=False`` to fail fast instead.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import DISTRIBUTION


# Playwright base image for the synthesized worker build. Kept in lockstep
# with the repo-root ``Dockerfile`` and ``_docker/Dockerfile.installed`` —
# drift guard: ``tests/test_parallel_worker_image.py``. Overridable per
# project via ``parallel.worker_base_image``.
DEFAULT_PLAYWRIGHT_BASE = "mcr.microsoft.com/playwright/python:v1.60.0-noble"

# Build-arg name declared by both Dockerfiles.
_BASE_IMAGE_ARG = "PLAYWRIGHT_BASE"

# Optional extras baked into the worker image by BOTH build modes: the repo
# Dockerfile via ``pip install '/tmp/steppy[retry,report]'``, the installed
# mode via :func:`runtime_requirements`. ``retry`` is load-bearing —
# ``steppy run --workers N --retries K`` resolves host-side but executes
# ``--reruns`` inside the container.
BAKED_EXTRAS = ("retry", "report")

# Matches ``extra == "report"`` / ``extra == 'report'`` in a requirement marker.
_EXTRA_MARKER_RE = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")

# Bumped whenever the worker image's CONTENT changes without any *.py under the
# package changing — i.e. a Dockerfile or baked-extras edit. ``compute_image_tag``
# hashes only ``*.py``, so without this a warm image would silently be reused and
# the new content would never land. Bump = one rebuild for everyone, once.
#   1 → original
#   2 → [retry,report] extras baked in
WORKER_IMAGE_CONTRACT = "2"

# Subdirectory of the coordinator's run tmp dir that holds a synthesized context.
_CONTEXT_DIRNAME = "worker-build-context"

# Never copied into a synthesized build context.
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


@dataclass(frozen=True)
class BuildInputs:
    """What ``docker build`` needs, plus which mode produced it."""

    dockerfile: Path
    context: Path
    mode: str  # "config" | "repo" | "installed"
    build_args: dict[str, str] = field(default_factory=dict)


def _package_version() -> str:
    """Installed steppy version, e.g. '0.3.0'. Falls back to '0-unknown' if
    the metadata isn't readable (shouldn't happen but is non-fatal)."""
    try:
        return importlib.metadata.version(DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return "0-unknown"


def _src_content_hash(package_root: Path) -> str:
    """Short hex digest over the package source. Used as a tag suffix so
    worker images rebuild when the steppy source changes.

    Hashes file paths + contents in sorted order — deterministic, fast on
    the steppy package size (~10K LOC). Excludes ``__pycache__``.
    """
    sha = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(package_root).as_posix().encode("utf-8")
        sha.update(rel)
        sha.update(b"\0")
        sha.update(path.read_bytes())
        sha.update(b"\0")
    return sha.hexdigest()[:12]


def compute_image_tag(package_root: Path, extra: str = "") -> str:
    """Tag to append after the image prefix: ``<version>-<src_hash>``.

    The hash is unconditional. It used to be added only on editable installs
    (detected via a sibling ``pyproject.toml``), which meant a wheel install
    collapsed to a bare ``0.3.0`` — so reinstalling a different build of the
    same version silently reused a stale worker image.

    ``extra`` folds additional build-affecting inputs (currently
    ``parallel.worker_base_image``) into the digest. Empty by default, so
    tags are unchanged for projects that don't set it.
    """
    version = _package_version()
    digest = _src_content_hash(package_root)
    if extra:
        digest = hashlib.sha256((digest + "\0" + extra).encode("utf-8")).hexdigest()[:12]
    # `-` separator (not `+`) — the Docker tag spec is [a-zA-Z0-9_.-] and rejects `+`.
    return f"{version}-{digest}"


def image_exists(image_ref: str) -> bool:
    """True if ``docker images`` lists the given <image>:<tag>."""
    try:
        result = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", image_ref],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return image_ref in result.stdout.splitlines()


def build_image(
    image_ref: str,
    dockerfile: Path,
    build_context: Path,
    build_args: Optional[dict[str, str]] = None,
) -> None:
    """Run ``docker build -t <image_ref> -f <dockerfile> <context>``. Streams
    output to stdout so the user sees progress on a long first build."""
    cmd = ["docker", "build", "-t", image_ref]
    for key, value in (build_args or {}).items():
        cmd.extend(["--build-arg", f"{key}={value}"])
    cmd.extend(["-f", str(dockerfile), str(build_context)])
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to build worker image {image_ref!r}. "
            f"Re-run the build manually to see the full error: {' '.join(cmd)}"
        )


# --------------------------------------------------------------------------
# Build-input resolution
# --------------------------------------------------------------------------


def _bundled_dockerfile() -> Path:
    """Path of the wheel-bundled ``Dockerfile.installed``."""
    path = Path(__file__).resolve().parent.parent / "_docker" / "Dockerfile.installed"
    if not path.is_file():
        raise RuntimeError(
            f"Bundled worker Dockerfile missing at {path}. This means the steppy install is "
            f"incomplete — reinstall steppy, or point `parallel.dockerfile` at a worker "
            f"Dockerfile you control."
        )
    return path


def _abs_path(raw: str, project_root: Path) -> Path:
    """Resolve a config path: absolute wins, otherwise relative to project root."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return Path(path)


def _dist_info_stem(name: str) -> str:
    """A distribution name as its ``.dist-info`` folder spells it: runs of
    ``-``, ``_`` and ``.`` become one ``_``, lowercased (the wheel spec).
    """
    return re.sub(r"[-_.]+", "_", name).lower()


def runtime_requirements() -> list[str]:
    """steppy's runtime dependencies, read from the installed metadata.

    Extras named in :data:`BAKED_EXTRAS` are **included** (with the marker
    stripped, since the container unconditionally wants them) so this image
    matches what the repo-root ``Dockerfile`` installs via
    ``pip install '/tmp/steppy[retry,report]'``. Any other optional-extra
    requirement is dropped. Non-extra environment markers are preserved
    verbatim for pip to evaluate.
    """
    try:
        reqs = importlib.metadata.requires(DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        reqs = None
    if not reqs:
        raise RuntimeError(
            "Cannot read steppy's dependency metadata, so the worker image build context "
            "cannot be synthesized. Point `parallel.dockerfile` (and optionally "
            "`parallel.build_context`) at a worker Dockerfile you control, or run steppy "
            "from a source checkout."
        )
    out: list[str] = []
    for raw in reqs:
        spec, _, marker = raw.partition(";")
        if "extra" in marker:
            named = _EXTRA_MARKER_RE.search(marker)
            if named and named.group(1) in BAKED_EXTRAS:
                # Strip the marker: inside the container the extra is not
                # conditional, it is simply wanted.
                out.append(spec.strip())
            continue
        line = raw.strip() if marker.strip() else spec.strip()
        if line:
            out.append(line)
    return out


def synthesize_installed_context(*, package_root: Path, staging_dir: Path) -> Path:
    """Build a self-contained ``docker build`` context from the INSTALLED package.

    Produces, under ``<staging_dir>/worker-build-context/``::

        Dockerfile                  copy of the bundled _docker/Dockerfile.installed
        requirements.txt            runtime deps from the installed metadata
        pkg/steppy/                 copy of the installed package
        pkg/steppy_dev-<ver>.dist-info/ so importlib.metadata works in the container

    This exists because the repo-root ``Dockerfile`` COPYs ``pyproject.toml``,
    ``hatch_build.py``, ``src/``, ``templates/``, ``skills/`` and
    ``CHANGELOG.md`` — none of which exist when steppy was installed as a
    wheel. The worker container only runs ``python -m steppy worker`` →
    pytest on the already-generated tests bind-mounted from the project, so a
    package copy is functionally equivalent to the repo build.
    """
    context = staging_dir / _CONTEXT_DIRNAME
    if context.exists():
        shutil.rmtree(context)
    pkg_dir = context / "pkg"
    pkg_dir.mkdir(parents=True)

    shutil.copytree(package_root, pkg_dir / package_root.name, ignore=_COPY_IGNORE)

    # dist-info is best-effort: without it the package still imports, only
    # importlib.metadata.version(DISTRIBUTION) stops working inside the container.
    # The folder is named after the distribution, not the import package, and
    # in its normalised form (`steppy_dev-0.3.0.dist-info`).
    for dist_info in sorted(package_root.parent.glob(f"{_dist_info_stem(DISTRIBUTION)}-*.dist-info")):
        shutil.copytree(dist_info, pkg_dir / dist_info.name, ignore=_COPY_IGNORE)
        break

    requirements = "\n".join(runtime_requirements())
    (context / "requirements.txt").write_text(requirements + "\n", encoding="utf-8")
    shutil.copyfile(_bundled_dockerfile(), context / "Dockerfile")
    return context


def resolve_build_inputs(
    *,
    package_root: Path,
    project_root: Path,
    parallel_cfg: Any,
    staging_dir: Path,
) -> BuildInputs:
    """Pick the Dockerfile + build context for the worker image.

    Priority: explicit config → source checkout → synthesized from the
    installed package. Raises ``RuntimeError`` with an actionable message
    when explicitly configured paths don't exist.
    """
    base_override = getattr(parallel_cfg, "worker_base_image", None)
    build_args = {_BASE_IMAGE_ARG: base_override} if base_override else {}

    dockerfile_cfg = getattr(parallel_cfg, "dockerfile", None)
    context_cfg = getattr(parallel_cfg, "build_context", None)

    if dockerfile_cfg:
        dockerfile = _abs_path(dockerfile_cfg, project_root)
        if not dockerfile.is_file():
            raise RuntimeError(
                f"`parallel.dockerfile` is set to {dockerfile_cfg!r} but no file exists at {dockerfile}. "
                f"Fix the path in config.yaml (relative paths resolve from the project root), "
                f"or remove the setting to let steppy build the worker image itself."
            )
        context = _abs_path(context_cfg, project_root) if context_cfg else dockerfile.parent
        if not context.is_dir():
            raise RuntimeError(
                f"`parallel.build_context` is set to {context_cfg!r} but no directory exists at {context}."
            )
        return BuildInputs(dockerfile=dockerfile, context=context, mode="config", build_args=build_args)

    if context_cfg:
        raise RuntimeError(
            "`parallel.build_context` is set but `parallel.dockerfile` is not. "
            "Set both to use a custom worker image, or neither to let steppy build it."
        )

    # Source checkout: <repo>/src/steppy → <repo>. Unchanged from day one.
    repo_root = package_root.parent.parent
    repo_dockerfile = repo_root / "Dockerfile"
    if repo_dockerfile.is_file() and (repo_root / "pyproject.toml").is_file():
        return BuildInputs(dockerfile=repo_dockerfile, context=repo_root, mode="repo", build_args=build_args)

    context = synthesize_installed_context(package_root=package_root, staging_dir=staging_dir)
    return BuildInputs(
        dockerfile=context / "Dockerfile",
        context=context,
        mode="installed",
        build_args=build_args,
    )


def ensure_worker_image(
    *,
    image_prefix: str,
    package_root: Path,
    project_root: Path,
    parallel_cfg: Any,
    staging_dir: Path,
    build: bool = True,
) -> tuple[str, str]:
    """Return ``(image_ref, mode)``, building the image if it isn't present.

    ``mode`` is ``"cached"`` when an existing local image was reused, else the
    :class:`BuildInputs` mode that produced the build.

    The local probe happens BEFORE build inputs are resolved, so a warm image
    costs nothing — no context is synthesized. That probe is also where a
    future registry ``docker pull`` slots in: local → pull → build.

    Raises ``RuntimeError`` if the image is missing and ``build=False`` (the
    user passed ``--no-build``), or if resolution or the build itself fails.
    """
    base_override = getattr(parallel_cfg, "worker_base_image", None) or ""
    # WORKER_IMAGE_CONTRACT rides in the tag digest so an image-content change
    # that touches no *.py (a Dockerfile / baked-extras edit) still invalidates
    # warm images instead of being silently ignored.
    tag_extra = f"{WORKER_IMAGE_CONTRACT}\0{base_override}"
    image_ref = f"{image_prefix}:{compute_image_tag(package_root, extra=tag_extra)}"
    if image_exists(image_ref):
        return image_ref, "cached"
    if not build:
        raise RuntimeError(
            f"Worker image {image_ref!r} not found locally and --no-build was passed. "
            f"Re-run without --no-build to let steppy build it, or build it yourself and "
            f"tag it {image_ref}."
        )
    inputs = resolve_build_inputs(
        package_root=package_root,
        project_root=project_root,
        parallel_cfg=parallel_cfg,
        staging_dir=staging_dir,
    )
    build_image(image_ref, inputs.dockerfile, inputs.context, build_args=inputs.build_args)
    return image_ref, inputs.mode
