"""Text-to-speech for video narration — provider-agnostic by construction.

The whole point of this package is that ``video.tts.provider`` is the only thing
that has to change to swap engines: ``none`` (the default: captions, no audio),
``piper`` or ``espeak`` (free, offline), a vendor preset (``elevenlabs``,
``openai``, ``azure``, ``google``), a hand-written REST shape under
``video.tts.http``, or a third-party plugin registered through the
``steppy.tts_providers`` entry point. No code path branches on vendor.

Narration is synthesized **before** the browser opens, because the video
timeline needs each clip's duration to know how long to hold its step. So this
package never imports ``steppy.video``, and ``steppy.video`` never imports this
one — they meet at a dict of durations and a finished WAV.

Synthesis is **per step**, and the cache is keyed on the text rather than on the
step's position, so editing one sentence re-voices one sentence: unchanged steps
hit, reordered steps hit, and the same sentence in two guides shares one clip.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .cache import ClipCache, cache_key
from .registry import available_providers, describe, readiness, resolve
from .types import SpeechClip, SpeechRequest, TtsError, TtsProvider

__all__ = [
    "ClipCache",
    "SpeechClip",
    "SpeechRequest",
    "TtsError",
    "TtsProvider",
    "Utterance",
    "Narration",
    "assemble_track",
    "available_providers",
    "cache_key",
    "describe",
    "normalize_narration",
    "readiness",
    "resolve",
    "synthesize_steps",
    "voice_jobs",
]


@dataclass(frozen=True)
class Utterance:
    """One step's narration."""

    step_id: str
    text: str


@dataclass(frozen=True)
class Narration:
    """Per-step clips and durations, ready for the timeline and the muxer."""

    durations_ms: dict[str, int]
    clips: dict[str, Path]
    provider: str

    @property
    def silent(self) -> bool:
        return not self.clips


def normalize_narration(text: Optional[str]) -> str:
    """Collapse authoring whitespace, so re-wrapping a line is not a re-bill.

    ``guide-text`` is YAML, and a hard wrap moved by one word changes the string
    without changing a syllable of the audio. Normalizing the *request* rather
    than the *key* is deliberate: were only the key normalized, two differently
    wrapped texts would collide on whichever clip synthesized first, and the
    audio would no longer correspond to the text that was sent.
    """
    return " ".join((text or "").split())


def synthesize_steps(
    utterances: Sequence[Utterance],
    cfg,
    *,
    locale: str,
    work_dir: Path,
    ffmpeg: str,
    cache_root: Optional[Path] = None,
    memo: Optional[dict[str, tuple[Path, int]]] = None,
    on_warning: Optional[Callable[[str], None]] = None,
) -> Narration:
    """Synthesize every step's narration for one locale.

    Returns durations keyed by step id — which is what the video timeline needs
    up front, to stretch each step so its voiceover is never cut off.

    ``cache_root`` overrides ``cfg.cache_dir`` and should be an **absolute**
    path resolved against the project root; the bare config value is relative to
    the CWD, so a build launched from a subdirectory would otherwise miss a warm
    cache and re-bill for it. ``memo`` is a process-local key→path map shared
    across a batch, so N layouts of one guide synthesize once even when the
    on-disk cache is disabled.

    A provider failure is reported and downgraded to silence rather than raised:
    losing the voiceover is a bad outcome, losing the release is a worse one.
    """
    from .track import normalize, wav_duration_ms

    resolved = resolve(cfg)
    if resolved.name == "none":
        return Narration(durations_ms={}, clips={}, provider="none")

    cache = ClipCache(Path(cache_root or cfg.cache_dir), enabled=cfg.cache)
    version = _safe_version(resolved.provider)
    durations: dict[str, int] = {}
    clips: dict[str, Path] = {}

    for utterance in utterances:
        text = normalize_narration(utterance.text)
        if not text:
            continue
        request = SpeechRequest(
            text=text,
            locale=locale,
            voice=cfg.voice_for(locale),
            model=cfg.model,
            speed=cfg.speed,
            fmt=cfg.format,
            options=dict(cfg.options or {}),
        )
        key = cache_key(request, resolved.name, version)
        try:
            wav, duration = _clip_for(request, key, cache, resolved, work_dir, ffmpeg, normalize, memo)
        except TtsError as exc:
            if on_warning:
                on_warning(f"narration for '{utterance.step_id}' ({locale}) failed: {exc}")
            continue
        if wav is None:
            continue
        if duration is None:
            try:
                duration = wav_duration_ms(wav)
            except Exception as exc:  # a clip we just produced and cannot read
                if on_warning:
                    on_warning(f"narration for '{utterance.step_id}' ({locale}) is unreadable: {exc}")
                continue
            cache.set_duration(key, "wav", duration)
        durations[utterance.step_id] = duration
        clips[utterance.step_id] = wav
        if memo is not None:
            memo[key] = (wav, duration)

    return Narration(durations_ms=durations, clips=clips, provider=resolved.name)


def _clip_for(request, key, cache, resolved, work_dir, ffmpeg, normalize, memo) -> tuple[Optional[Path], Optional[int]]:
    """Fetch from the memo, then the cache, else synthesize.

    Always returns normalized WAV, plus the clip's duration when it was already
    known. A cached clip that will not open is treated as a **miss**: the entry
    is dropped and re-synthesized, because a truncated WAV left by a killed run
    is otherwise a permanent hit whose duration no longer matches the timeline
    the video was rendered against.
    """
    from .track import is_intact_wav, wav_duration_ms

    if memo is not None:
        remembered = memo.get(key)
        if remembered is not None and remembered[0].is_file():
            return remembered

    hit = cache.get(key, "wav")
    if hit is not None:
        if is_intact_wav(hit.path):
            return hit.path, hit.duration_ms or wav_duration_ms(hit.path)
        cache.discard(key, "wav")

    clip = resolved.provider.synthesize(request)
    if clip.empty:
        return None, None

    # ``.raw.`` keeps the pre-transcode container from ever colliding with the
    # normalized artifact, which matters when the provider already returned WAV.
    work_dir.mkdir(parents=True, exist_ok=True)
    raw = work_dir / f"{key}.raw.{clip.fmt}"
    raw.write_bytes(clip.audio)
    try:
        if not cache.enabled:
            return normalize(raw, work_dir / f"{key}.wav", ffmpeg), None
        staging = cache.staging_path(key, "wav")
        try:
            normalize(raw, staging, ffmpeg)
            return cache.commit(key, staging, "wav"), None
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
    finally:
        # The normalized WAV is the artifact; the raw container is scratch, and
        # leaving it behind is what made ``tts-cache/work/`` grow forever.
        raw.unlink(missing_ok=True)


def _safe_version(provider) -> str:
    try:
        return str(provider.version())
    except Exception:  # pragma: no cover - a provider must not break on this
        return "?"


# ── Job-level helpers, shared by ``steppy build`` and ``steppy guide`` ───────


def voice_jobs(
    jobs: Sequence,
    cfg,
    *,
    ffmpeg: str,
    cache_root: Path,
    work_dir: Path,
    on_warning: Optional[Callable[[str], None]] = None,
) -> list:
    """Voice every job's steps before any browser opens.

    The timeline needs each clip's *duration* up front, to stretch its step so
    the voiceover is never cut off — which is why this cannot wait until render
    time. Where each clip is *placed* is a separate question, decided by the
    timeline and answered by :func:`assemble_track`.

    One home for both entry points: ``steppy build --video`` and
    ``steppy guide --video`` had byte-identical copies of this, and they had
    already drifted apart on cache-path resolution. The batch shares one memo,
    so the desktop and mobile jobs of one guide voice its steps once.

    With ``tts.provider: none`` (the shipped default) this is a no-op.
    """
    from dataclasses import replace

    if cfg.provider == "none":
        return list(jobs)

    memo: dict[str, tuple[Path, int]] = {}
    voiced: list = []
    for job in jobs:
        narration = synthesize_steps(
            [
                Utterance(step_id=step["id"], text=(step.get("text") or {}).get(job.locale) or "")
                for step in job.manifest["steps"]
            ],
            cfg,
            locale=job.locale,
            work_dir=work_dir,
            ffmpeg=ffmpeg,
            cache_root=cache_root,
            memo=memo,
            on_warning=on_warning,
        )
        voiced.append(
            job
            if narration.silent
            else replace(job, narration_ms=narration.durations_ms, narration_clips=narration.clips)
        )
    return voiced


def assemble_track(job, cues: list, total_ms: int, dest: Path) -> Optional[Path]:
    """Lay each step's clip at the offset the timeline gave that step.

    Injected into ``render_videos`` rather than called from it: ``video.py`` is a
    leaf module that must not import ``steppy.tts``, and the placement rule
    genuinely belongs to whoever owns the narration.
    """
    if not job.narration_clips:
        return None

    from .track import assemble

    placed = [(int(cue["startMs"]), job.narration_clips.get(cue["stepId"])) for cue in cues]
    return assemble(placed, total_ms=total_ms, dest=dest)
