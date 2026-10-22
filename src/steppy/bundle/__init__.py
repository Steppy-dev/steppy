"""Results-bundle producer — ``steppy bundle create`` and ``upload``.

Packages one run's ``output/`` tree into a portable, self-describing archive.
The contract it emits lives in :mod:`steppy.contracts.bundle`; this package is
the producer side of it.

``create`` and ``upload`` share everything up to the point bytes are written:
:func:`plan_bundle` decides the bundle, :func:`archive_members` maps each
manifest entry back to its bytes, and only ``create`` goes on to tar them.
Uploading writes no archive at all — the protocol is per-file. The transport
lives in :mod:`steppy.cloud`, not here.

**Lazy-imported only.** Nothing here may be reached by ``import steppy.cli`` —
``archive`` optionally pulls in ``zstandard`` and the walk is not free. Guard:
``tests/test_no_bundle_import_on_default_path.py``.

Module split follows the concerns, not the file count:

``hashing``   streaming sha256, shared with the worker-image tagger
``classify``  path → :class:`~steppy.contracts.bundle.BundleFile`; pure
``results``   junit XML + event JSONL → ``manifest.tests[]``
``manifest``  assembly: run identity, ordering, the size budget, ``truncated``
``archive``   deterministic tar + codec dispatch (zstd, gzip)
``create``    orchestration, and the only module that touches the console
"""

from .create import (
    BundleError,
    BundlePlan,
    BundleResult,
    archive_members,
    create_bundle,
    inspect_bundle,
    plan_bundle,
    verify_bundle,
)

__all__ = [
    "BundleError",
    "BundlePlan",
    "BundleResult",
    "archive_members",
    "create_bundle",
    "inspect_bundle",
    "plan_bundle",
    "verify_bundle",
]
