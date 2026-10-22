"""The tour grammar leaves import only the standard library.

``models.py`` imports them, and ``models.py`` sits on the eager CLI path that
``tests/test_no_playwright_import_on_default_path.py`` keeps free of heavy
dependencies. A leaf that grew a ``from .guide import …`` would drag
markdown-it onto every ``steppy`` invocation; this test fails first.
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "steppy"
LEAVES = ("naming.py", "tour_spec.py", "testids.py", "buildid.py")
#: Sibling modules a leaf may import; each is itself a stdlib-only leaf.
ALLOWED_SIBLINGS = {"assertions", "tour_spec", "naming"}


@pytest.mark.parametrize("leaf", LEAVES)
def test_leaf_imports_only_stdlib_and_sibling_leaves(leaf):
    tree = ast.parse((SRC / leaf).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in sys.stdlib_module_names, f"{leaf}: import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                assert node.module in ALLOWED_SIBLINGS, f"{leaf}: from .{node.module} import …"
            else:
                assert node.module.split(".")[0] in sys.stdlib_module_names or node.module == "__future__", (
                    f"{leaf}: from {node.module} import …"
                )
