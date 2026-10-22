"""Byte-identity harness for generated test code.

Not collected by pytest (leading underscore). Run it as a module:

    python -m tests._codegen_snapshot --out DIR [--corpus SCEN_DIR --config CFG]...
    python -m tests._codegen_snapshot --compare BEFORE AFTER

It renders ``generate_test_file`` for every scenario and ``generate_conftest``
per corpus, always from the repo's canonical ``templates/`` tree (so the result
is independent of the working directory and of a stale bundled copy), and
writes LF files plus a sorted ``MANIFEST.sha256``.

Why it exists: a refactor of ``test_scenario.py.j2`` or ``conftest.py.j2`` must
leave every existing output byte-identical, and in-suite tests cannot compare
against what the template produced *before* the change. Take a ``--out before``
snapshot first, change the template, take ``--out after``, then ``--compare``.

Default corpora are the repo's own scenarios plus a synthetic matrix that
exercises every action with its options. External corpora are passed only as
CLI arguments and their output must stay outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

# ── Synthetic matrix ──────────────────────────────────────────────────────────
#
# One scenario per family, each step varying the options its action accepts.
# Kept in the neutral invoice domain; selectors are plain test-ids.

_MATRIX: dict[str, str] = {
    "matrix--core": """---
title: Matrix core actions
description: Every core action with its options
tags: [matrix]
priority: high
timeout: 20000
---

### 1. Open the list
- role: editor
- action: navigate
- target: /invoices
- wait: domcontentloaded
- timeout: 10s

### 2. Reload
- role: editor
- action: navigate
- reload: true

### 3. Click plain
- role: editor
- action: click
- selector: '[data-testid="new-invoice-btn"]'
- screenshot: editor
- screenshot-when: before
- wait_after: 500

### 4. Click scoped
- role: editor
- action: click
- selector: 'button'
- within: '[data-testid="invoice-card"]'
- match: first
- force: true
- timeout: 5s
- screenshot: editor

### 5. Click synthetic
- role: editor
- action: click
- selector: '[data-testid="save-btn"]'
- synthetic: true
- expect: visible([data-testid="saved-toast"])

### 6. Fill plain
- role: editor
- action: fill
- selector: '[data-testid="invoice-title"]'
- value: "It's ${HOME:-x} {{ref}}"
- screenshot: editor

### 7. Fill sequential
- role: editor
- action: fill
- selector: '[data-testid="invoice-amount"]'
- value: "120.00"
- clear: true
- sequential: true
- blur-key: Tab

### 8. Fill form
- role: editor
- action: fill-form
- fields:
    '[data-testid="a"]': "one"
    '[data-testid="b"]': "two"
- screenshot: editor

### 9. Fill form progressive
- role: editor
- action: fill-form
- interactive-progressive: true
- fields:
    '[data-testid="c"]': "three"
- screenshot: editor

### 10. Native select
- role: editor
- action: select
- selector: '[data-testid="currency"]'
- value: EUR
- screenshot: editor

### 11. Wait
- role: editor
- action: wait
- duration: 2s

### 12. Wait for hidden
- role: editor
- action: wait-for
- selector: '[data-testid="dialog"]'
- state: hidden

### 13. Wait until
- role: editor
- action: wait-for
- selector: '[data-testid="total"]'
- until: value-not-empty
- interval: 300ms
- wait: networkidle

### 14. Wait for text
- role: editor
- action: wait-for-text
- value: Saved

### 15. Scroll
- role: editor
- action: scroll
- selector: '[data-testid="footer"]'

### 16. Hover
- role: editor
- action: hover
- selector: '[data-testid="menu"]'
- screenshot: editor

### 17. Keyboard
- role: editor
- action: keyboard
- value: Escape
- screenshot: editor

### 18. Login
- role: editor
- action: login

### 19. Upload
- role: editor
- action: upload
- selector: '[data-testid="file-input"]'
- value: fixtures/sample.pdf
- guide-cursor:
    behavior: click
    target: '[data-testid="upload-trigger"]'
- screenshot: editor
""",
    "matrix--widgets": """---
title: Matrix widget actions
tags: [matrix]
---

### 1. Open
- role: editor
- action: navigate
- target: /invoices

### 2. Dropdown first
- role: editor
- action: select-option
- selector: '[data-testid="customer-select"]'
- option: first
- screenshot: editor

### 3. Dropdown text with search
- role: editor
- action: select-option
- selector: '[data-testid="customer-select"]'
- search: Acme
- option: 'text:"Acme Corp"'

### 4. Dropdown multiple
- role: editor
- action: select-option
- selector: '[data-testid="tags-select"]'
- multiple:
  - Urgent
  - Draft

### 5. Dropdown nth
- role: editor
- action: select-option
- selector: '[data-testid="customer-select"]'
- option: "2"

### 6. Confirm accept
- role: editor
- action: confirm
- screenshot: editor

### 7. Confirm reject
- role: editor
- action: confirm
- variant: reject

### 8. Confirm by label
- role: editor
- action: confirm
- button: "Yes"

### 9. Confirm danger
- role: editor
- action: confirm
- danger: true

### 10. Fill date
- role: editor
- action: fill-date
- selector: '[data-testid="due-date"]'
- date: next-monday
- format: "%Y-%m-%d"

### 11. Toggle on
- role: editor
- action: toggle
- selector: '[data-testid="paid-switch"]'
- state: on

### 12. Toggle segment
- role: editor
- action: toggle
- selector: '[data-testid="period"]'
- option: Monthly

### 13. Toggle self
- role: editor
- action: toggle
- selector: '[data-testid="agree"]'
- control: self
- screenshot: editor

### 14. Expand
- role: editor
- action: expand
- selector: '[data-testid="details"]'

### 15. Expand aria off
- role: editor
- action: expand
- selector: '[data-testid="details"]'
- detect: aria
- state: off

### 16. Pick date
- role: editor
- action: pick-date
- selector: '[data-testid="issue-date"]'
- date: today
- screenshot: editor

### 17. Pick time
- role: editor
- action: pick-time
- selector: '[data-testid="issue-time"]'
- time: "09:30"
""",
    "matrix--checks": """---
title: Matrix checks and code
tags: [matrix]
seed:
  script: invoice_seed
  run: both
---

### 1. Open
- role: editor
- action: navigate
- target: /invoices

### 2. Assert list
- role: editor
- action: assert
- expect:
  - visible([data-testid="invoice-list"])
  - check: text([data-testid="title"], "Invoices")
    message: "title is wrong"
  - contains([data-testid="list"], "Acme")
  - count([data-testid="row"], >=2)
  - count([data-testid="row"], 3)
  - hidden([data-testid="spinner"])
  - not-visible([data-testid="error"])
  - value([data-testid="amount"], "120")
  - attribute([data-testid="link"], "href", "/x")
  - not-contains([data-testid="list"], "Ghost")
  - icontains([data-testid="list"], "acme")
  - checked([data-testid="paid"])
  - unchecked([data-testid="draft"])
  - stays-hidden([data-testid="toast"], "2s")
  - not-empty([data-testid="ref"])
  - disabled([data-testid="send"])
  - enabled([data-testid="save"])
  - toast("Saved")
  - row-contains("Acme")
  - url-contains("/invoices")

### 3. Assert scoped
- role: editor
- action: assert
- within: '[data-testid="card"]'
- match: last
- expect: visible(button)

### 4. Capture dom
- role: editor
- action: capture
- store: ref
- from:
    type: dom
    selector: '[data-testid="ref"]'
    extract: text

### 5. Call helper
- role: editor
- action: call
- fn: helpers.seed_invoice
- args:
  - one
  - 2
- kwargs:
    status: draft
- store: invoice_id
- screenshot: editor

### 6. Call without page
- role: editor
- action: call
- fn: helpers.ping
- page: false

### 7. Custom with cursor
- role: editor
- action: custom
- guide-cursor:
    behavior: click
    target: '[data-testid="row"]'
- screenshot: editor
- screenshot-when: before
- code: |
    page.locator('[data-testid="row"]').first.click()

### 8. Custom plain
- role: editor
- action: custom
- code: |
    page.mouse.move(1, 1)

### 9. Optional click
- role: editor
- action: click
- selector: '[data-testid="cookie-ok"]'
- optional: true

### 10. Guarded click
- role: editor
- action: click
- selector: '[data-testid="banner-close"]'
- when: visible([data-testid="banner"])

### 11. Test-only nav
- role: editor
- action: navigate
- target: /invoices/1
- only-in: test

### 12. Guide-only desktop hover
- role: editor
- action: hover
- selector: '[data-testid="sidebar"]'
- only-in: guide
- layouts:
  - desktop
""",
    "matrix--noauth-multirole": """---
title: Matrix no-auth multi-role
tags: [matrix]
auth: false
layouts: [desktop, mobile]
---

### 1. Editor opens
- role: editor
- action: navigate
- target: /

### 2. Editor logs in
- role: editor
- action: login

### 3. Admin opens
- role: admin
- action: navigate
- target: /admin

### 4. Admin approves
- role: admin
- action: click
- selector: '[data-testid="approve"]'
- layouts:
  - mobile
""",
}


def _config_variants():
    """(name, config) pairs: the repo config, the init template and synthetic variants."""
    from steppy.config import load_config

    repo = load_config(ROOT / "config.yaml")
    init = load_config(ROOT / "templates" / "init" / "config.yaml")
    variants = [("repo", repo), ("init", init)]
    timing = repo.timing.model_copy(update={"navigate_wait": "none"})
    variants.append(("repo-navwait-none", repo.model_copy(update={"timing": timing})))
    dropdown = repo.dropdown.model_copy(update={"settle": "none"})
    variants.append(("repo-settle-none", repo.model_copy(update={"dropdown": dropdown})))
    return variants


def _default_corpora() -> list[Path]:
    dirs = [
        ROOT / "scenarios",
        ROOT / "templates" / "init" / "scenarios",
        ROOT / "fixtures" / "demo-guide" / "e2e" / "scenarios" / "editor",
        ROOT / "fixtures" / "tour-demo" / "project" / "scenarios",
    ]
    return [d for d in dirs if d.is_dir()]


def _parse_dir(directory: Path, report: list[str]):
    from steppy.parser import ParseError, parse_scenario_file

    out = []
    for path in sorted(directory.glob("*.md")):
        try:
            out.append(parse_scenario_file(path))
        except (ParseError, Exception) as exc:  # noqa: BLE001 — a skipped file is reported, never fatal
            report.append(f"skip {path.name}: {str(exc).splitlines()[0][:160]}")
    return out


def _render_set(out: Path, label: str, scenarios, config, report: list[str]) -> None:
    from steppy.generator import SteppyGenerator

    gen = SteppyGenerator(config, templates_dir=TEMPLATES, project_root=ROOT)
    target = out / label
    target.mkdir(parents=True, exist_ok=True)
    for scenario in scenarios:
        try:
            code = gen.generate_test_file(scenario)
        except Exception as exc:  # noqa: BLE001
            report.append(f"render-fail {label}/{scenario.slug}: {exc}")
            continue
        (target / f"test_{scenario.slug}.py").write_bytes(code.encode("utf-8"))
    if scenarios:
        (target / "conftest.py").write_bytes(gen.generate_conftest(scenarios).encode("utf-8"))


def render(out: Path, extra: list[tuple[Path, Path]]) -> list[str]:
    """Render every corpus into ``out``; return the list of skipped/failed items."""
    from steppy.config import load_config
    from steppy.parser import parse_scenario_file

    out.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    variants = _config_variants()

    with tempfile.TemporaryDirectory() as tmp:
        matrix_dir = Path(tmp)
        for slug, text in _MATRIX.items():
            (matrix_dir / f"{slug}.md").write_text(text, encoding="utf-8", newline="\n")
        matrix = []
        for path in sorted(matrix_dir.glob("*.md")):
            try:
                # Pin the header path: the temp dir name would differ between runs.
                scenario = parse_scenario_file(path)
                matrix.append(scenario.model_copy(update={"file_path": Path("matrix") / path.name}))
            except Exception as exc:  # noqa: BLE001
                report.append(f"MATRIX-PARSE {path.name}: {exc}")
        for name, config in variants:
            _render_set(out, f"matrix/{name}", matrix, config, report)

    for corpus in _default_corpora():
        scenarios = _parse_dir(corpus, report)
        label = corpus.relative_to(ROOT).as_posix().replace("/", "_")
        for name, config in variants[:2]:
            _render_set(out, f"corpus/{label}/{name}", scenarios, config, report)

    for i, (corpus, cfg) in enumerate(extra):
        scenarios = _parse_dir(corpus, report)
        _render_set(out, f"external/{i}", scenarios, load_config(cfg), report)

    lines = []
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(out).as_posix()}")
    (out / "MANIFEST.sha256").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    (out / "REPORT.txt").write_bytes(("\n".join(report) + "\n").encode("utf-8"))
    return report


def compare(before: Path, after: Path) -> int:
    """Print every differing, missing or added file; return the number of differences."""

    def load(d: Path) -> dict[str, str]:
        entries = {}
        for line in (d / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, name = line.split("  ", 1)
                entries[name] = digest
        return entries

    a, b = load(before), load(after)
    diffs = 0
    for name in sorted(set(a) | set(b)):
        if name not in b:
            print(f"MISSING  {name}")
        elif name not in a:
            print(f"ADDED    {name}")
        elif a[name] != b[name]:
            print(f"DIFFERS  {name}")
        else:
            continue
        diffs += 1
    print(f"{len(a)} before, {len(b)} after, {diffs} different")
    return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--corpus", type=Path, action="append", default=[])
    parser.add_argument("--config", type=Path, action="append", default=[])
    parser.add_argument("--compare", type=Path, nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args(argv)

    if args.compare:
        return 1 if compare(*args.compare) else 0
    if not args.out:
        parser.error("--out or --compare is required")
    if len(args.corpus) != len(args.config):
        parser.error("each --corpus needs a matching --config")
    report = render(args.out, list(zip(args.corpus, args.config)))
    for line in report:
        print(line.encode("ascii", "backslashreplace").decode("ascii"))
    total = sum(1 for p in args.out.rglob("*.py"))
    print(f"rendered {total} files into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
