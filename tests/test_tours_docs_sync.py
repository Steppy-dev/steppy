"""``docs/tours.md`` and the design note stay true to the code.

Every user-facing name the feature defines must be documented, the golden
blocks must be the files they name, and diagnostic codes quoted in the docs
must exist.
"""

import re
from pathlib import Path

import pytest

from steppy import naming, tour_spec
from steppy.models import ScenarioTour, ToursConfig
from steppy.tours.diagnostics import CODES

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "tours.md"
DESIGN = ROOT / "docs" / "design" / "in-app-tours.md"
TEXT = DOC.read_text(encoding="utf-8")


def _documented(name: str) -> bool:
    return f"`{name}`" in TEXT or f"`{name}:" in TEXT or f" {name}:" in TEXT


@pytest.mark.parametrize("key", sorted(ToursConfig.model_fields))
def test_every_config_key_is_documented(key):
    assert _documented(key), f"tours.{key} is not in docs/tours.md"


@pytest.mark.parametrize("key", sorted(ScenarioTour.model_fields))
def test_every_frontmatter_key_is_documented(key):
    assert _documented(key), f"tour.{key} is not in docs/tours.md"


@pytest.mark.parametrize("key", ["tour", "tour-text", "tour-advance", "tour-placement"])
def test_every_step_key_is_documented(key):
    assert f"`{key}`" in TEXT


@pytest.mark.parametrize(
    "value",
    [
        *tour_spec.ADVANCE_KINDS,
        *tour_spec.PLACEMENTS,
        *tour_spec.STEP_TOUR_MODES,
        *tour_spec.TOUR_EVENTS,
        *tour_spec.TOUR_ERROR_REASONS,
    ],
)
def test_every_enum_value_is_documented(value):
    assert f"`{value}`" in TEXT or f"/`{value}`" in TEXT or f"`{value}/" in TEXT, value


def test_every_part_is_documented():
    parts_line = TEXT[TEXT.index("Every visible\npiece is a `::part()`") :][:600]
    for part in tour_spec.TOUR_PARTS:
        assert f"`{part}`" in parts_line, part


def test_every_theming_property_is_documented():
    styles = (ROOT / "packages" / "player" / "src" / "tour" / "styles.ts").read_text(encoding="utf-8")
    props = set(re.findall(r"var\((--steppy-tour-[a-z-]+)", styles))
    assert props
    for prop in props:
        assert f"`{prop}`" in TEXT, prop


def test_runtime_names_are_documented():
    for name in (naming.TOUR_HOST_TAG, naming.TOURS_KILL_SWITCH_GLOBAL, naming.TOUR_RESUME_STORAGE_KEY):
        assert name in TEXT, name
    assert naming.TOURS_MANIFEST_FILENAME in TEXT


@pytest.mark.parametrize("flag", ["--dry-run", "--check", "--build-id", "--explain", "-d/--scenarios-dir"])
def test_every_tours_flag_is_documented(flag):
    assert f"`{flag}" in TEXT, flag


def _code_rows(text: str) -> dict[str, str]:
    """``code -> severity`` for every ``| `code` | severity |`` table row."""
    return dict(re.findall(r"^\| `([a-z-]+)` \| (error|warning|info) \|", text, re.M))


def test_every_diagnostic_code_is_documented_with_its_severity():
    # Both directions: a user who sees a code in the output must find it here,
    # and nothing here may name a code the compiler does not have.
    section = TEXT[TEXT.index("### Compile errors and warnings") : TEXT.index("## Output files")]
    rows = _code_rows(section)
    assert set(rows) == set(CODES), {"undocumented": set(CODES) - set(rows), "unknown": set(rows) - set(CODES)}
    assert {c: s for c, s in rows.items() if CODES[c] != s} == {}


def test_the_authoring_skill_names_only_real_codes():
    skill = (ROOT / "skills" / "steppy-scenario-author" / "SKILL.md").read_text(encoding="utf-8")
    section = skill[skill.index("## In-app tours") :]
    rows = _code_rows(section)
    assert rows, "the skill's in-app tours section lists no diagnostics"
    assert set(rows) <= set(CODES), set(rows) - set(CODES)
    assert {c: s for c, s in rows.items() if CODES[c] != s} == {}
    # Every error an author can hit is in the skill: an agent fixes what it can look up.
    assert {c for c, s in CODES.items() if s == "error"} <= set(rows)


def _github_slugs(markdown: str) -> set[str]:
    """Heading anchors as GitHub renders them, plus explicit ``<a id="…">`` anchors."""
    out = set(re.findall(r'<a id="([^"]+)"></a>', markdown))
    seen: dict[str, int] = {}
    in_code = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else re.match(r"^#{1,6} (.+?)\s*$", line)
        if not m:
            continue
        slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


@pytest.mark.parametrize(
    "source, pattern",
    [
        (DOC, r"\]\(#([a-z0-9-]+)\)"),
        (ROOT / "README.md", r"\]\(docs/tours\.md#([a-z0-9-]+)\)"),
        (ROOT / "STEPPY-REFERENCE.md", r"\]\(docs/tours\.md#([a-z0-9-]+)\)"),
        (ROOT / "SECURITY.md", r"\]\(docs/tours\.md#([a-z0-9-]+)\)"),
        (DESIGN, r"\]\(\.\./tours\.md#([a-z0-9-]+)\)"),
    ],
    ids=lambda v: v.name if isinstance(v, Path) else "",
)
def test_links_into_the_tours_doc_resolve(source, pattern):
    # GitHub ignores `{#id}` heading attributes: anchors are `<a id>` lines or heading slugs.
    assert "{#" not in TEXT
    anchors = _github_slugs(TEXT)
    broken = sorted(set(re.findall(pattern, source.read_text(encoding="utf-8"))) - anchors)
    assert broken == [], f"{source.name} links to missing docs/tours.md anchors: {broken}"


def test_the_host_property_table_matches_the_element():
    host = (ROOT / "packages" / "player" / "src" / "tour" / "host.ts").read_text(encoding="utf-8")
    block = host[host.index("static override properties = {") :]
    declared = set(re.findall(r"^    (\w+): \{", block[: block.index("};")], re.M))
    table = TEXT[TEXT.index("| Property | Attribute |") :]
    documented = set(re.findall(r"^\| `(\w+)` \|", table[: table.index("\n\n")], re.M))
    assert declared and documented == declared, {"missing": declared - documented, "extra": documented - declared}


def test_every_chrome_string_key_is_documented():
    strings = (ROOT / "packages" / "player" / "src" / "tour" / "strings.ts").read_text(encoding="utf-8")
    body = strings[strings.index("export interface TourStrings {") :]
    keys = set(re.findall(r"^  (\w+): string;", body[: body.index("}")], re.M))
    section = TEXT[TEXT.index("## Localization") : TEXT.index("## Theming and accessibility")]
    assert keys and {k for k in keys if f"| `{k}` |" not in section} == set()


def test_golden_blocks_equal_their_files():
    blocks = re.findall(r"<!-- golden: (\S+) -->\n(`{3,4})\w*\n(.*?)\n\2\n", TEXT, re.S)
    assert len(blocks) >= 3
    for path, _fence, body in blocks:
        assert body + "\n" == (ROOT / path).read_text(encoding="utf-8"), path


def test_the_design_note_keeps_its_sections():
    design = DESIGN.read_text(encoding="utf-8")
    headings = re.findall(r"^## (\d+)\. ", design, re.M)
    assert headings == [str(n) for n in range(1, 13)]


@pytest.mark.parametrize("path", [DOC, DESIGN])
def test_no_local_paths(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\b[A-Za-z]:[\\/]", text), "a drive-letter path"
    assert not re.search(r"(?:/Users/|/home/)[A-Za-z]", text), "a home-directory path"
