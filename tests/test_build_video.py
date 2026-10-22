"""`steppy build --video`: job collection, both targets, and the two gates.

The renderer is patched out, so these assert what the build *asks* to be
rendered — which guides, which paths, and that nothing is rendered when either
gate is closed. The renderer itself is covered by tests/test_video.py.
"""

import json
import struct
import zlib
from pathlib import Path

import pytest

from steppy.build import BuildAssembler, BuildError
from steppy.build_models import BuildConfig
from steppy.models import SteppyConfig, TtsConfig, VideoConfig
from steppy.tts import assemble_track

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "demo-guide"


# ── Fixtures ────────────────────────────────────────────────────────────────


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


def _ensure_screenshots():
    sidecar = {
        "viewport": {"width": 1280, "height": 720},
        "target": {"kind": "single", "bbox": {"x": 100, "y": 200, "width": 50, "height": 30}},
    }
    for slug in ("onboarding", "invoices"):
        d = FIXTURE_ROOT / "output" / "screenshots" / slug
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 3):
            png = d / f"step-{i:02d}.png"
            if not png.exists():
                png.write_bytes(_png())
            meta = d / f"step-{i:02d}.json"
            if not meta.exists():
                meta.write_text(json.dumps(sidecar))


def make_config(tmp_path: Path, *, target="static", modes=("stepbystep", "video"), locales=("fr", "nl")) -> BuildConfig:
    _ensure_screenshots()
    return BuildConfig(
        scenariosRoot=str(FIXTURE_ROOT / "e2e" / "scenarios"),
        screenshotsDir=str(FIXTURE_ROOT / "output" / "screenshots"),
        outputDir=str(tmp_path / "generated"),
        assetsDir=str(tmp_path / "assets"),
        role="editor",
        locales=list(locales),
        defaultLocale=locales[0],
        target=target,
        site={"title": "Demo Guides"},
        guides=[
            {"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"},
            {
                "slug": "invoices",
                "title": "Invoices",
                "scenario": "editor/invoices.md",
                "modes": list(modes),
            },
        ],
        navbar={"order": ["onboarding", "invoices"]},
    )


@pytest.fixture
def rendered(monkeypatch):
    """Patch the renderer and ffmpeg discovery; return the recorded jobs."""
    import steppy.video as video_module

    jobs: list = []

    def _fake_render(batch, options=None, **kwargs):
        jobs.extend(batch)
        results = []
        for job in batch:
            job.out.parent.mkdir(parents=True, exist_ok=True)
            job.out.write_bytes(b"MP4")
            job.vtt.write_text("WEBVTT\n", encoding="utf-8")
            job.poster.write_bytes(b"JPEG")
            results.append(video_module.VideoResult(job=job, ok=True))
        return results

    monkeypatch.setattr(video_module, "render_videos", _fake_render)
    monkeypatch.setattr(video_module, "find_ffmpeg", lambda options: "/usr/bin/ffmpeg")
    return jobs


def build(config: BuildConfig, tmp_path: Path, *, video=True, dry_run=False, steppy_config=None):
    assembler = BuildAssembler(
        config,
        project_root=tmp_path,
        steppy_config=steppy_config or SteppyConfig(apps={}, auth={}),
    )
    return assembler.build_all(dry_run=dry_run, video=video)


# ── The two gates ───────────────────────────────────────────────────────────


def test_declaring_the_mode_without_the_flag_renders_nothing(rendered, tmp_path):
    """The mode is the content decision; the flag is the cost decision."""
    build(make_config(tmp_path), tmp_path, video=False)
    assert rendered == []


def test_the_flag_without_any_declaring_guide_renders_nothing(rendered, tmp_path, capsys):
    build(make_config(tmp_path, modes=("stepbystep",)), tmp_path, video=True)
    assert rendered == []
    assert "no guide declares" in capsys.readouterr().err


def test_both_gates_open_renders(rendered, tmp_path):
    build(make_config(tmp_path), tmp_path, video=True)
    assert rendered


def test_only_the_declaring_guide_gets_a_video(rendered, tmp_path):
    build(make_config(tmp_path), tmp_path, video=True)
    assert {job.manifest["slug"] for job in rendered} == {"invoices"}


def test_a_dry_run_renders_nothing_but_reports_the_artifacts(rendered, tmp_path):
    results = build(make_config(tmp_path), tmp_path, video=True, dry_run=True)
    assert rendered == []
    planned = [p for p, status in results.items() if p.endswith(".mp4")]
    assert planned
    assert all(results[p] == "planned" for p in planned)


def test_missing_ffmpeg_fails_before_any_work(tmp_path, monkeypatch):
    """A render is minutes per guide — the end is the worst time to find out."""
    import steppy.video as video_module

    monkeypatch.setattr(video_module.shutil, "which", lambda name: None)
    with pytest.raises(video_module.VideoRenderError, match="ffmpeg"):
        build(make_config(tmp_path), tmp_path, video=True)


def test_a_missing_config_warns_before_shipping_silent_videos(rendered, tmp_path, capsys):
    assembler = BuildAssembler(make_config(tmp_path), project_root=tmp_path, steppy_config=None)
    assembler.build_all(video=True)
    err = capsys.readouterr().err
    assert "no config.yaml" in err and "captions-only" in err


# ── Job shape ───────────────────────────────────────────────────────────────


def test_one_job_per_locale(rendered, tmp_path):
    build(make_config(tmp_path, locales=("fr", "nl")), tmp_path, video=True)
    assert sorted(job.locale for job in rendered) == ["fr", "nl"]


def test_static_videos_land_beside_the_pages(rendered, tmp_path):
    build(make_config(tmp_path, target="static"), tmp_path, video=True)
    out = {job.out.name for job in rendered}
    assert out == {"fr.mp4", "nl.mp4"}
    assert all(job.out.parent.name == "invoices" for job in rendered)


def test_angular_videos_land_in_the_assets_dir(rendered, tmp_path):
    """Angular serves them at /assets/<role>/<slug>/… like every other asset."""
    build(make_config(tmp_path, target="angular"), tmp_path, video=True)
    assert all("assets" in job.out.parts for job in rendered)
    assert all(job.out.parent.name == "invoices" for job in rendered)


def test_angular_is_not_rejected(rendered, tmp_path):
    """The mp4 is target-independent — only the embedding differs."""
    build(make_config(tmp_path, target="angular"), tmp_path, video=True)
    assert len(rendered) == 2


def test_angular_emits_a_video_host_component(rendered, tmp_path):
    build(make_config(tmp_path, target="angular"), tmp_path, video=True)
    component = tmp_path / "generated" / "invoices" / "invoices-video.component.ts"
    assert component.is_file()
    src = component.read_text(encoding="utf-8")
    assert "<video" in src
    assert 'kind="captions"' in src
    # Served like every other asset the host app already exports.
    assert "/assets/editor/invoices" in src


def test_angular_adds_video_to_the_mode_toggle(rendered, tmp_path):
    build(make_config(tmp_path, target="angular"), tmp_path, video=True)
    src = (tmp_path / "generated" / "invoices" / "invoices.component.ts").read_text(encoding="utf-8")
    assert "selectedMode() === 'video'" in src
    assert "{ label: 'Video', value: 'video' }" in src


def test_angular_offers_no_video_tab_when_the_flag_is_absent(rendered, tmp_path):
    """A tab with no file behind it is worse than no tab."""
    build(make_config(tmp_path, target="angular"), tmp_path, video=False)
    src = (tmp_path / "generated" / "invoices" / "invoices.component.ts").read_text(encoding="utf-8")
    assert "'video'" not in src
    assert not (tmp_path / "generated" / "invoices" / "invoices-video.component.ts").exists()


def test_the_manifest_is_scoped_to_one_locale(rendered, tmp_path):
    build(make_config(tmp_path), tmp_path, video=True)
    for job in rendered:
        assert job.manifest["locales"] == [job.locale]
        assert job.manifest["defaultLocale"] == job.locale


def test_screenshot_urls_are_disk_relative_not_published(rendered, tmp_path):
    """Angular's published URLs are root-absolute and would resolve to the
    filesystem root over file://; the renderer owns its own URL shape."""
    build(make_config(tmp_path, target="angular"), tmp_path, video=True)
    for job in rendered:
        for step in job.manifest["steps"]:
            for capture in step["captures"][job.locale].values():
                for frame in capture["frames"]:
                    assert not frame["screenshot"].startswith("/")
                    resolved = (job.page_dir / frame["screenshot"]).resolve()
                    assert resolved.suffix == ".png"


def test_screenshot_urls_resolve_to_the_copied_assets(rendered, tmp_path):
    build(make_config(tmp_path, target="static"), tmp_path, video=True)
    job = rendered[0]
    frame = job.manifest["steps"][0]["captures"][job.locale]
    url = next(iter(frame.values()))["frames"][0]["screenshot"]
    assert (job.page_dir / url).resolve().is_file()


def test_the_title_is_localized_per_job(rendered, tmp_path):
    build(make_config(tmp_path), tmp_path, video=True)
    assert all(job.title == "Invoices" for job in rendered)


def test_no_stamp_unless_enabled(rendered, tmp_path):
    build(make_config(tmp_path), tmp_path, video=True)
    assert all(job.stamp == "" for job in rendered)


def test_the_stamp_interpolates_version_and_locale(rendered, tmp_path):
    cfg = SteppyConfig(
        apps={},
        auth={},
        video=VideoConfig(stamp={"enabled": True, "text": "{version} · {locale}", "version": "2.14"}),
    )
    build(make_config(tmp_path), tmp_path, video=True, steppy_config=cfg)
    assert {job.stamp for job in rendered} == {"2.14 · fr", "2.14 · nl"}


# ── Static page wiring ──────────────────────────────────────────────────────


def test_the_static_guide_gets_a_video_page(rendered, tmp_path):
    build(make_config(tmp_path, target="static"), tmp_path, video=True)
    page = tmp_path / "generated" / "invoices" / "video.html"
    assert page.is_file()
    html = page.read_text(encoding="utf-8")
    assert "<video" in html
    assert 'kind="captions"' in html


def test_the_video_page_offers_every_locale(rendered, tmp_path):
    build(make_config(tmp_path, target="static"), tmp_path, video=True)
    html = (tmp_path / "generated" / "invoices" / "video.html").read_text(encoding="utf-8")
    assert "fr.mp4" in html and "nl.mp4" in html


def test_the_guide_page_links_the_video(rendered, tmp_path):
    """A video nobody can reach from the guide page is not a deliverable."""
    build(make_config(tmp_path, target="static"), tmp_path, video=True)
    html = (tmp_path / "generated" / "invoices" / "fr.html").read_text(encoding="utf-8")
    assert "./video.html" in html


def test_the_guide_page_also_links_the_interactive_tour(rendered, tmp_path):
    """Pre-existing gap fixed in passing: interactive.html was emitted and
    sitemapped but reachable only by typing the URL."""
    build(make_config(tmp_path, target="static", modes=("stepbystep", "interactive")), tmp_path, video=False)
    html = (tmp_path / "generated" / "invoices" / "fr.html").read_text(encoding="utf-8")
    assert "./interactive.html" in html


def test_a_guide_with_no_extra_modes_gets_no_mode_row(rendered, tmp_path):
    build(make_config(tmp_path, target="static"), tmp_path, video=True)
    html = (tmp_path / "generated" / "onboarding" / "fr.html").read_text(encoding="utf-8")
    assert "steppy-mode-links" not in html


def test_no_video_page_when_the_flag_is_absent(rendered, tmp_path):
    build(make_config(tmp_path, target="static"), tmp_path, video=False)
    assert not (tmp_path / "generated" / "invoices" / "video.html").exists()


# ── Narration hand-off ──────────────────────────────────────────────────────


def test_the_default_provider_leaves_jobs_silent(rendered, tmp_path):
    build(make_config(tmp_path), tmp_path, video=True)
    assert all(job.narration_ms == {} for job in rendered)
    assert all(job.narration_clips == {} for job in rendered)


def test_narration_durations_reach_the_job(rendered, tmp_path, monkeypatch):
    """The timeline needs them up front, to stretch each step to fit."""
    import steppy.build as build_module
    import steppy.tts as tts_module

    def fake_synth(utterances, cfg, *, locale, work_dir, ffmpeg, cache_root=None, memo=None, on_warning=None):
        clip = work_dir / "clip.wav"
        work_dir.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"RIFF")
        return tts_module.Narration(
            durations_ms={u.step_id: 1500 for u in utterances},
            clips={u.step_id: clip for u in utterances},
            provider="fake",
        )

    monkeypatch.setattr(build_module, "_warn", lambda msg: None)
    monkeypatch.setattr(tts_module, "synthesize_steps", fake_synth)

    cfg = SteppyConfig(apps={}, auth={}, video=VideoConfig(tts=TtsConfig(provider="piper", voice="x")))
    build(make_config(tmp_path), tmp_path, video=True, steppy_config=cfg)
    assert all(job.narration_ms for job in rendered)
    assert all(job.narration_clips for job in rendered)


def test_the_narration_track_is_laid_at_the_timeline_cues(tmp_path):
    """Placement is the timeline's call, so it happens after the page reports."""
    import wave

    from steppy.video import VideoJob

    clip = tmp_path / "a.wav"
    with wave.open(str(clip), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\x00\x00" * 48000)  # 1s

    job = VideoJob(
        manifest={},
        page_dir=tmp_path,
        out=tmp_path / "v.mp4",
        locale="fr",
        narration_clips={"step-1": clip},
    )
    cues = [{"stepId": "step-1", "startMs": 2000, "endMs": 4000}]
    track = assemble_track(job, cues, 6000, tmp_path / "track.wav")
    assert track is not None
    with wave.open(str(track), "rb") as handle:
        assert round(handle.getnframes() / 48000, 2) == 6.0


def test_no_clips_means_no_track(tmp_path):
    from steppy.video import VideoJob

    job = VideoJob(manifest={}, page_dir=tmp_path, out=tmp_path / "v.mp4", locale="fr")
    assert assemble_track(job, [], 1000, tmp_path / "t.wav") is None


# ── Errors ──────────────────────────────────────────────────────────────────


def test_a_render_failure_fails_the_build(tmp_path, monkeypatch):
    import steppy.video as video_module

    def _fail(batch, options=None, **kwargs):
        return [video_module.VideoResult(job=job, ok=False, error="ffmpeg exited 1") for job in batch]

    monkeypatch.setattr(video_module, "render_videos", _fail)
    monkeypatch.setattr(video_module, "find_ffmpeg", lambda options: "/usr/bin/ffmpeg")
    with pytest.raises(BuildError, match="failed to render"):
        build(make_config(tmp_path), tmp_path, video=True)


# ── What the machine-readable outputs advertise ─────────────────────────────


class TestEmittedModes:
    """`modes` is a promise that a page exists. Four places in build.py answer
    "which modes does this guide have"; two of them used to answer from the
    *declaration* rather than from what was produced, so `manifest.json` and
    `<role>.nav.json` advertised a video page that was never written and any
    consumer building a switcher from them constructed a 404.
    """

    def _manifest(self, tmp_path: Path) -> dict:
        site = tmp_path / "generated"
        return json.loads((site / "manifest.json").read_text(encoding="utf-8"))

    def _guide(self, manifest: dict, slug: str) -> dict:
        return next(g for g in manifest["guides"] if g["slug"] == slug)

    def test_manifest_drops_video_when_the_flag_is_closed(self, rendered, tmp_path):
        """The guide declares `video`; --video did not arm the render."""
        build(make_config(tmp_path), tmp_path, video=False)
        assert self._guide(self._manifest(tmp_path), "invoices")["modes"] == ["stepbystep"]

    def test_manifest_keeps_video_when_it_was_rendered(self, rendered, tmp_path):
        build(make_config(tmp_path), tmp_path, video=True)
        assert self._guide(self._manifest(tmp_path), "invoices")["modes"] == ["stepbystep", "video"]

    def test_manifest_modes_match_the_pages_on_disk(self, rendered, tmp_path):
        """The property the field is actually claiming, asserted directly."""
        build(make_config(tmp_path, modes=("stepbystep", "interactive", "video")), tmp_path, video=False)
        site = tmp_path / "generated"
        for guide in self._manifest(tmp_path)["guides"]:
            for mode in guide["modes"]:
                if mode == "stepbystep":
                    assert (site / guide["slug"] / "fr.html").exists()
                else:
                    assert (site / guide["slug"] / f"{mode}.html").exists(), (guide["slug"], mode)

    def test_nav_json_drops_video_when_the_flag_is_closed(self, rendered, tmp_path):
        build(make_config(tmp_path, target="angular"), tmp_path, video=False)
        nav = json.loads((tmp_path / "generated" / "editor.nav.json").read_text(encoding="utf-8"))
        entry = next(n for n in nav if n.get("slug") == "invoices")
        assert entry["modes"] == ["stepbystep"]

    def test_nav_json_keeps_video_when_it_was_rendered(self, rendered, tmp_path):
        build(make_config(tmp_path, target="angular"), tmp_path, video=True)
        nav = json.loads((tmp_path / "generated" / "editor.nav.json").read_text(encoding="utf-8"))
        entry = next(n for n in nav if n.get("slug") == "invoices")
        assert entry["modes"] == ["stepbystep", "video"]


# ── Every rendering links the others ────────────────────────────────────────


class TestModeLinks:
    """The interactive and video pages `rel=canonical` at the step-by-step page,
    so a crawler is told where the real version is. A reader was not: the
    interactive page had no outbound link to it at all, and the video page had a
    hand-rolled one whose label did not match MODE_LABELS.
    """

    def _pages(self, tmp_path: Path, **kw):
        build(make_config(tmp_path, modes=("stepbystep", "interactive", "video")), tmp_path, **kw)
        guide = tmp_path / "generated" / "invoices"
        return {p.name: p.read_text(encoding="utf-8") for p in guide.glob("*.html")}

    def test_interactive_links_the_stepbystep_page(self, rendered, tmp_path):
        pages = self._pages(tmp_path, video=False)
        assert 'href="./fr.html"' in pages["interactive.html"]

    def test_interactive_marks_itself_current(self, rendered, tmp_path):
        pages = self._pages(tmp_path, video=False)
        html = pages["interactive.html"]
        assert '<span aria-current="true">Interactive</span>' in html
        assert 'href="./interactive.html"' not in html

    def test_video_links_both_siblings(self, rendered, tmp_path):
        pages = self._pages(tmp_path, video=True)
        html = pages["video.html"]
        assert 'href="./fr.html"' in html
        assert 'href="./interactive.html"' in html
        assert '<span aria-current="true">Video</span>' in html

    def test_interactive_links_the_video_once_it_exists(self, rendered, tmp_path):
        assert 'href="./video.html"' not in self._pages(tmp_path, video=False)["interactive.html"]
        assert 'href="./video.html"' in self._pages(tmp_path, video=True)["interactive.html"]

    def test_the_hand_rolled_video_link_is_gone(self, rendered, tmp_path):
        """It hard-coded "Step by step" while MODE_LABELS says "Step by Step",
        so the same control was labelled two ways on two pages."""
        html = self._pages(tmp_path, video=True)["video.html"]
        assert ">Step by step<" not in html
        assert ">Step by Step<" in html

    def test_every_rendering_carries_the_same_row(self, rendered, tmp_path):
        pages = self._pages(tmp_path, video=True)
        for name in ("fr.html", "interactive.html", "video.html"):
            assert "steppy-mode-links" in pages[name], name
            assert pages[name].count('aria-current="true"') >= 1, name
