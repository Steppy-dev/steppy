"""In-app tours data contract v1 — the producer-side self-check.

Two files make a tour set: ``tours.manifest.json`` and one
``<tour-id>.<locale>.json`` per tour and locale. The overlay
(``packages/player/src/tour/validate.ts``) is the security boundary and
re-validates everything it loads; this module is the compiler's guarantee that
it never emits anything the overlay would reject.

**A documented exception to this package's rule.** The other contracts are
``extra="allow"`` because a newer producer's field must survive an older
consumer. These models are ``extra="forbid"`` and ``strict``: they check what
*this* producer writes, so an unexpected key is a compiler bug. The published
JSON Schemas stay open (no ``additionalProperties: false``), and the player
ignores unknown keys within a schema major — that is where forward
compatibility lives. Unknown enum values and unknown majors are rejected on
both sides: a tour file is data, never code (spec fixed decision 9).

Serialization is canonical — sorted keys, 2-space indent, ``ensure_ascii=False``,
LF, trailing newline, no timestamp — so the same scenarios always produce the
same bytes, and ``integrity`` (SHA-384 SRI over those exact bytes) can lock a
remote copy to the bundled one.

Not re-exported from :mod:`steppy.contracts`; import it directly. Lazily
imported only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..buildid import BUILD_ID_PATTERN, is_build_id
from ..locales import validate_locale_tag
from ..naming import TOUR_FILE_KIND, TOURS_MANIFEST_KIND
from ..tour_spec import (
    ACCEPTED_TOUR_SCHEMA_MAJORS,
    ADVANCE_KINDS,
    LAYOUT_NAME_PATTERN,
    LOCALE_PATTERN,
    MAX_LAYOUTS,
    MAX_LOCALES,
    MAX_ROUTE_CHARS,
    MAX_STEPS,
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
    MAX_TOURS,
    MAX_URL_CHARS,
    PLACEMENTS,
    ROLE_PATTERN,
    ROUTE_PATTERN,
    SRI_PATTERN,
    STEP_ID_PATTERN,
    TARGETED_ADVANCE_KINDS,
    TEST_ID_ATTRIBUTE_PATTERN,
    TEST_ID_PATTERN,
    TOUR_ID_PATTERN,
    TOUR_SCHEMA_VERSION,
    is_layout_name,
    is_media_query,
    is_route,
    is_tour_id,
    tour_file_name,
)

MANIFEST_SCHEMA_TITLE = "Steppy in-app tours manifest (tours.manifest.json)"
TOUR_SCHEMA_TITLE = "Steppy in-app tour file (<tour-id>.<locale>.json)"

AdvanceKind = Literal["click", "input", "change", "visible", "manual"]
Placement = Literal["top", "bottom", "left", "right", "auto"]
assert ADVANCE_KINDS == AdvanceKind.__args__  # type: ignore[attr-defined]
assert PLACEMENTS == Placement.__args__  # type: ignore[attr-defined]


class TourSchemaUnsupported(ValueError):
    """The payload's ``schema`` major is not one this version understands."""


class TourDataInvalid(ValueError):
    """The payload violates the v1 contract (the player's ``invalid-data``)."""


def _full(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value) is not None


def _check_text(value: Optional[str], limit: int, what: str) -> Optional[str]:
    if value is None:
        return value
    if not value.strip():
        raise ValueError(f"{what} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return value


def _check_locale(value: str) -> str:
    if not _full(LOCALE_PATTERN, value):
        raise ValueError(f"locale {value!r} does not match {LOCALE_PATTERN}")
    validate_locale_tag(value)
    return value


def _origin_of(url: str) -> Optional[str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname
    port = parts.port
    default = {"http": 80, "https": 443}[parts.scheme]
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default else "")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class TourEntry(_Strict):
    route: str = Field(pattern=ROUTE_PATTERN, max_length=MAX_ROUTE_CHARS)

    @field_validator("route")
    @classmethod
    def _route(cls, v: str) -> str:
        if not is_route(v):
            raise ValueError(f"route {v!r} is not a same-origin, base-relative route pattern")
        return v


class TourAdvance(_Strict):
    on: AdvanceKind


class TourStep(_Strict):
    id: str = Field(pattern=STEP_ID_PATTERN)
    target: Optional[str] = Field(..., pattern=TEST_ID_PATTERN)
    advance: TourAdvance
    waitFor: Optional[str] = Field(None, pattern=TEST_ID_PATTERN)
    title: Optional[str] = None
    text: Optional[str] = None
    placement: Optional[Placement] = None
    hold: Optional[str] = Field(None, pattern=TEST_ID_PATTERN)
    layouts: Optional[list[str]] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _full(STEP_ID_PATTERN, v):
            raise ValueError(f"step id {v!r} does not match {STEP_ID_PATTERN}")
        return v

    @field_validator("target", "waitFor", "hold")
    @classmethod
    def _test_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _full(TEST_ID_PATTERN, v):
            raise ValueError(f"{v!r} is not a test-id value (never a selector): {TEST_ID_PATTERN}")
        return v

    @field_validator("title")
    @classmethod
    def _title(cls, v: Optional[str]) -> Optional[str]:
        return _check_text(v, MAX_TITLE_CHARS, "step title")

    @field_validator("text")
    @classmethod
    def _text(cls, v: Optional[str]) -> Optional[str]:
        return _check_text(v, MAX_TEXT_CHARS, "step text")

    @field_validator("layouts")
    @classmethod
    def _layouts(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        if not 1 <= len(v) <= MAX_LAYOUTS or len(set(v)) != len(v):
            raise ValueError(f"step layouts must be 1..{MAX_LAYOUTS} distinct names")
        for name in v:
            if not is_layout_name(name):
                raise ValueError(f"layout name {name!r} does not match {LAYOUT_NAME_PATTERN}")
        return v

    @model_validator(mode="after")
    def _target_for_kind(self) -> "TourStep":
        if self.target is None and self.advance.on in TARGETED_ADVANCE_KINDS:
            raise ValueError(f"step {self.id!r}: advance.on={self.advance.on!r} needs a target")
        return self


class TourFile(_Strict):
    schema_: int = Field(alias="schema")
    kind: Literal[TOUR_FILE_KIND]  # type: ignore[valid-type]
    id: str = Field(pattern=TOUR_ID_PATTERN)
    buildId: str = Field(pattern=BUILD_ID_PATTERN)
    locale: str = Field(pattern=LOCALE_PATTERN)
    title: str
    guideUrl: Optional[str] = None
    entry: Optional[TourEntry] = Field(...)
    steps: list[TourStep]

    @field_validator("id")
    @classmethod
    def _tid(cls, v: str) -> str:
        if not is_tour_id(v):
            raise ValueError(f"tour id {v!r} is invalid")
        return v

    @field_validator("buildId")
    @classmethod
    def _bid(cls, v: str) -> str:
        if not is_build_id(v):
            raise ValueError(f"buildId {v!r} is invalid")
        return v

    @field_validator("locale")
    @classmethod
    def _loc(cls, v: str) -> str:
        return _check_locale(v)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _check_text(v, MAX_TITLE_CHARS, "tour title")  # type: ignore[return-value]

    @field_validator("guideUrl")
    @classmethod
    def _guide_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if len(v) > MAX_URL_CHARS or _origin_of(v) is None or urlsplit(v).username or urlsplit(v).password:
            raise ValueError(f"guideUrl {v!r} must be an absolute http(s) URL without credentials")
        return v

    @field_validator("steps")
    @classmethod
    def _steps(cls, v: list[TourStep]) -> list[TourStep]:
        if not 1 <= len(v) <= MAX_STEPS:
            raise ValueError(f"a tour has 1..{MAX_STEPS} steps")
        ids = [s.id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("step ids must be unique within a tour")
        return v

    @model_validator(mode="after")
    def _schema(self) -> "TourFile":
        if self.schema_ != TOUR_SCHEMA_VERSION:
            raise ValueError(f"schema must be {TOUR_SCHEMA_VERSION}")
        return self


class ManifestTour(_Strict):
    id: str = Field(pattern=TOUR_ID_PATTERN)
    role: str = Field(pattern=ROLE_PATTERN)
    title: dict[str, str]
    entry: Optional[TourEntry] = Field(...)
    steps: int
    files: dict[str, str]
    integrity: dict[str, str]

    @field_validator("id")
    @classmethod
    def _tid(cls, v: str) -> str:
        if not is_tour_id(v):
            raise ValueError(f"tour id {v!r} is invalid")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if not _full(ROLE_PATTERN, v):
            raise ValueError(f"role {v!r} does not match {ROLE_PATTERN}")
        return v

    @field_validator("title")
    @classmethod
    def _title(cls, v: dict[str, str]) -> dict[str, str]:
        for text in v.values():
            _check_text(text, MAX_TITLE_CHARS, "tour title")
        return v

    @field_validator("steps")
    @classmethod
    def _steps(cls, v: int) -> int:
        if not 1 <= v <= MAX_STEPS:
            raise ValueError(f"steps must be 1..{MAX_STEPS}")
        return v

    @field_validator("integrity")
    @classmethod
    def _sri(cls, v: dict[str, str]) -> dict[str, str]:
        for value in v.values():
            if not _full(SRI_PATTERN, value):
                raise ValueError(f"integrity {value!r} is not a sha384 SRI value")
        return v

    @model_validator(mode="after")
    def _keys(self) -> "ManifestTour":
        if not self.files:
            raise ValueError(f"tour {self.id!r} lists no files")
        if set(self.files) != set(self.title) or set(self.files) != set(self.integrity):
            raise ValueError(f"tour {self.id!r}: files, title and integrity must cover the same locales")
        for locale, name in self.files.items():
            if name != tour_file_name(self.id, locale):
                raise ValueError(f"tour {self.id!r}: file for {locale!r} must be {tour_file_name(self.id, locale)!r}")
        return self


class ToursManifest(_Strict):
    schema_: int = Field(alias="schema")
    kind: Literal[TOURS_MANIFEST_KIND]  # type: ignore[valid-type]
    buildId: str = Field(pattern=BUILD_ID_PATTERN)
    defaultLocale: str = Field(pattern=LOCALE_PATTERN)
    locales: list[str]
    testIdAttribute: str = Field(pattern=TEST_ID_ATTRIBUTE_PATTERN)
    guideOrigin: Optional[str] = None
    layouts: Optional[dict[str, str]] = None
    tours: list[ManifestTour]

    @field_validator("buildId")
    @classmethod
    def _bid(cls, v: str) -> str:
        if not is_build_id(v):
            raise ValueError(f"buildId {v!r} is invalid")
        return v

    @field_validator("locales")
    @classmethod
    def _locales(cls, v: list[str]) -> list[str]:
        # `fr-BE`, `fr-be` and `fr_BE` name one language and, on a case-insensitive
        # filesystem, one file: the second would overwrite the first and break its hash.
        if not 1 <= len(v) <= MAX_LOCALES or len({t.casefold().replace("_", "-") for t in v}) != len(v):
            raise ValueError(f"locales must be 1..{MAX_LOCALES} distinct tags (case and -/_ do not distinguish)")
        for tag in v:
            _check_locale(tag)
        return v

    @field_validator("testIdAttribute")
    @classmethod
    def _attr(cls, v: str) -> str:
        if not _full(TEST_ID_ATTRIBUTE_PATTERN, v):
            raise ValueError(f"testIdAttribute {v!r} does not match {TEST_ID_ATTRIBUTE_PATTERN}")
        return v

    @field_validator("guideOrigin")
    @classmethod
    def _origin(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and _origin_of(v) != v:
            raise ValueError(f"guideOrigin {v!r} must be an origin only (scheme://host[:port])")
        return v

    @field_validator("layouts")
    @classmethod
    def _layouts(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if v is None:
            return v
        if not 1 <= len(v) <= MAX_LAYOUTS:
            raise ValueError(f"layouts must map 1..{MAX_LAYOUTS} names")
        for name, query in v.items():
            if not is_layout_name(name) or not is_media_query(query):
                raise ValueError(f"layout {name!r}: {query!r} is not a valid name / media query")
        return v

    @field_validator("tours")
    @classmethod
    def _tours(cls, v: list[ManifestTour]) -> list[ManifestTour]:
        if len(v) > MAX_TOURS:
            raise ValueError(f"at most {MAX_TOURS} tours")
        ids = [t.id for t in v]
        if ids != sorted(ids):
            raise ValueError("tours must be sorted by id")
        if len({i.casefold() for i in ids}) != len(ids):
            raise ValueError("tour ids must be unique (case-insensitively)")
        return v

    @model_validator(mode="after")
    def _cross(self) -> "ToursManifest":
        if self.schema_ != TOUR_SCHEMA_VERSION:
            raise ValueError(f"schema must be {TOUR_SCHEMA_VERSION}")
        if self.defaultLocale not in self.locales:
            raise ValueError("defaultLocale must be one of locales")
        for tour in self.tours:
            if not set(tour.files) <= set(self.locales):
                raise ValueError(f"tour {tour.id!r} has files for locales outside 'locales'")
            if self.defaultLocale not in tour.files:
                raise ValueError(f"tour {tour.id!r} has no file for the default locale {self.defaultLocale!r}")
        return self


# ── Serialization ─────────────────────────────────────────────────────────────


def dumps_canonical(obj: Any) -> bytes:
    """The one serializer for tour data: deterministic UTF-8 JSON with LF endings."""
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def sri_sha384(data: bytes) -> str:
    """Subresource-Integrity value over ``data``, as the Fetch API checks it."""
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def _schema_major(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise TourDataInvalid("payload must be a JSON object")
    major = payload.get("schema")
    if isinstance(major, bool) or not isinstance(major, int):
        raise TourDataInvalid("missing integer 'schema'")
    if major not in ACCEPTED_TOUR_SCHEMA_MAJORS:
        raise TourSchemaUnsupported(
            f"tour schema {major} is not supported (supports {list(ACCEPTED_TOUR_SCHEMA_MAJORS)})"
        )


def _validate(model: type[BaseModel], payload: Any) -> Any:
    _schema_major(payload)
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise TourDataInvalid(f"{where}: {first['msg']}") from exc


def parse_tours_manifest(payload: Any) -> ToursManifest:
    return _validate(ToursManifest, payload)


def parse_tour_file(payload: Any) -> TourFile:
    return _validate(TourFile, payload)


def check_consistency(manifest: ToursManifest, files: Mapping[str, bytes]) -> list[str]:
    """Cross-file rules a single-file schema cannot express; returns the problems found."""
    problems: list[str] = []
    for tour in manifest.tours:
        for locale, name in tour.files.items():
            data = files.get(name)
            if data is None:
                problems.append(f"{name}: listed in the manifest but missing")
                continue
            if sri_sha384(data) != tour.integrity[locale]:
                problems.append(f"{name}: integrity does not match its bytes")
            try:
                tf = parse_tour_file(json.loads(data.decode("utf-8")))
            except (ValueError, UnicodeDecodeError) as exc:
                problems.append(f"{name}: {exc}")
                continue
            if tf.buildId != manifest.buildId:
                problems.append(f"{name}: buildId {tf.buildId!r} differs from the manifest's {manifest.buildId!r}")
            if tf.id != tour.id or tf.locale != locale:
                problems.append(f"{name}: id/locale do not match the manifest entry")
            if tf.title != tour.title[locale]:
                problems.append(f"{name}: title differs from the manifest entry")
            if (tf.entry.route if tf.entry else None) != (tour.entry.route if tour.entry else None):
                problems.append(f"{name}: entry differs from the manifest entry")
            if len(tf.steps) != tour.steps:
                problems.append(f"{name}: {len(tf.steps)} steps, manifest says {tour.steps}")
            known = set(manifest.layouts or {})
            for step in tf.steps:
                if step.layouts and not set(step.layouts) <= known:
                    problems.append(f"{name}: step {step.id!r} uses layouts the manifest does not map")
            if tf.guideUrl is not None and (
                manifest.guideOrigin is None or _origin_of(tf.guideUrl) != manifest.guideOrigin
            ):
                problems.append(f"{name}: guideUrl is not on the manifest's guideOrigin")
    return problems


# ── Published JSON Schemas ────────────────────────────────────────────────────


def _open(node: Any) -> Any:
    """Drop ``additionalProperties: false``: consumers must ignore unknown keys."""
    if isinstance(node, dict):
        return {k: _open(v) for k, v in node.items() if not (k == "additionalProperties" and v is False)}
    if isinstance(node, list):
        return [_open(v) for v in node]
    return node


def _published(model: type[BaseModel], title: str) -> dict:
    from ..config_schema import _canonicalise

    schema = _open(_canonicalise(model.model_json_schema(by_alias=True)))
    schema["title"] = title
    schema["x-steppy-tour-schema"] = TOUR_SCHEMA_VERSION
    return schema


def generate_manifest_schema() -> dict:
    return _published(ToursManifest, MANIFEST_SCHEMA_TITLE)


def generate_tour_schema() -> dict:
    return _published(TourFile, TOUR_SCHEMA_TITLE)


def manifest_schema_json() -> str:
    return json.dumps(generate_manifest_schema(), indent=2, sort_keys=True) + "\n"


def tour_schema_json() -> str:
    return json.dumps(generate_tour_schema(), indent=2, sort_keys=True) + "\n"
