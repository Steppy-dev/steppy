"""Vendor TTS presets — pure data, no logic.

Every cloud text-to-speech API is the same shape: POST some JSON, get audio
back. So Steppy has one generic HTTP provider and this table of vendor shapes,
rather than a class per vendor. Adding ElevenLabs' next competitor is a dict
entry; adding one Steppy has never heard of needs no code at all, just
``video.tts.http`` filled in directly. That is what makes the promise —
"change the provider by changing config" — literally true.

Mirrors ``steppy/presets.py`` (the UI-kit selector packs) in both spirit and
mechanics: a data registry plus a resolver that lets explicitly-set fields win.

**Keep this module free of ``httpx``.** It sits on an import path that reaches
the default CLI path, and the negative-import guard enforces it.

Placeholders available in any string leaf of ``headers`` / ``json`` / ``body``:

    {text} {voice} {locale} {model} {speed} {api_key} {region}

plus every key of ``video.tts.options``. Substitution happens on the parsed
structure, never on serialized JSON, so narration containing a brace is safe.
"""

from __future__ import annotations

from typing import Any

# ``format`` is what the provider returns, not what the user asks for; the audio
# assembler transcodes to WAV as needed. ``defaults`` fills fields the user left
# unset — an explicit ``video.tts.model`` always wins.
TTS_PRESETS: dict[str, dict[str, Any]] = {
    "elevenlabs": {
        "url": "https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        "method": "POST",
        "headers": {
            "xi-api-key": "{api_key}",
            "accept": "audio/mpeg",
            "content-type": "application/json",
        },
        "json": {
            "text": "{text}",
            "model_id": "{model}",
        },
        "response": "binary",
        "encoding": "raw",
        "format": "mp3",
        "defaults": {
            # Multilingual by default: a per-locale guide is the normal case here.
            "model": "eleven_multilingual_v2",
        },
        "requires": ["api_key", "voice"],
        "docs": "https://elevenlabs.io/docs/api-reference/text-to-speech",
    },
    "openai": {
        "url": "{base_url}/audio/speech",
        "method": "POST",
        "headers": {
            "Authorization": "Bearer {api_key}",
            "content-type": "application/json",
        },
        "json": {
            "model": "{model}",
            "input": "{text}",
            "voice": "{voice}",
            "speed": "{speed}",
            # WAV keeps the assembler on the stdlib path (no transcode).
            "response_format": "wav",
        },
        "response": "binary",
        "encoding": "raw",
        "format": "wav",
        "defaults": {
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "base_url": "https://api.openai.com/v1",
        },
        "requires": ["api_key"],
        "docs": "https://platform.openai.com/docs/api-reference/audio/createSpeech",
    },
    "azure": {
        "url": "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        "method": "POST",
        "headers": {
            "Ocp-Apim-Subscription-Key": "{api_key}",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        },
        # Azure speaks SSML, not JSON — which is exactly why the engine supports
        # a raw body as well as a JSON one.
        "body": (
            "<speak version='1.0' xml:lang='{locale}'>"
            "<voice xml:lang='{locale}' name='{voice}'>"
            "<prosody rate='{speed}'>{text}</prosody>"
            "</voice></speak>"
        ),
        "response": "binary",
        "encoding": "raw",
        "format": "wav",
        "defaults": {},
        "requires": ["api_key", "region", "voice"],
        "docs": "https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech",
    },
    "google": {
        "url": "https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}",
        "method": "POST",
        "headers": {"content-type": "application/json"},
        "json": {
            "input": {"text": "{text}"},
            "voice": {"languageCode": "{locale}", "name": "{voice}"},
            "audioConfig": {"audioEncoding": "LINEAR16", "speakingRate": "{speed}"},
        },
        # Google wraps the audio in JSON as base64 rather than returning it raw.
        "response": "json",
        "audio_path": "audioContent",
        "encoding": "base64",
        "format": "wav",
        "defaults": {},
        "requires": ["api_key", "voice"],
        "docs": "https://cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize",
    },
}


def available_presets() -> list[str]:
    return sorted(TTS_PRESETS)
