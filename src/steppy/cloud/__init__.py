"""The client for a Steppy Cloud instance: uploading a run's results.

Steppy works with no cloud at all, and nothing outside this package may assume
one exists. What lives here is the *client* half of the upload protocol
(:mod:`steppy.contracts.upload`): ``steppy run --upload`` and ``steppy bundle
upload``. It is open source on purpose: it runs inside a customer's CI holding
their repository and their credentials, and a closed binary doing that fails
the security review Steppy's whole positioning is built on passing.

**Lazy-imported only.** ``httpx`` costs ~40ms at import and no other command
needs it, so every import of this package lives inside a command body. Guard:
``tests/test_no_cloud_import_on_default_path.py``.

Split by concern, and the split is the boundary worth keeping:

``client``  transport — where the instance is, the API key, retries, errors
``upload``  the upload protocol, and the only module that knows what a run's
            files are
"""

from .client import (
    CloudAuthError,
    CloudClient,
    CloudConfigError,
    CloudError,
    CloudUnreachable,
    Endpoint,
    resolve_endpoint,
)
from .upload import (
    ArchiveSource,
    BlobSource,
    ReportedSource,
    TreeSource,
    UploadOutcome,
    attach_report,
    check_key,
    upload_run,
)


def connect(endpoint: Endpoint, *, timeout: float = 60.0) -> CloudClient:
    """Open a session against *endpoint*.

    The one place the CLI makes a client, so a test can swap in a fake
    transport by replacing this function.
    """
    return CloudClient(endpoint, timeout=timeout)


__all__ = [
    "ArchiveSource",
    "BlobSource",
    "CloudAuthError",
    "CloudClient",
    "CloudConfigError",
    "CloudError",
    "CloudUnreachable",
    "Endpoint",
    "ReportedSource",
    "TreeSource",
    "UploadOutcome",
    "attach_report",
    "check_key",
    "connect",
    "resolve_endpoint",
    "upload_run",
]
