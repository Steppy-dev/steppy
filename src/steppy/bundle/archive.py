"""Deterministic tar writing and compression-codec dispatch.

**Determinism is a feature, not tidiness.** The upload protocol the contract
describes is content-addressed: a consumer asks only for digests it does not
already hold. That only pays off if an unchanged run produces an unchanged
archive, so every source of ambient nondeterminism is pinned here — member
order, timestamps, ownership, mode, tar format, and the gzip header.

Codec: zstd when the optional ``steppy-dev[bundle]`` extra is installed, gzip
otherwise. Falling back rather than failing is deliberate — a results archive is
useful with no control plane at all, and making the free path depend on an extra
would be the wrong trade for a few percent of size on data that is already
PNG-compressed.
"""

from __future__ import annotations

import gzip
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

FORMAT_AUTO = "auto"
FORMAT_ZSTD = "zst"
FORMAT_GZIP = "gz"

#: zstd level 10 is the knee of the curve for mixed PNG/JSON/text: materially
#: smaller than the default 3 on the text members, and still fast enough that
#: compression never dominates the walk.
ZSTD_LEVEL = 10

_EXTRA_HINT = "install the extra with: pip install 'steppy-dev[bundle]'"


class ArchiveError(Exception):
    """Unusable codec request — always actionable."""


def zstandard_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("zstandard") is not None


def resolve_format(requested: str) -> str:
    """Resolve ``auto`` to a concrete codec, or validate an explicit one.

    An explicit ``zst`` that cannot be honoured is an ERROR, never a silent
    downgrade: someone who named the codec is entitled to get it or be told why.
    """
    if requested == FORMAT_AUTO:
        return FORMAT_ZSTD if zstandard_available() else FORMAT_GZIP
    if requested == FORMAT_ZSTD:
        if not zstandard_available():
            raise ArchiveError(f"--format zst needs the 'zstandard' package, which is not installed; {_EXTRA_HINT}")
        return FORMAT_ZSTD
    if requested == FORMAT_GZIP:
        return FORMAT_GZIP
    raise ArchiveError(f"unknown --format {requested!r} (expected auto, zst or gz)")


def extension_for(fmt: str) -> str:
    return ".tar.zst" if fmt == FORMAT_ZSTD else ".tar.gz"


def _normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip everything that varies between machines and runs."""
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    return info


@contextmanager
def _compressed_stream(path: Path, fmt: str) -> Iterator:
    """Open the outer compressed stream, pinned for reproducibility."""
    with open(path, "wb") as raw:
        if fmt == FORMAT_ZSTD:
            import zstandard

            # threads=0 keeps it single-threaded. Multithreaded zstd is
            # deterministic in practice, but pinning removes the question.
            compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL, threads=0)
            with compressor.stream_writer(raw) as stream:
                yield stream
        else:
            # NOT tarfile.open(mode="w:gz"): that stamps the current time into
            # the gzip header, which alone would make every archive differ.
            # `filename=""` is equally load-bearing — GzipFile otherwise takes
            # the name from `fileobj.name` and embeds it, so the same tree
            # written to two paths would produce two different files.
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
                yield stream


@dataclass(frozen=True)
class ArchiveMember:
    """One file to write: where it comes from, where it lands in the archive."""

    #: POSIX path inside the archive. Always ``/``-separated — a bundle built on
    #: Windows must extract identically on Linux.
    arcname: str
    source: Optional[Path] = None
    data: Optional[bytes] = None

    def __post_init__(self) -> None:
        if (self.source is None) == (self.data is None):
            raise ValueError("ArchiveMember needs exactly one of source or data")


def write_archive(target: Path, members: list[ArchiveMember], *, fmt: str) -> int:
    """Write *members* to *target*. Returns the compressed size in bytes.

    Members are written in the order given — the caller sorts, so that
    ``manifest.json`` can be member #1 and readable without a full scan.
    No directory entries are emitted at all, which sidesteps every
    Windows/POSIX directory-metadata difference.
    """
    import io

    target.parent.mkdir(parents=True, exist_ok=True)
    with _compressed_stream(target, fmt) as stream:
        # PAX, not GNU: GNU's long-name and sparse handling is not stable
        # across platforms, and paths here can be long (scenario/locale/layout).
        with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT, encoding="utf-8") as tar:
            for member in members:
                if member.data is not None:
                    info = tarfile.TarInfo(name=member.arcname)
                    info.size = len(member.data)
                    tar.addfile(_normalise(info), io.BytesIO(member.data))
                else:
                    info = tar.gettarinfo(name=str(member.source), arcname=member.arcname)
                    info = _normalise(info)
                    with open(member.source, "rb") as fh:
                        tar.addfile(info, fh)
    return target.stat().st_size


@contextmanager
def open_archive(path: Path) -> Iterator[tarfile.TarFile]:
    """Read an archive written by :func:`write_archive`, sniffing the codec.

    Sniffs by extension: the producer chose it, and a bundle is always named by
    its producer.
    """
    if path.suffix == ".zst":
        if not zstandard_available():
            raise ArchiveError(f"reading {path.name} needs the 'zstandard' package; {_EXTRA_HINT}")
        import zstandard

        with open(path, "rb") as raw:
            decompressor = zstandard.ZstdDecompressor()
            with decompressor.stream_reader(raw) as stream:
                with tarfile.open(fileobj=stream, mode="r|") as tar:
                    yield tar
    elif path.suffix == ".gz":
        with tarfile.open(path, mode="r:gz") as tar:
            yield tar
    else:
        raise ArchiveError(f"unrecognised bundle extension {path.suffix!r} (expected .tar.zst or .tar.gz)")


def read_members(
    path: Path,
    *,
    into: Optional[dict[str, Path]] = None,
    texts: Optional[set[str]] = None,
) -> tuple[dict[str, bytes], list[str]]:
    """Extract selected members in **one** sequential pass.

    ``into`` maps an arcname to the file it should be written to; ``texts`` names
    arcnames to return as bytes. Returns ``(fetched_texts, warnings)``, with one
    warning per requested arcname the archive did not contain.

    One pass because the zstd path is a non-seekable ``r|`` stream: members arrive
    once, in archive order, and ``getmember`` does not exist. That also rules out
    "extract everything, then pick" — hence the caller declaring what it wants up
    front, which it can do because ``manifest.json`` is member #1.

    **Destinations come from the caller, never from the archive.** Going through
    ``tarfile`` member-by-member skips Python's extraction filters, and an
    arcname in a downloaded bundle is attacker-controlled — so this function
    writes only to paths a caller already decided on, and refuses anything that
    is not a regular file (a hostile archive can carry symlinks and hardlinks,
    for which ``extractfile`` returns ``None`` anyway).
    """
    wanted_files = dict(into or {})
    wanted_texts = set(texts or ())
    fetched: dict[str, bytes] = {}
    warnings: list[str] = []
    seen: set[str] = set()

    with open_archive(path) as tar:
        for member in tar:
            name = member.name
            if name not in wanted_files and name not in wanted_texts:
                continue
            if not member.isfile():
                warnings.append(f"{name} is not a regular file in the archive; skipped")
                seen.add(name)
                continue
            handle = tar.extractfile(member)
            if handle is None:
                warnings.append(f"{name} could not be read from the archive; skipped")
                seen.add(name)
                continue
            seen.add(name)
            if name in wanted_texts:
                payload = handle.read()
                fetched[name] = payload
                target = wanted_files.get(name)
                if target is not None:
                    _write_bytes(target, payload)
                continue
            _stream_to(handle, wanted_files[name])

    for name in sorted((set(wanted_files) | wanted_texts) - seen):
        warnings.append(f"{name} is listed in the manifest but absent from the archive")
    return fetched, warnings


def _write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _stream_to(handle, target: Path, chunk: int = 1024 * 1024) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as out:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            out.write(block)
