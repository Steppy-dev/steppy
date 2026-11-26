"""The rules that turn somebody else's name into a path, or refuse to.

Text rules are asserted for both platforms on whichever one runs, through the
``windows`` argument. Links are real ones, skipped where the system makes none.
"""

import os
from pathlib import Path

import pytest

from steppy.safe_paths import (
    UnsafePath,
    is_plain_name,
    is_reserved,
    member_path,
    points_outside,
    shown,
    unsafe_reason,
)

REFUSED = [
    "",
    ".",
    "..",
    "../x.png",
    "a/../../x.png",
    "a/..",
    "./a",
    "a/./b",
    "a//b",
    "a/",
    "/etc/passwd",
    "//host/share/x",
    "a/.. /x",  # Windows strips the trailing space
    "a/... /x",
    "a/.../x",
    "a/ /x",
    "..\\x.png",
    "a\\..\\..\\x.png",
    "a\\b.png",
    "\\x",
    "\\\\host\\share\\x.png",
    "\\\\?\\C:\\x",
    "//?/C:/x",
    "C:\\x.png",
    "C:/x.png",
    "C:x.png",
    "a/C:/x.png",
    "a.png:stream",
    "a\x00b",
    "a\nb",
    "a\x1b[31m",
    "a\x7f",
]


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
@pytest.mark.parametrize("name", REFUSED, ids=ascii)
def test_a_name_that_may_leave_its_directory_is_refused_everywhere(name, windows):
    assert unsafe_reason(name, windows=windows)
    assert not is_plain_name(name, windows=windows)
    with pytest.raises(UnsafePath):
        member_path(Path.cwd(), name)


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
@pytest.mark.parametrize(
    "name",
    [
        "junit.xml",
        "output/screenshots/checkout/fr-BE/mobile/step-01-a.png",
        "events/checkout__fr-mobile.jsonl",
        "output/screenshots/mon scénario/step-01.png",
        "output/.steppy-run.json",
        "a..b/..c/d..",
        "output/console/x.png",  # starts like a device, and is none
        "com10/x",
    ],
)
def test_what_a_run_writes_is_allowed(name, windows):
    if windows and name.endswith("."):
        pytest.skip("a trailing dot is no name on Windows")
    assert unsafe_reason(name, windows=windows) is None


@pytest.mark.parametrize(
    "name",
    ["CON", "con.png", "NUL", "nul.json", "a/aux/b.png", "COM1", "lpt9.txt", "com\u00b9", "CONOUT$", "a. ", "a."],
)
def test_the_names_windows_keeps_are_refused_there_and_ordinary_elsewhere(name):
    assert unsafe_reason(name, windows=True) == "it is a name Windows reserves"
    assert unsafe_reason(name, windows=False) is None


def test_the_platform_is_the_one_running_unless_said():
    assert (unsafe_reason("nul") is None) == (os.name != "nt")


def test_a_device_name_is_one_whatever_its_extension_or_case():
    assert is_reserved("Nul.tar.gz") and is_reserved("CON ")
    assert not is_reserved("null") and not is_reserved("console") and not is_reserved(".nul")


@pytest.mark.parametrize("name", ["20261125T204017Z-902eec", "FROM-THE-PLANE", "01M8A71FA7RFVQZHR0C7CR6NJ6", "a.b"])
def test_a_run_id_is_a_plain_name(name):
    assert is_plain_name(name)


@pytest.mark.parametrize("name", [None, 5, "a/b", ["a"], b"a"])
def test_anything_else_is_not(name):
    assert not is_plain_name(name)


def test_a_member_is_the_resolved_file(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.png").write_bytes(b"x")
    assert member_path(tmp_path, "a/x.png") == (tmp_path / "a" / "x.png").resolve()
    assert member_path(tmp_path, "a/missing.png").name == "missing.png"


def test_a_directory_whose_name_merely_begins_the_same_is_outside(tmp_path: Path):
    """``/bundle`` must not hold ``/bundle-secrets``."""
    root, other = tmp_path / "bundle", tmp_path / "bundle-secrets"
    root.mkdir()
    other.mkdir()
    link_to(root / "x", other)
    with pytest.raises(UnsafePath, match="link"):
        member_path(root, "x/s.txt")


def link_to(link: Path, target: Path) -> None:
    """A link to a file or a directory, by whatever the system lets a test make."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and target.is_dir():
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("this system lets a test make no link")


def test_a_link_that_stays_inside_is_followed(tmp_path: Path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "x").write_text("x")
    link_to(tmp_path / "alias", tmp_path / "real")
    assert member_path(tmp_path, "alias/x") == (tmp_path / "real" / "x").resolve()


def test_a_link_that_leads_out_is_refused(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "x").write_text("x")
    link_to(root / "dir", tmp_path / "secret")
    with pytest.raises(UnsafePath, match="link"):
        member_path(root, "dir/x")


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows link")
def test_a_junction_that_leads_out_is_refused(tmp_path: Path):
    import _winapi

    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").mkdir()
    _winapi.CreateJunction(str(tmp_path / "secret"), str(root / "dir"))
    with pytest.raises(UnsafePath, match="link"):
        member_path(root, "dir/x")


def test_a_loop_of_links_is_refused_not_followed(tmp_path: Path):
    try:
        (tmp_path / "a").symlink_to(tmp_path / "b")
        (tmp_path / "b").symlink_to(tmp_path / "a")
    except (OSError, NotImplementedError):
        pytest.skip("this system lets a test make no link")
    try:
        path = member_path(tmp_path, "a")
    except UnsafePath:
        return
    assert not path.is_file()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("../x", True),
        ("a/../../x", True),
        ("a\\..\\x", True),
        ("/etc/x", True),
        ("\\\\host\\x", True),
        ("C:\\x", True),
        ("c:x", True),
        ("a/.. /x", True),
        ("output/20:00:00.log", False),
        ("output/a\\b.txt", False),
        ("output/..hidden/x..", False),
        ("output/x.png", False),
    ],
)
def test_a_name_that_leads_out_says_so_itself(name, expected):
    assert points_outside(name) is expected


def test_a_name_is_shown_without_its_control_characters():
    assert shown("a\x1b[31m\nb") == "'a\\x1b[31m\\nb'"
    assert len(shown("x" * 500)) == 120
