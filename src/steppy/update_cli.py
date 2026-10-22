"""``steppy update`` — reconcile a scaffolded project with the installed version.

This is **not** a self-updater: pip/uv/pipx own upgrading the ``steppy-dev`` package,
and a CLI can't reliably reinstall itself across venv/uv/pipx environments.
``steppy update`` runs *after* a package upgrade and does three things (FLAWS
§7.7):

1. **Refresh managed scaffold assets** — the config JSON Schema, the packaged
   Claude skills, and the CI workflow — using the ``.steppy/scaffold.json``
   manifest that ``steppy init`` records. Files still matching their recorded
   hash are replaced silently; a file the user has edited is left untouched and
   the new version is written alongside as ``<file>.new`` (the pacman ``.pacnew``
   pattern — never clobber user edits, never silently skip an update either).
   Starter files the user owns (``config.yaml``, scenarios, ``credentials.yaml``)
   are never touched. A skill shipped after the project was scaffolded is
   installed, and the old hand-copied skill folders (``init_cli.LEGACY_SKILLS``)
   are reported.
2. **Compatibility report** (``--check``) — a report-only, CI-able scan of
   ``scenarios/`` + ``config.yaml`` against the deprecation registry
   (:mod:`steppy.compat`), exiting non-zero on breakage.
3. **Changelog highlights** — the CHANGELOG sections between the version the
   manifest recorded and the installed one, breaking entries flagged.

A leaf module: imports only ``_cli_common``, ``init_cli`` (asset primitives),
``compat``, ``config_schema`` + stdlib — no cycle with ``cli``. Registered via
:func:`register_update` at the bottom of ``cli.py``.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from pathlib import Path

import typer

from . import __version__
from ._cli_common import _cli_errors, _error, console
from .compat import DEPRECATIONS, Deprecation
from .config_schema import schema_json
from .init_cli import APP_SKILL_NAMES, SKILL_NAMES, _bundled, _bundled_skill, _report_legacy_skills, _sha256

_MANIFEST_REL = ".steppy/scaffold.json"
_SCHEMA_FILENAME = "steppy-config.schema.json"
_CI_WORKFLOW_REL = ".github/workflows/steppy.yml"
_CI_GITLAB_REL = ".gitlab-ci.yml"

# Severity → colour tag for the compat report (matches the rest of the CLI).
_SEVERITY_STYLE = {"info": "dim", "warning": "yellow", "error": "red"}


# ── Managed-asset refresh ──────────────────────────────────────────────────


def _skill_path(rel: str) -> tuple[str, str] | None:
    """``(base, name)`` for a packaged skill at ``<base>/.claude/skills/<name>/SKILL.md``.

    ``base`` is ``""`` for the project's own skills, else the app repository
    ``init --app-dir`` installed into, as the manifest records it (``../app``).
    ``None`` for anything else, a skill of the user's own included.
    """
    parts = rel.split("/")
    if (
        len(parts) >= 4
        and parts[-4:-2] == [".claude", "skills"]
        and parts[-1] == "SKILL.md"
        and parts[-2] in SKILL_NAMES + APP_SKILL_NAMES
    ):
        return "/".join(parts[:-4]), parts[-2]
    return None


def _refreshable_content(rel: str) -> str | None:
    """Fresh bundled content for a *managed* scaffold asset, or ``None``.

    ``None`` means "not a Steppy-owned asset" — a starter file the user owns
    (``config.yaml``, scenarios, ``credentials.yaml.example``, ``.gitignore``),
    which ``update`` never touches. The classification is purely by relpath so it
    stays in lockstep with what ``init`` lays down.
    """
    if rel == _SCHEMA_FILENAME:
        return schema_json()
    if rel == _CI_WORKFLOW_REL:
        return _bundled("ci", "github-actions.yml").read_text(encoding="utf-8")
    if rel == _CI_GITLAB_REL:
        return _bundled("ci", "gitlab-ci.yml").read_text(encoding="utf-8")
    skill = _skill_path(rel)
    if skill is not None:
        return _bundled_skill(skill[1], "SKILL.md").read_text(encoding="utf-8")
    return None


def _install_new_skills(root: Path, files: dict[str, str]) -> bool:
    """Install the skills this version ships that the manifest has never recorded.

    Done per skill folder the manifest knows: the project's (every skill in
    ``SKILL_NAMES``) and each app's from ``init --app-dir`` (``APP_SKILL_NAMES``).
    A folder qualifies only while one of its recorded skills is still on disk, so
    a project scaffolded with ``--no-skills``, or whose skills were all deleted,
    gets none. A recorded skill the user deleted stays deleted: that is
    :func:`_refresh_assets`' rule, and this never sees recorded paths.

    A file already at the path is never overwritten. If it matches the shipped
    skill it is adopted; otherwise the shipped one is written beside it as
    ``SKILL.md.new``, like any diverged asset. Returns whether anything was reported.
    """
    in_use: dict[str, bool] = {}
    for rel in files:
        skill = _skill_path(rel)
        if skill is not None:
            in_use[skill[0]] = in_use.get(skill[0], False) or (root / rel).is_file()

    acted = False
    for base, used in sorted(in_use.items()):
        if not used:
            continue
        for name in SKILL_NAMES if base == "" else APP_SKILL_NAMES:
            rel = f"{base}/.claude/skills/{name}/SKILL.md" if base else f".claude/skills/{name}/SKILL.md"
            if rel in files:
                continue
            content = _bundled_skill(name, "SKILL.md").read_text(encoding="utf-8")
            target = root / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
                files[rel] = _sha256(content)
                console.print(f"  [green]install[/green] {rel} (new in this version)")
                acted = True
            elif _sha256(target.read_text(encoding="utf-8")) == _sha256(content):
                files[rel] = _sha256(content)
            else:
                target.with_name("SKILL.md.new").write_text(content, encoding="utf-8", newline="\n")
                console.print(
                    f"  [yellow]diverged[/yellow] {rel} — not written by Steppy; kept it, "
                    f"the shipped skill is in {rel}.new"
                )
                acted = True
    return acted


def _refresh_assets(root: Path, manifest: dict) -> None:
    """Reconcile managed assets against the installed bundle (pacman pattern).

    Mutates ``manifest['files']`` in place with the new baselines so the caller
    can persist it. Reports each action to the console.
    """
    files: dict[str, str] = manifest.get("files", {})
    any_action = False

    for rel in sorted(files):
        new_content = _refreshable_content(rel)
        if new_content is None:
            continue  # starter file — user owns it, never touched.
        recorded = files[rel]
        new_hash = _sha256(new_content)
        target = root / rel

        if not target.exists():
            # User deleted it deliberately — don't resurrect it.
            console.print(f"  [dim]absent[/dim] {rel} (removed; not restored)")
            any_action = True
            continue

        current = _sha256(target.read_text(encoding="utf-8"))

        if current == recorded:
            # Pristine — safe to replace.
            if new_hash != recorded:
                target.write_text(new_content, encoding="utf-8", newline="\n")
                files[rel] = new_hash
                console.print(f"  [green]update[/green] {rel}")
                any_action = True
            # else: already up to date — say nothing.
        else:
            # User-modified since we last wrote it.
            if new_hash == current:
                # Their edits happen to match the new version — bless it.
                files[rel] = new_hash
            elif new_hash == recorded:
                # No new version shipped; keep their edits, stay quiet.
                pass
            else:
                new_path = target.with_name(target.name + ".new")
                new_path.write_text(new_content, encoding="utf-8", newline="\n")
                console.print(f"  [yellow]diverged[/yellow] {rel} — kept your copy; new version written to {rel}.new")
                # Leave the manifest baseline unchanged so we keep re-detecting
                # the divergence until the user merges.
                any_action = True

    if _install_new_skills(root, files):
        any_action = True
    if _report_legacy_skills(root):
        any_action = True

    if not any_action:
        console.print("  [dim]all managed assets up to date[/dim]")


def _rewrite_manifest(root: Path, manifest: dict) -> None:
    """Persist the manifest, bumping ``steppy_version`` to the installed one.

    Same serialization as ``init`` (:func:`init_cli._write_manifest`) but merges
    — non-managed (starter) entries are preserved verbatim.
    """
    manifest["steppy_version"] = __version__
    manifest["files"] = dict(sorted(manifest.get("files", {}).items()))
    (root / _MANIFEST_REL).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


# ── Compatibility scan ─────────────────────────────────────────────────────


def _dep(dep_id: str) -> Deprecation:
    return next(d for d in DEPRECATIONS if d.id == dep_id)


def _scan_scenarios_for_action(root: Path, action: str) -> list[tuple[Path, int, str]]:
    """Raw line scan of scenarios/ for a deprecated step-action spelling.

    Raw text (not the parser) because ``models.py`` normalizes the alias away at
    parse time — by the time a ``Scenario`` exists the original spelling is gone.
    """
    pattern = re.compile(rf"^\s*-\s*{re.escape(action)}\b")
    hits: list[tuple[Path, int, str]] = []
    scenarios_dir = root / "scenarios"
    if not scenarios_dir.is_dir():
        return hits
    for path in sorted(scenarios_dir.rglob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append((path, lineno, line.strip()))
    return hits


def _scan_config_auth_mode(root: Path) -> list[tuple[Path, int, str]]:
    """Raw line scan of config.yaml for ``mode: auth0`` (the deprecated mode)."""
    pattern = re.compile(r"^\s*mode:\s*['\"]?auth0\b")
    return _scan_config_lines(root, pattern)


def _scan_config_nested_key(root: Path, parent: str, child: str) -> list[tuple[Path, int, str]]:
    """Detect a ``<child>:`` key nested directly under top-level ``<parent>:``.

    Indent-aware on purpose: a bare ``child`` regex would also match the same
    word appearing under some other block (a per-app ``mode: auth0``, another
    section's ``reports_dir``). Raw text rather than the parser, for the same
    reason the action scan is: by the time a model exists the deprecated
    spelling has been normalized or dropped.
    """
    cfg = root / "config.yaml"
    hits: list[tuple[Path, int, str]] = []
    if not cfg.is_file():
        return hits
    parent_re = re.compile(rf"^{re.escape(parent)}:\s*(#.*)?$")
    child_re = re.compile(rf"^{re.escape(child)}:\s*")
    in_parent = False
    parent_indent = 0
    for lineno, raw in enumerate(cfg.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if not in_parent:
            if parent_re.match(raw):
                in_parent = True
                parent_indent = indent
            continue
        # Inside the block — a key at the same indent ends it.
        if indent <= parent_indent:
            in_parent = parent_re.match(raw) is not None
            parent_indent = indent if in_parent else parent_indent
            continue
        if child_re.match(stripped):
            hits.append((cfg, lineno, stripped))
    return hits


def _scan_config_lines(root: Path, pattern: re.Pattern) -> list[tuple[Path, int, str]]:
    cfg = root / "config.yaml"
    hits: list[tuple[Path, int, str]] = []
    if not cfg.is_file():
        return hits
    for lineno, line in enumerate(cfg.read_text(encoding="utf-8").splitlines(), 1):
        if pattern.search(line):
            hits.append((cfg, lineno, line.strip()))
    return hits


def _scan_deprecations(root: Path) -> list[dict]:
    """Scan a project for every known deprecated spelling.

    Returns a list of ``{deprecation, file, line, text}`` hits. Matchers are keyed
    to :data:`steppy.compat.DEPRECATIONS` entries by id — this is the live-matcher
    layer the ``compat`` docstring anticipates.
    """
    scans = (
        (_dep("action-auth0-login-to-login"), _scan_scenarios_for_action(root, "auth0-login")),
        (_dep("auth-mode-auth0-to-form"), _scan_config_auth_mode(root)),
        (_dep("auth-global-key-auth0-to-form"), _scan_config_nested_key(root, "auth", "auth0")),
        (_dep("output-reports-dir-inert"), _scan_config_nested_key(root, "output", "reports_dir")),
    )
    hits: list[dict] = []
    for dep, found in scans:
        for path, line, text in found:
            hits.append({"deprecation": dep, "file": path, "line": line, "text": text})
    return hits


def _report_deprecations(root: Path, hits: list[dict]) -> None:
    if not hits:
        console.print("  [dim]no deprecated spellings found[/dim]")
        return
    for hit in hits:
        dep: Deprecation = hit["deprecation"]
        style = _SEVERITY_STYLE.get(dep.severity, "dim")
        rel = hit["file"].relative_to(root).as_posix()
        console.print(f"  [{style}]{dep.severity}[/{style}] {rel}:{hit['line']} — {dep.message}")


def _exit_code_for(hits: list[dict]) -> int:
    """Non-zero only on breakage (an ``error``-severity hit)."""
    return 1 if any(h["deprecation"].severity == "error" for h in hits) else 0


# ── Changelog highlights ───────────────────────────────────────────────────

_VERSION_RE = re.compile(r"^##\s*\[?(\d+(?:\.\d+)*)\]?")


def _parse_version(text: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in text.split("."))
    except ValueError:
        return None


def _bundled_changelog() -> str | None:
    try:
        path = Path(importlib.resources.files("steppy") / "CHANGELOG.md")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _changelog_sections(text: str) -> list[tuple[tuple[int, ...], str, str]]:
    """Split a Keep-a-Changelog body into ``(version_tuple, heading, body)``.

    ``[Unreleased]`` and any non-numeric heading are skipped (no version tuple).
    """
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    sections: list[tuple[tuple[int, ...], str, str]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        heading = lines[start]
        m = _VERSION_RE.match(heading)
        if not m:
            continue
        version = _parse_version(m.group(1))
        if version is None:
            continue
        body = "\n".join(lines[start + 1 : end]).strip("\n")
        sections.append((version, heading.lstrip("# ").strip(), body))
    return sections


def _changelog_highlights(recorded_version: str) -> None:
    """Print CHANGELOG sections in ``(recorded, installed]``, flagging breaking."""
    text = _bundled_changelog()
    if not text:
        return
    lo = _parse_version(recorded_version)
    hi = _parse_version(__version__)
    if lo is None or hi is None:
        return
    selected = [(heading, body) for version, heading, body in _changelog_sections(text) if lo < version <= hi]
    if not selected:
        console.print(f"  [dim]no changelog entries between {recorded_version} and {__version__}[/dim]")
        return
    for heading, body in selected:
        breaking = bool(re.search(r"breaking|### Removed", body, re.IGNORECASE))
        tag = " [red](breaking)[/red]" if breaking else ""
        console.print(f"  [bold]{heading}[/bold]{tag}")
        for line in body.splitlines():
            if line.strip():
                console.print(f"    {line}")


# ── Command ────────────────────────────────────────────────────────────────


def _run_update(directory: Path, check: bool = False) -> None:
    root = directory.resolve()

    # `--check` is a pure, manifest-free compat scan (CI-able).
    if check:
        console.print("[bold]Compatibility check[/bold]")
        hits = _scan_deprecations(root)
        _report_deprecations(root, hits)
        code = _exit_code_for(hits)
        if code:
            _error("compatibility check found breaking usage — see above")
        raise typer.Exit(code)

    manifest_path = root / _MANIFEST_REL
    if not manifest_path.is_file():
        _error(
            "not a Steppy-scaffolded project (no .steppy/scaffold.json) — run `steppy init` first; "
            "in an existing project it only adds what is missing"
        )
        raise typer.Exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_version = manifest.get("steppy_version", "0.0.0")

    console.print(f"[bold]Updating project[/bold] (scaffolded with {recorded_version}, installed {__version__})\n")

    console.print("[bold]Managed assets[/bold]")
    _refresh_assets(root, manifest)
    _rewrite_manifest(root, manifest)

    console.print("\n[bold]Compatibility[/bold]")
    _report_deprecations(root, _scan_deprecations(root))

    console.print("\n[bold]Changelog[/bold]")
    _changelog_highlights(recorded_version)


def register_update(app: typer.Typer) -> None:
    """Register the ``update`` command on the root Typer app."""

    @app.command("update")
    def update(
        directory: Path = typer.Argument(
            Path("."),
            help="Project directory to reconcile (default: current directory).",
        ),
        check: bool = typer.Option(
            False,
            "--check",
            help="Only scan scenarios/config for deprecated usage (no writes); "
            "exit non-zero on breaking usage. CI-able.",
        ),
    ):
        """Reconcile a scaffolded project with the installed Steppy version.

        Refreshes Steppy-owned assets (config schema, Claude skills, CI workflow)
        from the installed package — pristine files silently, user-edited files
        alongside as <file>.new — then reports deprecated usage and changelog
        highlights. Starter files you own (config.yaml, scenarios) are untouched.
        Only assets recorded in .steppy/scaffold.json are managed, plus the skills
        shipped since, which are installed beside them.
        """
        with _cli_errors():
            _run_update(directory, check)
