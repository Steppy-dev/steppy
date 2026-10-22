"""Guard: pip knows this package as ``steppy-dev``.

``steppy`` on PyPI is an unrelated project, so the distribution is named
``steppy-dev`` while the import package and the command stay ``steppy``. The two
ways to get that wrong only show on someone else's machine: an install hint
reading ``pip install 'steppy[report]'`` installs the other project, and a
``steppy @ git+...`` requirement fails with pip's "inconsistent name" error.
"""

import importlib.metadata
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

import steppy

_ROOT = Path(__file__).resolve().parents[1]

# `steppy[extras]` (also Rich-escaped as `steppy\[extras]`) and `steppy @ <url>`
# are where pip reads a distribution name. The lookbehind leaves `steppy-dev[`,
# a path such as `/tmp/steppy[retry,report]` and `foo.steppy[` alone.
_PIP_NAME_RE = re.compile(r"(?<![\w./-])steppy(?:\\*\[| @ )")
_BARE_INSTALL_RE = re.compile(r"pip install +(?:-U +|--upgrade +)?['\"]?steppy(?![\w-])")

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


def test_pyproject_names_the_distribution():
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == steppy.DISTRIBUTION
    assert project["scripts"] == {"steppy": "steppy.cli:app"}, "the command stays `steppy`"


def test_the_installed_metadata_answers_to_the_distribution_name():
    """The worker image reads its requirements and its tag's version from the
    installed metadata, under :data:`steppy.DISTRIBUTION`."""
    try:
        version = importlib.metadata.version(steppy.DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        pytest.fail(f"{steppy.DISTRIBUTION} is not installed; reinstall with `python -m pip install -e .`")
    assert version == steppy.__version__


def test_no_install_hint_names_the_other_project():
    offenders = []
    for path in _published_files():
        rel = path.relative_to(_ROOT).as_posix()
        if rel == "tests/test_distribution_name.py":
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        for lineno, line in enumerate(raw.decode("utf-8", errors="ignore").splitlines(), 1):
            if _PIP_NAME_RE.search(line) or _BARE_INSTALL_RE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not offenders, "pip would read `steppy` as the unrelated project here; write `steppy-dev`:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("pip install 'steppy[report]'", True),
        ("Install it with: pip install 'steppy\\[retry]'", True),
        ('pip install "steppy @ git+https://example.test/steppy.git"', True),
        ("steppy[report,retry] @ git+https://example.test/steppy.git@v1", True),
        ("pip install steppy", True),
        ("pip install -U steppy", True),
        ("pip install 'steppy-dev[report]'", False),
        ("pip install steppy-dev", False),
        ("RUN pip install '/tmp/steppy[retry,report]'", False),
        ("pip install git+https://example.test/steppy.git", False),
        ("pip uninstall steppy", False),
    ],
)
def test_the_patterns(line: str, flagged: bool):
    assert bool(_PIP_NAME_RE.search(line) or _BARE_INSTALL_RE.search(line)) is flagged
