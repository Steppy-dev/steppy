"""Tests for `steppy init` project scaffolding (FLAWS §7.1)."""

import json

import pytest
import typer

from steppy.config import load_config
from steppy.init_cli import APP_SKILL_NAMES, LEGACY_SKILLS, SKILL_NAMES, _run_init
from tests._private_terms import private_terms_re


def test_init_writes_all_files(tmp_path):
    _run_init(tmp_path, ci=False)
    for rel in (
        "config.yaml",
        "scenarios/example--first-flow.md",
        "credentials.yaml.example",
        "steppy-config.schema.json",
        ".gitignore",
        ".steppy/scaffold.json",
    ):
        assert (tmp_path / rel).is_file(), f"missing {rel}"


def test_init_ci_writes_workflow(tmp_path):
    _run_init(tmp_path, ci=True)
    assert (tmp_path / ".github" / "workflows" / "steppy.yml").is_file()


def test_init_installs_skills_by_default(tmp_path):
    _run_init(tmp_path, ci=False)
    for name in SKILL_NAMES:
        skill = tmp_path / ".claude" / "skills" / name / "SKILL.md"
        assert skill.is_file(), f"missing skill {name}"


def test_init_no_skills_flag_skips_them(tmp_path):
    _run_init(tmp_path, ci=False, no_skills=True)
    assert not (tmp_path / ".claude").exists()


def test_installed_skills_are_recorded_in_manifest(tmp_path):
    _run_init(tmp_path, ci=False)
    manifest = json.loads((tmp_path / ".steppy" / "scaffold.json").read_text("utf-8"))
    for name in SKILL_NAMES:
        rel = f".claude/skills/{name}/SKILL.md"
        assert rel in manifest["files"], f"{rel} not in manifest"


def test_installed_skills_are_neutralized(tmp_path):
    """The shipped skills must carry no private client name or former example domain (FLAWS §4.5)."""
    pattern = private_terms_re()
    _run_init(tmp_path, ci=False)
    for name in SKILL_NAMES:
        text = (tmp_path / ".claude" / "skills" / name / "SKILL.md").read_text("utf-8")
        m = pattern.search(text)
        assert m is None, f"{name} still contains {m.group(0)!r}"


def test_init_no_workflow_without_ci(tmp_path):
    _run_init(tmp_path, ci=False)
    assert not (tmp_path / ".github").exists()


def test_init_does_not_overwrite(tmp_path):
    sentinel = "# my edits\n"
    (tmp_path / "config.yaml").write_text(sentinel, encoding="utf-8")
    _run_init(tmp_path, ci=False)
    # Existing file preserved.
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == sentinel


def test_generated_config_parses(tmp_path):
    _run_init(tmp_path, ci=False)
    cfg = load_config(tmp_path / "config.yaml")
    assert set(cfg.apps) == {"admin", "editor", "viewer"}
    assert cfg.default_locale == "en"


def test_config_has_schema_header(tmp_path):
    _run_init(tmp_path, ci=False)
    first = (tmp_path / "config.yaml").read_text(encoding="utf-8").splitlines()[0]
    assert "$schema=./steppy-config.schema.json" in first


def test_gitignore_append_is_idempotent(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _run_init(tmp_path, ci=False)
    first = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # Re-run must not duplicate lines.
    _run_init(tmp_path, ci=False)
    second = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert first == second
    assert second.count(".auth/") == 1
    assert second.count("node_modules/") == 1
    assert "!.steppy/scaffold.json" in second


def test_manifest_is_well_formed(tmp_path):
    _run_init(tmp_path, ci=False)
    manifest = json.loads((tmp_path / ".steppy" / "scaffold.json").read_text("utf-8"))
    assert "steppy_version" in manifest
    assert "config.yaml" in manifest["files"]
    # Every recorded hash is a 64-char sha256 hex digest.
    for digest in manifest["files"].values():
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_second_run_skips_and_reports_nothing_written(tmp_path):
    _run_init(tmp_path, ci=False)
    # A pure re-run overwrites nothing.
    before = (tmp_path / "config.yaml").read_text("utf-8")
    _run_init(tmp_path, ci=False)
    assert (tmp_path / "config.yaml").read_text("utf-8") == before


def _manifest(root):
    return json.loads((root / ".steppy" / "scaffold.json").read_text("utf-8"))


def test_app_dir_installs_the_app_skills_in_the_app(tmp_path):
    project, app = tmp_path / "project", tmp_path / "app"
    app.mkdir()
    _run_init(project, ci=False, app_dir=app)
    for name in APP_SKILL_NAMES:
        assert (app / ".claude" / "skills" / name / "SKILL.md").is_file()
        # Recorded relative to the project, so `steppy update` refreshes it too.
        assert f"../app/.claude/skills/{name}/SKILL.md" in _manifest(project)["files"]


def test_app_dir_must_exist(tmp_path):
    with pytest.raises(typer.Exit) as exc:
        _run_init(tmp_path / "project", ci=False, app_dir=tmp_path / "missing")
    assert exc.value.exit_code == 1
    assert not (tmp_path / "project").exists()


def test_app_dir_is_honoured_without_project_skills(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    _run_init(tmp_path / "project", ci=False, no_skills=True, app_dir=app)
    assert not (tmp_path / "project" / ".claude").exists()
    assert (app / ".claude" / "skills" / APP_SKILL_NAMES[0] / "SKILL.md").is_file()


def test_rerun_merges_the_manifest(tmp_path):
    _run_init(tmp_path, ci=False)
    first = _manifest(tmp_path)
    m = dict(first, steppy_version="0.1.0")
    (tmp_path / ".steppy" / "scaffold.json").write_text(json.dumps(m), encoding="utf-8")

    _run_init(tmp_path, ci=True)

    second = _manifest(tmp_path)
    # Every earlier entry survives, the new file joins, and the recorded version
    # stays the one the earlier baselines date from.
    assert first["files"].items() <= second["files"].items()
    assert ".github/workflows/steppy.yml" in second["files"]
    assert second["steppy_version"] == "0.1.0"


def test_existing_scenarios_leave_the_starter_out(tmp_path, capsys):
    (tmp_path / "scenarios" / "billing").mkdir(parents=True)
    (tmp_path / "scenarios" / "billing" / "create-invoice.md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
    _run_init(tmp_path, ci=False)
    assert not (tmp_path / "scenarios" / "example--first-flow.md").exists()
    assert "scenarios/ already has scenarios" in capsys.readouterr().out


def test_legacy_skill_folders_are_reported_and_kept(tmp_path, capsys):
    for old in LEGACY_SKILLS:
        (tmp_path / ".claude" / "skills" / old).mkdir(parents=True)
        (tmp_path / ".claude" / "skills" / old / "SKILL.md").write_text("old\n", encoding="utf-8")
    _run_init(tmp_path, ci=False)
    out = capsys.readouterr().out
    for old, new in LEGACY_SKILLS.items():
        assert (tmp_path / ".claude" / "skills" / old / "SKILL.md").read_text("utf-8") == "old\n"
        assert f".claude/skills/{old}/" in out and new in out
