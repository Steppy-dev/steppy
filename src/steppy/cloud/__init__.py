"""Clients for a Steppy control plane.

Steppy works with no control plane at all, and nothing outside this package may
assume one exists. What lives here is the *client* half of the versioned wire
contracts a plane publishes — pushing results (``steppy bundle upload``), and
later claiming work (``steppy ci run``, ``steppy agent``). They are open source
on purpose: they run inside a customer's CI holding their repository and their
credentials, and a closed binary doing that fails the security review Steppy's
whole positioning is built on passing.

**Lazy-imported only.** ``httpx`` costs ~40ms at import and no other command
needs it, so every import of this package lives inside a command body. Guard:
``tests/test_no_cloud_import_on_default_path.py``.

Split by concern, and the split is the boundary worth keeping:

``client``  transport — endpoint resolution, bearer auth, retries, errors
``upload``  the bundle upload protocol, and the only module that knows what a
            bundle is
"""

from typing import Optional

from .client import CloudAuthError, CloudClient, CloudError, Endpoint, resolve_endpoint
from .upload import ArchiveSource, BlobSource, TreeSource, UploadOutcome, derive_exit_code, upload_bundle


def cloud_client(
    *,
    url: Optional[str] = None,
    token: Optional[str] = None,
    config_url: Optional[str] = None,
    timeout: float = 60.0,
) -> CloudClient:
    """Resolve an endpoint and open a session against it.

    The two halves are separately useful — tests build a client around a fake
    transport, and a future ``steppy cloud status`` will want the endpoint
    without a session — so this is only the pairing every command actually
    performs, not a replacement for either.
    """
    return CloudClient(resolve_endpoint(url, token, config_url=config_url), timeout=timeout)


__all__ = [
    "ArchiveSource",
    "BlobSource",
    "CloudAuthError",
    "CloudClient",
    "CloudError",
    "Endpoint",
    "TreeSource",
    "UploadOutcome",
    "cloud_client",
    "derive_exit_code",
    "resolve_endpoint",
    "upload_bundle",
]
