"""CLI plumbing for `steppy guide --video` and `steppy video check`.

The renderer is patched out, so these assert what the command *asks* to be
rendered — which guides, in which directories, and that nothing is rendered
when the flag is absent. The renderer itself is covered by tests/test_video.py.
"""

import json
import struct
import time
import zlib
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from steppy.cli import app

runner = CliRunner()

CONFIG_YAML = """
apps:
  editor:
    name: Editor Portal
    url: http://localhost:4200
    auth:
      mode: bypass
locales: [fr, nl]
default_locale: fr
"""


def _scenario(slug: str) -> str:
    return dedent(
        f"""\
        ---
        title: {slug.title()}
        tags:
          - user-guide-editor
        guide:
          enabled: true
        ---

        ### 1. Open the page
        - role: editor
        - action: navigate
        - target: /{slug}
        - screenshot: editor
        - guide-text:
          fr: "Ouvrez la page {slug}."
          nl: "Open de {slug} pagina."

        ### 2. Click through
        - role: editor
        - action: click
        - selector: [data-testid="go"]
        - screenshot: editor
        - guide-text:
          fr: "Cliquez sur le bouton."
          nl: "Klik op de knop."
        """
    )


def _png() -> bytes:
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "invoices.md").write_text(_scenario("invoices"), encoding="utf-8")

    sidecar = {
        "viewport": {"width": 1280, "height": 720},
        "target": {"kind": "single", "bbox": {"x": 10, "y": 20, "width": 50, "height": 30}},
    }
    for locale in ("fr", "nl"):
        shots = tmp_path / "output" / "screenshots" / "invoices" / locale
        shots.mkdir(parents=True)
        for i in (1, 2):
            (shots / f"step-{i:02d}.png").write_bytes(_png())
            (shots / f"step-{i:02d}.json").write_text(json.dumps(sidecar), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def rendered(monkeypatch):
    """Patch the renderer + ffmpeg discovery; return the recorded jobs."""
    import steppy.video as video_module

    jobs: list = []

    def _fake(batch, options=None, on_done=None, **kwargs):
        jobs.extend(batch)
        results = []
        for job in batch:
            job.out.write_bytes(b"MP4")
            result = video_module.VideoResult(job=job, ok=True)
            results.append(result)
            if on_done:
                on_done(result)
        return results

    monkeypatch.setattr(video_module, "render_videos", _fake)
    monkeypatch.setattr(video_module, "find_ffmpeg", lambda options: "/usr/bin/ffmpeg")
    return jobs


# ── The flag ────────────────────────────────────────────────────────────────


def test_no_video_flag_renders_nothing(project, rendered):
    result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr"])
    assert result.exit_code == 0, result.output
    assert rendered == []


def test_the_flag_renders_one_video_per_locale(project, rendered):
    result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--locale", "nl", "--video"])
    assert result.exit_code == 0, result.output
    assert sorted(job.locale for job in rendered) == ["fr", "nl"]


def test_videos_land_beside_the_guide_page(project, rendered):
    """Relative screenshot URLs are computed from the page's own directory, so
    the harness — and therefore the video — has to live there too."""
    result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--video"])
    assert result.exit_code == 0, result.output
    job = rendered[0]
    assert job.out.resolve() == (project / "output" / "guides" / "editor" / "fr" / "invoices.mp4").resolve()
    assert job.page_dir == job.out.parent


def test_the_guide_mode_flag_also_carries_video(project, rendered):
    result = runner.invoke(app, ["guide", "--guide", "--locale", "fr", "--video"])
    assert result.exit_code == 0, result.output
    assert len(rendered) == 1


# ── Manifest shape ──────────────────────────────────────────────────────────


def test_the_manifest_is_scoped_to_one_locale(project, rendered):
    runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--video"])
    manifest = rendered[0].manifest
    assert manifest["locales"] == ["fr"]
    assert manifest["defaultLocale"] == "fr"
    assert len(manifest["steps"]) == 2


def test_screenshot_urls_resolve_from_the_page_directory(project, rendered):
    runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--video"])
    job = rendered[0]
    url = job.manifest["steps"][0]["captures"]["fr"]["_"]["frames"][0]["screenshot"]
    assert (job.page_dir / url).resolve().is_file()


def test_narration_text_comes_from_guide_text(project, rendered):
    runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--video"])
    texts = [step["text"]["fr"] for step in rendered[0].manifest["steps"]]
    assert texts == ["Ouvrez la page invoices.", "Cliquez sur le bouton."]
    # No new authoring surface: narration is the guide-text already written.


# ── Failure handling ────────────────────────────────────────────────────────


def test_a_scenario_that_cannot_make_a_manifest_is_skipped_not_fatal(project, rendered, monkeypatch):
    """The interactive manifest validates far more strictly than the HTML guide
    (missing bbox, missing guide-text), so a perfectly good page can have no
    video. That must not take the run down with it."""
    import steppy.guide as guide_module

    def _boom(self, *args, **kwargs):
        raise ValueError("step 2: interactive step has no bbox")

    monkeypatch.setattr(guide_module.GuideAssembler, "prepare_video_manifest", _boom)
    result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--video"])
    assert result.exit_code == 0, result.output
    assert "SKIP" in result.output
    assert "no bbox" in result.output
    assert rendered == []


def test_missing_ffmpeg_is_reported_with_the_install_command(project, monkeypatch):
    import steppy.video as video_module

    monkeypatch.setattr(video_module.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--video"])
    assert result.exit_code == 1
    assert "ffmpeg" in result.output


def test_a_render_failure_is_counted(project, monkeypatch):
    import steppy.video as video_module

    def _fail(batch, options=None, on_done=None, **kwargs):
        return [video_module.VideoResult(job=job, ok=False, error="ffmpeg exited 1") for job in batch]

    monkeypatch.setattr(video_module, "render_videos", _fail)
    monkeypatch.setattr(video_module, "find_ffmpeg", lambda options: "/usr/bin/ffmpeg")
    result = runner.invoke(app, ["guide", "--tag", "user-guide-editor", "--locale", "fr", "--video"])
    assert result.exit_code == 1


# ── steppy video check ──────────────────────────────────────────────────────


def test_video_check_reports_every_moving_part(project, monkeypatch):
    import steppy.video_cli as video_cli

    monkeypatch.setattr(video_cli, "_check_chromium", lambda: (True, "launches"))
    monkeypatch.setattr(video_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(video_cli, "_version_suffix", lambda binary: "")
    result = runner.invoke(app, ["video", "check"])
    assert result.exit_code == 0, result.output
    for label in ("ffmpeg", "ffprobe", "chromium", "tts provider", "video cache"):
        assert label in result.output
    assert "ready to render" in result.output


def test_video_check_fails_when_chromium_cannot_launch(project, monkeypatch):
    import steppy.video_cli as video_cli

    monkeypatch.setattr(video_cli, "_check_chromium", lambda: (False, "cannot launch"))
    monkeypatch.setattr(video_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(video_cli, "_version_suffix", lambda binary: "")
    result = runner.invoke(app, ["video", "check"])
    assert result.exit_code == 1
    assert "playwright install chromium" in result.output


def test_video_check_flags_an_unresolved_api_key(project, monkeypatch):
    import steppy.video_cli as video_cli

    (project / "config.yaml").write_text(
        CONFIG_YAML
        + dedent(
            """
            video:
              tts:
                provider: elevenlabs
                voice: Rachel
                api_key: ${STEPPY_TEST_UNSET_KEY}
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("STEPPY_TEST_UNSET_KEY", raising=False)
    monkeypatch.setattr(video_cli, "_check_chromium", lambda: (True, "launches"))
    monkeypatch.setattr(video_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(video_cli, "_version_suffix", lambda binary: "")
    result = runner.invoke(app, ["video", "check"])
    assert result.exit_code == 1
    assert "STEPPY_TEST_UNSET_KEY" in result.output


# ── steppy video cache ──────────────────────────────────────────────────────


def _seed_caches(root):
    """One entry in each cache, with the sidecars a real entry carries."""
    video = root / ".steppy" / "video-cache"
    tts = root / ".steppy" / "tts-cache"
    video.mkdir(parents=True)
    tts.mkdir(parents=True)
    (video / "abc.mp4").write_bytes(b"M" * 4096)
    (video / "abc.vtt").write_text("WEBVTT\n", encoding="utf-8")
    (video / "abc.meta").write_text('{"sidecars": [".vtt"]}\n', encoding="utf-8")
    (tts / "def.wav").write_bytes(b"R" * 2048)
    (tts / "def.json").write_text('{"duration_ms": 400}\n', encoding="utf-8")
    return video, tts


def test_video_cache_reports_both_caches_without_touching_them(project):
    video, tts = _seed_caches(project)
    result = runner.invoke(app, ["video", "cache"])
    assert result.exit_code == 0, result.output
    assert "video cache" in result.output and "tts cache" in result.output
    assert (video / "abc.mp4").is_file() and (tts / "def.wav").is_file()


def test_video_cache_prunes_by_last_use_and_takes_the_sidecars_along(project):
    """A dropped .mp4 whose .vtt survives leaves the directory looking
    populated while every lookup misses, and nothing ever collects the orphan."""
    import os

    video, tts = _seed_caches(project)
    stale = time.time() - 40 * 86400
    for path in (*video.glob("abc.*"), *tts.glob("def.*")):
        os.utime(path, (stale, stale))

    result = runner.invoke(app, ["video", "cache", "--prune-older-than", "30"])
    assert result.exit_code == 0, result.output
    assert list(video.glob("abc.*")) == []
    assert list(tts.glob("def.*")) == []


def test_video_cache_scoped_to_one_cache_leaves_the_other_alone(project):
    video, tts = _seed_caches(project)
    result = runner.invoke(app, ["video", "cache", "--clear", "--yes", "--tts"])
    assert result.exit_code == 0, result.output
    assert list(tts.glob("def.*")) == []
    assert (video / "abc.mp4").is_file()


def test_video_cache_refuses_both_scopes_at_once(project):
    _seed_caches(project)
    result = runner.invoke(app, ["video", "cache", "--tts", "--video"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output
