"""Docker Compose helpers for parallel mode.

Three things live here:

1. ``check_compose_version()`` — detect the local Docker Compose, fail fast
   if older than 2.20 (we depend on the ``!reset`` YAML tag for nullifying
   inherited port mappings).

2. ``introspect_user_compose()`` — run ``docker compose -f <user> config
   --format json`` to learn which services exist (and which need their host
   ports nulled out).

3. ``render_override()`` — build a per-worker ``override-w<N>.yml`` that adds
   the ``steppy-worker`` service and uses ``!reset []`` to clear port
   bindings on user services.

We hand-build the YAML rather than going through PyYAML because the
``!reset`` tag is a Compose-specific extension and PyYAML's representer for
unknown tags is awkward. The override is small and stable, so a templated
string is the simplest tool.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


MIN_COMPOSE_VERSION = (2, 20, 0)


class ComposeError(RuntimeError):
    """Raised when a docker compose command fails or returns unparseable output."""


@dataclass(frozen=True)
class ComposeIntrospection:
    """The bits of the user's compose we need for override generation."""

    services_with_ports: list[str]
    networks: list[str]  # named networks declared at the top level
    # Services declaring a `healthcheck:` block — the only ones a preauth/worker
    # gate can meaningfully wait on via `condition: service_healthy`.
    services_with_healthcheck: list[str] = field(default_factory=list)


def _run_compose(args: list[str], cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run ``docker compose <args>`` with stderr captured. Raises on non-zero."""
    cmd = ["docker", "compose", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise ComposeError(
            "docker not found on PATH. Install Docker Desktop (Windows/macOS) "
            "or the docker CLI (Linux) and ensure `docker compose` works."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ComposeError(f"docker compose timed out after {timeout}s: {' '.join(cmd)}") from e
    if result.returncode != 0:
        raise ComposeError(
            f"docker compose failed (exit {result.returncode}):\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    return result


def check_compose_version() -> tuple[int, int, int]:
    """Return the parsed ``(major, minor, patch)`` of the local Docker Compose.
    Raises ``ComposeError`` if Compose is older than 2.20 (we need ``!reset``)."""
    result = _run_compose(["version", "--format", "json"])
    try:
        data = json.loads(result.stdout)
        version_str = data.get("version", "").lstrip("v")
    except json.JSONDecodeError as e:
        raise ComposeError(f"unparseable `docker compose version` output: {result.stdout!r}") from e
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_str)
    if not match:
        raise ComposeError(f"could not parse compose version: {version_str!r}")
    parsed = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if parsed < MIN_COMPOSE_VERSION:
        min_str = ".".join(str(x) for x in MIN_COMPOSE_VERSION)
        raise ComposeError(
            f"steppy parallel mode requires Docker Compose >= {min_str} (found {version_str}). "
            f"Compose 2.20 introduced the !reset YAML tag we use to clear inherited port mappings. "
            f"Update Docker Desktop or your standalone docker-compose-plugin."
        )
    return parsed


def introspect_user_compose(compose_file: Path) -> ComposeIntrospection:
    """Discover which services declare host port mappings (need nulling) and
    which named networks the user defines (need joining by steppy-worker).

    Uses ``docker compose -f <file> config --format json`` so we get a fully
    resolved view (env-var substitution applied, !include processed, etc.) —
    much safer than re-implementing Compose semantics in PyYAML.
    """
    result = _run_compose(["-f", str(compose_file), "config", "--format", "json"], cwd=compose_file.parent)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ComposeError(f"unparseable `docker compose config` output: {e}") from e

    services = data.get("services", {}) or {}
    services_with_ports = [name for name, svc in services.items() if svc.get("ports")]

    networks = list((data.get("networks") or {}).keys())

    # Pre-flight: every depends_on with `condition: service_healthy` must point
    # at a service that actually declares a healthcheck. Without this,
    # `compose up --wait` returns immediately on a false success.
    missing: list[str] = []
    for svc_name, svc in services.items():
        depends = svc.get("depends_on") or {}
        if isinstance(depends, list):
            continue  # short form, no condition
        for dep_name, spec in depends.items():
            if isinstance(spec, dict) and spec.get("condition") == "service_healthy":
                if not (services.get(dep_name) or {}).get("healthcheck"):
                    missing.append(
                        f"{svc_name} depends on {dep_name} (service_healthy) but {dep_name} has no healthcheck block"
                    )
    if missing:
        raise ComposeError(
            "docker-compose pre-flight failed:\n  - "
            + "\n  - ".join(missing)
            + "\nAdd a `healthcheck:` block to each service referenced via "
            "`depends_on: condition: service_healthy`, or `compose up --wait` "
            "will report a false success and workers will race the app."
        )

    return ComposeIntrospection(
        services_with_ports=sorted(services_with_ports),
        networks=sorted(networks),
        services_with_healthcheck=sorted(name for name, svc in services.items() if svc.get("healthcheck")),
    )


def render_override(
    *,
    introspection: ComposeIntrospection,
    worker_image: str,
    image_tag: str,
    worker_index: int,
    run_id: str,
    project_root: Path,
    tmp_dir: Path,
    base_url_override: str,  # JSON-encoded {role: url} map
    locales: str,
    layouts: str,
    guide_mode: str,
    no_asserts: str,
    screenshots: str,
    coverage: str = "",
    pre_auth_command: str | list[str] | None = None,
    pre_auth_image: str | None = None,
    pre_auth_timeout: int = 600,
    trace_mode: str = "off",
    video_mode: str = "off",
    retries: int = 0,
    pytest_extra: list[str] | None = None,
    coord_subdir: str = "",
    auth_mount: str = "",
    events_dir: str = "",
    junit_dir: str = "",
    tour_expect_build_id: str = "",
) -> str:
    """Render a docker-compose override YAML for one worker.

    Pairs with the user's compose via ``-f <user> -f <override>``. Uses the
    ``!reset`` YAML tag (Compose 2.20+) to clear inherited host port mappings
    so multiple stacks can run concurrently without port collisions —
    workers reach the app via internal Docker DNS, host ports aren't needed.

    Mount strategy:
      /steppy/project    — project root, RO
      /steppy/project/.auth     — overlay RW (storage_state cache shared across workers,
                                  or a per-worker private dir when ``auth_mount`` is set)
      /steppy/project/output    — overlay RW (each scenario writes to its own subdir)
      /steppy/coord      — coordinator's tmp dir (queue/, running/, results/)

    Optional knobs, all inert when unset so the rendered YAML stays
    byte-identical to the pre-feature output (guard:
    ``tests/test_parallel_report_flags.py`` / ``tests/test_parallel_preauth.py``):

    ``trace_mode`` / ``video_mode`` / ``retries`` / ``pytest_extra``
        Report settings resolved host-side by ``cli._resolve_report_modes`` and
        the ``--`` passthrough argv. Emitted as ``STEPPY_TRACE`` /
        ``STEPPY_VIDEO`` / ``STEPPY_RETRIES`` / ``STEPPY_PYTEST_EXTRA``; the
        first two are read by the generated conftest, the last two by
        ``worker._build_pytest_argv``.
    ``coord_subdir``
        Points ``STEPPY_COORD_DIR`` at a subdirectory of the shared coord mount.
        The retry pass uses this to give attempt *n* its own queue/claims/results
        tree, so each attempt's evidence survives independently.
    ``auth_mount``
        Absolute host path to bind at ``/steppy/project/.auth`` instead of the
        project's shared ``.auth``. Used by ``parallel.isolate_auth`` to give
        each worker a private storage-state dir.
    ``tour_expect_build_id``
        The host's ``STEPPY_TOUR_EXPECT_BUILD_ID``, read inside the container by
        the tour replays' helper (``_steppy_tours.py``): the build id the app
        must serve.
    """
    worker_id = f"w{worker_index}"
    networks_block = ", ".join(introspection.networks) if introspection.networks else "default"

    # !reset on each user service that had ports. Hand-rolled because PyYAML
    # doesn't represent custom tags cleanly.
    reset_blocks = "\n".join(f"  {svc}:\n    ports: !reset []" for svc in introspection.services_with_ports)
    if not reset_blocks:
        reset_blocks = "  # (no user services declare host ports; nothing to clear)"

    # Volume bind sources are absolute host paths so Docker Desktop's
    # path-translation works on Windows, macOS, and Linux without surprises.
    project_abs = project_root.resolve().as_posix()
    tmp_abs = tmp_dir.resolve().as_posix()

    # The worker's view of the coordinator dir. A retry pass hands us a subdir
    # so attempt N gets its own queue/claims/results without disturbing the
    # records the previous attempt left behind.
    coord_dir = "/steppy/coord" + (f"/{coord_subdir}" if coord_subdir else "")
    # Where the worker's `.auth` comes from. Default = the project's shared
    # dir (one storage-state cache for every worker); `parallel.isolate_auth`
    # swaps in a per-worker private dir.
    auth_source = auth_mount or f"{project_abs}/.auth"

    # Optional env lines. Each is emitted ONLY when set, so an unused feature
    # leaves the rendered override byte-identical to the pre-feature output.
    extra_env = ""
    if trace_mode and trace_mode != "off":
        extra_env += f"      STEPPY_TRACE: '{trace_mode}'\n"
    if video_mode and video_mode != "off":
        extra_env += f"      STEPPY_VIDEO: '{video_mode}'\n"
    if retries:
        extra_env += f"      STEPPY_RETRIES: '{retries}'\n"
    if pytest_extra:
        # shlex-joined so the worker can round-trip it with shlex.split. Single
        # quotes are the YAML string delimiter here, so double them to escape.
        joined = shlex.join(pytest_extra).replace("'", "''")
        extra_env += f"      STEPPY_PYTEST_EXTRA: '{joined}'\n"
    # ``--bundle`` sinks. In-container paths under the writable output/ mount —
    # every worker writes into the SAME shared directories, which is safe
    # because both are keyed by scenario and a scenario is claimed by exactly
    # one worker.
    if events_dir:
        extra_env += f"      STEPPY_EVENTS_DIR: '{events_dir}'\n"
        # Mirrors cli._build_run_env: step markers ride with the event records,
        # and STEPPY_STEP_EVENTS=0 on the host opts out.
        if os.environ.get("STEPPY_STEP_EVENTS") != "0":
            extra_env += "      STEPPY_STEP_EVENTS: '1'\n"
    if junit_dir:
        extra_env += f"      STEPPY_JUNIT_DIR: '{junit_dir}'\n"
    if tour_expect_build_id:
        quoted = tour_expect_build_id.replace("'", "''")
        extra_env += f"      STEPPY_TOUR_EXPECT_BUILD_ID: '{quoted}'\n"

    # Optional pre-auth gate (config `parallel.pre_auth`). When set, add a
    # one-off `steppy-preauth` service that runs the login command in-network
    # (reusing the worker image + the project/.auth/coord mounts) and make
    # `steppy-worker` wait for it via `service_completed_successfully`. Absent ⇒
    # both blocks are empty strings ⇒ the override is byte-identical to before.
    preauth_service = ""
    worker_depends = ""
    if pre_auth_command is not None:
        preauth_image = pre_auth_image or f"{worker_image}:{image_tag}"
        inner = (
            ["sh", "-c", pre_auth_command] if isinstance(pre_auth_command, str) else [str(p) for p in pre_auth_command]
        )
        entrypoint_json = json.dumps(["python", "-m", "steppy", "_preauth-run"])
        command_json = json.dumps(["--timeout", str(pre_auth_timeout), "--", *inner])
        # Gate the login on every user service that declares a healthcheck, so
        # it only runs once the app is actually serving. Services without a
        # healthcheck can't be gated (see the pre_auth docs) — if none exist the
        # container relies on the login command's own readiness waits.
        if introspection.services_with_healthcheck:
            dep_lines = "\n".join(
                f"      {svc}:\n        condition: service_healthy" for svc in introspection.services_with_healthcheck
            )
            preauth_depends = f"    depends_on:\n{dep_lines}\n"
        else:
            preauth_depends = ""
        preauth_service = f"""
  steppy-preauth:
    image: {preauth_image}
    networks: [{networks_block}]
    entrypoint: {entrypoint_json}
    command: {command_json}
    environment:
      STEPPY_COORD_DIR: /steppy/coord
      STEPPY_BASE_URL_OVERRIDE: '{base_url_override}'
      PYTHONUNBUFFERED: '1'
    working_dir: /steppy/project
    volumes:
      - {project_abs}:/steppy/project:ro
      - {project_abs}/.auth:/steppy/project/.auth
      - {tmp_abs}:/steppy/coord
{preauth_depends}"""
        worker_depends = "    depends_on:\n      steppy-preauth:\n        condition: service_completed_successfully\n"

    return f"""# Auto-generated by steppy run --workers — do NOT edit.
# Pairs with the user compose via: docker compose -f <user> -f <this>
# Worker: {worker_id}  RunId: {run_id}

services:
{reset_blocks}
{preauth_service}
  steppy-worker:
    image: {worker_image}:{image_tag}
    networks: [{networks_block}]
{worker_depends}    environment:
      STEPPY_WORKER_ID: '{worker_id}'
      STEPPY_RUN_ID: '{run_id}'
      STEPPY_COORD_DIR: {coord_dir}
      STEPPY_BASE_URL_OVERRIDE: '{base_url_override}'
      STEPPY_LOCALES: '{locales}'
      STEPPY_LAYOUTS: '{layouts}'
      STEPPY_GUIDE_MODE: '{guide_mode}'
      STEPPY_NO_ASSERTS: '{no_asserts}'
      STEPPY_SCREENSHOTS: '{screenshots}'
      STEPPY_COVERAGE: '{coverage}'
{extra_env}      # Parallel-mode visibility: the generated conftest loads the events
      # plugin only when this is '1', and the worker switches pytest argv
      # to ``-q --tb=short``. Single-worker runs never set these.
      STEPPY_EMIT_EVENTS: '1'
      # Unblock Python's stdout buffering inside the container so pytest /
      # events appear in real time over ``docker compose logs -f``.
      PYTHONUNBUFFERED: '1'
    working_dir: /steppy/project
    volumes:
      - {project_abs}:/steppy/project:ro
      - {auth_source}:/steppy/project/.auth
      - {project_abs}/output:/steppy/project/output
      - {tmp_abs}:/steppy/coord
"""
