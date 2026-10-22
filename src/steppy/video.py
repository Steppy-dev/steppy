"""Video export — deterministic frame-stepping over the interactive manifest.

Steppy does not screen-record anything. The interactive guide manifest already
carries every screenshot, cursor bbox and narration string; ``packages/player``
turns that into a timeline whose state at any instant is a pure function of the
inputs. This module walks that timeline frame by frame in headless Chromium and
pipes the stills to ffmpeg, so the same guide renders to the same video on a
laptop and on a throttled CI runner — which is the whole claim behind
"regenerated in CI on every release".

A **leaf module**: stdlib + ``models`` + ``_player_bundle`` only, and
``playwright`` is imported inside :func:`render_videos` so importing
``steppy.cli`` stays cheap (guard:
``tests/test_no_video_import_on_default_path.py``). It also never imports
``steppy.tts`` — narration arrives as durations and a finished WAV, computed
before the browser opens.

Four invariants worth keeping if you touch this:

* **The harness is written beside the page it belongs to.** Screenshot URLs in
  the manifest are relative, and Chromium resolves them against the document —
  so the harness lands in the same directory as the guide's own HTML, exactly
  as the PDF exporter loads over ``file://`` for the same reason.
* **The manifest is inlined, never fetched.** ``fetch`` on a ``file://`` page is
  blocked by the opaque-origin rule, so there is no version of this that reads
  ``guide.json`` over the wire.
* **ffmpeg's stderr goes to a file, never a pipe.** An undrained stderr pipe
  fills, ffmpeg blocks writing to it, stops reading stdin, and the frame loop
  deadlocks with no output and no error.
* **``reduced_motion`` is pinned.** The player's stylesheet disables every
  animation under ``prefers-reduced-motion``; a render host that emulates it
  would silently produce cursor-less video with nothing failing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from ._player_bundle import get_video_iife
from .models import VideoConfig


class VideoRenderError(Exception):
    """Fatal video-export failure — playwright, Chromium or ffmpeg unusable.

    Per-guide failures are *not* this: they land in a :class:`VideoResult` so
    one broken guide can't cost the other forty.
    """


@dataclass(frozen=True)
class VideoJob:
    """One (guide, locale, layout) video to render."""

    manifest: dict
    # Directory the harness is written to; relative screenshot URLs resolve here.
    page_dir: Path
    out: Path
    locale: str
    layout: Optional[str] = None
    title: str = ""
    subtitle: str = ""
    outro: str = ""
    stamp: str = ""
    logo_data_uri: str = ""
    # Narration: how long each step's voiceover runs (the timeline needs this up
    # front, to stretch the step so nothing is cut off) and where each clip is.
    # They are assembled into one track only once the timeline has decided where
    # each step actually starts — see ``assemble_audio`` on ``render_videos``.
    narration_ms: dict[str, int] = field(default_factory=dict)
    narration_clips: dict[str, Path] = field(default_factory=dict)

    @property
    def vtt(self) -> Path:
        return self.out.with_suffix(".vtt")

    @property
    def poster(self) -> Path:
        return self.out.with_suffix(".jpg")


@dataclass(frozen=True)
class VideoResult:
    job: VideoJob
    ok: bool
    error: Optional[str] = None
    duration_ms: int = 0
    frames: int = 0
    # Frames actually screenshotted; the rest reused the previous still.
    rendered_frames: int = 0
    cached: bool = False


_FFMPEG_HINT = (
    "ffmpeg is required for video export (even with tts.provider: none — a\n"
    "silent captioned video still has to be encoded). Install it with:\n"
    "  Windows:  winget install Gyan.FFmpeg\n"
    "  macOS:    brew install ffmpeg\n"
    "  Debian:   sudo apt-get install ffmpeg\n"
    "or point config.yaml's video.ffmpeg_path at an existing binary."
)

_BROWSER_HINT = "Chromium is required for video export. Install it with:\n  python -m playwright install chromium"

# Placeholders are substituted with str.replace, not str.format: the template
# carries CSS and JS, and brace-escaping a 40 KB bundle into a format string is
# a footgun with no upside.
_HARNESS = """<!doctype html>
<html><head><meta charset="utf-8"><title>steppy video</title>
<style>
  html, body { margin: 0; padding: 0; background: #f4f1ea; overflow: hidden; }
  #steppy-video { display: block; }
</style>
</head><body>
<div id="steppy-video"></div>
<script type="application/json" id="steppy-manifest">/*__STEPPY_MANIFEST__*/</script>
<script type="application/json" id="steppy-options">/*__STEPPY_OPTIONS__*/</script>
<script>/*__STEPPY_BUNDLE__*/</script>
<script>
  window.__steppyReady = (async () => {
    const manifest = JSON.parse(document.getElementById('steppy-manifest').textContent);
    const options = JSON.parse(document.getElementById('steppy-options').textContent);
    const stage = window.__STEPPY_VIDEO__.mount(manifest, options);
    await stage.ready();
    return {
      size: stage.size(),
      frameCount: stage.frameCount,
      durationMs: stage.durationMs,
      cues: stage.cues(),
      missing: stage.missingShots(),
    };
  })();
</script>
</body></html>
"""


def find_ffmpeg(options: VideoConfig) -> str:
    """Locate the ffmpeg binary, or explain how to get one.

    Deliberately *not* falling back to the ffmpeg Playwright bundles for its own
    webm recording: that build has no libx264, so using it would produce a file
    named ``.mp4`` that nothing can play — a worse failure than this one.
    """
    candidate = options.ffmpeg_path or "ffmpeg"
    found = shutil.which(candidate)
    if found:
        return found
    if os.path.isfile(candidate):
        return candidate
    raise VideoRenderError(f"ffmpeg not found (looked for {candidate!r}).\n{_FFMPEG_HINT}")


def ffmpeg_args(
    options: VideoConfig,
    *,
    ffmpeg: str,
    width: int,
    height: int,
    audio: Optional[Path],
    out: Path,
) -> list[str]:
    """Build the encoder command line.

    Split out from :func:`render_videos` so the mapping is testable without
    ffmpeg — the same reason ``pdf.pdf_options`` is its own function.
    """
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-framerate",
        str(options.fps),
        "-i",
        "-",
    ]
    if audio is not None:
        args += ["-i", str(audio)]
    args += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        options.preset,
        "-crf",
        str(options.crf),
        # The stage already reports even dimensions; this is belt-and-braces for
        # libx264, which refuses odd sizes outright under yuv420p.
        "-vf",
        f"scale={width}:{height},pad=ceil(iw/2)*2:ceil(ih/2)*2",
    ]
    if audio is not None:
        args += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    args += ["-movflags", "+faststart", str(out)]
    return args


def _vtt_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def build_vtt(cues: Sequence[dict]) -> str:
    """WebVTT captions from the timeline's cues.

    Nearly free once the timeline exists, and the difference between a video and
    an accessible, indexable, uploadable one.
    """
    lines = ["WEBVTT", ""]
    for i, cue in enumerate(cues, start=1):
        text = (cue.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{_vtt_timestamp(cue['startMs'])} --> {_vtt_timestamp(cue['endMs'])}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


# Config fields that cannot change a single pixel. Excluded from the render
# fingerprint so that setting an API key, moving the cache, or bumping a timeout
# does not re-encode an entire corpus. A **denylist** rather than an allowlist:
# a field added later is invalidating by default, which is the safe direction.
_NON_VISUAL_OPTIONS = frozenset({"tts", "cache", "cache_dir", "ffmpeg_path", "timeout"})


def job_fingerprint(
    job: VideoJob,
    options: VideoConfig,
    bundle_hash: str,
    hashes: Optional[dict[Path, str]] = None,
) -> str:
    """Content address for a render: same inputs ⇒ same video ⇒ skip the work.

    Includes the player bundle's hash, so a change to the animation code
    invalidates every cached video rather than silently shipping the old motion,
    and the **bytes** of every screenshot — the manifest only carries their URLs,
    so a recapture that leaves the target element where it was would otherwise
    reuse a video of the old UI.

    ``hashes`` memoizes file digests for the lifetime of one batch; a corpus is
    guides x locales x layouts over a shared pool of PNGs, so without it each
    file is read many times over.
    """
    if hashes is None:
        hashes = {}
    payload = {
        "manifest": job.manifest,
        "locale": job.locale,
        "layout": job.layout,
        "title": job.title,
        "subtitle": job.subtitle,
        "outro": job.outro,
        "stamp": job.stamp,
        "logo": hashlib.sha256(job.logo_data_uri.encode("utf-8")).hexdigest() if job.logo_data_uri else "",
        "narration": job.narration_ms,
        "audio": sorted((step, _file_hash(path, hashes)) for step, path in job.narration_clips.items()),
        "screenshots": _screenshot_hashes(job, hashes),
        "options": options.model_dump(mode="json", exclude=set(_NON_VISUAL_OPTIONS)),
        "bundle": bundle_hash,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def screenshot_urls(manifest: dict) -> list[str]:
    """Every screenshot the manifest references, in stable order.

    The shape is ``steps[].captures[locale][layout].frames[].screenshot`` (see
    ``guide.prepare_interactive_manifest``); a video manifest holds exactly one
    locale and one layout, but walking the whole structure costs nothing and
    keeps this honest if that ever changes.
    """
    urls: set[str] = set()
    for step in manifest.get("steps") or []:
        for by_layout in (step.get("captures") or {}).values():
            for capture in (by_layout or {}).values():
                for frame in (capture or {}).get("frames") or []:
                    url = frame.get("screenshot")
                    if url:
                        urls.add(str(url))
    return sorted(urls)


def _screenshot_hashes(job: VideoJob, hashes: dict[Path, str]) -> list[list[str]]:
    """Pair each screenshot URL with the digest of the file it resolves to.

    URLs in a video manifest are relative to ``page_dir`` by construction — that
    is the same property the harness relies on to load them over ``file://``.
    """
    out: list[list[str]] = []
    for url in screenshot_urls(job.manifest):
        out.append([url, _file_hash(_resolve_media(job.page_dir, url), hashes)])
    return out


def _resolve_media(page_dir: Path, url: str) -> Optional[Path]:
    if not url or "://" in url:
        return None
    try:
        return (page_dir / url.split("?", 1)[0].split("#", 1)[0]).resolve()
    except OSError:
        return None


def _file_hash(path: Optional[Path], hashes: Optional[dict[Path, str]] = None) -> str:
    if not path:
        return ""
    if hashes is not None and path in hashes:
        return hashes[path]
    digest = ""
    if path.is_file():
        acc = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                acc.update(chunk)
        digest = acc.hexdigest()
    if hashes is not None:
        hashes[path] = digest
    return digest


def _stage_options(job: VideoJob, options: VideoConfig) -> dict:
    return {
        "locale": job.locale,
        "layout": job.layout,
        "fps": options.fps,
        "width": options.width,
        "maxHeight": options.max_height,
        "captions": options.captions,
        "holdMs": options.hold_ms,
        "minStepMs": options.min_step_ms,
        "narrationPadMs": options.narration_pad_ms,
        "narrationMs": job.narration_ms,
        "introMs": options.intro_ms,
        "outroMs": options.outro_ms,
        "title": job.title,
        "subtitle": job.subtitle,
        "outroText": job.outro,
        "stamp": job.stamp,
        "logo": job.logo_data_uri or None,
        "accent": options.branding.accent,
        "fontFamily": options.branding.font_family,
        "imageTimeoutMs": min(options.timeout, 30000),
    }


def _default_warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def render_videos(
    jobs: Sequence[VideoJob],
    options: Optional[VideoConfig] = None,
    *,
    assemble_audio: Optional[Callable[[VideoJob, list, int, Path], Optional[Path]]] = None,
    cache_root: Optional[Path] = None,
    on_done: Optional[Callable[[VideoResult], None]] = None,
    on_progress: Optional[Callable[[VideoJob, int, int], None]] = None,
    on_warning: Optional[Callable[[str], None]] = None,
) -> list[VideoResult]:
    """Render every job, reusing one browser and one page for the batch.

    A guide project is a cartesian of guides x locales x layouts, so the batch is
    routinely in the dozens; a browser launch per video would dominate an already
    expensive operation.

    ``assemble_audio(job, cues, total_ms, dest)`` is called once the page has
    reported its timeline, and returns the narration track to mux (or None). It
    is injected rather than imported because this module must not depend on
    ``steppy.tts`` — and because the cue offsets it needs only exist after the
    browser has measured the stage.

    ``cache_root`` overrides ``options.cache_dir``, which is a bare relative path
    and so resolves against the process CWD. Callers pass it already resolved
    against the project root, the same rule ``config.output.*`` follows, so one
    project has one cache however the command was launched.

    ``on_done`` fires after each job and ``on_progress`` during one, so callers
    can stream progress. Returns one :class:`VideoResult` per job, in order.
    Raises :class:`VideoRenderError` only when the toolchain itself is unusable.
    """
    if not jobs:
        return []

    options = options or VideoConfig()
    warn = on_warning or _default_warn
    ffmpeg = find_ffmpeg(options)
    cache_dir = Path(cache_root) if cache_root else Path(options.cache_dir)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - playwright is a hard dep
        raise VideoRenderError(f"playwright is not installed: {exc}") from exc

    bundle = get_video_iife()
    bundle_hash = hashlib.sha256(bundle.encode("utf-8")).hexdigest()
    # One digest per file for the whole batch: a corpus is guides x locales x
    # layouts over a shared pool of screenshots.
    hashes: dict[Path, str] = {}

    results: list[VideoResult] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                raise VideoRenderError(f"Could not launch Chromium: {exc}\n{_BROWSER_HINT}") from exc
            try:
                # The player's stylesheet kills every animation under
                # prefers-reduced-motion. A host that emulates it would produce
                # cursor-less video with nothing failing anywhere.
                context = browser.new_context(reduced_motion="no-preference")
                page = context.new_page()
                for job in jobs:
                    result = _render_one(
                        page,
                        job,
                        options,
                        ffmpeg,
                        bundle,
                        bundle_hash,
                        warn,
                        on_progress,
                        assemble_audio,
                        cache_dir,
                        hashes,
                    )
                    results.append(result)
                    if on_done:
                        on_done(result)
            finally:
                browser.close()
    except VideoRenderError:
        raise
    except Exception as exc:  # driver startup / teardown failures
        raise VideoRenderError(f"Video export failed: {exc}\n{_BROWSER_HINT}") from exc

    return results


def _render_one(
    page,
    job: VideoJob,
    options: VideoConfig,
    ffmpeg: str,
    bundle: str,
    bundle_hash: str,
    warn: Callable[[str], None],
    on_progress: Optional[Callable[[VideoJob, int, int], None]],
    assemble_audio: Optional[Callable[[VideoJob, list, int, Path], Optional[Path]]] = None,
    cache_dir: Optional[Path] = None,
    hashes: Optional[dict[Path, str]] = None,
) -> VideoResult:
    """Render a single job. Never raises — failures come back as a result."""
    harness: Optional[Path] = None
    audio_dir: Optional[tempfile.TemporaryDirectory] = None
    staging: Optional[Path] = None
    try:
        job.out.parent.mkdir(parents=True, exist_ok=True)

        root = Path(cache_dir) if cache_dir is not None else Path(options.cache_dir)
        cache_key = job_fingerprint(job, options, bundle_hash, hashes) if options.cache else ""
        cached = (root / f"{cache_key}.mp4") if cache_key else None
        if cached and _cache_entry_is_usable(cached):
            _touch_entry(cached)
            shutil.copyfile(cached, job.out)
            _copy_sidecars(cached, job)
            return VideoResult(job=job, ok=True, cached=True)

        job.page_dir.mkdir(parents=True, exist_ok=True)
        harness = _write_harness(job, options, bundle)

        page.goto(harness.resolve().as_uri(), wait_until="load", timeout=options.timeout)
        info = page.evaluate("() => window.__steppyReady")

        missing = info.get("missing") or []
        if missing:
            # A gappy video still beats no video, and naming the files is what
            # turns "the middle is blank" into a one-line fix.
            warn(
                f"{job.out.name}: {len(missing)} screenshot(s) did not load "
                f"(first: {missing[0]}). The video will show gaps."
            )

        size = info["size"]
        width, height = int(size["width"]), int(size["height"])
        frame_count = int(info["frameCount"])
        duration_ms = int(info.get("durationMs") or 0)
        cues = info.get("cues") or []
        page.set_viewport_size({"width": width, "height": height})

        # Now — and only now — the timeline has said where each step starts, so
        # the narration can be laid against it.
        audio: Optional[Path] = None
        if assemble_audio is not None and job.narration_clips:
            audio_dir = tempfile.TemporaryDirectory(prefix="steppy-audio-")
            audio = assemble_audio(job, cues, duration_ms, Path(audio_dir.name) / "narration.wav")

        # ffmpeg never writes the final path directly. A killed encode would
        # otherwise leave a truncated file *at the cache key*, and every later
        # run would copy it out and report a hit — the failure is silent, and
        # permanent. Stage beside the target, then replace in one step.
        target = cached if cached else job.out
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = _staging_path(target)
        rendered = _pipe_frames(page, job, options, ffmpeg, width, height, frame_count, staging, on_progress, audio)
        os.replace(str(staging), str(target))
        staging = None

        # Always write the track, even when every cue is empty. The page that
        # embeds this video advertises a ``<track src=...>`` decided at build
        # time, long before the render knows whether there is anything to say —
        # so "no captions" has to be an empty track, not a missing file.
        job.vtt.write_text(build_vtt(cues), encoding="utf-8")

        if cached:
            shutil.copyfile(cached, job.out)
            # Seed the sidecars too, so a later cache hit restores the whole set
            # rather than an .mp4 with no captions and no poster.
            seeded = []
            if job.vtt.is_file():
                _cache_sidecar(cached, ".vtt").write_text(job.vtt.read_text(encoding="utf-8"), encoding="utf-8")
                seeded.append(".vtt")
            if job.poster.is_file():
                _cache_sidecar(cached, ".jpg").write_bytes(job.poster.read_bytes())
                seeded.append(".jpg")
            # Record what the entry is supposed to carry. Without it, an entry
            # whose sidecars were lost is indistinguishable from one that never
            # had them — and the first case must re-render, not ship silently.
            _cache_sidecar(cached, ".meta").write_text(
                json.dumps({"sidecars": seeded}) + "\n",
                encoding="utf-8",
            )

        return VideoResult(
            job=job,
            ok=True,
            duration_ms=duration_ms,
            frames=frame_count,
            rendered_frames=rendered,
        )
    except Exception as exc:
        return VideoResult(job=job, ok=False, error=str(exc))
    finally:
        if harness is not None:
            harness.unlink(missing_ok=True)
        if audio_dir is not None:
            audio_dir.cleanup()
        if staging is not None:
            staging.unlink(missing_ok=True)


def _pipe_frames(
    page,
    job: VideoJob,
    options: VideoConfig,
    ffmpeg: str,
    width: int,
    height: int,
    frame_count: int,
    out: Path,
    on_progress: Optional[Callable[[VideoJob, int, int], None]],
    audio: Optional[Path] = None,
) -> int:
    """Step every frame into ffmpeg's stdin. Returns the count actually captured.

    Frames are JPEG rather than PNG: the output is lossy H.264 either way, so PNG
    buys nothing and costs several times more in encode and CDP transfer. A seek
    that reports no pixel change reuses the previous buffer instead of taking
    another screenshot — holds and voiceover pauses dominate a narrated video, so
    this is the difference between minutes and hours across a real corpus.
    """
    args = ffmpeg_args(options, ffmpeg=ffmpeg, width=width, height=height, audio=audio, out=out)

    # stderr to a temp file, never a pipe: an undrained pipe fills, ffmpeg blocks
    # writing to it, stops reading stdin, and this loop hangs forever.
    with tempfile.TemporaryFile() as errfile:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=errfile,
        )
        rendered = 0
        last: bytes = b""
        # Poster = the first frame past the intro card. Grabbing frame 0 would
        # make every thumbnail on the site the same title screen.
        poster_at = min(frame_count - 1, -(-options.intro_ms * options.fps // 1000))
        try:
            assert proc.stdin is not None
            for index in range(frame_count):
                dirty = page.evaluate("(i) => window.__STEPPY_VIDEO__.stage.seek(i)", index)
                if dirty or not last:
                    last = page.screenshot(
                        type="jpeg",
                        quality=options.jpeg_quality,
                        animations="disabled",
                    )
                    rendered += 1
                if index == poster_at and last:
                    job.poster.parent.mkdir(parents=True, exist_ok=True)
                    job.poster.write_bytes(last)
                proc.stdin.write(last)
                if on_progress:
                    on_progress(job, index + 1, frame_count)
            proc.stdin.close()
            code = proc.wait(timeout=max(60, options.timeout // 1000))
        except BaseException:
            # An orphaned ffmpeg keeps the output file open, and the next build's
            # rmtree of output/ then fails on Windows.
            proc.kill()
            proc.wait(timeout=10)
            raise
        if code != 0:
            errfile.seek(0)
            tail = errfile.read().decode("utf-8", "replace").strip().splitlines()[-8:]
            raise RuntimeError(f"ffmpeg exited {code}: " + " / ".join(tail))
    return rendered


def _write_harness(job: VideoJob, options: VideoConfig, bundle: str) -> Path:
    """Write the throwaway render page next to the guide it belongs to.

    It must live in ``page_dir``: the manifest's screenshot URLs are relative to
    the guide's own HTML, and Chromium resolves them against the document.
    """
    html = (
        _HARNESS.replace("/*__STEPPY_MANIFEST__*/", json.dumps(job.manifest, ensure_ascii=False))
        .replace("/*__STEPPY_OPTIONS__*/", json.dumps(_stage_options(job, options), ensure_ascii=False))
        .replace("/*__STEPPY_BUNDLE__*/", bundle)
    )
    fd, name = tempfile.mkstemp(prefix=".steppy-video-", suffix=".html", dir=str(job.page_dir))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(html)
    return Path(name)


def _cache_path(options: VideoConfig, key: str) -> Path:
    """Cached renders live under ``.steppy/``, never ``output/``.

    ``steppy build`` wipes its output directory on every run, so a sidecar next
    to the artifact would be gone before it could ever be a hit.
    """
    return Path(options.cache_dir) / f"{key}.mp4"


def _staging_path(target: Path) -> Path:
    """A temp path beside *target*.

    ``os.replace`` is only atomic within one filesystem, so the system temp dir
    will not do. Pid-suffixed so two builds sharing a cache stage into different
    files — and it **keeps the real extension**, because ffmpeg picks its muxer
    from the output filename and would refuse a bare ``.tmp``.
    """
    return target.with_name(f".{target.stem}.{os.getpid()}.tmp{target.suffix}")


def _cache_entry_is_usable(cached: Path) -> bool:
    """Is this entry a real hit, or the wreckage of a killed run?

    Cheap checks only — the writes are atomic now, so this is a backstop for
    entries left by an older steppy and for a sidecar set that went missing.
    Serving a cache entry whose captions vanished is worse than re-rendering:
    the video ships with a ``<track>`` pointing at nothing and nobody is told.
    """
    try:
        if not cached.is_file() or cached.stat().st_size == 0:
            return False
    except OSError:
        return False

    meta = _cache_sidecar(cached, ".meta")
    if not meta.is_file():
        # Written by an older steppy: no record of what the entry should carry,
        # so accept it rather than invalidate every cache in the wild.
        return True
    try:
        expected = json.loads(meta.read_text(encoding="utf-8")).get("sidecars") or []
    except (ValueError, OSError):
        return True
    return all(_cache_sidecar(cached, suffix).is_file() for suffix in expected)


def _touch_entry(cached: Path) -> None:
    """Mark an entry as used, so ``steppy video cache`` can evict by LRU.

    Without it a video rebuilt into every release looks exactly as stale as one
    whose guide was deleted a year ago. Best-effort — a read-only cache is
    still a usable one.
    """
    for suffix in (".mp4", ".vtt", ".jpg", ".meta"):
        path = _cache_sidecar(cached, suffix)
        try:
            if path.is_file():
                path.touch()
        except OSError:
            pass


def _cache_sidecar(cached: Path, suffix: str) -> Path:
    return cached.with_suffix(suffix)


def _copy_sidecars(cached: Path, job: VideoJob) -> None:
    vtt = _cache_sidecar(cached, ".vtt")
    if vtt.is_file():
        job.vtt.write_text(vtt.read_text(encoding="utf-8"), encoding="utf-8")
    poster = _cache_sidecar(cached, ".jpg")
    if poster.is_file():
        job.poster.write_bytes(poster.read_bytes())
