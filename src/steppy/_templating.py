"""Shared Jinja template location + environment construction.

**One precedence rule for every command** (`generate`, `guide`, `build`):

    1. an explicit ``templates_dir``   — hard error if it does not exist
    2. ``<project_root>/templates``    — used when the directory exists
    3. the bundled package templates   — always the final fallback

The three consumers used to implement this three different ways, which produced
two user-visible traps:

  * ``build`` resolved the local override against ``project_root`` while
    ``generate`` and ``guide`` used ``Path.cwd()`` — so running steppy from
    anywhere but the project root applied a project's ``templates/``
    customisation to ``steppy build`` and silently not to the other two;
  * ``generate`` additionally required the directory to contain top-level
    ``*.j2`` files, so a project overriding only subdirectory templates
    (``templates/static/guides.css``, ``templates/angular/component.ts.j2``)
    had those honoured by ``build``/``guide`` and silently ignored by
    ``generate``;
  * an explicit-but-missing ``templates_dir`` was a hard error for ``generate``
    and a silent fallback to bundled templates for the other two — dropping the
    user's entire customisation while reporting success.

Callers register their own filters and globals on the returned environment; the
environment options themselves (``trim_blocks`` / ``lstrip_blocks`` /
``keep_trailing_newline``) are load-bearing for generated-output byte stability
and are set here so they cannot drift between consumers.

A leaf module: it imports ``jinja2`` (which all three consumers already import)
and nothing else from ``steppy``, so the lazy-import guards
(``tests/test_no_parallel_import_on_default_path.py`` and siblings) are
unaffected.
"""

import importlib.resources
from pathlib import Path
from typing import Optional

import jinja2


def bundled_templates_dir() -> Path:
    """Path to the templates bundled with the installed ``steppy`` package.

    This tree is *generated* from the repo-root ``templates/`` by the hatchling
    build hook (see CLAUDE.md, "Single template source") and is the fallback
    used when a project has no local ``templates/`` of its own.
    """
    return Path(importlib.resources.files("steppy") / "templates")


def template_search_paths(
    templates_dir: Optional[Path],
    project_root: Optional[Path] = None,
) -> list[Path]:
    """Resolve the template search path, most specific first.

    See the module docstring for the rule. ``project_root`` defaults to the
    current working directory, which is what every CLI invocation wants.

    Raises:
        ValueError: if ``templates_dir`` is given but does not exist. Silently
            falling back would ship bundled templates while the user believes
            their customisation is in effect.
    """
    root = project_root if project_root is not None else Path.cwd()
    paths: list[Path] = []

    if templates_dir is not None:
        if not templates_dir.is_dir():
            raise ValueError(
                f"Templates directory not found: {templates_dir}. "
                "Remove the option to use the project's templates/ directory "
                "or the bundled defaults."
            )
        paths.append(templates_dir)

    local = root / "templates"
    if local.is_dir() and local not in paths:
        paths.append(local)

    paths.append(bundled_templates_dir())
    return paths


def make_template_env(
    templates_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    *,
    autoescape: bool = False,
) -> jinja2.Environment:
    """Build a Jinja environment over the unified template search path.

    The options set here are the ones generated output depends on — changing
    any of them changes every generated test file, so they live in exactly one
    place. Callers add their own filters/globals afterwards.

    ``autoescape`` defaults to **False**, and that default is load-bearing rather
    than merely historical: the guide templates inject markdown-rendered HTML
    through plain ``{{ }}``, so turning it on globally would silently escape every
    ``<em>`` in every guide. It is opt-in for the one consumer whose input is
    genuinely hostile — ``steppy report`` renders failure tails, which are
    arbitrary page content — where escaping-by-default is the only safe posture
    and a missed ``| e`` would be a hole rather than a cosmetic bug.

    Note it applies to *every* template the environment loads, including CSS
    rendered through it: a stylesheet under an autoescaping environment must
    contain no ``{{ }}``, or a colour would come out HTML-escaped.
    """
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader([str(p) for p in template_search_paths(templates_dir, project_root)]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=autoescape,
    )
