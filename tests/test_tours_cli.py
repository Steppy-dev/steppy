"""``steppy tours`` / ``generate`` / ``validate`` with in-app tours enabled, and the emit rules."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steppy.cli import app

runner = CliRunner()

SCENARIO = """---
title: Create an invoice
tour:
  id: create-invoice
---

### 1. Open the list
- role: editor
- action: navigate
- target: /invoices

### 2. New invoice
- role: editor
- action: click
- selector: '[data-testid="new-invoice-btn"]'
- guide-text:
    en: "Click **New invoice**."

### 3. Name it
- role: editor
- action: fill
- selector: '[data-testid="invoice-title"]'
- value: "Maintenance"
"""

OTHER = """---
title: Not a tour
---

### 1. Open
- role: editor
- action: navigate
- target: /
"""


def _project(tmp_path: Path, targets="[{type: app-assets, path: ./app/assets/tours}, {type: guide}]", extra=""):
    (tmp_path / "scenarios").mkdir(parents=True)
    (tmp_path / "app" / "assets").mkdir(parents=True)
    (tmp_path / "scenarios" / "editor--create-invoice.md").write_text(SCENARIO, encoding="utf-8")
    (tmp_path / "scenarios" / "editor--other.md").write_text(OTHER, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "apps:\n  editor: {name: E, url: 'http://localhost:3000', auth: {mode: bypass}}\n"
        "locales: [en]\ndefault_locale: en\n"
        f"tours:\n  enabled: true\n  build_id: b-1\n  targets: {targets}\n{extra}",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return _project(tmp_path)


def _invoke(*args):
    return runner.invoke(app, list(args))


def _flat(result) -> str:
    """The output with Rich's line wrapping undone."""
    return " ".join(result.output.split())


def test_tours_writes_the_canonical_tree_and_the_app_assets_copy(project):
    result = _invoke("tours")
    assert result.exit_code == 0, result.output
    canonical = project / "output" / "tours" / "b-1"
    flat = project / "app" / "assets" / "tours"
    names = {"tours.manifest.json", "create-invoice.en.json"}
    assert {p.name for p in canonical.iterdir()} == names == {p.name for p in flat.iterdir()}
    for name in names:
        data = (canonical / name).read_bytes()
        assert data == (flat / name).read_bytes() and b"\r" not in data
    manifest = json.loads((flat / "tours.manifest.json").read_text(encoding="utf-8"))
    assert manifest["buildId"] == "b-1" and [t["id"] for t in manifest["tours"]] == ["create-invoice"]
    assert not (project / "tests").exists()  # `tours` never touches the tests


def test_a_second_run_is_unchanged_and_check_passes(project):
    assert _invoke("tours").exit_code == 0
    again = _invoke("tours")
    assert "UNCHANGED" in again.output and "GENERATED" not in again.output
    assert _invoke("tours", "--check").exit_code == 0


def test_check_and_dry_run_write_nothing(project):
    missing = _invoke("tours", "--check")
    assert missing.exit_code == 1 and "MISSING" in missing.output
    assert _invoke("tours", "--dry-run").exit_code == 0
    assert not (project / "output").exists() and not (project / "app" / "assets" / "tours").exists()


# ── --check: the committed app-assets copies, compared for their own build id ──


def test_check_passes_on_a_fresh_checkout_without_the_canonical_tree(project):
    # CI checks out the app's committed copy; `output/` is gitignored and absent.
    assert _invoke("tours").exit_code == 0
    shutil.rmtree(project / "output")
    result = _invoke("tours", "--check")
    assert result.exit_code == 0, result.output
    assert not (project / "output").exists()


def test_check_fails_when_scenario_text_changed_without_regenerating(project):
    assert _invoke("tours").exit_code == 0
    path = project / "scenarios" / "editor--create-invoice.md"
    path.write_text(SCENARIO.replace("Click **New invoice**.", "Click the button."), encoding="utf-8")
    result = _invoke("tours", "--check")
    assert result.exit_code == 1 and "UPDATED" in result.output and "stale" in _flat(result)


def test_check_names_a_line_ending_conversion(project):
    # A Windows checkout with core.autocrlf rewrites the committed copies:
    # regenerating cannot help, so --check says what can.
    assert _invoke("tours").exit_code == 0
    path = project / "app" / "assets" / "tours" / "create-invoice.en.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    result = _invoke("tours", "--check")
    assert result.exit_code == 1
    assert "differ only in line endings" in _flat(result) and "-text" in _flat(result)
    path.write_bytes(path.read_bytes().replace(b'"en"', b'"fr"'))
    assert "line endings" not in _flat(_invoke("tours", "--check"))


def test_check_compares_content_for_the_recorded_build_id(project):
    # A git-sha or CI-stamped id differs from the one the committed copy records:
    # a note, never a failure — the replay's STEPPY_TOUR_EXPECT_BUILD_ID gates ids.
    assert _invoke("tours").exit_code == 0
    result = _invoke("tours", "--check", "--build-id", "b-2")
    assert result.exit_code == 0, result.output
    assert "NOTE" in result.output and "'b-1'" in _flat(result) and "'b-2'" in _flat(result)


def test_check_with_an_unresolvable_build_id_still_compares(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("b-1", "${UNSET_TOUR_BUILD_ID}"), encoding="utf-8")
    monkeypatch.delenv("UNSET_TOUR_BUILD_ID", raising=False)
    assert _invoke("tours", "--build-id", "b-1").exit_code == 0
    result = _invoke("tours", "--check")
    assert result.exit_code == 0, result.output
    assert "cannot be resolved" in _flat(result)


def test_check_without_app_assets_targets_has_nothing_to_compare(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, targets="[{type: guide}]")
    result = _invoke("tours", "--check")
    assert result.exit_code == 0, result.output
    assert "no app-assets target" in _flat(result)


def test_check_rejects_a_corrupted_recorded_build_id(project):
    assert _invoke("tours").exit_code == 0
    manifest = project / "app" / "assets" / "tours" / "tours.manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["buildId"] = "../x"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    result = _invoke("tours", "--check")
    assert result.exit_code == 1 and "build-id" in _flat(result)


# ── An app-assets folder is someone else's: only tour files are replaced or deleted ──


def test_a_foreign_file_with_a_tour_file_name_is_never_overwritten(project):
    flat = project / "app" / "assets" / "tours"
    flat.mkdir()
    (flat / "create-invoice.en.json").write_text('{"mine": true}', encoding="utf-8")
    result = _invoke("tours")
    assert result.exit_code == 1 and "target-path" in _flat(result) and "not a Steppy tour file" in _flat(result)
    assert (flat / "create-invoice.en.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert not (flat / "tours.manifest.json").exists() and not (project / "output").exists()


def test_a_listed_file_replaced_by_foreign_content_is_never_deleted(project):
    assert _invoke("tours").exit_code == 0
    flat = project / "app" / "assets" / "tours"
    (flat / "create-invoice.en.json").write_text('{"mine": true}', encoding="utf-8")
    (project / "scenarios" / "editor--create-invoice.md").write_text(
        SCENARIO.replace("tour:\n  id: create-invoice\n", ""), encoding="utf-8"
    )
    result = _invoke("tours")
    assert result.exit_code == 1 and "refusing to delete" in _flat(result)
    assert (flat / "create-invoice.en.json").read_text(encoding="utf-8") == '{"mine": true}'


def test_build_id_override(project):
    assert _invoke("tours", "--build-id", "rel-2").exit_code == 0
    assert (project / "output" / "tours" / "rel-2" / "tours.manifest.json").is_file()
    assert _invoke("tours", "--build-id", "../x").exit_code == 1


def test_a_new_build_id_prunes_the_old_canonical_tree(project):
    assert _invoke("tours").exit_code == 0
    assert _invoke("tours", "--build-id", "b-2").exit_code == 0
    assert not (project / "output" / "tours" / "b-1").exists()


def _case_insensitive(directory: Path) -> bool:
    probe = directory / "Case-Probe"
    probe.write_text("x", encoding="utf-8")
    try:
        return (directory / "case-probe").exists()
    finally:
        probe.unlink()


def test_a_case_only_rename_of_a_tour_id_keeps_the_new_file(project):
    # On Windows/macOS the old name and the new one are the same file: pruning
    # the old name must not delete what was just written.
    assert _invoke("tours").exit_code == 0
    path = project / "scenarios" / "editor--create-invoice.md"
    path.write_text(SCENARIO.replace("id: create-invoice", "id: Create-Invoice"), encoding="utf-8")
    result = _invoke("tours")
    assert result.exit_code == 0, result.output
    flat = project / "app" / "assets" / "tours"
    assert json.loads((flat / "Create-Invoice.en.json").read_text(encoding="utf-8"))["id"] == "Create-Invoice"
    if not _case_insensitive(flat):
        assert not (flat / "create-invoice.en.json").exists()
    assert _invoke("tours", "--check").exit_code == 0


def test_a_case_only_build_id_change_keeps_the_new_tree(project):
    assert _invoke("tours").exit_code == 0
    assert _invoke("tours", "--build-id", "B-1").exit_code == 0
    root = project / "output" / "tours"
    manifest = json.loads((root / "B-1" / "tours.manifest.json").read_text(encoding="utf-8"))
    assert manifest["buildId"] == "B-1"
    if not _case_insensitive(root):
        assert not (root / "b-1").exists()


def test_a_dropped_tour_is_pruned_but_foreign_files_survive(project):
    assert _invoke("tours").exit_code == 0
    flat = project / "app" / "assets" / "tours"
    (flat / "keep-me.json").write_text("{}", encoding="utf-8")
    (project / "scenarios" / "editor--create-invoice.md").write_text(
        SCENARIO.replace("tour:\n  id: create-invoice\n", ""), encoding="utf-8"
    )
    result = _invoke("tours")
    assert result.exit_code == 0, result.output
    assert "REMOVED" in result.output
    assert not (flat / "create-invoice.en.json").exists() and (flat / "keep-me.json").exists()


def test_a_foreign_manifest_is_never_touched(project):
    flat = project / "app" / "assets" / "tours"
    flat.mkdir()
    (flat / "tours.manifest.json").write_text('{"kind": "someone-else"}', encoding="utf-8")
    result = _invoke("tours")
    assert result.exit_code == 1 and "not a Steppy tours manifest" in _flat(result)
    assert (flat / "tours.manifest.json").read_text(encoding="utf-8") == '{"kind": "someone-else"}'


@pytest.mark.parametrize("path", [".", "./scenarios", "./tests/e2e/tours", "./output/screenshots/x"])
def test_unsafe_app_assets_paths_are_refused(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, targets=f"[{{type: app-assets, path: '{path}'}}]")
    result = _invoke("tours")
    assert result.exit_code == 1 and "target-path" in _flat(result)


def test_a_missing_app_checkout_is_an_error_for_tours_and_a_skip_for_generate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, targets="[{type: app-assets, path: ../missing-app/public/tours}, {type: guide}]")
    assert _invoke("tours").exit_code == 1
    result = _invoke("generate", "-o", "tests/e2e")
    assert result.exit_code == 0, result.output
    assert "app-assets-skipped" in _flat(result)
    assert (tmp_path / "output" / "tours" / "b-1" / "tours.manifest.json").is_file()


def test_tours_disabled_is_an_explicit_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("enabled: true", "enabled: false"), encoding="utf-8")
    result = _invoke("tours")
    assert result.exit_code == 1 and "disabled" in _flat(result)


def test_generate_writes_tests_replays_helper_and_tours(project):
    result = _invoke("generate", "-o", "tests/e2e")
    assert result.exit_code == 0, result.output
    e2e = project / "tests" / "e2e"
    test = (e2e / "test_editor--create-invoice.py").read_text(encoding="utf-8")
    assert "def test_editor__create_invoice__tour(" in test
    assert "_tour_start(_tour" in test
    assert "__tour" not in (e2e / "test_editor--other.py").read_text(encoding="utf-8")
    assert (e2e / "_steppy_tours.py").is_file()
    assert "_tours_with_kill_switch" in (e2e / "conftest.py").read_text(encoding="utf-8")
    assert (project / "app" / "assets" / "tours" / "create-invoice.en.json").is_file()


def test_generate_one_file_still_writes_the_full_manifest(project):
    result = _invoke("generate", "scenarios/editor--other.md", "-o", "tests/e2e")
    assert result.exit_code == 0, result.output
    manifest = json.loads((project / "output" / "tours" / "b-1" / "tours.manifest.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in manifest["tours"]] == ["create-invoice"]


def test_replay_off_generates_no_replay_function(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, extra="  replay: false\n")
    assert _invoke("generate", "-o", "tests/e2e").exit_code == 0
    test = (tmp_path / "tests" / "e2e" / "test_editor--create-invoice.py").read_text(encoding="utf-8")
    assert "__tour" not in test


def test_a_compile_error_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    path = tmp_path / "scenarios" / "editor--create-invoice.md"
    path.write_text(SCENARIO.replace("'[data-testid=\"new-invoice-btn\"]'", "button.new"), encoding="utf-8")
    result = _invoke("generate", "-o", "tests/e2e")
    assert result.exit_code == 1
    assert "target-not-test-id" in _flat(result) and "button.new" in _flat(result)
    assert not (tmp_path / "tests").exists() and not (tmp_path / "output").exists()


def test_validate_reports_tour_errors_and_successes(project):
    ok = _invoke("validate")
    assert ok.exit_code == 0 and "1 in-app tour(s) compile" in _flat(ok)
    path = project / "scenarios" / "editor--create-invoice.md"
    path.write_text(
        SCENARIO.replace("'[data-testid=\"new-invoice-btn\"]'", "'[data-testid=\"a\"] a[href]'"), encoding="utf-8"
    )
    bad = _invoke("validate")
    assert bad.exit_code == 1
    # Rich must not swallow the bracketed selector in the message.
    assert '[data-testid="a"] a[href]' in _flat(bad)
