"""Tests for `steppy update` — asset refresh + changelog highlights (FLAWS §7.7)."""

import hashlib
import json

import pytest
import typer

from steppy import __version__
from steppy.init_cli import APP_SKILL_NAMES, SKILL_NAMES, _run_init
from steppy.update_cli import (
    _changelog_highlights,
    _refreshable_content,
    _run_update,
    _skill_path,
)

_SCHEMA = "steppy-config.schema.json"
_SKILL_REL = f".claude/skills/{SKILL_NAMES[0]}/SKILL.md"
_CI_REL = ".github/workflows/steppy.yml"


def _manifest(root):
    return json.loads((root / ".steppy" / "scaffold.json").read_text("utf-8"))


def test_pristine_project_reports_up_to_date(tmp_path):
    _run_init(tmp_path, ci=False)
    # A freshly-scaffolded project matches the installed bundle exactly.
    _run_update(tmp_path)  # must not raise
    # Manifest version reflects the reconcile.
    assert _manifest(tmp_path)["steppy_version"] == __version__


def test_modified_asset_gets_new_file_and_original_preserved(tmp_path):
    import hashlib

    _run_init(tmp_path, ci=False)
    skill = tmp_path / _SKILL_REL
    # User-edited on disk, AND an *older* recorded baseline — so the installed
    # bundle differs from both (a real "new version shipped over your edits").
    sentinel = "# my local edit\n"
    skill.write_text(sentinel, encoding="utf-8")
    old_hash = hashlib.sha256(b"# some older bundled skill\n").hexdigest()
    m = _manifest(tmp_path)
    m["files"][_SKILL_REL] = old_hash
    (tmp_path / ".steppy" / "scaffold.json").write_text(
        json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    _run_update(tmp_path)

    # Original untouched; new version dropped alongside.
    assert skill.read_text("utf-8") == sentinel
    new = skill.with_name("SKILL.md.new")
    assert new.is_file()
    assert new.read_text("utf-8") != sentinel
    # Manifest baseline for a diverged file is left unchanged (keeps re-detecting).
    assert _manifest(tmp_path)["files"][_SKILL_REL] == old_hash


def test_pristine_stale_asset_is_replaced(tmp_path):
    _run_init(tmp_path, ci=False)
    skill = tmp_path / _SKILL_REL
    real = skill.read_text("utf-8")
    # Simulate an *older* bundled version on disk, but keep the manifest baseline
    # matching disk (pristine) — then update must overwrite with the real bundle.
    stale = "# stale\n"
    skill.write_text(stale, encoding="utf-8")
    import hashlib

    m = _manifest(tmp_path)
    m["files"][_SKILL_REL] = hashlib.sha256(stale.encode()).hexdigest()
    (tmp_path / ".steppy" / "scaffold.json").write_text(
        json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    _run_update(tmp_path)

    assert skill.read_text("utf-8") == real
    assert not skill.with_name("SKILL.md.new").exists()


def test_deleted_asset_is_not_resurrected(tmp_path):
    _run_init(tmp_path, ci=True)
    ci = tmp_path / _CI_REL
    assert ci.is_file()
    ci.unlink()

    _run_update(tmp_path)

    assert not ci.exists()


def test_starter_files_are_never_touched(tmp_path):
    _run_init(tmp_path, ci=False)
    config = tmp_path / "config.yaml"
    edited = config.read_text("utf-8") + "\n# hand edit\n"
    config.write_text(edited, encoding="utf-8")

    _run_update(tmp_path)

    assert config.read_text("utf-8") == edited
    assert not (tmp_path / "config.yaml.new").exists()


def test_update_without_manifest_exits_one(tmp_path):
    with pytest.raises(typer.Exit) as exc:
        _run_update(tmp_path)
    assert exc.value.exit_code == 1


def test_refreshable_content_classifies_paths():
    assert _refreshable_content(_SCHEMA) is not None
    assert _refreshable_content(_SKILL_REL) is not None
    assert _refreshable_content(_CI_REL) is not None
    # Starter files the user owns → not managed.
    assert _refreshable_content("config.yaml") is None
    assert _refreshable_content("scenarios/example--first-flow.md") is None
    assert _refreshable_content(".gitignore") is None


def test_changelog_highlights_selects_range(capsys):
    # The bundled CHANGELOG has a released section for the current version's line.
    _changelog_highlights("0.0.1")
    out = capsys.readouterr().out
    # Either it prints a section, or (if only [Unreleased] exists) the empty note.
    assert out  # never crashes; always emits something

    # A recorded version >= installed selects nothing in range.
    _changelog_highlights(__version__)
    out2 = capsys.readouterr().out
    assert "no changelog entries" in out2 or out2 == ""


def _save(root, manifest):
    (root / ".steppy" / "scaffold.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _as_if_scaffolded_before(root, name):
    """Make ``root`` look scaffolded by a version that did not ship skill ``name`` yet."""
    (root / ".claude" / "skills" / name / "SKILL.md").unlink()
    m = _manifest(root)
    del m["files"][f".claude/skills/{name}/SKILL.md"]
    _save(root, m)


def test_a_skill_shipped_since_the_scaffold_is_installed(tmp_path):
    _run_init(tmp_path, ci=False)
    new = SKILL_NAMES[-1]
    _as_if_scaffolded_before(tmp_path, new)

    _run_update(tmp_path)

    assert (tmp_path / ".claude" / "skills" / new / "SKILL.md").is_file()
    assert f".claude/skills/{new}/SKILL.md" in _manifest(tmp_path)["files"]


def test_no_skills_project_gets_no_new_skill(tmp_path):
    _run_init(tmp_path, ci=False, no_skills=True)
    _run_update(tmp_path)
    assert not (tmp_path / ".claude").exists()


def test_project_whose_skills_were_all_deleted_gets_no_new_skill(tmp_path):
    _run_init(tmp_path, ci=False)
    new = SKILL_NAMES[-1]
    _as_if_scaffolded_before(tmp_path, new)
    for name in SKILL_NAMES[:-1]:
        (tmp_path / ".claude" / "skills" / name / "SKILL.md").unlink()

    _run_update(tmp_path)

    assert not (tmp_path / ".claude" / "skills" / new / "SKILL.md").exists()


def test_an_unrecorded_skill_of_the_same_name_is_kept(tmp_path):
    _run_init(tmp_path, ci=False)
    name = SKILL_NAMES[0]
    skill = tmp_path / ".claude" / "skills" / name / "SKILL.md"
    m = _manifest(tmp_path)
    del m["files"][f".claude/skills/{name}/SKILL.md"]
    _save(tmp_path, m)
    skill.write_text("# copied in by hand, long ago\n", encoding="utf-8")

    _run_update(tmp_path)

    assert skill.read_text("utf-8") == "# copied in by hand, long ago\n"
    assert skill.with_name("SKILL.md.new").is_file()
    assert f".claude/skills/{name}/SKILL.md" not in _manifest(tmp_path)["files"]


def test_an_unrecorded_identical_skill_is_adopted(tmp_path):
    _run_init(tmp_path, ci=False)
    name = SKILL_NAMES[0]
    m = _manifest(tmp_path)
    digest = m["files"].pop(f".claude/skills/{name}/SKILL.md")
    _save(tmp_path, m)

    _run_update(tmp_path)

    assert _manifest(tmp_path)["files"][f".claude/skills/{name}/SKILL.md"] == digest
    assert not (tmp_path / ".claude" / "skills" / name / "SKILL.md.new").exists()


def test_the_app_skill_is_refreshed_in_the_app(tmp_path):
    project, app = tmp_path / "project", tmp_path / "app"
    app.mkdir()
    _run_init(project, ci=False, app_dir=app)
    rel = f"../app/.claude/skills/{APP_SKILL_NAMES[0]}/SKILL.md"
    skill = project / rel
    real = skill.read_text("utf-8")
    skill.write_text("# stale\n", encoding="utf-8")
    m = _manifest(project)
    m["files"][rel] = hashlib.sha256(b"# stale\n").hexdigest()
    _save(project, m)

    _run_update(project)

    assert skill.read_text("utf-8") == real


def test_legacy_skill_folders_are_reported_not_removed(tmp_path, capsys):
    _run_init(tmp_path, ci=False)
    legacy = tmp_path / ".claude" / "skills" / "steppy" / "SKILL.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old\n", encoding="utf-8")
    capsys.readouterr()

    _run_update(tmp_path)

    assert legacy.read_text("utf-8") == "old\n"
    assert ".claude/skills/steppy/" in capsys.readouterr().out


def test_skill_paths_are_classified_by_their_last_four_parts():
    assert _skill_path(".claude/skills/steppy-template/SKILL.md") == ("", "steppy-template")
    assert _skill_path("../app/.claude/skills/steppy-app-tours/SKILL.md") == ("../app", "steppy-app-tours")
    # A skill of the user's own, or an old hand-copied one, is never managed.
    assert _skill_path(".claude/skills/my-own/SKILL.md") is None
    assert _refreshable_content(".claude/skills/steppy/SKILL.md") is None
    assert _refreshable_content("../app/.claude/skills/steppy-app-tours/SKILL.md") is not None
