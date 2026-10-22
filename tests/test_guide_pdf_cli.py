"""CLI plumbing for `steppy guide --pdf` / `--handbook`.

The renderer itself is covered by tests/test_pdf.py; here it is patched out so
these assert what the command *asks* to be printed — which pages, in which
directories, and that nothing is rendered when neither flag is passed.
"""

import json
import struct
import zlib

import pytest
from typer.testing import CliRunner

from steppy.cli import app


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


def _scenario(title: str, slug_note: str) -> str:
    return f"""---
title: {title}
tags:
  - user-guide-editor
guide:
  enabled: true
---

### 1. Open {slug_note}
- role: editor
- action: navigate
- target: /{slug_note}
- screenshot: editor
- guide-text:
    fr: "Ouvrez {slug_note}"
"""


def _png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    return sig + ihdr + idat + chunk(b"IEND", b"")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal guide project: two guide-enabled scenarios with captures."""
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "alpha.md").write_text(_scenario("Alpha flow", "alpha"), encoding="utf-8")
    (scenarios / "beta.md").write_text(_scenario("Beta flow", "beta"), encoding="utf-8")

    sidecar = {
        "viewport": {"width": 1280, "height": 720},
        "target": {"kind": "single", "bbox": {"x": 10, "y": 20, "width": 30, "height": 40}},
    }
    for slug in ("alpha", "beta"):
        shots = tmp_path / "output" / "screenshots" / slug
        shots.mkdir(parents=True)
        (shots / "step-01.png").write_bytes(_png())
        (shots / "step-01.json").write_text(json.dumps(sidecar), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def rendered(monkeypatch):
    """Patch the renderer; return the list it records jobs into."""
    import steppy.pdf as pdf_module

    jobs: list = []

    def _fake(batch, options=None, on_done=None, **kwargs):
        jobs.extend(batch)
        results = [pdf_module.PdfResult(job=j, ok=True) for j in batch]
        for r in results:
            if on_done:
                on_done(r)
            r.job.pdf.write_bytes(b"%PDF-fake")
        return results

    monkeypatch.setattr(pdf_module, "render_pdfs", _fake)
    return jobs


def test_no_flags_renders_nothing(project, rendered):
    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr"])
    assert result.exit_code == 0, result.output
    assert rendered == []


def test_pdf_flag_prints_every_generated_guide(project, rendered):
    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr", "--pdf"])
    assert result.exit_code == 0, result.output

    assert {j.pdf.name for j in rendered} == {"alpha.pdf", "beta.pdf"}
    # PDFs land beside the HTML they were printed from.
    for job in rendered:
        assert job.pdf.parent == job.html.parent
        assert job.html.exists()
    # The footer title comes from the scenario, not the filename.
    assert {j.title for j in rendered} == {"Alpha flow", "Beta flow"}


def test_handbook_writes_one_document_per_locale(project, rendered):
    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr", "--handbook"])
    assert result.exit_code == 0, result.output

    handbook = project / "output" / "guides" / "editor" / "fr" / "editor-handbook.html"
    assert handbook.is_file()
    html = handbook.read_text(encoding="utf-8")
    # Both guides, in slug order, plus a table of contents.
    assert html.index("Alpha flow") < html.index("Beta flow")
    assert "steppy-book-toc" in html
    # Screenshot URLs are the same relative paths the per-guide pages use.
    assert "../../../screenshots/alpha/step-01.png" in html

    assert [j.pdf.name for j in rendered] == ["editor-handbook.pdf"]


def test_handbook_and_pdf_compose(project, rendered):
    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr", "--pdf", "--handbook"])
    assert result.exit_code == 0, result.output
    assert {j.pdf.name for j in rendered} == {"alpha.pdf", "beta.pdf", "editor-handbook.pdf"}


def test_tag_mode_supports_the_flags(project, rendered):
    result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--pdf"])
    assert result.exit_code == 0, result.output
    assert {j.pdf.name for j in rendered} == {"alpha.pdf", "beta.pdf"}


def test_render_failure_fails_the_command(project, monkeypatch):
    import steppy.pdf as pdf_module

    def _fake(batch, options=None, on_done=None, **kwargs):
        return [pdf_module.PdfResult(job=j, ok=False, error="nope") for j in batch]

    monkeypatch.setattr(pdf_module, "render_pdfs", _fake)
    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr", "--pdf"])

    assert result.exit_code == 1
    assert "Errors:" in result.output


def test_browser_missing_is_reported_once(project, monkeypatch):
    import steppy.pdf as pdf_module

    def _boom(*args, **kwargs):
        raise pdf_module.PdfRenderError("Could not launch Chromium: ...\nplaywright install chromium")

    monkeypatch.setattr(pdf_module, "render_pdfs", _boom)
    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr", "--pdf"])

    assert result.exit_code == 1
    assert "playwright install chromium" in result.output


def test_handbook_skips_a_scenario_it_cannot_assemble(project, rendered):
    """A guide missing its capture must not cost the whole handbook."""
    (project / "output" / "screenshots" / "beta" / "step-01.png").unlink()

    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr", "--handbook"])

    handbook = project / "output" / "guides" / "editor" / "fr" / "editor-handbook.html"
    html = handbook.read_text(encoding="utf-8")
    assert "Alpha flow" in html
    assert "Beta flow" not in html
    assert "SKIP" in result.output


# ── Locale-flag validation ──────────────────────────────────────────────────


class TestLocaleFlagValidation:
    """`--locale` is the third entry point that turns a user string into a
    directory segment (after `run --locales` and config.yaml's `locales:`), and
    the one that had no check at all. `steppy build` rejects a malformed tag, so
    accepting one here only defers the failure.
    """

    def test_accepts_a_regional_tag(self, project):
        result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr_BE"])
        # Missing guide-text for fr_BE is a different (and correct) failure --
        # what matters is that the flag itself was not what stopped it.
        assert "--locale:" not in result.output

    def test_rejects_an_unusable_tag(self, project):
        result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "x-default"])
        assert result.exit_code == 1
        assert "--locale:" in result.output
        assert "private-use" in result.output
