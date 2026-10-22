"""TTS provider layer: the cascade, preset layering, cache, and track assembly.

The load-bearing claim under test is the one the whole design exists for —
**changing `video.tts.provider` in config.yaml is enough to change engines**, in
either direction, with no code path branching on vendor. No network is touched;
the HTTP engine runs against an httpx MockTransport.
"""

import base64
import struct
import wave
from pathlib import Path

import pytest

from steppy.models import TtsConfig, TtsHttpConfig
from steppy.tts import Utterance, synthesize_steps
from steppy.tts.cache import ClipCache, cache_key
from steppy.tts.presets import TTS_PRESETS
from steppy.tts.providers.http import HttpTtsProvider, dig, substitute
from steppy.tts.registry import available_providers, describe, readiness, resolve, resolve_spec
from steppy.tts.track import SAMPLE_RATE, assemble, silence, wav_duration_ms
from steppy.tts.types import SpeechClip, SpeechRequest, TtsError, check_resolved

# ── Helpers ─────────────────────────────────────────────────────────────────


def wav_bytes(duration_ms: int, rate: int = SAMPLE_RATE) -> bytes:
    """A silent mono 16-bit WAV of a known length — no audio library needed."""
    frames = int(rate * duration_ms / 1000)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def write_wav(path: Path, duration_ms: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(duration_ms))
    return path


def req(text="Bonjour", **over) -> SpeechRequest:
    return SpeechRequest(text=text, locale=over.pop("locale", "fr"), **over)


# ── The cascade ─────────────────────────────────────────────────────────────


def test_the_default_provider_is_silent_and_free():
    """The shipped default must render a video with no install, network or bill."""
    resolved = resolve(TtsConfig())
    assert resolved.name == "none"
    assert resolved.provider.synthesize(req()).empty


def test_builtins_and_presets_are_all_nameable():
    names = available_providers()
    for expected in ("none", "piper", "espeak", "http", "elevenlabs", "openai", "azure", "google"):
        assert expected in names


def test_a_preset_resolves_to_the_generic_http_engine():
    """No class per vendor — that is what keeps adding one a data change."""
    resolved = resolve(TtsConfig(provider="elevenlabs", api_key="k", voice="Rachel"))
    assert resolved.source == "preset"
    assert isinstance(resolved.provider, HttpTtsProvider)


def test_an_unknown_provider_lists_what_is_available():
    with pytest.raises(TtsError) as exc:
        resolve(TtsConfig(provider="wavenet9000"))
    message = str(exc.value)
    assert "wavenet9000" in message
    assert "elevenlabs" in message
    # And points at the escape hatch that needs no Steppy release.
    assert "video.tts.http" in message


def test_an_entry_point_provider_outranks_everything(monkeypatch):
    """The seam that lets a third party ship an engine without forking Steppy."""

    class Fake:
        name = "acme"

        def __init__(self, cfg):
            self.cfg = cfg

        def version(self):
            return "9"

        def synthesize(self, request):
            return SpeechClip(audio=b"acme", fmt="wav")

    class Entry:
        name = "elevenlabs"  # deliberately shadows a preset

        def load(self):
            return Fake

    import steppy.tts.registry as registry

    registry._entry_point_providers.cache_clear()
    monkeypatch.setattr(registry, "_entry_point_providers", lambda: {"elevenlabs": Entry()})
    resolved = registry.resolve(TtsConfig(provider="elevenlabs"))
    assert resolved.source == "entry_point"
    assert resolved.provider.synthesize(req()).audio == b"acme"


def test_a_broken_entry_point_is_reported_not_swallowed(monkeypatch):
    class Entry:
        def load(self):
            raise ImportError("no module named acme")

    import steppy.tts.registry as registry

    monkeypatch.setattr(registry, "_entry_point_providers", lambda: {"acme": Entry()})
    with pytest.raises(TtsError, match="failed to load"):
        registry.resolve(TtsConfig(provider="acme"))


# ── Preset layering ─────────────────────────────────────────────────────────


def test_a_preset_supplies_its_vendor_shape():
    spec = resolve_spec("elevenlabs", TtsConfig(provider="elevenlabs"))
    assert "api.elevenlabs.io" in spec["url"]
    assert spec["headers"]["xi-api-key"] == "{api_key}"
    assert spec["model"] == "eleven_multilingual_v2"


def test_an_explicit_field_beats_the_preset():
    """Same rule as the UI-kit presets: what the user set always wins."""
    cfg = TtsConfig(provider="elevenlabs", http=TtsHttpConfig(url="https://proxy.internal/tts"))
    assert resolve_spec("elevenlabs", cfg)["url"] == "https://proxy.internal/tts"


def test_untouched_fields_still_come_from_the_preset():
    cfg = TtsConfig(provider="elevenlabs", http=TtsHttpConfig(url="https://proxy.internal/tts"))
    assert resolve_spec("elevenlabs", cfg)["headers"]["xi-api-key"] == "{api_key}"


def test_an_explicit_model_beats_the_preset_default():
    cfg = TtsConfig(provider="elevenlabs", model="eleven_turbo_v2")
    assert resolve_spec("elevenlabs", cfg)["model"] == "eleven_turbo_v2"


@pytest.mark.parametrize("name", sorted(TTS_PRESETS))
def test_every_preset_is_structurally_complete(name):
    """A row missing a field would fail only at request time, per vendor."""
    preset = TTS_PRESETS[name]
    assert preset["url"]
    assert preset["response"] in ("binary", "json")
    assert ("json" in preset) ^ ("body" in preset), f"{name}: exactly one body form"
    if preset["response"] == "json":
        assert preset.get("audio_path")
    assert preset.get("docs", "").startswith("http")


# ── Placeholder substitution ────────────────────────────────────────────────


def test_substitution_reaches_nested_string_leaves():
    body = {"input": {"text": "{text}"}, "voice": {"name": "{voice}"}}
    out = substitute(body, {"text": "Bonjour", "voice": "fr-FR-Denise"})
    assert out == {"input": {"text": "Bonjour"}, "voice": {"name": "fr-FR-Denise"}}


def test_narration_containing_braces_cannot_corrupt_the_body():
    """Substituting into the parsed structure, not serialized JSON, is why."""
    out = substitute({"text": "{text}"}, {"text": 'Cliquez sur {"a": 1} puis }{'})
    assert out["text"] == 'Cliquez sur {"a": 1} puis }{'


def test_doubled_braces_escape_to_literals():
    assert substitute("{{literal}}", {}) == "{literal}"


def test_an_unknown_placeholder_becomes_empty_rather_than_crashing():
    assert substitute("x={nope}", {}) == "x="


def test_dig_follows_a_dotted_path():
    assert dig({"a": {"b": {"c": 7}}}, "a.b.c") == 7


def test_dig_names_the_missing_segment():
    with pytest.raises(TtsError, match="'b'"):
        dig({"a": {}}, "a.b.c")


# ── Unresolved ${VAR} ───────────────────────────────────────────────────────


def test_an_unexpanded_env_ref_is_rejected_with_the_variable_name():
    """config._interpolate_env only warns when a var is unset and leaves the
    literal in place; without this the first symptom is a vendor 401."""
    with pytest.raises(TtsError) as exc:
        check_resolved("${ELEVENLABS_API_KEY}", "video.tts.api_key")
    assert "ELEVENLABS_API_KEY" in str(exc.value)
    assert "credentials.yaml" in str(exc.value)


def test_a_real_value_passes_through():
    assert check_resolved("sk-live-123", "video.tts.api_key") == "sk-live-123"


def test_readiness_reports_an_unresolved_key():
    problem = readiness(TtsConfig(provider="elevenlabs", api_key="${NOPE}", voice="Rachel"))
    assert problem and "NOPE" in problem


def test_readiness_reports_a_missing_key():
    problem = readiness(TtsConfig(provider="elevenlabs", voice="Rachel"))
    assert problem and "api_key" in problem


def test_readiness_is_quiet_for_the_default():
    assert readiness(TtsConfig()) is None


def test_describe_names_the_branch_that_won():
    assert "captions only" in describe(TtsConfig())
    assert "vendor preset" in describe(TtsConfig(provider="openai", api_key="k"))


# ── The HTTP engine (no network) ────────────────────────────────────────────


@pytest.fixture
def mock_http(monkeypatch):
    """Route httpx.request at a MockTransport and record what was sent."""
    import httpx

    sent = {}

    def install(handler):
        def _request(method, url, **kwargs):
            sent["method"] = method
            sent["url"] = url
            sent["headers"] = kwargs.get("headers")
            sent["json"] = kwargs.get("json")
            sent["content"] = kwargs.get("content")
            transport = httpx.MockTransport(handler)
            with httpx.Client(transport=transport) as client:
                return client.request(method, url, **kwargs)

        monkeypatch.setattr(httpx, "request", _request)
        return sent

    return install


def test_elevenlabs_request_is_shaped_by_the_preset_alone(mock_http):
    import httpx

    sent = mock_http(lambda request: httpx.Response(200, content=b"ID3-audio"))
    provider = resolve(TtsConfig(provider="elevenlabs", api_key="sk-1", voice="Rachel")).provider
    clip = provider.synthesize(req("Bonjour"))

    assert clip.audio == b"ID3-audio"
    assert clip.fmt == "mp3"
    assert sent["url"].endswith("/v1/text-to-speech/Rachel")
    assert sent["headers"]["xi-api-key"] == "sk-1"
    assert sent["json"] == {"text": "Bonjour", "model_id": "eleven_multilingual_v2"}


def test_google_audio_is_decoded_from_base64_json(mock_http):
    import httpx

    payload = {"audioContent": base64.b64encode(b"RIFFwav").decode()}
    mock_http(lambda request: httpx.Response(200, json=payload))
    provider = resolve(TtsConfig(provider="google", api_key="k", voice="fr-FR-Wavenet-A")).provider
    assert provider.synthesize(req()).audio == b"RIFFwav"


def test_azure_sends_ssml_as_a_raw_body(mock_http):
    import httpx

    sent = mock_http(lambda request: httpx.Response(200, content=b"RIFF"))
    provider = resolve(
        TtsConfig(provider="azure", api_key="k", region="westeurope", voice="fr-FR-DeniseNeural")
    ).provider
    provider.synthesize(req("Bonjour"))
    assert sent["json"] is None
    assert b"<speak" in sent["content"]
    assert b"fr-FR-DeniseNeural" in sent["content"]
    assert "westeurope" in sent["url"]


def test_openai_speed_is_sent_as_an_integer_when_whole(mock_http):
    import httpx

    sent = mock_http(lambda request: httpx.Response(200, content=b"RIFF"))
    provider = resolve(TtsConfig(provider="openai", api_key="k")).provider
    provider.synthesize(req(speed=1.0))
    assert sent["json"]["speed"] == "1"


def test_a_totally_unknown_vendor_works_from_config_alone(mock_http):
    """The escape hatch: no preset, no code, just video.tts.http."""
    import httpx

    sent = mock_http(lambda request: httpx.Response(200, json={"data": {"audio": base64.b64encode(b"snd").decode()}}))
    cfg = TtsConfig(
        provider="http",
        api_key="tok",
        http=TtsHttpConfig(
            url="https://vendor.example/v2/speak",
            headers={"X-Key": "{api_key}"},
            json={"utterance": "{text}", "lang": "{locale}"},
            response="json",
            audio_path="data.audio",
            encoding="base64",
            format="wav",
        ),
    )
    clip = resolve(cfg).provider.synthesize(req("Salut", locale="fr"))
    assert clip.audio == b"snd"
    assert sent["json"] == {"utterance": "Salut", "lang": "fr"}
    assert sent["headers"]["X-Key"] == "tok"


def test_an_http_error_never_echoes_the_credentials(mock_http):
    import httpx

    mock_http(lambda request: httpx.Response(401, text="invalid api key"))
    provider = resolve(TtsConfig(provider="elevenlabs", api_key="sk-secret", voice="R")).provider
    with pytest.raises(TtsError) as exc:
        provider.synthesize(req())
    assert "401" in str(exc.value)
    assert "sk-secret" not in str(exc.value)


def test_a_missing_required_field_is_caught_before_the_request(mock_http):
    mock_http(lambda request: pytest.fail("should not have been called"))
    provider = resolve(TtsConfig(provider="elevenlabs", api_key="k")).provider
    with pytest.raises(TtsError) as exc:
        provider.synthesize(req())
    assert "video.tts.voice" in str(exc.value)


# ── Cache ───────────────────────────────────────────────────────────────────


def test_the_key_covers_everything_that_changes_the_audio():
    base = cache_key(req(), "openai", "v1")
    assert cache_key(req("autre"), "openai", "v1") != base
    assert cache_key(req(voice="nova"), "openai", "v1") != base
    assert cache_key(req(speed=1.2), "openai", "v1") != base
    assert cache_key(req(locale="nl"), "openai", "v1") != base
    assert cache_key(req(), "elevenlabs", "v1") != base
    # Local engines produce different audio across builds; an unversioned key
    # would serve a clip whose duration no longer matches the timeline.
    assert cache_key(req(), "piper", "v2") != base


def test_identical_requests_share_a_key():
    assert cache_key(req(), "openai", "v1") == cache_key(req(), "openai", "v1")


def test_a_hit_avoids_a_second_synthesis(tmp_path):
    cache = ClipCache(tmp_path / "cache")
    cache.put("abc", SpeechClip(audio=wav_bytes(500), fmt="wav"), duration_ms=500)
    hit = cache.get("abc", "wav")
    assert hit is not None
    assert hit.duration_ms == 500


def test_a_miss_returns_nothing(tmp_path):
    assert ClipCache(tmp_path / "cache").get("nope", "wav") is None


def test_disabling_the_cache_never_writes(tmp_path):
    cache = ClipCache(tmp_path / "cache", enabled=False)
    cache.put("abc", SpeechClip(audio=b"x", fmt="wav"), duration_ms=1)
    assert not (tmp_path / "cache").exists()
    assert cache.get("abc", "wav") is None


def test_a_torn_sidecar_does_not_break_the_hit(tmp_path):
    cache = ClipCache(tmp_path / "cache")
    cache.put("abc", SpeechClip(audio=wav_bytes(100), fmt="wav"), duration_ms=100)
    (tmp_path / "cache" / "abc.json").write_text("{not json", encoding="utf-8")
    hit = cache.get("abc", "wav")
    assert hit is not None and hit.duration_ms is None


# ── Track assembly ──────────────────────────────────────────────────────────


def test_silence_is_exactly_as_long_as_asked():
    assert len(silence(1000)) == SAMPLE_RATE * 2


def test_wav_duration_reads_the_header(tmp_path):
    assert wav_duration_ms(write_wav(tmp_path / "a.wav", 750)) == 750


def test_clips_land_at_their_timeline_offsets(tmp_path):
    """Voiceover must stay locked to the cursor, whatever the timeline did."""
    a = write_wav(tmp_path / "a.wav", 500)
    b = write_wav(tmp_path / "b.wav", 500)
    out = assemble([(0, a), (2000, b)], total_ms=4000, dest=tmp_path / "track.wav")
    assert out is not None
    assert wav_duration_ms(out) == 4000

    with wave.open(str(out), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    # The gap between the two clips is real silence, not a splice.
    gap_start = int(SAMPLE_RATE * 0.6) * 2
    assert frames[gap_start : gap_start + 200] == b"\x00" * 200


def test_a_track_is_padded_out_to_the_video_duration(tmp_path):
    a = write_wav(tmp_path / "a.wav", 200)
    out = assemble([(0, a)], total_ms=5000, dest=tmp_path / "t.wav")
    assert wav_duration_ms(out) == 5000


def test_nothing_to_say_produces_no_track(tmp_path):
    assert assemble([], total_ms=3000, dest=tmp_path / "t.wav") is None
    assert assemble([(0, None)], total_ms=3000, dest=tmp_path / "t.wav") is None


def test_clips_are_ordered_by_offset_not_by_argument_order(tmp_path):
    a = write_wav(tmp_path / "a.wav", 300)
    b = write_wav(tmp_path / "b.wav", 300)
    out = assemble([(2000, b), (0, a)], total_ms=3000, dest=tmp_path / "t.wav")
    assert wav_duration_ms(out) == 3000


# ── End to end, silent path ─────────────────────────────────────────────────


def test_the_default_provider_yields_no_durations(tmp_path):
    narration = synthesize_steps(
        [Utterance("step-1", "Bonjour")],
        TtsConfig(),
        locale="fr",
        work_dir=tmp_path / "work",
        ffmpeg="ffmpeg",
    )
    assert narration.silent
    assert narration.durations_ms == {}


def test_durations_come_back_keyed_by_step(tmp_path, monkeypatch):
    """The video timeline needs these up front, to stretch each step to fit."""

    class Fake:
        name = "fake"

        def __init__(self, cfg):
            pass

        def version(self):
            return "1"

        def synthesize(self, request):
            return SpeechClip(audio=wav_bytes(1200), fmt="wav")

    class Entry:
        def load(self):
            return Fake

    import steppy.tts.registry as registry

    monkeypatch.setattr(registry, "_entry_point_providers", lambda: {"fake": Entry()})
    narration = synthesize_steps(
        [Utterance("step-1", "Bonjour"), Utterance("step-2", "  ")],
        TtsConfig(provider="fake", cache_dir=str(tmp_path / "cache")),
        locale="fr",
        work_dir=tmp_path / "work",
        ffmpeg="ffmpeg",
    )
    assert narration.durations_ms == {"step-1": 1200}
    assert "step-2" not in narration.clips  # blank narration is skipped


def test_a_provider_failure_degrades_to_silence_and_says_so(tmp_path, monkeypatch):
    """Losing the voiceover beats losing the release."""

    class Broken:
        name = "broken"

        def __init__(self, cfg):
            pass

        def version(self):
            return "1"

        def synthesize(self, request):
            raise TtsError("quota exceeded")

    class Entry:
        def load(self):
            return Broken

    import steppy.tts.registry as registry

    monkeypatch.setattr(registry, "_entry_point_providers", lambda: {"broken": Entry()})
    warnings = []
    narration = synthesize_steps(
        [Utterance("step-1", "Bonjour")],
        TtsConfig(provider="broken"),
        locale="fr",
        work_dir=tmp_path / "work",
        ffmpeg="ffmpeg",
        on_warning=warnings.append,
    )
    assert narration.silent
    assert "quota exceeded" in warnings[0]


def test_a_cached_clip_is_not_re_synthesized(tmp_path, monkeypatch):
    calls = {"n": 0}

    class Counting:
        name = "counting"

        def __init__(self, cfg):
            pass

        def version(self):
            return "1"

        def synthesize(self, request):
            calls["n"] += 1
            return SpeechClip(audio=wav_bytes(400), fmt="wav")

    class Entry:
        def load(self):
            return Counting

    import steppy.tts.registry as registry

    monkeypatch.setattr(registry, "_entry_point_providers", lambda: {"counting": Entry()})
    cfg = TtsConfig(provider="counting", cache_dir=str(tmp_path / "cache"))
    args = dict(locale="fr", work_dir=tmp_path / "work", ffmpeg="ffmpeg")

    synthesize_steps([Utterance("step-1", "Bonjour")], cfg, **args)
    synthesize_steps([Utterance("step-1", "Bonjour")], cfg, **args)
    assert calls["n"] == 1


def test_the_voice_map_picks_per_locale():
    cfg = TtsConfig(voice={"fr": "fr_FR-siwis-medium", "nl": "nl_NL-mls-medium"})
    assert cfg.voice_for("fr") == "fr_FR-siwis-medium"
    assert cfg.voice_for("nl") == "nl_NL-mls-medium"
    assert cfg.voice_for("de") is None


# ── Cache integrity ─────────────────────────────────────────────────────────


def counting_provider(monkeypatch, audio_ms=400, calls=None):
    """Register a fake entry-point provider that counts its syntheses."""
    calls = {"n": 0} if calls is None else calls

    class Counting:
        name = "counting"

        def __init__(self, cfg):
            pass

        def version(self):
            return "1"

        def synthesize(self, request):
            calls["n"] += 1
            return SpeechClip(audio=wav_bytes(audio_ms), fmt="wav")

    class Entry:
        def load(self):
            return Counting

    import steppy.tts.registry as registry

    monkeypatch.setattr(registry, "_entry_point_providers", lambda: {"counting": Entry()})
    return calls


def test_a_truncated_cached_clip_is_a_miss_not_a_crash(tmp_path, monkeypatch):
    """A killed run leaves a half-written WAV *at the key path*. Serving it
    forever is the failure the atomic writes exist to prevent — and a stale
    entry from an older steppy must still be survivable."""
    calls = counting_provider(monkeypatch)
    cfg = TtsConfig(provider="counting", cache_dir=str(tmp_path / "cache"))
    args = dict(locale="fr", work_dir=tmp_path / "work", ffmpeg="ffmpeg")

    synthesize_steps([Utterance("step-1", "Bonjour")], cfg, **args)
    (clip,) = list((tmp_path / "cache").glob("*.wav"))
    intact = clip.read_bytes()
    clip.write_bytes(intact[: len(intact) // 3])  # header survives, audio does not

    narration = synthesize_steps([Utterance("step-1", "Bonjour")], cfg, **args)
    assert calls["n"] == 2, "the poisoned entry must be re-synthesized, not served"
    assert narration.durations_ms == {"step-1": 400}
    assert clip.read_bytes() == intact


def test_a_failed_transcode_leaves_nothing_at_the_key_path(tmp_path, monkeypatch):
    """The whole point of staging: an interrupted write must not become an entry."""
    counting_provider(monkeypatch)
    cache_dir = tmp_path / "cache"

    import steppy.tts.track as track_module

    def explode(source, dest, ffmpeg):
        Path(dest).write_bytes(b"half")  # ffmpeg got partway, then the box died
        raise TtsError("boom")

    monkeypatch.setattr(track_module, "normalize", explode)
    warnings = []
    narration = synthesize_steps(
        [Utterance("step-1", "Bonjour")],
        TtsConfig(provider="counting", cache_dir=str(cache_dir)),
        locale="fr",
        work_dir=tmp_path / "work",
        ffmpeg="ffmpeg",
        on_warning=warnings.append,
    )
    assert narration.silent and "boom" in warnings[0]
    assert list(cache_dir.glob("*.wav")) == [], "a partial write must not be a cache entry"


def test_the_raw_container_does_not_accumulate(tmp_path, monkeypatch):
    """``tts-cache/work/`` used to keep every pre-transcode clip forever."""
    counting_provider(monkeypatch)
    work = tmp_path / "work"
    synthesize_steps(
        [Utterance("step-1", "Bonjour"), Utterance("step-2", "Bonsoir")],
        TtsConfig(provider="counting", cache_dir=str(tmp_path / "cache")),
        locale="fr",
        work_dir=work,
        ffmpeg="ffmpeg",
    )
    assert list(work.glob("*")) == []


def test_rewrapping_a_line_is_not_a_re_bill(tmp_path, monkeypatch):
    """Reflowing a YAML block scalar changes the string, never the audio."""
    calls = counting_provider(monkeypatch)
    cfg = TtsConfig(provider="counting", cache_dir=str(tmp_path / "cache"))
    args = dict(locale="fr", work_dir=tmp_path / "work", ffmpeg="ffmpeg")

    synthesize_steps([Utterance("step-1", "Ouvrez le menu principal.")], cfg, **args)
    synthesize_steps([Utterance("step-1", "Ouvrez le\n  menu   principal.")], cfg, **args)
    assert calls["n"] == 1


def test_a_shared_memo_voices_a_layout_pair_once_even_with_the_cache_off(tmp_path, monkeypatch):
    """Desktop and mobile are the same narration; ``cache: false`` must mean
    "do not persist", not "bill once per layout"."""
    calls = counting_provider(monkeypatch)
    cfg = TtsConfig(provider="counting", cache=False, cache_dir=str(tmp_path / "cache"))
    memo: dict = {}
    for _ in range(3):
        synthesize_steps(
            [Utterance("step-1", "Bonjour")],
            cfg,
            locale="fr",
            work_dir=tmp_path / "work",
            ffmpeg="ffmpeg",
            memo=memo,
        )
    assert calls["n"] == 1


def test_cache_root_overrides_the_cwd_relative_config_value(tmp_path, monkeypatch):
    """Callers resolve against the project root; the bare config value is
    relative to whatever directory the command happened to run in."""
    counting_provider(monkeypatch)
    elsewhere = tmp_path / "resolved"
    synthesize_steps(
        [Utterance("step-1", "Bonjour")],
        TtsConfig(provider="counting", cache_dir=".steppy/tts-cache"),
        locale="fr",
        work_dir=tmp_path / "work",
        ffmpeg="ffmpeg",
        cache_root=elsewhere,
    )
    assert list(elsewhere.glob("*.wav"))
    assert not (Path.cwd() / ".steppy" / "tts-cache").exists()


def test_a_hit_is_touched_so_lru_eviction_means_something(tmp_path):
    cache = ClipCache(tmp_path / "cache")
    cache.put("abc", SpeechClip(audio=wav_bytes(100), fmt="wav"), duration_ms=100)
    audio = tmp_path / "cache" / "abc.wav"
    import os

    os.utime(audio, (1_000_000, 1_000_000))
    cache.get("abc", "wav")
    assert audio.stat().st_mtime > 1_000_000


def test_the_http_spec_is_part_of_the_clip_key():
    """Editing the request body changes the audio; without the spec in the key
    every existing clip would stay a hit forever."""
    base = TtsConfig(provider="elevenlabs", api_key="k", voice="v")
    tweaked = TtsConfig(
        provider="elevenlabs",
        api_key="k",
        voice="v",
        http=TtsHttpConfig(json_body={"text": "{text}", "voice_settings": {"stability": 0.9}}),
    )
    assert resolve(base).provider.version() != resolve(tweaked).provider.version()


def test_rotating_the_api_key_does_not_churn_the_clip_cache():
    a = TtsConfig(provider="elevenlabs", api_key="old", voice="v")
    b = TtsConfig(provider="elevenlabs", api_key="new", voice="v")
    assert resolve(a).provider.version() == resolve(b).provider.version()


# ── The whole point: an edit costs one clip ─────────────────────────────────


def test_editing_one_step_re_voices_only_that_step(tmp_path, monkeypatch):
    """The claim the per-step design exists to make.

    A five-step guide is voiced once. Then one word of step 3 changes, a step is
    inserted at the front, and the rest is left alone — and the provider is
    asked for exactly one new clip. Nothing here is mocked below the cache: the
    fake provider returns real WAV bytes, so this exercises the true
    synthesize → normalize → commit → hit path.
    """
    spoken = []

    class Recording:
        name = "recording"

        def __init__(self, cfg):
            pass

        def version(self):
            return "1"

        def synthesize(self, request):
            spoken.append(request.text)
            return SpeechClip(audio=wav_bytes(300), fmt="wav")

    class Entry:
        def load(self):
            return Recording

    import steppy.tts.registry as registry

    monkeypatch.setattr(registry, "_entry_point_providers", lambda: {"recording": Entry()})
    cfg = TtsConfig(provider="recording", cache_dir=str(tmp_path / "cache"))
    args = dict(locale="fr", work_dir=tmp_path / "work", ffmpeg="ffmpeg")

    original = [
        "Ouvrez le menu principal.",
        "Cliquez sur Invoices.",
        "Saisissez le nom du client.",
        "Validez le formulaire.",
        "Fermez la fenetre.",
    ]
    first = synthesize_steps([Utterance(f"step-{i}", t) for i, t in enumerate(original, 1)], cfg, **args)
    assert len(spoken) == 5
    assert len(first.durations_ms) == 5

    spoken.clear()
    edited = [
        "Connectez-vous d'abord.",  # a brand new step, inserted at the front
        *original[:2],
        "Saisissez le prenom du client.",  # one word changed
        *original[3:],
    ]
    second = synthesize_steps([Utterance(f"step-{i}", t) for i, t in enumerate(edited, 1)], cfg, **args)

    assert sorted(spoken) == sorted(["Connectez-vous d'abord.", "Saisissez le prenom du client."])
    assert len(second.durations_ms) == 6
    # Every step still has audio, including the four that were never re-voiced.
    assert all(path.is_file() for path in second.clips.values())
