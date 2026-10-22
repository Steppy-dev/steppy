"""Content-addressed clip cache.

Not an optimisation — a correctness-of-cost feature. A cloud provider bills per
character, and a guide corpus re-synthesized on every CI run would bill for
text that has not changed since the last release. With the cache, only edited
narration costs anything.

Lives under ``.steppy/``, never ``output/``, for the same reason ``coverage.db``
and ``history.jsonl`` do: ``steppy build`` wipes its output directory on every
run, so a cache there would never survive to be a hit.

**Every write lands atomically.** A clip is written to a sibling temp file and
``os.replace``d into position, because the failure mode otherwise is permanent:
a Ctrl-C or a full disk leaves a truncated WAV *at the final key path*, and a
lookup that only asks ``is_file()`` will serve it forever.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .types import SpeechClip, SpeechRequest


@dataclass(frozen=True)
class CachedClip:
    path: Path
    fmt: str
    duration_ms: Optional[int]


def cache_key(request: SpeechRequest, provider: str, version: str) -> str:
    """Everything that can change the audio, and nothing that cannot.

    ``version`` is load-bearing for the local engines: espeak-ng and Piper
    produce different audio across builds, so an unversioned key would serve a
    stale clip whose duration no longer matches the timeline the video was
    rendered against. For the HTTP engine it also carries a hash of the request
    shape, so editing ``video.tts.http`` re-synthesizes rather than serving
    audio the old body produced.

    ``request.text`` is already whitespace-normalized by ``synthesize_steps``,
    so re-wrapping a YAML block scalar is not a cache miss.
    """
    payload = {
        "provider": provider,
        "version": version,
        "text": request.text,
        "locale": request.locale,
        "voice": request.voice or "",
        "model": request.model or "",
        "speed": round(float(request.speed), 4),
        "fmt": request.fmt or "",
        "options": request.options or {},
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def atomic_replace(source: Path, dest: Path) -> Path:
    """Move *source* onto *dest* in one indivisible step.

    ``os.replace`` is atomic on NTFS and POSIX alike **as long as both paths sit
    on the same filesystem** — which is why callers stage their temp file in
    ``dest.parent`` rather than the system temp dir.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(source), str(dest))
    return dest


def atomic_write_bytes(dest: Path, data: bytes) -> Path:
    return _atomic_write(dest, lambda handle: handle.write(data))


def atomic_write_text(dest: Path, text: str) -> Path:
    return _atomic_write(dest, lambda handle: handle.write(text.encode("utf-8")))


def _atomic_write(dest: Path, write) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            write(handle)
        return atomic_replace(Path(name), dest)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _touch(path: Path) -> None:
    """Mark an entry as used, so LRU eviction means what it says.

    Without this a clip reused daily for a year looks exactly as stale as one
    abandoned on day two. Best-effort: a read-only cache is still a usable one.
    """
    try:
        path.touch()
    except OSError:
        pass


class ClipCache:
    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled

    def _paths(self, key: str, fmt: str) -> tuple[Path, Path]:
        return self.root / f"{key}.{fmt}", self.root / f"{key}.json"

    def get(self, key: str, fmt: str) -> Optional[CachedClip]:
        if not self.enabled:
            return None
        audio, meta = self._paths(key, fmt)
        if not audio.is_file():
            return None
        duration = None
        if meta.is_file():
            try:
                duration = json.loads(meta.read_text(encoding="utf-8")).get("duration_ms")
            except (ValueError, OSError):
                # A torn sidecar just means we re-measure; the audio is still good.
                duration = None
            _touch(meta)
        _touch(audio)
        return CachedClip(path=audio, fmt=fmt, duration_ms=duration)

    def put(self, key: str, clip: SpeechClip, duration_ms: Optional[int] = None) -> Optional[CachedClip]:
        """Store raw provider bytes. Returns None when the cache is disabled —
        there is no path to hand back, because nothing was written."""
        if not self.enabled:
            return None
        audio, _ = self._paths(key, clip.fmt)
        atomic_write_bytes(audio, clip.audio)
        self.set_duration(key, clip.fmt, duration_ms)
        return CachedClip(path=audio, fmt=clip.fmt, duration_ms=duration_ms)

    def commit(self, key: str, source: Path, fmt: str = "wav") -> Path:
        """Adopt an already-produced file as this key's cache entry.

        The normalize step writes into the cache directory directly (so the
        transcode is not paid twice), then hands the temp file here to be moved
        into position — the move being the only moment the entry becomes
        visible to a lookup.
        """
        if not self.enabled:
            return source
        audio, _ = self._paths(key, fmt)
        if source == audio:
            return audio
        return atomic_replace(source, audio)

    def set_duration(self, key: str, fmt: str, duration_ms: Optional[int]) -> None:
        if not self.enabled:
            return
        _, meta = self._paths(key, fmt)
        atomic_write_text(
            meta,
            json.dumps({"duration_ms": duration_ms, "format": fmt}, ensure_ascii=False) + "\n",
        )

    def discard(self, key: str, fmt: str) -> None:
        """Drop a poisoned entry so the next lookup is an honest miss."""
        for path in self._paths(key, fmt):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def staging_path(self, key: str, fmt: str) -> Path:
        """Where a producer should write before calling :meth:`commit`.

        Same directory as the final entry, so ``os.replace`` stays atomic; and
        pid-suffixed, so two concurrent builds sharing one cache stage into
        different files and the loser's replace is merely redundant.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / f".{key}.{fmt}.{os.getpid()}.tmp"
