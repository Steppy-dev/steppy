"""The ``none`` provider — captions, no voiceover.

Steppy's shipped default. A video renders with burned-in captions and a silent
track, with no install, no network and no bill; adding a voice is one config
line. That ordering is deliberate: the free path has to work out of the box,
and a default that silently starts charging per character would not.
"""

from __future__ import annotations

from ..types import SpeechClip, SpeechRequest


class NullTtsProvider:
    name = "none"

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg

    def version(self) -> str:
        return "none"

    def synthesize(self, request: SpeechRequest) -> SpeechClip:
        return SpeechClip(audio=b"", fmt="wav")


def build(cfg=None) -> NullTtsProvider:
    return NullTtsProvider(cfg)
