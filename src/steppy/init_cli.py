"""``steppy init`` — scaffold a new project (FLAWS §7.1).

Zero-to-first-test is otherwise ~7 non-trivial steps, two of which (authoring
``config.yaml`` and a first scenario) have no scaffolding at all. ``init`` writes
a commented neutral ``config.yaml`` (with a ``$schema`` header for editor
autocomplete), a runnable starter scenario, ``credentials.yaml.example``, a
config JSON Schema, and a ``.steppy/scaffold.json`` manifest, and appends the
right ``.gitignore`` entries. ``--ci`` also drops in the GitHub Actions workflow,
and ``--app-dir`` puts the app-side skill in the app's own repository.

Re-running it is safe, which is also how a project set up by hand adopts the
scaffold: existing files are kept, the starter scenario is left out when
``scenarios/`` already holds some, and the manifest is merged, not replaced.

A leaf module: imports only from ``_cli_common`` + stdlib (no cycle with
``cli``). Registered via :func:`register_init` at the bottom of ``cli.py``.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from ._cli_common import _cli_errors, _error, console
from .config_schema import schema_json

# Files init lays down, resolved relative to the target project root. Value is
# the bundled source path under steppy/templates/ (None ⇒ generated in code).
_SCHEMA_FILENAME = "steppy-config.schema.json"

# Packaged Claude skills installed into <project>/.claude/skills/<name>/SKILL.md
# (FLAWS §7.1). Each name matches the skill's frontmatter `name:` and its
# bundled source dir under steppy/skills/<name>/.
SKILL_NAMES = ("steppy-scenario-author", "steppy-template", "steppy-ci-report", "steppy-app-tours")

# The skills that also belong in the app's own repository (``--app-dir``). The
# code they walk an assistant through lives there, and an assistant only sees
# the skills of the tree it works in.
APP_SKILL_NAMES = ("steppy-app-tours",)

# Skill folders people copied in by hand before Steppy packaged its skills, and
# what replaced each. init never wrote them, so no manifest has their hash and
# nothing can tell a pristine copy from an edited one: they are reported, never
# removed. Left in place, an assistant loads them beside their replacement.
LEGACY_SKILLS = {
    "steppy": "steppy-scenario-author",
    "steppy-angular-export": "steppy-scenario-author",
}

# .gitignore lines init ensures are present (idempotent append). The negation
# un-ignores the committed scaffold manifest that lives under the ignored
# .steppy/ dir.
_GITIGNORE_LINES = (
    ".auth/",
    "output/",
    ".steppy/",
    "credentials.yaml",
    "!.steppy/scaffold.json",
)


def _bundled(*parts: str) -> Path:
    """Absolute path to a bundled asset under ``steppy/templates/``."""
    return Path(importlib.resources.files("steppy") / "templates" / Path(*parts))


def _bundled_skill(*parts: str) -> Path:
    """Absolute path to a bundled asset under ``steppy/skills/``."""
    return Path(importlib.resources.files("steppy") / "skills" / Path(*parts))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(root: Path, path: Path) -> str:
    """``path`` as the manifest records it: relative to ``root``, ``../`` included.

    An app repository given to ``--app-dir`` usually sits beside the project.
    On another Windows drive no relative path exists, so the path stays absolute.
    """
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:
        return path.as_posix()


def _write(target: Path, content: str, written: dict, skipped: list) -> None:
    """Write ``content`` to ``target`` unless it already exists.

    Records the file (relative to the project root, recorded by the caller) so
    the scaffold manifest can hash exactly what init produced. Never clobbers a
    user file — an existing path is reported and skipped.
    """
    if target.exists():
        skipped.append((target, "already exists"))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    written[target] = _sha256(content)


def _ensure_gitignore(root: Path, written: dict) -> None:
    """Append missing ignore lines idempotently (never duplicates a line)."""
    gitignore = root / ".gitignore"
    existing = ""
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
    present = {line.strip() for line in existing.splitlines()}
    to_add = [line for line in _GITIGNORE_LINES if line not in present]
    if not to_add:
        return
    block = ""
    if existing and not existing.endswith("\n"):
        block += "\n"
    if existing:
        block += "\n"
    block += "# Steppy\n" + "\n".join(to_add) + "\n"
    with open(gitignore, "a", encoding="utf-8", newline="\n") as f:
        f.write(block)
    # The manifest records the resulting file's hash so `update` can tell a
    # pristine .gitignore from a user-edited one.
    written[gitignore] = _sha256(gitignore.read_text(encoding="utf-8"))


def _write_manifest(root: Path, written: dict) -> None:
    """Write ``.steppy/scaffold.json`` = {steppy_version, files:{rel: sha256}}.

    Must ship from the first ``init`` — modification detection (§7.7 ``steppy
    update``) can't be retrofitted onto files whose original hashes were never
    recorded.

    A re-run merges into the manifest it finds. Replacing it would forget every
    file an earlier run recorded, and ``update`` would stop managing them. The
    recorded ``steppy_version`` is kept too: ``update`` shows the changelog since
    that version, and the older baselines still date from it.
    """
    manifest_path = root / ".steppy" / "scaffold.json"
    manifest = {"steppy_version": __version__, "files": {}}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = dict(manifest.get("files", {}))
    files.update({_rel(root, path): digest for path, digest in written.items()})
    manifest["files"] = dict(sorted(files.items()))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _legacy_skills(root: Path) -> list[tuple[str, str]]:
    """The :data:`LEGACY_SKILLS` folders present under ``root``, with each one's replacement."""
    return [
        (old, new) for old, new in LEGACY_SKILLS.items() if (root / ".claude" / "skills" / old / "SKILL.md").is_file()
    ]


def _report_legacy_skills(root: Path) -> bool:
    """Print one line per legacy skill folder; returns whether there was any."""
    found = _legacy_skills(root)
    for old, new in found:
        console.print(
            f"  [yellow]legacy[/yellow] .claude/skills/{old}/ — an old copy, superseded by {new}; "
            "delete the folder once any local edits are carried over"
        )
    return bool(found)


def _run_init(
    directory: Path,
    ci: bool,
    no_skills: bool = False,
    ci_gitlab: bool = False,
    app_dir: Optional[Path] = None,
) -> None:
    root = directory.resolve()
    app_root = app_dir.resolve() if app_dir is not None else None
    if app_root is not None and not app_root.is_dir():
        _error(f"--app-dir: {app_dir} is not a directory")
        raise typer.Exit(1)
    root.mkdir(parents=True, exist_ok=True)

    written: dict[Path, str] = {}
    skipped: list[tuple[Path, str]] = []

    # Core scaffold assets (bundled templates + the generated schema).
    _write(
        root / "config.yaml",
        _bundled("init", "config.yaml").read_text(encoding="utf-8"),
        written,
        skipped,
    )
    # The starter scenario is for an empty project. In one that already has
    # scenarios, it would be generated and run against the real app.
    starter = root / "scenarios" / "example--first-flow.md"
    if not starter.exists() and any((root / "scenarios").rglob("*.md")):
        skipped.append((starter, "scenarios/ already has scenarios"))
    else:
        _write(
            starter,
            _bundled("init", "scenarios", "example--first-flow.md").read_text(encoding="utf-8"),
            written,
            skipped,
        )
    _write(
        root / "credentials.yaml.example",
        _bundled("init", "credentials.yaml.example").read_text(encoding="utf-8"),
        written,
        skipped,
    )
    _write(root / _SCHEMA_FILENAME, schema_json(), written, skipped)

    if ci:
        _write(
            root / ".github" / "workflows" / "steppy.yml",
            _bundled("ci", "github-actions.yml").read_text(encoding="utf-8"),
            written,
            skipped,
        )

    # A separate flag rather than `--ci <vendor>`: `--ci` is a boolean people
    # already type, and giving it a value would break every existing invocation.
    # Writing both files unconditionally would leave a stray .github/ in a GitLab
    # project, which is noise in a scaffold meant to be read.
    if ci_gitlab:
        _write(
            root / ".gitlab-ci.yml",
            _bundled("ci", "gitlab-ci.yml").read_text(encoding="utf-8"),
            written,
            skipped,
        )

    # Packaged Claude skills — installed by default so the project's AI
    # assistant is a Steppy expert on day one (FLAWS §7.1). Routed through
    # _write so they land in the manifest and never clobber user edits.
    if not no_skills:
        for name in SKILL_NAMES:
            _write(
                root / ".claude" / "skills" / name / "SKILL.md",
                _bundled_skill(name, "SKILL.md").read_text(encoding="utf-8"),
                written,
                skipped,
            )

    # The app-side skills, in the app's repository. Recorded in this project's
    # manifest (as ../<app>/…), so `steppy update` refreshes them with the rest.
    # When the app is this directory, the loop above already installed them.
    app_skills: list[Path] = []
    if app_root is not None and (app_root != root or no_skills):
        for name in APP_SKILL_NAMES:
            target = app_root / ".claude" / "skills" / name / "SKILL.md"
            _write(target, _bundled_skill(name, "SKILL.md").read_text(encoding="utf-8"), written, skipped)
            if target in written:
                app_skills.append(target)

    _ensure_gitignore(root, written)
    _write_manifest(root, written)

    # Report.
    for path in sorted(written):
        console.print(f"  [green]create[/green] {_rel(root, path)}")
    for path, reason in sorted(skipped):
        console.print(f"  [yellow]skip[/yellow]   {_rel(root, path)} ({reason})")
    _report_legacy_skills(root)

    if skipped and not written:
        console.print("\n[yellow]Nothing to do[/yellow] — all scaffold files already exist.")
        return

    if app_skills:
        console.print(
            "\nCommit the skill in the app's repository: it is what an AI assistant working on the app reads."
        )

    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. Edit config.yaml — set your app URLs, roles, and credentials.")
    console.print("  2. steppy validate   # check your scenarios")
    console.print("  3. steppy generate   # produce the Playwright tests")
    console.print("  4. steppy run        # run them")


def register_init(app: typer.Typer) -> None:
    """Register the ``init`` command on the root Typer app."""

    @app.command("init")
    def init(
        directory: Path = typer.Argument(
            Path("."),
            help="Directory to scaffold (default: current directory).",
        ),
        ci: bool = typer.Option(
            False,
            "--ci",
            help="Also write a GitHub Actions workflow (.github/workflows/steppy.yml).",
        ),
        ci_gitlab: bool = typer.Option(
            False,
            "--ci-gitlab",
            help="Also write a GitLab CI pipeline (.gitlab-ci.yml).",
        ),
        no_skills: bool = typer.Option(
            False,
            "--no-skills",
            help="Do not install the packaged Claude skills into .claude/skills/.",
        ),
        app_dir: Optional[Path] = typer.Option(
            None,
            "--app-dir",
            help="Also install the app-side skill (steppy-app-tours: wiring in-app tours into the app's code) "
            "into this app repository's .claude/skills/. `steppy update` keeps it current.",
        ),
    ):
        """Scaffold a new Steppy project (config, example scenario, schema, gitignore).

        Installs the packaged Claude skills into .claude/skills/ by default so the
        project's AI assistant knows the Steppy scenario format (use --no-skills to
        opt out). Safe to re-run in an existing project: it only adds what is
        missing, and leaves the starter scenario out when scenarios/ has some.
        """
        with _cli_errors():
            _run_init(directory, ci, no_skills, ci_gitlab=ci_gitlab, app_dir=app_dir)
