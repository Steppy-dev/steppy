"""One template-resolution policy for every command.

`generate`, `guide` and `build` used to implement three different rules, which
produced two user-visible traps:

  * a project overriding only *subdirectory* templates (``templates/static/…``,
    ``templates/angular/…``) had them honoured by ``build``/``guide`` and
    silently ignored by ``generate``, which additionally required top-level
    ``*.j2`` files before it would consider the directory at all;
  * an explicit-but-missing ``templates_dir`` was a hard error for ``generate``
    and a silent fallback to bundled templates for the other two — dropping the
    user's whole customisation while reporting success.

The rule is now: explicit dir (error if missing) → ``<project_root>/templates``
→ bundled. These tests pin it for all three consumers so it cannot drift apart
again.
"""

from pathlib import Path

import pytest

from steppy._templating import bundled_templates_dir, template_search_paths
from steppy.build import BuildAssembler
from steppy.build_models import BuildConfig
from steppy.generator import SteppyGenerator
from steppy.guide import GuideAssembler
from steppy.models import AppAuthConfig, AppConfig, SteppyConfig


def _steppy_config() -> SteppyConfig:
    return SteppyConfig(apps={"editor": AppConfig(name="N", url="http://x", auth=AppAuthConfig(mode="bypass"))})


def _build_config(root: Path) -> BuildConfig:
    return BuildConfig(
        scenariosRoot="./scenarios",
        screenshotsDir="./screenshots",
        outputDir="./out",
        assetsDir="./assets",
        role="editor",
        locales=["fr"],
        defaultLocale="fr",
        target="static",
        site={"title": "T"},
        guides=[{"slug": "g", "title": "G", "scenario": "g.md"}],
        navbar={"order": ["g"]},
    )


def _assemblers(root: Path, templates_dir=None):
    """One factory per consumer, so each test covers all three."""
    return {
        "generate": lambda: SteppyGenerator(_steppy_config(), templates_dir, project_root=root),
        "guide": lambda: GuideAssembler(_steppy_config(), templates_dir, project_root=root),
        "build": lambda: BuildAssembler(_build_config(root), root, templates_dir=templates_dir),
    }


# ── the shared rule ─────────────────────────────────────────────────────────


def test_search_path_order(tmp_path):
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (tmp_path / "templates").mkdir()

    paths = template_search_paths(explicit, tmp_path)
    assert paths == [explicit, tmp_path / "templates", bundled_templates_dir()]


def test_project_templates_used_without_top_level_j2(tmp_path):
    """A subdirectory-only override must count.

    `generate` used to require `any(templates_dir.glob("*.j2"))`, so a project
    customizing only templates/static/guides.css got its override silently
    dropped by `steppy generate` while `build` and `guide` honoured it.
    """
    nested = tmp_path / "templates" / "static"
    nested.mkdir(parents=True)
    (nested / "guides.css").write_text("body{}", encoding="utf-8")

    assert (tmp_path / "templates") in template_search_paths(None, tmp_path)


def test_bundled_is_always_the_final_fallback(tmp_path):
    assert template_search_paths(None, tmp_path)[-1] == bundled_templates_dir()


def test_no_project_templates_falls_back_to_bundled_only(tmp_path):
    assert template_search_paths(None, tmp_path) == [bundled_templates_dir()]


# ── missing explicit dir is fatal, for every consumer ───────────────────────


@pytest.mark.parametrize("consumer", ["generate", "guide", "build"])
def test_missing_explicit_templates_dir_raises(tmp_path, consumer):
    """Silently shipping bundled templates would drop the user's whole override."""
    missing = tmp_path / "does-not-exist"
    factory = _assemblers(tmp_path, templates_dir=missing)[consumer]

    with pytest.raises(ValueError, match="Templates directory not found"):
        factory()


@pytest.mark.parametrize("consumer", ["generate", "guide", "build"])
def test_existing_explicit_templates_dir_is_accepted(tmp_path, consumer):
    """The happy path must not be broken by the stricter check."""
    explicit = tmp_path / "custom"
    explicit.mkdir()
    _assemblers(tmp_path, templates_dir=explicit)[consumer]()  # must not raise


# ── every consumer resolves against the same root ───────────────────────────


def test_all_consumers_agree_on_the_project_root(tmp_path, monkeypatch):
    """Running from a different cwd must not change which templates apply.

    `build` resolved local templates from project_root while `generate` and
    `guide` used Path.cwd(), so a monorepo subdirectory (or any CI step with a
    different working directory) silently applied a project's customisation to
    one command and not the others.
    """
    (tmp_path / "templates").mkdir()
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    for name, factory in _assemblers(tmp_path).items():
        env = factory().env
        roots = [Path(p) for p in env.loader.searchpath]
        assert tmp_path / "templates" in roots, f"{name} ignored the project's templates/ (cwd={elsewhere})"


def test_build_gives_its_guide_assembler_the_same_root(tmp_path):
    """One build must not consult two different templates/ trees."""
    (tmp_path / "templates").mkdir()
    assembler = BuildAssembler(_build_config(tmp_path), tmp_path)

    build_paths = [Path(p) for p in assembler.env.loader.searchpath]
    guide_paths = [Path(p) for p in assembler._guide_assembler.env.loader.searchpath]
    assert build_paths == guide_paths
