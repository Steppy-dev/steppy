"""``config.output.*`` is a live knob, not decoration (FLAWS §4.6).

The flaw these pin was quiet and total: ``screenshots_dir`` / ``guides_dir`` /
``reports_dir`` were validated, schema'd and scaffolded, and read by nothing —
every consumer resolved its own hardcoded copy of the same literal, so setting
one changed exactly nothing. What matters now is that the **writer and every
reader resolve the same value**, since a knob honoured by half the pipeline is
worse than one honoured by none.

``reports_dir`` went the other way: it is deprecated and inert, because nothing
has ever written ``output/reports``.
"""

import json
import struct
import zlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steppy.captures import DEFAULT_GUIDES_DIR, DEFAULT_SCREENSHOTS_DIR, resolve_output_dir
from steppy.cli import app
from steppy.config import load_config


runner = CliRunner()

CONFIG_YAML = """\
apps:
  editor:
    name: Editor Portal
    url: https://app.example.com
    auth:
      mode: storage-state
      storage_state_path: ./.auth/editor.json
locales:
  - fr
default_locale: fr
"""

SCENARIO = """\
---
title: Alpha flow
tags:
  - user-guide-editor
guide:
  enabled: true
---

### 1. Open alpha
- role: editor
- action: navigate
- target: /alpha
- screenshot: editor
- guide-text:
    fr: "Ouvrez alpha"
"""


def _png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    return sig + ihdr + idat + chunk(b"IEND", b"")


def _write_captures(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "step-01.png").write_bytes(_png())
    (directory / "step-01.json").write_text(
        json.dumps(
            {
                "viewport": {"width": 1280, "height": 720},
                "target": {"kind": "single", "bbox": {"x": 10, "y": 20, "width": 30, "height": 40}},
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal guide project. Captures are written per-test, since where they
    live is the thing under test."""
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "alpha.md").write_text(SCENARIO, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── the shared resolver ─────────────────────────────────────────────────────


def test_resolve_output_dir_handles_relative_absent_and_absolute(tmp_path):
    root = tmp_path / "proj"
    assert resolve_output_dir(root, None, DEFAULT_SCREENSHOTS_DIR) == (root / "output" / "screenshots").resolve()
    # The shipped default carries a "./" prefix; pathlib normalises it away, so
    # the wired value is byte-for-byte the historical hardcoded path.
    assert (
        resolve_output_dir(root, "./output/screenshots", DEFAULT_SCREENSHOTS_DIR)
        == (root / "output" / "screenshots").resolve()
    )
    assert (
        resolve_output_dir(root, "./artifacts/shots", DEFAULT_SCREENSHOTS_DIR)
        == (root / "artifacts" / "shots").resolve()
    )
    assert (
        resolve_output_dir(root, str(tmp_path / "elsewhere"), DEFAULT_SCREENSHOTS_DIR)
        == (tmp_path / "elsewhere").resolve()
    )


# ── the writer: the generated conftest ──────────────────────────────────────

_CONFTEST = Path(__file__).parent.parent / "templates" / "conftest.py.j2"


def _conftest_screenshots_dir(config: dict, project_root: Path) -> Path:
    """Execute the template's SCREENSHOTS_DIR assignment against a fake CONFIG.

    Extracted from the canonical template rather than reimplemented — the point
    of the test is that *that source line* resolves config, and a paraphrase
    would keep passing after someone reverted it to a literal. Same idiom as
    tests/test_totp.py and tests/test_coverage_record_template.py.
    """
    source = _CONFTEST.read_text(encoding="utf-8")
    start = source.index("SCREENSHOTS_DIR = Path(")
    end = source.index("\n\n", start)
    snippet = source[start:end]
    assert "CONFIG" in snippet, "SCREENSHOTS_DIR no longer reads CONFIG — the §4.6 wiring was reverted"

    # The template resolves the project root as <conftest>/../../.., i.e.
    # tests/e2e/conftest.py -> project root.
    namespace = {"Path": Path, "CONFIG": config, "__file__": str(project_root / "tests" / "e2e" / "conftest.py")}
    exec(snippet, namespace)  # noqa: S102 — executing our own template, by design
    return namespace["SCREENSHOTS_DIR"]


def test_conftest_capture_root_defaults_to_the_conventional_path(tmp_path):
    """No `output:` block at all. CONFIG is raw YAML, so pydantic's
    default_factory never runs here — the fallback has to be in the template."""
    assert _conftest_screenshots_dir({}, tmp_path) == tmp_path / "output" / "screenshots"
    assert _conftest_screenshots_dir({"output": None}, tmp_path) == tmp_path / "output" / "screenshots"


def test_conftest_capture_root_follows_config(tmp_path):
    config = {"output": {"screenshots_dir": "./artifacts/shots"}}
    assert _conftest_screenshots_dir(config, tmp_path) == tmp_path / "artifacts" / "shots"


def test_conftest_capture_root_is_read_at_runtime_not_baked(tmp_path):
    """The value must not be Jinja-interpolated: a baked path would silently
    keep writing to the old directory until the user re-ran `steppy generate`,
    and would make the generated file differ per project."""
    source = _CONFTEST.read_text(encoding="utf-8")
    start = source.index("SCREENSHOTS_DIR = Path(")
    snippet = source[start : source.index("\n\n", start)]
    assert "{{" not in snippet and "{%" not in snippet


# ── readers: steppy guide ───────────────────────────────────────────────────


def test_guide_defaults_both_paths_from_config(project):
    _write_captures(project / "artifacts" / "shots" / "alpha")
    (project / "config.yaml").write_text(
        CONFIG_YAML + "output:\n  screenshots_dir: ./artifacts/shots\n  guides_dir: ./artifacts/books\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr"])
    assert result.exit_code == 0, result.output
    assert (project / "artifacts" / "books" / "editor" / "fr" / "alpha.html").is_file()
    # And nothing was written to the conventional location.
    assert not (project / "output" / "guides").exists()


def test_guide_flags_still_beat_config(project):
    _write_captures(project / "elsewhere" / "alpha")
    (project / "config.yaml").write_text(
        CONFIG_YAML + "output:\n  screenshots_dir: ./artifacts/shots\n  guides_dir: ./artifacts/books\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "guide",
            "--guide",
            "--locale",
            "fr",
            "--screenshots-dir",
            "elsewhere",
            "--output-dir",
            "flag-guides",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (project / "flag-guides" / "editor" / "fr" / "alpha.html").is_file()
    assert not (project / "artifacts" / "books").exists()


def test_guide_with_no_output_block_is_unchanged(project):
    """The whole point of the defaults: a project that sets nothing keeps the
    exact paths it had before §4.6 was wired up."""
    _write_captures(project / "output" / "screenshots" / "alpha")

    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr"])
    assert result.exit_code == 0, result.output
    assert (project / "output" / "guides" / "editor" / "fr" / "alpha.html").is_file()


# ── readers: steppy build ───────────────────────────────────────────────────


def _assembler(project_root: Path, *, ts_screenshots_dir=None, steppy_config=None):
    from steppy.build import BuildAssembler
    from steppy.build_models import BuildConfig

    raw = {
        "scenariosRoot": "./scenarios",
        "outputDir": "./site",
        "assetsDir": "./site/assets",
        "role": "editor",
        "site": {"title": "Docs"},
        "guides": [{"slug": "alpha", "title": "Alpha flow", "scenario": "alpha.md"}],
        "navbar": {"order": ["alpha"]},
    }
    if ts_screenshots_dir is not None:
        raw["screenshotsDir"] = ts_screenshots_dir
    return BuildAssembler(
        BuildConfig(**raw),
        project_root=project_root,
        steppy_config=steppy_config,
    )


def test_build_prefers_an_explicit_typescript_screenshots_dir(project):
    config = load_config(project / "config.yaml")
    config.output.screenshots_dir = "./artifacts/shots"
    asm = _assembler(project, ts_screenshots_dir="./ts-shots", steppy_config=config)
    assert asm._screenshots_root() == (project / "ts-shots").resolve()


def test_build_falls_back_to_config_output_when_typescript_omits_it(project):
    """`screenshotsDir` is optional in the TS config and carries a default of
    its own, so `model_fields_set` is the only thing that can tell "the author
    chose this" from "pydantic filled it in"."""
    config = load_config(project / "config.yaml")
    config.output.screenshots_dir = "./artifacts/shots"
    asm = _assembler(project, steppy_config=config)
    assert asm._screenshots_root() == (project / "artifacts" / "shots").resolve()


def test_build_falls_back_to_the_default_without_a_steppy_config(project):
    """`cli.build` tolerates an unloadable config.yaml, so this rung has to
    exist — and it must be the conventional path."""
    asm = _assembler(project, steppy_config=None)
    assert asm._screenshots_root() == (project / "output" / "screenshots").resolve()


# ── reader: the parallel failure harvest ────────────────────────────────────


def test_failure_harvest_reads_the_configured_capture_root(tmp_path):
    """A *reader* of what the conftest wrote — it has to follow the same knob
    or it silently harvests nothing."""
    from steppy._parallel.event_bus import EventBus
    from steppy._parallel.failures import persist_failure_artifacts
    from steppy._parallel.queue import ScenarioResult

    shots = tmp_path / "artifacts" / "shots" / "_failures"
    shots.mkdir(parents=True)
    (shots / "test_alpha__editor_page.png").write_bytes(_png())

    def _harvest(screenshots_dir):
        return persist_failure_artifacts(
            project_root=tmp_path,
            tmp_dir=tmp_path / "coord",
            bus=EventBus(),
            scenario_results=[
                ScenarioResult(
                    slug="alpha", worker_id="w1", exit_code=1, duration_s=1.0, started_at=0.0, finished_at=1.0
                )
            ],
            orphans={},
            screenshots_dir=screenshots_dir,
        )

    written = _harvest(tmp_path / "artifacts" / "shots")
    assert written is not None
    summary = (written.scenario_dirs["alpha"] / "summary.txt").read_text(encoding="utf-8")
    assert "test_alpha__editor_page.png" in summary

    # Omitted ⇒ the conventional path, which is empty here. This is the failure
    # the wiring prevents: a relocated capture root and a harvest that finds
    # nothing, with no indication the two disagreed.
    written = _harvest(None)
    assert written is not None
    assert "test_alpha__editor_page.png" not in (written.scenario_dirs["alpha"] / "summary.txt").read_text(
        encoding="utf-8"
    )


def _parallel_project(tmp_path: Path, screenshots_dir: str) -> Path:
    """A project with a `parallel:` block and a deliberately-missing compose
    file, so `run_parallel` reaches the capture-root check and then bails
    before it touches Docker."""
    (tmp_path / "config.yaml").write_text(
        CONFIG_YAML.replace(
            "url: https://app.example.com", "url: http://localhost:4200\n    internal_url: http://a:3000"
        )
        + f"output:\n  screenshots_dir: {screenshots_dir}\n"
        + "parallel:\n  workers: 2\n  compose_file: docker-compose.yml\n",
        encoding="utf-8",
    )
    (tmp_path / "scenarios").mkdir(exist_ok=True)
    return tmp_path


def test_workers_warn_when_the_capture_root_escapes_the_bind_mount(tmp_path, monkeypatch, capsys):
    """`--workers N` mounts only `output/` back from the container and mounts
    the project read-only, so a capture root outside it cannot produce
    host-visible screenshots. Warn instead of finishing with nothing."""
    from steppy.coordinator import run_parallel

    monkeypatch.chdir(_parallel_project(tmp_path, "../artifacts/shots"))
    # Missing docker-compose.yml ⇒ returns 1 right after the check under test.
    assert run_parallel(**_RUN_PARALLEL_ARGS) == 1
    out = capsys.readouterr().out
    assert "screenshots_dir" in out and "only mounts output/" in out


def test_workers_do_not_warn_for_a_root_inside_output(tmp_path, monkeypatch, capsys):
    from steppy.coordinator import run_parallel

    monkeypatch.chdir(_parallel_project(tmp_path, "./output/shots"))
    assert run_parallel(**_RUN_PARALLEL_ARGS) == 1
    assert "screenshots_dir" not in capsys.readouterr().out


_RUN_PARALLEL_ARGS = dict(
    workers=2,
    scenario=None,
    tag=None,
    headed=False,
    screenshots=False,
    guide=False,
    no_asserts=False,
    no_guide=False,
    locales=None,
    layouts=None,
    tests_dir=Path("tests/e2e"),
    scenarios_dir=Path("scenarios"),
    pytest_passthrough=[],
)


# ── the retired key ─────────────────────────────────────────────────────────


def test_reports_dir_still_parses_but_warns(project, capsys):
    """Kept parseable on purpose: SteppyConfig forbids extras, so removing the
    field outright would stop an existing config.yaml from loading."""
    (project / "config.yaml").write_text(
        CONFIG_YAML + "output:\n  reports_dir: ./output/reports\n",
        encoding="utf-8",
    )
    config = load_config(project / "config.yaml")
    assert config.output.reports_dir == "./output/reports"
    assert "reports_dir is deprecated" in capsys.readouterr().err


def test_no_reports_dir_no_warning(project, capsys):
    load_config(project / "config.yaml")
    assert "reports_dir" not in capsys.readouterr().err


def test_output_block_rejects_typos(project):
    """`extra = "forbid"` matters more now that the block does something — a
    silently-swallowed `screenshot_dir:` would recreate the original flaw."""
    from steppy.config import ConfigError

    (project / "config.yaml").write_text(
        CONFIG_YAML + "output:\n  screenshot_dir: ./artifacts/shots\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(project / "config.yaml")


def test_update_check_reports_reports_dir(project):
    from steppy.update_cli import _scan_deprecations

    (project / "config.yaml").write_text(
        CONFIG_YAML + "output:\n  reports_dir: ./output/reports\n",
        encoding="utf-8",
    )
    ids = {hit["deprecation"].id for hit in _scan_deprecations(project)}
    assert "output-reports-dir-inert" in ids


def test_update_check_does_not_flag_a_reports_dir_under_another_block(project):
    """The scan is indent-aware: only ``reports_dir`` directly under a
    top-level ``output:`` counts."""
    from steppy.update_cli import _scan_deprecations

    (project / "config.yaml").write_text(
        CONFIG_YAML + "output:\n  guides_dir: ./output/guides\nreport:\n  junit_xml: ./output/reports/junit.xml\n",
        encoding="utf-8",
    )
    ids = {hit["deprecation"].id for hit in _scan_deprecations(project)}
    assert "output-reports-dir-inert" not in ids


# ── the shipped scaffold ────────────────────────────────────────────────────


def test_scaffolded_config_does_not_ship_the_dead_key():
    scaffold = (Path(__file__).parent.parent / "templates" / "init" / "config.yaml").read_text(encoding="utf-8")
    assert "reports_dir" not in scaffold
    assert "screenshots_dir" in scaffold and "guides_dir" in scaffold
    assert DEFAULT_GUIDES_DIR in scaffold
