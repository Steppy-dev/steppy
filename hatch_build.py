"""Hatchling build hook: vendor the template + skill trees into the package
(FLAWS §4.1, §7.1).

``templates/`` at the repo root is the single source of truth for Steppy's
Jinja templates — it is what downstream projects customize and what wins in the
runtime loader precedence. The wheel ships a byte-identical copy under
``src/steppy/templates/`` as the fallback used when a project has no local
``templates/`` dir.

``skills/`` at the repo root holds the packaged Claude skills (the
``steppy-scenario-author`` / ``steppy-template`` / ``steppy-ci-report`` payload
that ``steppy init`` installs into a project's ``.claude/skills/``); it is
vendored the same way into ``src/steppy/skills/``. The whole tree is copied, so a
new skill directory needs no change here — only an entry in
``init_cli.SKILL_NAMES``.

The repo-root ``CHANGELOG.md`` is likewise vendored as a single file into
``src/steppy/CHANGELOG.md`` so ``steppy update`` can print release highlights at
runtime (FLAWS §7.7).

Historically the template tree was committed and hand-synced (and silently
diverged twice). Now both bundled copies are **generated** here from the root
copies before every build and are gitignored — mirroring how
``src/steppy/_player_bundle`` is vendored. See ``pyproject.toml``
(``[tool.hatch.build.hooks.custom]`` + the ``force-include`` entries) for the
wiring.
"""

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class VendorTemplatesBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        # (canonical source dir, generated bundled dir). Copy ALL file types,
        # not just *.j2 / *.md: the template tree also carries the
        # _steppy_events.py pytest plugin and static/guides.css.
        for name in ("templates", "skills"):
            src = root / name
            dst = root / "src" / "steppy" / name
            if not src.is_dir():
                # No canonical tree (e.g. an unusual build context) — leave any
                # existing bundled copy untouched rather than wiping it.
                continue
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

        # Vendor the single-file CHANGELOG.md into the package so `steppy update`
        # can read it at runtime in both the wheel and editable installs (FLAWS
        # §7.7). Gitignored + force-included like the trees above.
        changelog = root / "CHANGELOG.md"
        if changelog.is_file():
            shutil.copyfile(changelog, root / "src" / "steppy" / "CHANGELOG.md")
