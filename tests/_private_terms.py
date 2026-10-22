"""The local deny-list behind the publication guards.

The guards keep client project names, private machine paths and the author's
former example domain out of every published file. The list of those terms
cannot live in the repository: a plain list publishes the names it guards, and
a hashed one still confirms any name a reader guesses. It lives instead in
``private-terms.txt`` at the repository root, which ``.gitignore`` excludes, and
the guards skip in a checkout that does not have it. Only someone who knows a
term can introduce it, so the check runs where the list is.

Format: one case-insensitive regular expression per line. Blank lines and lines
starting with ``#`` are ignored. If the file is ever committed by mistake, the
published-files guard flags it, because its own lines match.
"""

import re
from pathlib import Path

import pytest

PRIVATE_TERMS_FILE = Path(__file__).resolve().parents[1] / "private-terms.txt"


def private_terms_re() -> re.Pattern[str]:
    """Return the deny-list as one pattern, or skip the calling test without one."""
    if not PRIVATE_TERMS_FILE.is_file():
        pytest.skip(f"no local deny-list ({PRIVATE_TERMS_FILE.name}) in this checkout")
    lines = PRIVATE_TERMS_FILE.read_text(encoding="utf-8").splitlines()
    terms = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not terms:
        pytest.skip(f"{PRIVATE_TERMS_FILE.name} lists no terms")
    return re.compile("|".join(f"(?:{term})" for term in terms), re.IGNORECASE)
