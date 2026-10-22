"""Built-in TTS provider implementations.

Imported lazily by ``registry._build_builtin`` so that naming a provider in
config costs nothing until a video is actually rendered — and so ``httpx`` stays
off the default CLI path.
"""
