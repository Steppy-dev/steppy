"""Guard: the packaged Claude skills carry no private client domain (FLAWS §7.5).

`steppy init` installs `skills/**/SKILL.md` into every consumer project, so any
leftover client name or former example domain would leak into strangers' repos.
This scans the canonical source tree against the local deny-list described in
``tests/_private_terms.py`` and fails on any occurrence.
"""

from pathlib import Path

from tests._private_terms import private_terms_re

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def test_skills_dir_exists_and_nonempty():
    files = list(_SKILLS_DIR.glob("*/SKILL.md"))
    assert files, "expected skills/<name>/SKILL.md"


def test_no_private_terms_in_skills():
    pattern = private_terms_re()
    offenders = []
    for skill in _SKILLS_DIR.glob("*/SKILL.md"):
        for i, line in enumerate(skill.read_text(encoding="utf-8").splitlines(), 1):
            m = pattern.search(line)
            if m:
                offenders.append(f"{skill.parent.name}:{i}: {m.group(0)!r} in {line.strip()}")
    assert not offenders, "private terms found:\n" + "\n".join(offenders)
