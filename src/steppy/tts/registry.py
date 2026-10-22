"""Provider resolution — the seam that makes ``video.tts.provider`` a config knob.

Four sources, in priority order:

1. **Entry points** (``steppy.tts_providers``) — a third party ships a package,
   declares one entry point, and their engine is nameable in ``config.yaml``
   with no change to Steppy. This is why ``TtsConfig.provider`` is a plain
   ``str`` and not a ``Literal``.
2. **Built-in code providers** — ``none``, ``piper``, ``espeak``, ``http``.
3. **Vendor presets** (``tts/presets.py``) — ``elevenlabs``, ``openai``,
   ``azure``, ``google``, each a data row over the generic HTTP engine, with any
   explicitly-set ``video.tts.http.*`` field layered on top.
4. Anything else is an error that lists what *is* available.

Layering in (3) follows the same rule as the UI-kit presets in ``models.py``:
what the user set explicitly always wins, what they left alone gets filled in.

Structured as a priority cascade returning a tagged result, like
``_parallel/image.py:resolve_build_inputs`` — the caller can report which branch
won, which is most of what ``steppy video check`` needs to be useful.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from .presets import TTS_PRESETS
from .types import TtsError, TtsProvider

ENTRY_POINT_GROUP = "steppy.tts_providers"

_BUILTINS = ("none", "piper", "espeak", "http")

# Fields a preset may supply and the user may override. ``defaults`` is handled
# separately because it fills TtsConfig, not the request spec.
_SPEC_FIELDS = (
    "url",
    "method",
    "headers",
    "json",
    "body",
    "response",
    "audio_path",
    "encoding",
    "format",
)


@dataclass(frozen=True)
class Resolved:
    """A provider plus the branch that produced it."""

    provider: TtsProvider
    name: str
    # entry_point | builtin | preset
    source: str
    spec: dict[str, Any]


@lru_cache(maxsize=1)
def _entry_point_providers() -> dict[str, Any]:
    """Discover third-party providers.

    Cached and called lazily: ``entry_points()`` walks the metadata of every
    installed distribution, which is not a cost the default CLI path should pay.
    """
    from importlib.metadata import entry_points

    found: dict[str, Any] = {}
    try:
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            found[entry.name] = entry
    except Exception:  # pragma: no cover - broken metadata must not break a build
        return {}
    return found


def available_providers() -> list[str]:
    return sorted({*_BUILTINS, *TTS_PRESETS, *_entry_point_providers()})


def resolve_spec(name: str, cfg) -> dict[str, Any]:
    """Merge a vendor preset with the user's ``video.tts.http`` overrides.

    Explicit wins: any field the user actually set in config takes precedence
    over the preset's value, exactly like ``_PresetModel._apply_preset``.
    """
    preset = dict(TTS_PRESETS.get(name, {}))
    spec: dict[str, Any] = {k: v for k, v in preset.items() if k in _SPEC_FIELDS}
    spec["requires"] = list(preset.get("requires", []))
    spec["docs"] = preset.get("docs")
    for key, value in (preset.get("defaults") or {}).items():
        spec.setdefault(key, value)

    http = cfg.http
    explicit = http.model_fields_set
    overrides = {
        "url": http.url,
        "method": http.method,
        "headers": http.headers,
        "json": http.json_body,
        "body": http.body,
        "response": http.response,
        "audio_path": http.audio_path,
        "encoding": http.encoding,
        "format": http.format,
    }
    for field_name, value in overrides.items():
        # `json_body` carries the alias `json` on the model.
        set_name = "json_body" if field_name == "json" else field_name
        if set_name in explicit and value is not None:
            spec[field_name] = value

    # A user-set model/voice always beats the preset's default.
    if cfg.model:
        spec["model"] = cfg.model
    if cfg.base_url:
        spec["base_url"] = cfg.base_url
    return spec


def resolve(cfg) -> Resolved:
    """Resolve ``video.tts.provider`` into something that can speak."""
    name = (cfg.provider or "none").strip()

    entry = _entry_point_providers().get(name)
    if entry is not None:
        try:
            factory = entry.load()
        except Exception as exc:
            raise TtsError(f"TTS provider '{name}' is registered but failed to load: {exc}") from exc
        try:
            provider = factory(cfg)
        except Exception as exc:
            raise TtsError(f"TTS provider '{name}' failed to initialise: {exc}") from exc
        return Resolved(provider=provider, name=name, source="entry_point", spec={})

    if name in _BUILTINS:
        return Resolved(provider=_build_builtin(name, cfg), name=name, source="builtin", spec=resolve_spec(name, cfg))

    if name in TTS_PRESETS:
        from .providers.http import build as build_http

        spec = resolve_spec(name, cfg)
        return Resolved(provider=build_http(name, spec, cfg), name=name, source="preset", spec=spec)

    raise TtsError(
        f"Unknown TTS provider '{name}'. Available: {', '.join(available_providers())}.\n"
        "Set video.tts.provider to one of those, or describe an unsupported API "
        "directly under video.tts.http (url / headers / json / response)."
    )


def _build_builtin(name: str, cfg):
    if name == "none":
        from .providers.null import build

        return build(cfg)
    if name == "http":
        from .providers.http import build as build_http

        return build_http("http", resolve_spec("http", cfg), cfg)
    from .providers.local import build_espeak, build_piper

    return build_piper(cfg) if name == "piper" else build_espeak(cfg)


def describe(cfg) -> str:
    """Human-readable one-liner for ``steppy video check``."""
    try:
        resolved = resolve(cfg)
    except TtsError as exc:
        return f"unusable — {exc}"
    detail = {"entry_point": "third-party plugin", "builtin": "built in", "preset": "vendor preset"}[resolved.source]
    if resolved.name == "none":
        return "none (captions only, no audio)"
    return f"{resolved.name} ({detail})"


def readiness(cfg) -> Optional[str]:
    """Return a problem description, or None when the provider looks usable."""
    try:
        resolved = resolve(cfg)
    except TtsError as exc:
        return str(exc)
    if resolved.source == "builtin" and resolved.name in ("piper", "espeak"):
        from .providers.local import probe

        return probe(resolved.name, cfg)
    if resolved.name != "none" and resolved.spec.get("requires"):
        from .types import check_resolved

        try:
            check_resolved(cfg.api_key, "video.tts.api_key")
        except TtsError as exc:
            return str(exc)
        if "api_key" in resolved.spec["requires"] and not cfg.api_key:
            return f"video.tts.api_key is not set (required by '{resolved.name}')"
    return None
