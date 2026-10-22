"""Offline text-to-speech providers: Piper (neural) and espeak-ng (formant).

Both are driven as **subprocesses**, never imported. For espeak that is simply
how it ships; for Piper it is a licensing boundary worth stating plainly:
`piper1-gpl <https://github.com/OHF-Voice/piper1-gpl>`_ is GPL-3.0, and Steppy
is headed for a permissive license. Running ``python -m piper`` is arms-length
use of a tool the user installed — the same posture already taken with ffmpeg —
whereas ``from piper import PiperVoice`` would make Steppy a derivative work.
For the same reason Piper is deliberately **not** a ``pyproject.toml`` extra:
users install it themselves with ``pip install piper-tts``.

(The archived ``rhasspy/piper`` repo is not this. Development moved to
OHF-Voice under the Open Home Foundation in late 2025; voice names and the
``download_voices`` module below are that project's.)

Both engines emit WAV, which is the format the narration assembler wants — so
the free path never pays a transcode.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..types import SpeechClip, SpeechRequest, TtsError


class PiperProvider:
    """Free, offline, neural. Needs a per-language voice model on disk."""

    name = "piper"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.python = cfg.piper.python or sys.executable
        self.data_dir = Path(cfg.piper.data_dir)

    def version(self) -> str:
        return _piper_version(self.python)

    def synthesize(self, request: SpeechRequest) -> SpeechClip:
        voice = request.voice
        if not voice:
            raise TtsError(
                "video.tts.voice is required for the piper provider. Voices are "
                "per-language, so use the map form:\n"
                "  voice: { fr: fr_FR-siwis-medium, nl: nl_NL-mls-medium }\n"
                "Browse them at https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md"
            )
        self._ensure_voice(voice)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.wav"
            args = [
                self.python,
                "-m",
                "piper",
                "-m",
                voice,
                "--data-dir",
                str(self.data_dir),
                "-f",
                str(out),
            ]
            # Piper's length_scale is inverse to speed: slower speech is longer.
            if request.speed and request.speed != 1.0:
                args += ["--length-scale", f"{1.0 / request.speed:.4f}"]
            for flag in ("sentence_silence", "volume"):
                value = (request.options or {}).get(flag)
                if value is not None:
                    args += [f"--{flag.replace('_', '-')}", str(value)]
            args += ["--", request.text]

            _run(args, what="piper", hint=_PIPER_HINT, timeout=self.cfg.timeout / 1000)
            if not out.is_file():
                raise TtsError("piper produced no audio file")
            return SpeechClip(audio=out.read_bytes(), fmt="wav")

    def _ensure_voice(self, voice: str) -> None:
        if (self.data_dir / f"{voice}.onnx").is_file():
            return
        if not self.cfg.piper.download:
            raise TtsError(
                f"piper voice '{voice}' is not in {self.data_dir} and "
                "video.tts.piper.download is false. Fetch it with:\n"
                f"  {self.python} -m piper.download_voices --data-dir {self.data_dir} {voice}"
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _run(
            [self.python, "-m", "piper.download_voices", "--data-dir", str(self.data_dir), voice],
            what="piper.download_voices",
            hint=_PIPER_HINT,
            # A voice model is tens of MB; the per-clip budget is far too small.
            timeout=600,
        )


class EspeakProvider:
    """Free, offline, zero-download — and audibly robotic.

    Ships as the honest fallback for a machine that cannot spare a voice model:
    fine for a CI smoke test, not for a guide a customer watches.
    """

    name = "espeak"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def version(self) -> str:
        return _espeak_version()

    def synthesize(self, request: SpeechRequest) -> SpeechClip:
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            raise TtsError(
                "espeak-ng is not installed. Install it with:\n"
                "  Windows:  winget install espeak-ng.espeak-ng\n"
                "  macOS:    brew install espeak-ng\n"
                "  Debian:   sudo apt-get install espeak-ng"
            )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.wav"
            # espeak's default is ~175 wpm; scale it rather than exposing raw wpm.
            args = [binary, "-w", str(out), "-s", str(int(175 * request.speed))]
            voice = request.voice or request.locale
            if voice:
                args += ["-v", voice]
            args += [request.text]
            _run(args, what="espeak-ng", hint="", timeout=self.cfg.timeout / 1000)
            if not out.is_file():
                raise TtsError("espeak-ng produced no audio file")
            return SpeechClip(audio=out.read_bytes(), fmt="wav")


_PIPER_HINT = (
    "Install it with:\n"
    "  pip install piper-tts\n"
    "It is not a Steppy dependency on purpose: piper1-gpl is GPL-3.0, so Steppy "
    "runs it as a separate program rather than linking it."
)


def _run(args: list[str], *, what: str, hint: str, timeout: float) -> None:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=max(10.0, timeout))
    except FileNotFoundError as exc:
        raise TtsError(f"{what} is not available: {exc}.\n{hint}".rstrip()) from exc
    except subprocess.TimeoutExpired as exc:
        raise TtsError(f"{what} timed out after {exc.timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-5:]
        raise TtsError(f"{what} exited {proc.returncode}: " + " / ".join(tail))


@lru_cache(maxsize=4)
def _piper_version(python: str) -> str:
    """Fold the engine version into the cache key.

    Different Piper builds produce different audio, so clip durations differ,
    so the whole video timeline shifts — with no other signal that anything
    changed.
    """
    try:
        proc = subprocess.run(
            [python, "-c", "import piper,sys; sys.stdout.write(getattr(piper,'__version__','?'))"],
            capture_output=True,
            timeout=30,
        )
        return proc.stdout.decode("utf-8", "replace").strip() or "?"
    except Exception:
        return "?"


@lru_cache(maxsize=1)
def _espeak_version() -> str:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        return "?"
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, timeout=20)
        return proc.stdout.decode("utf-8", "replace").strip()[:80] or "?"
    except Exception:
        return "?"


def build_piper(cfg) -> PiperProvider:
    return PiperProvider(cfg)


def build_espeak(cfg) -> EspeakProvider:
    return EspeakProvider(cfg)


def probe(provider: str, cfg) -> Optional[str]:
    """One-line readiness report for ``steppy video check``."""
    if provider == "piper":
        version = _piper_version(cfg.piper.python or sys.executable)
        return None if version != "?" else "piper is not installed (pip install piper-tts)"
    if provider == "espeak":
        return None if _espeak_version() != "?" else "espeak-ng is not on PATH"
    return None
