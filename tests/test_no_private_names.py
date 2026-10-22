"""Guard: no client project name, private machine path or former example domain ships.

The repository is public, and Steppy grew up inside client projects, so their
names used to leak into docs, fixtures and example configs, along with the
author's original example domain. This test scans every file git would publish
(tracked, plus untracked-but-not-ignored, so a new file is caught before its
first commit) against the local deny-list described in ``tests/_private_terms.py``,
and skips in a checkout that does not have that list.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests._private_terms import private_terms_re

_ROOT = Path(__file__).resolve().parents[1]

_BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".mp4", ".wav", ".zip"})


def _published_files() -> list[Path]:
    if shutil.which("git") is None or not (_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    paths = (_ROOT / p for p in out.decode("utf-8").split("\0") if p)
    return [p for p in paths if p.is_file() and p.suffix.lower() not in _BINARY_SUFFIXES]


def test_no_private_terms_in_published_files():
    pattern = private_terms_re()
    offenders = []
    for path in _published_files():
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            m = pattern.search(line)
            if m:
                offenders.append(f"{path.relative_to(_ROOT).as_posix()}:{lineno}: {m.group(0)!r}")
    assert not offenders, "private terms found in published files:\n" + "\n".join(offenders)
