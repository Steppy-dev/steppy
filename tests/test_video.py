"""Video renderer: batching, encoder mapping, dedup and error isolation.

Playwright and ffmpeg are both faked so every invariant below is covered without
launching a browser or encoding a frame. One real render is available behind
``STEPPY_VIDEO_E2E=1`` (mirrors ``tests/test_pdf.py``'s ``STEPPY_PDF_E2E``).
"""

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from steppy.models import VideoConfig
from steppy.video import (
    VideoJob,
    VideoRenderError,
    build_vtt,
    ffmpeg_args,
    find_ffmpeg,
    job_fingerprint,
    render_videos,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


def _path_from_file_url(url):
    # Stripping "file:///" only works on Windows: on POSIX it drops the leading
    # slash too and leaves a relative path.
    return Path(url2pathname(urlparse(url).path))


MANIFEST = {
    "version": "1.0",
    "slug": "invoices",
    "mode": "interactive",
    "role": "editor",
    "title": {"fr": "Factures"},
    "locales": ["fr"],
    "defaultLocale": "fr",
    "layouts": ["desktop"],
    "defaultLayout": "desktop",
    "cursor": {
        "pre_action_ms": 200,
        "action_ms": 300,
        "post_action_ms": 150,
        "travel_speed_px_per_ms": 1.5,
    },
    "steps": [
        {
            "id": "step-1",
            "index": 1,
            "action": "click",
            "interactive": True,
            "screenshotWhen": "after",
            "waitAfter": 0,
            "text": {"fr": "Cliquez sur Nouveau"},
            "captures": {
                "fr": {
                    "desktop": {
                        "frames": [
                            {
                                "screenshot": "../assets/invoices/fr/desktop/step-01.png",
                                "captureSize": {"width": 1000, "height": 600},
                                "cursor": {
                                    "kind": "click",
                                    "target": {"bbox": {"x": 1, "y": 2, "width": 3, "height": 4}},
                                },
                            }
                        ]
                    }
                }
            },
        }
    ],
}

INFO = {
    "size": {"width": 1280, "height": 864},
    "frameCount": 6,
    "durationMs": 250,
    "cues": [{"stepId": "step-1", "stepIndex": 0, "startMs": 0, "endMs": 250, "text": "Cliquez sur Nouveau"}],
    "missing": [],
}


def make_job(tmp_path: Path, name: str = "fr", **over) -> VideoJob:
    page_dir = tmp_path / "site" / "invoices"
    page_dir.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        manifest=MANIFEST,
        page_dir=page_dir,
        out=page_dir / f"{name}.mp4",
        locale="fr",
        layout="desktop",
        title="Invoices",
    )
    kwargs.update(over)
    return VideoJob(**kwargs)


class FakePage:
    """Records seeks and screenshots; ``dirty`` drives the dedup path."""

    def __init__(self, rec, dirty_pattern=None, info=None):
        self.rec = rec
        self.dirty_pattern = dirty_pattern
        self.info = info or INFO

    def goto(self, url, **kwargs):
        self.rec.gotos.append(url)

    def evaluate(self, script, arg=None):
        if "__steppyReady" in script:
            self.rec.harness_html = _path_from_file_url(self.rec.gotos[-1]).read_text(encoding="utf-8")
            return self.info
        self.rec.seeks.append(arg)
        if self.dirty_pattern is None:
            return True
        return self.dirty_pattern[arg % len(self.dirty_pattern)]

    def set_viewport_size(self, size):
        self.rec.viewports.append(size)

    def screenshot(self, **kwargs):
        self.rec.shots.append(kwargs)
        return b"\xff\xd8jpeg" + bytes([len(self.rec.shots) % 251])


class FakeContext:
    def __init__(self, rec, page):
        self.rec = rec
        self._page = page

    def new_page(self):
        return self._page


class FakeBrowser:
    def __init__(self, rec, page):
        self.rec = rec
        self._page = page

    def new_context(self, **kwargs):
        self.rec.contexts.append(kwargs)
        return FakeContext(self.rec, self._page)

    def close(self):
        self.rec.closed += 1


class FakeChromium:
    def __init__(self, rec, page, fail=False):
        self.rec = rec
        self._page = page
        self._fail = fail

    def launch(self, **kwargs):
        if self._fail:
            raise RuntimeError("no browser")
        self.rec.launches += 1
        return FakeBrowser(self.rec, self._page)


class FakePlaywrightCtx:
    def __init__(self, rec, **kwargs):
        self.rec = rec
        self.kwargs = kwargs

    def __enter__(self):
        page = FakePage(self.rec, self.kwargs.get("dirty_pattern"), self.kwargs.get("info"))
        self.chromium = FakeChromium(self.rec, page, self.kwargs.get("launch_fails", False))
        return self

    def __exit__(self, *exc):
        return False


class Recorder:
    def __init__(self):
        self.launches = 0
        self.closed = 0
        self.contexts = []
        self.gotos = []
        self.seeks = []
        self.shots = []
        self.viewports = []
        self.harness_html = ""
        self.ffmpeg_calls = []
        self.stdin_writes = []


class FakePopen:
    """Stands in for ffmpeg: records argv and stdin, writes the output file."""

    def __init__(self, rec, args, exit_code=0, **kwargs):
        self.rec = rec
        self.args = args
        self._code = exit_code
        self._out = Path(args[-1])
        self.stderr = kwargs.get("stderr")
        rec.ffmpeg_calls.append(args)

        class _Stdin:
            def __init__(self, outer):
                self.outer = outer

            def write(self, data):
                self.outer.rec.stdin_writes.append(data)

            def close(self):
                pass

        self.stdin = _Stdin(self)

    def wait(self, timeout=None):
        self._out.parent.mkdir(parents=True, exist_ok=True)
        if self._code == 0:
            self._out.write_bytes(b"MP4")
        else:
            # A real ffmpeg that dies mid-encode leaves the bytes it had already
            # written. That partial file is the whole reason renders stage.
            self._out.write_bytes(b"MP")
            if self.stderr is not None:
                self.stderr.write(b"x264: something broke\n")
        return self._code

    def kill(self):
        pass


@pytest.fixture
def fake_stack(monkeypatch, tmp_path):
    """Install fake playwright + ffmpeg; return the recorder factory."""
    import playwright.sync_api as sync_api

    import steppy.video as video_module

    def install(*, exit_code=0, **kwargs) -> Recorder:
        rec = Recorder()
        monkeypatch.setattr(sync_api, "sync_playwright", lambda: FakePlaywrightCtx(rec, **kwargs))
        monkeypatch.setattr(video_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(
            video_module.subprocess,
            "Popen",
            lambda args, **kw: FakePopen(rec, args, exit_code=exit_code, **kw),
        )
        return rec

    return install


def _opts(**over) -> VideoConfig:
    base = dict(cache=False, cache_dir=str(Path("nonexistent-cache")), intro_ms=0, outro_ms=0)
    base.update(over)
    return VideoConfig(**base)


# ── find_ffmpeg ─────────────────────────────────────────────────────────────


def test_missing_ffmpeg_names_the_install_command(monkeypatch):
    import steppy.video as video_module

    monkeypatch.setattr(video_module.shutil, "which", lambda name: None)
    with pytest.raises(VideoRenderError) as exc:
        find_ffmpeg(VideoConfig())
    assert "winget install" in str(exc.value)
    assert "brew install ffmpeg" in str(exc.value)
    # The delta vs --pdf that surprises people: needed even with no voiceover.
    assert "tts.provider: none" in str(exc.value)


def test_configured_ffmpeg_path_wins(monkeypatch, tmp_path):
    import steppy.video as video_module

    monkeypatch.setattr(video_module.shutil, "which", lambda name: f"/resolved/{name}")
    assert find_ffmpeg(VideoConfig(ffmpeg_path="my-ffmpeg")) == "/resolved/my-ffmpeg"


# ── ffmpeg_args ─────────────────────────────────────────────────────────────


def test_ffmpeg_args_encode_for_universal_playback():
    args = ffmpeg_args(_opts(), ffmpeg="ffmpeg", width=1280, height=864, audio=None, out=Path("o.mp4"))
    assert "-f" in args and args[args.index("-f") + 1] == "image2pipe"
    assert args[args.index("-c:v") + 1] == "libx264"
    # Safari/QuickTime refuse anything else.
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-movflags") + 1] == "+faststart"
    assert args[-1] == "o.mp4"


def test_ffmpeg_args_force_even_dimensions():
    args = ffmpeg_args(_opts(), ffmpeg="ffmpeg", width=1281, height=721, audio=None, out=Path("o.mp4"))
    vf = args[args.index("-vf") + 1]
    assert "pad=ceil(iw/2)*2:ceil(ih/2)*2" in vf


def test_ffmpeg_args_omit_the_audio_branch_entirely_when_silent():
    args = ffmpeg_args(_opts(), ffmpeg="ffmpeg", width=640, height=480, audio=None, out=Path("o.mp4"))
    assert "-c:a" not in args
    assert "-shortest" not in args


def test_ffmpeg_args_mux_narration_when_present(tmp_path):
    wav = tmp_path / "n.wav"
    wav.write_bytes(b"RIFF")
    args = ffmpeg_args(_opts(), ffmpeg="ffmpeg", width=640, height=480, audio=wav, out=Path("o.mp4"))
    assert args[args.index("-c:a") + 1] == "aac"
    assert "-shortest" in args
    assert str(wav) in args


def test_ffmpeg_args_carry_the_quality_knobs():
    args = ffmpeg_args(
        _opts(crf=18, preset="slow", fps=30), ffmpeg="ffmpeg", width=8, height=8, audio=None, out=Path("o.mp4")
    )
    assert args[args.index("-crf") + 1] == "18"
    assert args[args.index("-preset") + 1] == "slow"
    assert args[args.index("-framerate") + 1] == "30"


# ── Batching ────────────────────────────────────────────────────────────────


def test_empty_batch_never_launches_anything(fake_stack):
    rec = fake_stack()
    assert render_videos([], _opts()) == []
    assert rec.launches == 0
    assert rec.ffmpeg_calls == []


def test_one_browser_for_the_whole_batch(fake_stack, tmp_path):
    """A project is guides x locales x layouts — a launch per video would dominate."""
    rec = fake_stack()
    jobs = [make_job(tmp_path, f"v{i}") for i in range(4)]
    results = render_videos(jobs, _opts())
    assert all(r.ok for r in results), [r.error for r in results]
    assert rec.launches == 1
    assert rec.closed == 1
    assert len(rec.ffmpeg_calls) == 4


def test_reduced_motion_is_pinned(fake_stack, tmp_path):
    """A host emulating prefers-reduced-motion would silently strip every
    ripple and pulse, with nothing failing anywhere."""
    rec = fake_stack()
    render_videos([make_job(tmp_path)], _opts())
    assert rec.contexts == [{"reduced_motion": "no-preference"}]


def test_viewport_matches_the_stage_the_page_reported(fake_stack, tmp_path):
    rec = fake_stack()
    render_videos([make_job(tmp_path)], _opts())
    assert rec.viewports == [{"width": 1280, "height": 864}]


def test_pages_load_over_file_url_from_the_guide_directory(fake_stack, tmp_path):
    """Screenshot URLs are relative to the guide's own page; a harness anywhere
    else resolves them against the wrong directory and renders a blank stage."""
    rec = fake_stack()
    job = make_job(tmp_path)
    render_videos([job], _opts())
    assert rec.gotos[0].startswith("file:///")
    assert _path_from_file_url(rec.gotos[0]).parent == job.page_dir.resolve()


def test_harness_inlines_the_manifest_and_bundle(fake_stack, tmp_path):
    """fetch() on a file:// page is blocked, so the manifest cannot be loaded."""
    rec = fake_stack()
    render_videos([make_job(tmp_path)], _opts())
    assert "invoices" in rec.harness_html
    assert "__STEPPY_VIDEO__" in rec.harness_html
    assert "/*__STEPPY_MANIFEST__*/" not in rec.harness_html
    assert "/*__STEPPY_BUNDLE__*/" not in rec.harness_html


def test_harness_options_carry_the_config(fake_stack, tmp_path):
    rec = fake_stack()
    render_videos([make_job(tmp_path)], _opts(fps=25, width=960, captions="off"))
    start = rec.harness_html.index('id="steppy-options">') + len('id="steppy-options">')
    payload = json.loads(rec.harness_html[start : rec.harness_html.index("</script>", start)])
    assert payload["fps"] == 25
    assert payload["width"] == 960
    assert payload["captions"] == "off"


def test_harness_is_deleted_after_a_successful_render(fake_stack, tmp_path):
    fake_stack()
    job = make_job(tmp_path)
    render_videos([job], _opts())
    assert list(job.page_dir.glob(".steppy-video-*.html")) == []


def test_harness_is_deleted_after_a_failure(fake_stack, tmp_path):
    """steppy guide has no output-dir wipe to clean up after it."""
    fake_stack(exit_code=1)
    job = make_job(tmp_path)
    results = render_videos([job], _opts())
    assert not results[0].ok
    assert list(job.page_dir.glob(".steppy-video-*.html")) == []


# ── Frame loop ──────────────────────────────────────────────────────────────


def test_every_frame_reaches_the_encoder(fake_stack, tmp_path):
    rec = fake_stack()
    render_videos([make_job(tmp_path)], _opts())
    assert len(rec.stdin_writes) == INFO["frameCount"]


def test_unchanged_frames_are_not_re_screenshotted(fake_stack, tmp_path):
    """Holds and voiceover pauses dominate a narrated video; re-capturing them
    is the single largest avoidable cost in the pipeline."""
    rec = fake_stack(dirty_pattern=[True, False, False])
    results = render_videos([make_job(tmp_path)], _opts())
    assert len(rec.stdin_writes) == INFO["frameCount"]
    assert len(rec.shots) == 2
    assert results[0].rendered_frames == 2
    assert results[0].frames == INFO["frameCount"]


def test_a_skipped_frame_reuses_the_previous_bytes(fake_stack, tmp_path):
    rec = fake_stack(dirty_pattern=[True, False])
    render_videos([make_job(tmp_path)], _opts())
    assert rec.stdin_writes[0] == rec.stdin_writes[1]


def test_frames_are_jpeg_with_animations_disabled(fake_stack, tmp_path):
    """Output is lossy H.264 regardless, so PNG buys nothing; and the default
    animations='allow' lets a live animation tear a frame."""
    rec = fake_stack()
    render_videos([make_job(tmp_path)], _opts(jpeg_quality=77))
    assert rec.shots[0]["type"] == "jpeg"
    assert rec.shots[0]["quality"] == 77
    assert rec.shots[0]["animations"] == "disabled"


def test_seeks_run_in_order_over_every_frame(fake_stack, tmp_path):
    rec = fake_stack()
    render_videos([make_job(tmp_path)], _opts())
    assert rec.seeks == list(range(INFO["frameCount"]))


def test_poster_skips_the_intro_card(fake_stack, tmp_path):
    fake_stack()
    job = make_job(tmp_path)
    render_videos([job], _opts(intro_ms=1000, fps=24))
    assert job.poster.is_file()


# ── Error isolation ─────────────────────────────────────────────────────────


def test_launch_failure_is_fatal_and_names_the_fix(fake_stack, tmp_path):
    fake_stack(launch_fails=True)
    with pytest.raises(VideoRenderError) as exc:
        render_videos([make_job(tmp_path)], _opts())
    assert "playwright install chromium" in str(exc.value)


def test_an_encoder_failure_is_reported_not_raised(fake_stack, tmp_path):
    fake_stack(exit_code=1)
    results = render_videos([make_job(tmp_path)], _opts())
    assert not results[0].ok
    assert "ffmpeg exited 1" in results[0].error
    assert "x264" in results[0].error


def test_one_broken_guide_does_not_cost_the_others(fake_stack, tmp_path, monkeypatch):
    import steppy.video as video_module

    rec = fake_stack()
    calls = {"n": 0}

    def flaky(args, **kw):
        calls["n"] += 1
        return FakePopen(rec, args, exit_code=1 if calls["n"] == 2 else 0, **kw)

    monkeypatch.setattr(video_module.subprocess, "Popen", flaky)
    results = render_videos([make_job(tmp_path, f"v{i}") for i in range(3)], _opts())
    assert [r.ok for r in results] == [True, False, True]


def test_missing_screenshots_warn_but_still_render(fake_stack, tmp_path):
    """A gappy video beats no video — but say which file is missing."""
    info = dict(INFO, missing=["../assets/x/step-03.png"])
    fake_stack(info=info)
    warnings = []
    results = render_videos([make_job(tmp_path)], _opts(), on_warning=warnings.append)
    assert results[0].ok
    assert "step-03.png" in warnings[0]


# ── Content-addressed cache ─────────────────────────────────────────────────


def test_fingerprint_is_stable_for_identical_inputs(tmp_path):
    a, b = make_job(tmp_path), make_job(tmp_path)
    assert job_fingerprint(a, _opts(), "bundle") == job_fingerprint(b, _opts(), "bundle")


def test_fingerprint_tracks_every_input_that_changes_the_pixels(tmp_path):
    job = make_job(tmp_path)
    base = job_fingerprint(job, _opts(), "bundle")
    assert job_fingerprint(job, _opts(fps=30), "bundle") != base
    assert job_fingerprint(job, _opts(width=640), "bundle") != base
    # A change to the animation code must invalidate every cached video, or the
    # build silently ships the old motion.
    assert job_fingerprint(job, _opts(), "other-bundle") != base
    assert job_fingerprint(make_job(tmp_path, title="Autre"), _opts(), "bundle") != base
    assert job_fingerprint(make_job(tmp_path, narration_ms={"step-1": 900}), _opts(), "bundle") != base


def test_a_second_render_of_unchanged_inputs_does_no_work(fake_stack, tmp_path):
    rec = fake_stack()
    opts = _opts(cache=True, cache_dir=str(tmp_path / "cache"))
    job = make_job(tmp_path)

    first = render_videos([job], opts)
    assert first[0].ok and not first[0].cached
    assert len(rec.ffmpeg_calls) == 1

    second = render_videos([job], opts)
    assert second[0].cached
    assert len(rec.ffmpeg_calls) == 1  # no new encode
    assert job.out.read_bytes() == b"MP4"


def test_the_cache_lives_outside_the_output_tree(fake_stack, tmp_path):
    """steppy build wipes output/ on every run, so a sidecar next to the
    artifact would be gone before it could ever be a hit."""
    fake_stack()
    cache = tmp_path / "dot-steppy" / "video-cache"
    job = make_job(tmp_path)
    render_videos([job], _opts(cache=True, cache_dir=str(cache)))
    assert list(cache.glob("*.mp4"))
    assert not list(job.page_dir.glob("*.sha256"))


def test_a_cache_hit_restores_the_sidecars_too(fake_stack, tmp_path):
    fake_stack()
    opts = _opts(cache=True, cache_dir=str(tmp_path / "cache"))
    job = make_job(tmp_path)
    render_videos([job], opts)
    job.vtt.unlink()
    job.poster.unlink()

    result = render_videos([job], opts)
    assert result[0].cached
    assert job.vtt.is_file()
    assert job.poster.is_file()


def test_a_recaptured_screenshot_invalidates_the_video(tmp_path):
    """The manifest carries screenshot *URLs*. A recapture that leaves the
    target element where it was changes nothing in it — so without hashing the
    pixels, a build ships a video of the old UI and says it was cached."""
    job = make_job(tmp_path)
    shot = job.page_dir.parent / "assets" / "invoices" / "fr" / "desktop" / "step-01.png"
    shot.parent.mkdir(parents=True, exist_ok=True)

    shot.write_bytes(b"old-pixels")
    before = job_fingerprint(job, _opts(), "bundle")
    shot.write_bytes(b"new-pixels")
    assert job_fingerprint(job, _opts(), "bundle") != before


def test_fields_that_cannot_change_a_pixel_do_not_re_encode_the_corpus(tmp_path):
    """Setting an API key used to invalidate every cached video in the project."""
    from steppy.models import TtsConfig

    job = make_job(tmp_path)
    base = job_fingerprint(job, _opts(), "bundle")
    assert job_fingerprint(job, _opts(tts=TtsConfig(api_key="secret")), "bundle") == base
    assert job_fingerprint(job, _opts(timeout=999000), "bundle") == base
    assert job_fingerprint(job, _opts(ffmpeg_path="/opt/ffmpeg"), "bundle") == base
    assert job_fingerprint(job, _opts(cache_dir="/somewhere/else"), "bundle") == base


def test_the_hash_memo_is_shared_across_a_batch(tmp_path):
    """A corpus is guides x locales x layouts over one pool of screenshots."""
    job = make_job(tmp_path)
    shot = job.page_dir.parent / "assets" / "invoices" / "fr" / "desktop" / "step-01.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"pixels")

    memo: dict = {}
    job_fingerprint(job, _opts(), "bundle", memo)
    assert shot.resolve() in memo
    # A second job reuses the digest even after the file changes underneath.
    shot.write_bytes(b"different")
    assert job_fingerprint(job, _opts(), "bundle", memo) == job_fingerprint(job, _opts(), "bundle", memo)


def test_a_failed_encode_leaves_no_entry_in_the_cache(fake_stack, tmp_path):
    """The worst bug this file guards: a truncated MP4 at the key path is a
    permanent hit, reported as ok=True, cached=True, forever."""
    fake_stack(exit_code=1)
    cache = tmp_path / "cache"
    result = render_videos([make_job(tmp_path)], _opts(cache=True, cache_dir=str(cache)))
    assert not result[0].ok
    assert list(cache.glob("*.mp4")) == []
    assert list(cache.glob("*")) == []


def test_an_entry_whose_sidecars_vanished_re_renders(fake_stack, tmp_path):
    """Serving it would ship a <track> pointing at nothing, silently."""
    rec = fake_stack()
    opts = _opts(cache=True, cache_dir=str(tmp_path / "cache"))
    job = make_job(tmp_path)
    render_videos([job], opts)
    for stale in (tmp_path / "cache").glob("*.vtt"):
        stale.unlink()

    result = render_videos([job], opts)
    assert not result[0].cached
    assert len(rec.ffmpeg_calls) == 2


def test_cache_root_overrides_the_cwd_relative_config_value(fake_stack, tmp_path):
    fake_stack()
    resolved = tmp_path / "resolved-cache"
    render_videos(
        [make_job(tmp_path)],
        _opts(cache=True, cache_dir=".steppy/video-cache"),
        cache_root=resolved,
    )
    assert list(resolved.glob("*.mp4"))


# ── WebVTT ──────────────────────────────────────────────────────────────────


def test_vtt_has_a_header_and_one_cue_per_step():
    vtt = build_vtt(
        [
            {"startMs": 0, "endMs": 2500, "text": "Ouvrez le menu"},
            {"startMs": 2500, "endMs": 5000, "text": "Cliquez sur Nouveau"},
        ]
    )
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:02.500" in vtt
    assert "00:00:02.500 --> 00:00:05.000" in vtt
    assert "Ouvrez le menu" in vtt


def test_vtt_timestamps_carry_hours():
    vtt = build_vtt([{"startMs": 3_723_004, "endMs": 3_724_000, "text": "tard"}])
    assert "01:02:03.004" in vtt


def test_vtt_skips_empty_cues():
    vtt = build_vtt([{"startMs": 0, "endMs": 1, "text": "  "}, {"startMs": 1, "endMs": 2, "text": "ok"}])
    assert vtt.count("-->") == 1


def test_a_video_without_narration_still_writes_an_empty_track(fake_stack, tmp_path):
    """The embedding page advertises a <track> decided at build time, long
    before the render knows whether there is anything to say. "No captions" has
    to be an empty track, not a 404."""
    fake_stack(info=dict(INFO, cues=[]))
    job = make_job(tmp_path)
    render_videos([job], _opts())
    assert job.vtt.read_text(encoding="utf-8").strip() == "WEBVTT"


# ── Real render (opt-in) ────────────────────────────────────────────────────


@pytest.mark.skipif(os.environ.get("STEPPY_VIDEO_E2E") != "1", reason="set STEPPY_VIDEO_E2E=1")
def test_real_render_produces_a_playable_mp4(tmp_path):
    """End-to-end through real Chromium and real ffmpeg."""
    import struct
    import zlib

    def png(width=1000, height=600):
        def chunk(tag, data):
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        raw = b"".join(b"\x00" + b"\x99\xaa\xbb" * width for _ in range(height))
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    shots = tmp_path / "assets" / "invoices" / "fr" / "desktop"
    shots.mkdir(parents=True)
    (shots / "step-01.png").write_bytes(png())
    page_dir = tmp_path / "invoices"
    page_dir.mkdir()

    job = VideoJob(
        manifest=MANIFEST,
        page_dir=page_dir,
        out=page_dir / "fr.mp4",
        locale="fr",
        layout="desktop",
        title="Invoices",
    )
    results = render_videos([job], VideoConfig(cache=False, fps=12, width=640, intro_ms=200, outro_ms=200))
    assert results[0].ok, results[0].error
    assert job.out.stat().st_size > 1000
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", str(job.out)],
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert streams[0]["codec_name"] == "h264"
    assert streams[0]["width"] % 2 == 0 and streams[0]["height"] % 2 == 0
