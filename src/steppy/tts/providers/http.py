"""Generic HTTP text-to-speech provider — the engine every vendor preset runs on.

One class serves ElevenLabs, OpenAI, Azure, Google, and any vendor that has not
shipped yet, because the differences between them are data (``tts/presets.py``)
rather than code. A user facing an unsupported API fills in ``video.tts.http``
and is done — no fork, no plugin, no release of Steppy.

``httpx`` is imported **inside** :meth:`synthesize`. It is already a Steppy
dependency, but ``tests/test_no_playwright_import_on_default_path.py`` forbids
it transitively from ``import steppy.cli``, and ``TtsConfig`` lives in
``models.py`` on the eager path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from ..types import SpeechClip, SpeechRequest, TtsError, check_resolved

# Only whole ``{name}`` tokens are substituted; ``{{`` / ``}}`` escape a literal
# brace. Applied per string leaf, never to serialized JSON, so narration text
# containing braces cannot alter the request structure.
_TOKEN = re.compile(r"\{\{|\}\}|\{(\w+)\}")


def substitute(value: Any, context: dict[str, str]) -> Any:
    """Fill ``{placeholders}`` in every string leaf of a parsed structure."""
    if isinstance(value, dict):
        return {k: substitute(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, context) for v in value]
    if not isinstance(value, str):
        return value

    def _sub(match: "re.Match") -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        return context.get(match.group(1), "")

    return _TOKEN.sub(_sub, value)


def dig(payload: Any, path: str) -> Any:
    """Follow a dotted path into a decoded JSON response."""
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise TtsError(f"TTS response has no field '{path}' (looking for '{part}')")
        current = current[part]
    return current


class HttpTtsProvider:
    """Config-driven REST provider.

    ``spec`` is a merged preset + user override (see ``registry.resolve_spec``),
    so this class never knows which vendor it is talking to.
    """

    def __init__(self, name: str, spec: dict, cfg) -> None:
        self.name = name
        self.spec = spec
        self.cfg = cfg

    def version(self) -> str:
        """Model name plus a hash of the request shape.

        A remote voice has nothing local to fingerprint, so the model id is the
        only version there is — but the *request* is configuration too. Editing
        ``video.tts.http.json`` (ElevenLabs ``stability``/``style``, Azure SSML
        prosody, a different ``format``) changes the audio, and without the spec
        in the key every existing clip would stay a hit forever.

        The spec is hashed **pre-substitution**, so it holds ``{api_key}``
        placeholders rather than the secret — safe to fold in, and rotating a
        key does not churn the cache.
        """
        shape = hashlib.sha256(
            json.dumps(self.spec, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:12]
        return f"{self.spec.get('model') or self.cfg.model or ''}:{shape}"

    def _context(self, request: SpeechRequest) -> dict[str, str]:
        api_key = check_resolved(self.cfg.api_key, "video.tts.api_key")
        # Fall back to config so the provider is usable on its own, not only
        # through synthesize_steps() — the request carries per-utterance intent,
        # the config carries the project's defaults.
        voice = request.voice or self.cfg.voice_for(request.locale) or ""
        model = request.model or self.spec.get("model") or self.cfg.model or ""
        context: dict[str, str] = {
            "text": request.text,
            "locale": request.locale,
            "voice": voice,
            "model": model,
            "speed": _format_number(request.speed),
            "api_key": api_key or "",
            "region": self.cfg.region or "",
            "base_url": (self.cfg.base_url or self.spec.get("base_url") or "").rstrip("/"),
        }
        # Provider-specific extras last, so a project can override anything above.
        for key, value in (request.options or {}).items():
            context[str(key)] = "" if value is None else str(value)
        return context

    def _require(self, context: dict[str, str]) -> None:
        missing = [f for f in self.spec.get("requires", []) if not context.get(f)]
        if not missing:
            return
        hint = self.spec.get("docs")
        raise TtsError(
            f"TTS provider '{self.name}' needs {', '.join('video.tts.' + m for m in missing)}."
            + (f" See {hint}" if hint else "")
        )

    def synthesize(self, request: SpeechRequest) -> SpeechClip:
        import httpx  # lazy: see the module docstring

        context = self._context(request)
        self._require(context)

        url = substitute(self.spec.get("url") or "", context)
        if not url:
            raise TtsError(
                f"TTS provider '{self.name}' has no URL. Set video.tts.http.url, or pick a "
                "provider preset that supplies one."
            )

        headers = substitute(self.spec.get("headers") or {}, context)
        json_body = self.spec.get("json")
        raw_body = self.spec.get("body")
        kwargs: dict[str, Any] = {"headers": headers}
        if json_body is not None:
            kwargs["json"] = substitute(json_body, context)
        elif raw_body is not None:
            kwargs["content"] = substitute(raw_body, context).encode("utf-8")

        timeout = self.cfg.timeout / 1000
        try:
            response = httpx.request(self.spec.get("method", "POST"), url, timeout=timeout, **kwargs)
        except Exception as exc:
            raise TtsError(f"TTS request to '{self.name}' failed: {exc}") from exc

        if response.status_code >= 400:
            # Never echo the headers back — they carry the API key.
            detail = response.text[:300].strip()
            raise TtsError(f"TTS provider '{self.name}' returned HTTP {response.status_code}: {detail}")

        audio = self._extract(response)
        if not audio:
            raise TtsError(f"TTS provider '{self.name}' returned no audio for a {len(request.text)}-char utterance")
        return SpeechClip(audio=audio, fmt=self._format())

    def _extract(self, response) -> bytes:
        if self.spec.get("response") == "json":
            payload = response.json()
            value = dig(payload, self.spec.get("audio_path") or "")
            if self.spec.get("encoding") == "base64":
                return base64.b64decode(value)
            return value.encode("utf-8") if isinstance(value, str) else bytes(value)
        return response.content

    def _format(self) -> str:
        return str(self.cfg.format or self.spec.get("format") or "wav")


def _format_number(value: float) -> str:
    """1.0 renders as "1", not "1.0" — some APIs reject the float form."""
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def build(name: str, spec: dict, cfg) -> HttpTtsProvider:
    return HttpTtsProvider(name, spec, cfg)


__all__ = ["HttpTtsProvider", "build", "dig", "substitute"]
