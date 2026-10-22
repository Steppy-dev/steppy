"""``steppy build`` publishing in-app tours (the tours ``guide`` target).

The build republishes the canonical tree ``steppy generate`` wrote, byte for
byte, at ``<site>/tours/<build-id>/`` — after verifying it, and before wiping
anything.
"""

import json
import shutil
from pathlib import Path

import pytest

from steppy.build import BuildAssembler, BuildError
from steppy.models import SteppyConfig
from tests.test_build_seo import _static_project

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "contracts" / "fixtures" / "tours" / "valid"
FILES = sorted(p.name for p in GOLDEN.iterdir())


def _config(tmp_path: Path, *, targets=None, tours_dir=None, enabled=True) -> SteppyConfig:
    data = {
        "apps": {"editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}},
        "locales": ["fr", "nl"],
        "default_locale": "fr",
        "tours": {
            "enabled": enabled,
            "build_id": "demo-0001",
            "guide_base_url": "https://guide.example.com/",
            "targets": targets or [{"type": "guide"}],
        },
    }
    if tours_dir:
        data["output"] = {"tours_dir": tours_dir}
    return SteppyConfig(**data)


def _tree(root: Path, tours_dir: str = "output/tours") -> Path:
    tree = root / tours_dir / "demo-0001"
    shutil.copytree(GOLDEN, tree)
    return tree


_ENABLED = object()


def _build(tmp_path, *, config=_ENABLED, tree=True, seo=None, **project) -> Path:
    build_config, root = _static_project(tmp_path, seo=seo, **project)
    if tree:
        _tree(root)
    steppy_config = _config(root) if config is _ENABLED else config
    BuildAssembler(build_config, root, steppy_config=steppy_config).build_all()
    return root / "output" / "generated"


def test_the_tree_is_published_byte_for_byte(tmp_path):
    gen = _build(tmp_path)
    published = gen / "tours" / "demo-0001"
    assert sorted(p.name for p in published.iterdir()) == FILES
    for name in FILES:
        assert (published / name).read_bytes() == (GOLDEN / name).read_bytes(), name
        assert b"\r\n" not in (published / name).read_bytes()


def test_the_site_manifest_and_headers_announce_it(tmp_path):
    gen = _build(tmp_path, seo={"mode": "public", "baseUrl": "https://guides.example.com"})
    manifest = json.loads((gen / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tours"] == {
        "buildId": "demo-0001",
        "manifest": "tours/demo-0001/tours.manifest.json",
        "ids": ["create-invoice", "remove-inactive-customers", "send-invoice"],
    }
    headers = (gen / "_headers").read_text(encoding="utf-8")
    block = headers[headers.index("/tours/*") :]
    assert "Access-Control-Allow-Origin: *" in block and "X-Content-Type-Options: nosniff" in block
    assert "X-Robots-Tag: noindex" in block


def test_a_private_site_already_noindexes_everything(tmp_path):
    headers = (_build(tmp_path) / "_headers").read_text(encoding="utf-8")
    block = headers[headers.index("/tours/*") :]
    assert "X-Robots-Tag" not in block and headers.count("X-Robots-Tag: noindex") == 1


def test_nothing_changes_without_tours(tmp_path):
    with_off = _build(tmp_path / "off", config=_config(tmp_path / "off", enabled=False))
    without = _build(tmp_path / "none", config=None, tree=False)
    for gen in (with_off, without):
        assert not (gen / "tours").exists()
        assert "tours" not in json.loads((gen / "manifest.json").read_text(encoding="utf-8"))
    assert (with_off / "_headers").read_bytes() == (without / "_headers").read_bytes()
    assert b"/tours/" not in (without / "_headers").read_bytes()


def test_a_missing_tree_fails_before_the_wipe(tmp_path):
    build_config, root = _static_project(tmp_path)
    previous = root / "output" / "generated" / "previous-site.html"
    previous.parent.mkdir(parents=True)
    previous.write_text("still here", encoding="utf-8")
    with pytest.raises(BuildError, match="Run `steppy generate` or `steppy tours` first"):
        BuildAssembler(build_config, root, steppy_config=_config(root)).build_all()
    assert previous.read_text(encoding="utf-8") == "still here"


def test_an_edited_file_is_refused(tmp_path):
    build_config, root = _static_project(tmp_path)
    tree = _tree(root)
    path = tree / "create-invoice.en.json"
    path.write_bytes(path.read_bytes().replace(b"New invoice", b"New Invoice"))
    with pytest.raises(BuildError, match="stale or was edited"):
        BuildAssembler(build_config, root, steppy_config=_config(root)).build_all()


def test_a_tree_for_another_build_is_refused(tmp_path):
    build_config, root = _static_project(tmp_path)
    shutil.copytree(GOLDEN, root / "output" / "tours" / "demo-0002")  # demo-0001's files, in demo-0002's place
    config = _config(root)
    config.tours.build_id = "demo-0002"
    with pytest.raises(BuildError, match="is for build 'demo-0001', expected 'demo-0002'"):
        BuildAssembler(build_config, root, steppy_config=config).build_all()


def test_the_tours_slug_is_reserved(tmp_path):
    with pytest.raises(BuildError, match="slug 'tours' is reserved"):
        _build(tmp_path, guides=[{"slug": "tours", "title": "T", "scenario": "t.md"}])


def test_a_tours_dir_inside_the_build_output_is_refused(tmp_path):
    build_config, root = _static_project(tmp_path)
    config = _config(root, tours_dir="./output/generated/tours")
    with pytest.raises(BuildError, match="overlaps the build's outputDir"):
        BuildAssembler(build_config, root, steppy_config=config).build_all()


def test_the_angular_target_warns_and_publishes_nothing(tmp_path, capsys):
    from tests.test_build import _make_config, _make_scenario, _make_screenshots

    _make_scenario(tmp_path / "scenarios", "onboarding.md")
    _make_screenshots(tmp_path, "onboarding")
    _tree(tmp_path)
    BuildAssembler(_make_config(tmp_path), tmp_path, steppy_config=_config(tmp_path)).build_all()
    assert "the guide target publishes only with target: 'static'" in capsys.readouterr().err
    assert not list((tmp_path / "output").rglob("generated/tours"))


def test_guide_links_this_build_cannot_satisfy_warn(tmp_path, capsys):
    _build(tmp_path)
    err = capsys.readouterr().err
    assert "links to guide 'create-invoice', which this build does not publish" in err


def test_no_guide_target_publishes_nothing(tmp_path):
    gen = _build(tmp_path, config=_config(tmp_path, targets=[{"type": "app-assets", "path": "./app/tours"}]))
    assert not (gen / "tours").exists()


def test_a_dry_run_plans_the_files(tmp_path):
    build_config, root = _static_project(tmp_path)
    _tree(root)
    results = BuildAssembler(build_config, root, steppy_config=_config(root)).build_all(dry_run=True)
    planned = {k for k, v in results.items() if "tours/demo-0001" in k.replace("\\", "/")}
    assert len(planned) == len(FILES) and all(results[k] == "planned" for k in planned)
    assert not (root / "output" / "generated").exists()
