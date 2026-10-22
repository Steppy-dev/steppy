"""Streaming content hashing.

``init_cli._sha256`` hashes *text* and is fine for scaffold files; a bundle
walks screenshot trees that reach hundreds of megabytes, so it needs a chunked
reader that never holds a file in memory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: 1 MiB. Large enough that syscall overhead disappears, small enough that a
#: many-worker run does not multiply into meaningful resident memory.
CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path, *, chunk_size: int = CHUNK_SIZE) -> str:
    """Hex sha256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
