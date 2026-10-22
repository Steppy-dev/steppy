"""What is vendored from ``packages/player/dist`` into the wheel, and nothing more.

``dist/`` holds four bundles once the in-app tour overlay exists; only the
guide player, the legacy cursor IIFE and the video renderer have a Python
consumer. A glob copy (``cp dist/*.js``) would quietly ship the overlay in the
wheel, so the set is pinned here, alongside the demo-guide fixture's copy of
the player, which nothing else keeps in step.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "src" / "steppy" / "_player_bundle"
VENDORED = ("steppy-player", "steppy-cursor-legacy", "steppy-video")


def test_exactly_the_three_wheel_bundles_are_vendored():
    js = sorted(p.name for p in BUNDLE.glob("*.js"))
    maps = sorted(p.name for p in BUNDLE.glob("*.js.map"))
    assert js == sorted(f"{name}.js" for name in VENDORED)
    assert maps == sorted(f"{name}.js.map" for name in VENDORED)


def test_every_vendored_bundle_points_at_its_map():
    for name in VENDORED:
        text = (BUNDLE / f"{name}.js").read_text(encoding="utf-8")
        assert text.rstrip().endswith(f"//# sourceMappingURL={name}.js.map"), name


def test_the_demo_guide_fixture_ships_the_vendored_player():
    fixture = ROOT / "fixtures" / "demo-guide" / "public" / "assets" / "editor" / "steppy-player.js"
    assert fixture.read_bytes() == (BUNDLE / "steppy-player.js").read_bytes()


def test_source_maps_carry_no_absolute_paths():
    # A map built through a symlinked or junctioned node_modules records the
    # resolved absolute path, which would publish a developer's directory layout.
    for name in VENDORED:
        text = (BUNDLE / f"{name}.js.map").read_text(encoding="utf-8")
        assert '"sources":["' in text
        sources = text.split('"sources":[', 1)[1].split("]", 1)[0]
        assert ":/" not in sources and ":\\\\" not in sources and '"/' not in sources, name
