"""Narration track assembly — per-step clips into one WAV, on the stdlib.

The video timeline decides when each step starts; this lays the matching clip
at that offset and pads the gaps with silence, so the voiceover stays locked to
the cursor no matter how the timeline stretched to accommodate it.

Assembly uses the stdlib ``wave`` module rather than an ffmpeg filter graph:
concatenating PCM is arithmetic, it is deterministic, and it unit-tests without
a subprocess. ffmpeg is still used for the one thing it is needed for —
normalising whatever container a cloud provider returned (usually MP3) into the
PCM this works on.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional, Sequence

from .cache import atomic_replace, atomic_write_bytes
from .types import TtsError

# One target format for everything, so concatenation is a byte-copy.
SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def wav_duration_ms(path: Path) -> int:
    """Length of a WAV file, from its own header."""
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate() or SAMPLE_RATE
    return int(round(frames * 1000 / rate))


def is_intact_wav(path: Path) -> bool:
    """Does this file actually contain the audio its header promises?

    A header read alone is not enough to trust a cached clip: a truncated write
    usually leaves the 44-byte header intact, so ``getnframes()`` still reports
    the *intended* length and the timeline gets sized against audio that is not
    there. Comparing the declared payload against the bytes on disk is what
    turns a poisoned entry into an honest miss.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            declared = handle.getnframes() * handle.getnchannels() * handle.getsampwidth()
        # A zero-frame clip is intact, just useless. Rejecting it would make an
        # engine that returns silence for one line re-bill for it every run.
        return path.stat().st_size >= declared
    except (wave.Error, OSError, EOFError):
        return False


def normalize(source: Path, dest: Path, ffmpeg: str) -> Path:
    """Transcode any clip into 48 kHz mono 16-bit PCM WAV.

    Piper and espeak already emit WAV, so the free path usually skips this
    entirely — but a cloud provider's MP3 has to become PCM before it can be
    laid onto a timeline by arithmetic.

    ``dest`` only ever appears complete. ffmpeg writes a sibling temp file that
    is ``os.replace``d into position on success, because *dest* is routinely a
    cache path: a half-written file there is not a slow build, it is a
    permanently poisoned entry that every later run happily serves.
    """
    if _is_target_wav(source):
        if source != dest:
            atomic_write_bytes(dest, source.read_bytes())
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".wav")
    os.close(fd)
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        "-sample_fmt",
        "s16",
        staging,
    ]
    try:
        proc = subprocess.run(args, capture_output=True)
        if proc.returncode != 0:
            tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-4:]
            raise TtsError(f"could not convert {source.name} to WAV: " + " / ".join(tail))
        return atomic_replace(Path(staging), dest)
    except BaseException:
        Path(staging).unlink(missing_ok=True)
        raise


def _is_target_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as handle:
            return (
                handle.getframerate() == SAMPLE_RATE
                and handle.getnchannels() == CHANNELS
                and handle.getsampwidth() == SAMPLE_WIDTH
            )
    except (wave.Error, OSError):
        return False


def silence(duration_ms: int) -> bytes:
    frames = max(0, int(round(duration_ms * SAMPLE_RATE / 1000)))
    return b"\x00" * (frames * CHANNELS * SAMPLE_WIDTH)


def assemble(clips: Sequence[tuple[int, Optional[Path]]], total_ms: int, dest: Path) -> Optional[Path]:
    """Lay each clip at its offset and pad the rest with silence.

    ``clips`` is ``(start_ms, wav_path | None)`` in timeline order. Returns None
    when there is nothing to say — the caller then renders a silent video rather
    than muxing an empty track.

    A clip that would overrun the next one is **truncated, not dropped**: the
    timeline already stretched each step to fit its narration, so an overrun
    means something upstream disagreed, and a clipped last syllable is a far
    better failure than losing a whole step's voiceover.
    """
    usable = [(start, path) for start, path in clips if path and path.is_file()]
    if not usable:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(SAMPLE_RATE)

        cursor_ms = 0
        for start_ms, path in sorted(usable, key=lambda item: item[0]):
            if start_ms > cursor_ms:
                out.writeframes(silence(start_ms - cursor_ms))
                cursor_ms = start_ms
            with wave.open(str(path), "rb") as clip:
                frames = clip.readframes(clip.getnframes())
            out.writeframes(frames)
            cursor_ms += int(round(len(frames) / (CHANNELS * SAMPLE_WIDTH) * 1000 / SAMPLE_RATE))

        if total_ms > cursor_ms:
            out.writeframes(silence(total_ms - cursor_ms))
    return dest
