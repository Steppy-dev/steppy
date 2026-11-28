"""Names somebody else wrote, and the paths they may become.

A results bundle, a run record and a coverage record all name files, and any of them
may come from another machine. Joined to a directory unchecked, such a name reads or
writes anywhere: ``..``, an absolute path, a drive, a link. On Windows an UNC path
(``\\\\host\\share``) also makes the machine authenticate to whoever owns the host, just
by asking whether the file exists.

So a name is checked **as text first**, before the file system is touched at all, and
then the path it makes is resolved and must still be inside its root, links followed.
The text rules are the same on every platform, so a bundle is judged the same wherever
it is read. Only the names Windows reserves for devices (``CON``, ``NUL``, ``COM1``…)
depend on it: elsewhere they are ordinary files, and a scenario may be called ``aux``.

Stdlib-only leaf: ``bundle``, ``report`` and ``coverage`` all import it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

#: Windows opens a device for these, whatever the directory and the extension.
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9\u00b9\u00b2\u00b3])", re.IGNORECASE)

#: A control character, NUL included, is in no name a run writes.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class UnsafePath(ValueError):
    """A name that must not become a path. The message says why."""


def is_reserved(name: str) -> bool:
    """Whether Windows reads this component as a device (``nul.png`` is ``NUL``)."""
    return bool(_RESERVED.fullmatch(name.split(".", 1)[0].rstrip(" ")))


def _component_reason(part: str, windows: bool) -> Optional[str]:
    if not part:
        return "it has an empty part"
    if not part.strip(" ."):
        # ``.``, ``..``, and the spellings Windows reduces to them (``.. ``, ``...``).
        return "it has a . or .. part"
    if windows and (part[-1] in " ." or is_reserved(part)):
        return "it is a name Windows reserves"
    return None


def unsafe_reason(name: str, *, windows: Optional[bool] = None) -> Optional[str]:
    """Why a relative POSIX path must not be followed, or ``None`` when it may.

    Looks at the text only. ``windows`` defaults to the platform.
    """
    windows = os.name == "nt" if windows is None else windows
    if not isinstance(name, str) or not name:
        return "it is empty"
    if _CONTROL.search(name):
        return "it has a control character"
    if "\\" in name:
        return "it has a backslash"
    if name.startswith("/"):
        return "it is absolute"
    if ":" in name:
        # A drive (``C:/x``, ``C:x``), or a stream of another file (``a.png:b``).
        return "it has a colon"
    for part in name.split("/"):
        reason = _component_reason(part, windows)
        if reason:
            return reason
    return None


def points_outside(name: str) -> bool:
    """Whether the text of *name* itself leads out: absolute, a drive, or a ``..``.

    Narrower than :func:`unsafe_reason`, for a name nobody is about to follow: a file a
    project wrote as ``20:00:00.log`` is ordinary on the system that wrote it.
    """
    parts = name.replace("\\", "/").split("/")
    return (
        not parts[0] or bool(re.match(r"[A-Za-z]:", name)) or any(p and not p.strip(" .") and ".." in p for p in parts)
    )


def is_plain_name(name: object, *, windows: Optional[bool] = None) -> bool:
    """Whether *name* is one ordinary path component: a run id, a digest."""
    return isinstance(name, str) and "/" not in name and unsafe_reason(name, windows=windows) is None


def member_path(root: Path, name: str) -> Path:
    """The file *name* means under *root*, resolved. Raises :class:`UnsafePath`.

    The resolved path is what to open: opening ``root / name`` again would follow a
    link that changed meanwhile.
    """
    reason = unsafe_reason(name)
    if reason:
        raise UnsafePath(reason)
    try:
        base = Path(root).resolve()
        path = (base / name).resolve()
    except (OSError, RuntimeError, ValueError) as e:
        # A loop of links is a RuntimeError before Python 3.13.
        raise UnsafePath("it cannot be resolved") from e
    if not path.is_relative_to(base):
        raise UnsafePath("a link takes it outside")
    return path


def shown(name: object, limit: int = 120) -> str:
    """*name* as it may be printed: quoted, so no control character reaches a terminal."""
    text = ascii(name)
    return text if len(text) <= limit else text[: limit - 1] + "…"
