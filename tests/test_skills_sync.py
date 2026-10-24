"""The packaged Claude skills against the code they describe.

An assistant trusts a skill more than its own guesses, so a stale one is worse
than none: it writes confidently against a command, a diagnostic or an API
that is gone or was never written down. These checks read the skills as text
and fail when the surface they cover grows without them:

- the skill set itself — ``skills/`` and ``init_cli.SKILL_NAMES`` list the same
  skills, each named after its folder;
- every visible CLI command is named in some skill;
- every in-app tour diagnostic code is in the author skill's table;
- the app-side skill covers the overlay's and the Angular adapter's public API
  (the host's properties, the six events, the error reasons, the adapter's
  config keys and exports);
- every ``--steppy-tour-*`` theme token is in the styling skill (the docs:
  ``test_tours_docs_sync.py``).
"""

import re
from pathlib import Path

from typer.main import get_command

from steppy.cli import app
from steppy.init_cli import APP_SKILL_NAMES, LEGACY_SKILLS, SKILL_NAMES
from steppy.tours.diagnostics import CODES

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PLAYER = ROOT / "packages" / "player" / "src"
ADAPTER = ROOT / "packages" / "angular" / "src"


def _skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter_name(text: str) -> str:
    match = re.match(r"---\nname: ([\w-]+)\n", text)
    assert match, "a skill starts with `---` then `name:`"
    return match.group(1)


def _ts_tuple(path: Path, name: str) -> tuple[str, ...]:
    match = re.search(rf"^export const {name} = \[([^\]]*)\] as const;$", path.read_text(encoding="utf-8"), re.M)
    assert match, f"{path.name}: no one-line `export const {name} = [...] as const`"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _commands(command, prefix=()):
    for name, sub in sorted(getattr(command, "commands", {}).items()):
        if getattr(sub, "hidden", False):
            continue
        yield (*prefix, name)
        yield from _commands(sub, (*prefix, name))


# ── The skill set ──────────────────────────────────────────────────────────


def test_the_skills_folder_and_skill_names_agree():
    assert sorted(p.name for p in SKILLS.iterdir() if p.is_dir()) == sorted(SKILL_NAMES)


def test_each_skill_is_named_after_its_folder():
    for name in SKILL_NAMES:
        assert _frontmatter_name(_skill(name)) == name


def test_app_skills_and_legacy_names_are_consistent():
    assert set(APP_SKILL_NAMES) <= set(SKILL_NAMES)
    assert not set(LEGACY_SKILLS) & set(SKILL_NAMES)
    assert set(LEGACY_SKILLS.values()) <= set(SKILL_NAMES)


# ── Commands and diagnostics ───────────────────────────────────────────────


def test_every_command_is_named_in_a_skill():
    # A line naming `steppy <group>` and, after it, each subcommand word — which
    # also accepts the tables' `steppy auth login \| status \| clear` form.
    lines = "\n".join(_skill(name) for name in SKILL_NAMES).splitlines()

    def named(path: tuple[str, ...]) -> bool:
        head = f"steppy {path[0]}"
        for line in lines:
            if head not in line:
                continue
            rest = line.split(head, 1)[1]
            if all(re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", rest) for word in path[1:]):
                return True
        return False

    missing = [" ".join(path) for path in _commands(get_command(app)) if not named(path)]
    assert not missing, f"commands no skill names: {missing}"


def test_every_tour_diagnostic_is_in_the_author_skill():
    text = _skill("steppy-scenario-author")
    assert not [code for code in CODES if f"`{code}`" not in text]


# ── The app-side skill against the overlay and the adapter ─────────────────


def test_the_app_skill_names_every_host_property():
    block = re.search(
        r"static override properties = \{(.*?)\n  \};", (PLAYER / "tour" / "host.ts").read_text(encoding="utf-8"), re.S
    )
    assert block, "host.ts: static properties block not found"
    text = _skill("steppy-app-tours")
    assert not [p for p in re.findall(r"^\s+(\w+): \{", block.group(1), re.M) if f"`{p}`" not in text]


def test_the_app_skill_names_every_event_and_error_reason():
    text = _skill("steppy-app-tours")
    contract = PLAYER / "tour" / "contract.ts"
    names = _ts_tuple(contract, "TOUR_EVENTS") + _ts_tuple(contract, "TOUR_ERROR_REASONS")
    assert not [n for n in names if f"`{n}`" not in text]


def test_the_app_skill_names_every_adapter_option_and_export():
    text = _skill("steppy-app-tours")
    config = (ADAPTER / "config.ts").read_text(encoding="utf-8")
    body = re.search(r"export interface SteppyToursConfig \{(.*?)\n\}", config, re.S)
    assert body, "config.ts: SteppyToursConfig not found"
    options = re.findall(r"^  (\w+)\?:", body.group(1), re.M)
    exports = re.findall(r"^export \{ (\w+)", (ADAPTER / "public-api.ts").read_text(encoding="utf-8"), re.M)
    assert options and exports
    assert not [n for n in (*options, *exports) if f"`{n}" not in text]


# ── The overlay's theme tokens, in the styling skill ───────────────────────


def _tour_theme_properties() -> list[str]:
    source = (PLAYER / "tour" / "theme.ts").read_text(encoding="utf-8")
    block = re.search(r"export const TOUR_THEME_KEYS = \[(.*?)\] as const;", source, re.S)
    assert block, "theme.ts: TOUR_THEME_KEYS not found"
    keys = re.findall(r"'(\w+)'", block.group(1))
    assert keys
    return ["--steppy-tour-" + re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), k) for k in keys]


def test_every_tour_theme_token_is_in_the_styling_skill():
    skill = _skill("steppy-template")
    assert not [p for p in _tour_theme_properties() if f"`{p}`" not in skill]
