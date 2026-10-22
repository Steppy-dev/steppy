"""Core TTS types — the contract every provider implements.

Stdlib only, deliberately. This module sits on the import path of the registry
and every provider, so anything heavier here would leak into ``steppy.models``
and back onto the default CLI path (guard:
``tests/test_no_video_import_on_default_path.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

# Same grammar config.py resolves at load time. Repeated (not imported) to keep
# this module free of steppy imports beyond models.
_ENV_REF = re.compile(r"\$\{([A-Za-z_]\w*)(?::-([^}]*))?\}")


class TtsError(Exception):
    """Narration could not be synthesized.

    Never fatal to a build on its own: the caller falls back to a silent,
    captioned video and reports why, because losing the voiceover is better than
    losing the release.
    """


@dataclass(frozen=True)
class SpeechRequest:
    """One utterance to synthesize — normally one guide step's ``guide-text``."""

    text: str
    locale: str
    voice: Optional[str] = None
    model: Optional[str] = None
    speed: float = 1.0
    fmt: Optional[str] = None
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechClip:
    """Synthesized audio, in whatever container the provider produced."""

    audio: bytes
    fmt: str = "wav"

    @property
    def empty(self) -> bool:
        return not self.audio


@runtime_checkable
class TtsProvider(Protocol):
    """What a provider must offer. Deliberately one method.

    Third parties register implementations through the ``steppy.tts_providers``
    entry-point group, so this signature is a public contract: widen it only
    additively.
    """

    name: str

    def synthesize(self, request: SpeechRequest) -> SpeechClip:  # pragma: no cover - protocol
        ...

    def version(self) -> str:  # pragma: no cover - protocol
        """Identifier folded into the cache key.

        Load-bearing for the local engines: espeak-ng and Piper produce
        different audio across binary versions, so clip durations shift and the
        whole video timeline moves — silently, unless the version is in the key.
        """
        ...


def check_resolved(value: Optional[str], field_name: str) -> Optional[str]:
    """Reject a config value that is still an unexpanded ``${VAR}`` reference.

    ``config._interpolate_env`` leaves the literal in place and only warns when
    the variable is unset, so without this the user's first symptom is a 401
    from a vendor rather than a message about their own configuration.
    """
    if not value:
        return value
    match = _ENV_REF.search(value)
    if match:
        raise TtsError(
            f"{field_name} is still the literal {match.group(0)!r} — the environment "
            f"variable {match.group(1)} is not set. Export it, put the value in the "
            f"gitignored credentials.yaml overlay, or use ${{{match.group(1)}:-default}}."
        )
    return value
