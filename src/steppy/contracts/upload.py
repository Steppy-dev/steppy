"""The upload protocol: the ``/v1`` calls the CLI makes with a project's API key.

What ``steppy run --upload`` and ``steppy bundle upload`` send and read. The
server lives elsewhere (Steppy Cloud, or a self-hosted instance of it) and
mirrors these models; this module is the reference both sides build against::

    GET  /v1/key                       → KeyResponse        the start check of `steppy run --upload`
    POST /v1/runs                      CreateRunRequest     Idempotency-Key: manifest.run.run_id
                                       → CreateRunResponse  one upload URL per wanted, missing file
    PUT  <upload url>                  the file's bytes, no Authorization header → 200 or 204
    POST /v1/runs/{run_id}/finalize    → FinalizeResponse   the run's URL

Every call but the PUT sends ``Authorization: Bearer stp_…``. An error answer
carries an :class:`ErrorBody`. The client never retries 401 or 403, reads 413 as
"the manifest is too large", prints the ``detail`` of a 422 (a report version
the server does not accept says to upgrade the CLI there), and retries 429, 5xx
and transport errors twice.

**The run's metadata is ``manifest.run``**: identity, git, the resolved run
spec, the Steppy version and ``created_at``. It is not repeated beside the
manifest, so the two can never disagree. New metadata lands there additively
under the bundle contract's rules.

**``report.json`` is a file like the others.** The CLI builds it with
:func:`steppy.report.model.build_model` and lists it in ``manifest.files`` at
:data:`REPORT_PATH` with kind :data:`REPORT_KIND`, so the server can ask for it,
deduplicate it and store it like a screenshot. Other files of kind ``report``
can exist (anything under ``output/reports/``); the run's report is the one at
that path. In it, every capture reference (``ReportStep.screenshots``,
``ReportVariant.failure_screenshot``) is the file's **manifest path**, resolved
to bytes through ``manifest.files[].sha256``. The server asked only for the
files it wanted, so a reader must not assume it holds every file referenced.

**Idempotent by key.** Creating the run again with the same ``Idempotency-Key``
returns the same ``run_id`` and the files still missing, which is how an
interrupted upload resumes and how an expired upload URL is replaced. Finalizing
twice changes nothing.

Same compatibility charter as :mod:`steppy.contracts.bundle`: every model is
``extra="allow"``, absent keys default, and fields are only ever added. The API
version is the path (:data:`API_VERSION`); the manifest and the report carry
their own versions (``steppy_bundle``, ``steppy_report``).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .bundle import BundleFile, BundleManifest

#: The API version, and the path segment every endpoint above sits under.
API_VERSION = "v1"

#: Where the run's ``report.json`` sits in the manifest, and the kind it carries.
#: At the root, beside ``junit.xml`` and ``events/``: every file from the
#: ``output/`` tree has a path under ``output/``, so this one cannot collide.
REPORT_PATH = "report.json"
REPORT_KIND = "report"

#: The header ``POST /v1/runs`` must carry, set to ``manifest.run.run_id``.
IDEMPOTENCY_HEADER = "Idempotency-Key"


class _Wire(BaseModel):
    """Base for every wire model: unknown fields are kept, never rejected."""

    model_config = ConfigDict(extra="allow")


class ProjectRef(_Wire):
    """The project an API key belongs to."""

    id: str
    name: str = ""
    #: The project's page, for the CLI to print.
    url: Optional[str] = None


class KeyResponse(_Wire):
    """``GET /v1/key``: who the key is. The start check of ``steppy run --upload``."""

    project: ProjectRef


class CreateRunRequest(_Wire):
    """``POST /v1/runs``: the run, described by its manifest.

    ``manifest.files`` lists every file of the run with its hash, size and kind,
    ``report.json`` among them (:data:`REPORT_PATH`). ``manifest.run`` is the
    run's metadata.
    """

    manifest: BundleManifest


class UploadTarget(_Wire):
    """One file the server wants and does not hold yet."""

    sha256: str
    #: Where to PUT the bytes: presigned on S3, a signed API URL on the
    #: filesystem driver. Carries its own authorisation, so the PUT sends no
    #: ``Authorization`` header (a store given both rejects the pair).
    url: str
    #: Headers the PUT must send besides ``Content-Length``, for a URL that was
    #: signed with them (a content type, a checksum). Usually empty.
    headers: dict[str, str] = Field(default_factory=dict)


class CreateRunResponse(_Wire):
    """The run, created or found again by its ``Idempotency-Key``."""

    run_id: str
    #: The run's number within its project, when the server numbers runs.
    number: Optional[int] = None
    #: The run's page. Also returned by finalize, which is the one to print.
    url: Optional[str] = None
    #: One entry per **distinct digest** the server wants and does not hold.
    #: Files it does not want (by default the screenshots of passing variants)
    #: and files it already holds are absent; an empty list means there is
    #: nothing to send. Two manifest paths sharing a digest are one upload.
    uploads: list[UploadTarget] = Field(default_factory=list)


class FinalizeResponse(_Wire):
    """``POST /v1/runs/{run_id}/finalize``: the files checked, the report ingested."""

    run_id: Optional[str] = None
    number: Optional[int] = None
    #: The run's page, which the CLI prints.
    url: Optional[str] = None
    #: The run's status once ingested (``passed``, ``failed``, …). A plain
    #: string: unknown values are data, not errors.
    status: Optional[str] = None


class ErrorBody(_Wire):
    """What every non-2xx answer carries.

    ``detail`` is a sentence written for the person reading the CLI's output —
    a string, or FastAPI's list of ``{loc, msg, type}`` for a request that did
    not validate. ``code`` is optional and machine-readable.
    """

    detail: Any = None
    code: Optional[str] = None


def report_file(manifest: BundleManifest) -> Optional[BundleFile]:
    """The run's ``report.json`` entry in *manifest*, or ``None``."""
    return next((f for f in manifest.files if f.path == REPORT_PATH), None)


SCHEMA_TITLE = "Steppy upload protocol"

#: Which model each call sends and reads, published in the schema so a server
#: in another language can find them without reading this module.
_ENDPOINTS: dict[str, dict[str, Any]] = {
    "GET /v1/key": {"response": "KeyResponse"},
    "POST /v1/runs": {
        "request": "CreateRunRequest",
        "response": "CreateRunResponse",
        "headers": [IDEMPOTENCY_HEADER],
    },
    "PUT <upload url>": {"request": "the file's bytes", "response": "200 or 204"},
    "POST /v1/runs/{run_id}/finalize": {"response": "FinalizeResponse"},
}

_MODELS = (KeyResponse, CreateRunRequest, CreateRunResponse, FinalizeResponse, ErrorBody)


def generate_upload_schema() -> dict:
    """JSON Schema for every model of the protocol, under one ``$defs``.

    Same arrangement as the bundle and report schemas: generated from the
    models, committed, and byte-compared by a drift guard.
    """
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema([(model, "validation") for model in _MODELS], title=SCHEMA_TITLE)
    schema["x-steppy-api-version"] = API_VERSION
    schema["x-steppy-endpoints"] = {
        call: {
            key: (f"#/$defs/{value}" if key in ("request", "response") and value in schema["$defs"] else value)
            for key, value in shape.items()
        }
        for call, shape in _ENDPOINTS.items()
    }
    schema["x-steppy-error"] = "#/$defs/ErrorBody"
    schema["x-steppy-report"] = {"path": REPORT_PATH, "kind": REPORT_KIND}
    return schema


def schema_json() -> str:
    """The canonical serialized form written to disk and compared in tests."""
    import json

    return json.dumps(generate_upload_schema(), indent=2, sort_keys=True) + "\n"
