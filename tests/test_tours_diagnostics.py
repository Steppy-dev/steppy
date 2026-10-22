"""The tour diagnostic codes are a closed, live vocabulary.

``steppy.tours.diagnostics.CODES`` is what the docs table and the
scenario-author skill list. A code nothing emits is a promise the tool never
keeps (a reader searches for it in vain); a code emitted but missing from the
table raises ``KeyError`` at compile time. This pins the first half; the
compiler's own ``CODES[code]`` lookup enforces the second.
"""

import re
from pathlib import Path

import pytest

from steppy.tours.diagnostics import CODES

SRC = Path(__file__).resolve().parents[1] / "src" / "steppy"
EMITTERS = [p for p in sorted((SRC / "tours").glob("*.py")) if p.name != "diagnostics.py"] + [SRC / "tours_cli.py"]
TEXT = "\n".join(p.read_text(encoding="utf-8") for p in EMITTERS)


@pytest.mark.parametrize("code", sorted(CODES))
def test_every_code_is_emitted_somewhere(code):
    assert re.search(rf"[\"']{re.escape(code)}[\"']", TEXT), f"{code} is in CODES but nothing emits it"
