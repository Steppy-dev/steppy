"""``steppy record`` - record a browser session into a *draft* scenario file.

A recorder in the Scribe/Tango mold whose output is **source code that
regenerates forever**: a Markdown scenario draft, not a frozen screenshot album.
V1 is a post-processor over ``playwright codegen`` - a headed browser opens, the
user performs the flow and closes the window, and the generated pytest is
transformed (with :mod:`ast`) into Steppy scenario Markdown. Recorded fill values
that look like secrets are placeholderized to ``${VAR}`` refs (login is collapsed
to ``- action: login``), a blanket ``input[type='password']`` screenshot mask is
recommended, and the existing selector lint runs on the output. Recordings always
land as **drafts for review** - the ``steppy-scenario-author`` skill refines them.

This is a **foundational V1 ("CLI plumbing")**. It will be extended and improved
when Steppy is implemented on the open-source React/Vue showcases (the
client-facing browser-extension recorder + visual blur picker + instant preview
is a later phase). Selectors and mappings are
best-effort against public Playwright-codegen output; the draft is meant to be
reviewed, not shipped verbatim.

A leaf module: imports only ``_cli_common`` + ``parser`` + stdlib (no cycle with
``cli``). Registered via :func:`register_record` at the bottom of ``cli.py``.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import typer
import yaml
from rich.markup import escape

from ._cli_common import (
    _cli_errors,
    _debug_enabled,
    _error,
    _load_config_or_exit,
    console,
)
from .parser import ParseError, lint_scenario_selectors, parse_scenario_file

# Field tokens (test-id / role name / label / placeholder) that mark a value as
# secret → placeholderize + mask. Best-effort; the draft is reviewed.
_SECRET_RE = re.compile(r"pass|secret|token|otp|cvv|pin|card|ssn|api[-_]?key", re.I)
# A subset of the above that specifically signals the login password field.
_PASSWORD_RE = re.compile(r"pass", re.I)

# Sentinel for a codegen argument that isn't a plain literal (e.g. re.compile(...)).
_MISSING = object()


@dataclass
class _Seg:
    """One link in a Playwright locator/terminal chain."""

    name: str
    is_call: bool
    args: list
    kwargs: dict


@dataclass
class RecordResult:
    """Outcome of :func:`codegen_source_to_scenario`."""

    markdown: str
    step_count: int
    notes: list = field(default_factory=list)
    env_vars: list = field(default_factory=list)
    mask_selectors: list = field(default_factory=list)


# ── AST walk ────────────────────────────────────────────────────────────────


def _lit(node):
    """Literal value of an AST node, or ``_MISSING`` for anything non-constant."""
    if isinstance(node, ast.Constant):
        return node.value
    return _MISSING


def _lits(args) -> list:
    return [_lit(a) for a in args]


def _kwlits(keywords) -> dict:
    return {kw.arg: _lit(kw.value) for kw in keywords if kw.arg}


def _walk(node):
    """Flatten a method-chain expression.

    Returns ``(base_name, base_arg_nodes, segs)`` where ``base_name`` is the
    innermost name (``"page"``, ``"expect"``, ...), ``base_arg_nodes`` are the raw
    AST args of a base *call* (``expect(<locator>)``) or ``None``, and ``segs`` is
    the ordered list of :class:`_Seg` from just-after-base out to the terminal.
    Returns ``None`` when the shape isn't understood.
    """
    segs: list[_Seg] = []
    cur = node
    while True:
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            attr = cur.func
            segs.append(_Seg(attr.attr, True, _lits(cur.args), _kwlits(cur.keywords)))
            cur = attr.value
        elif isinstance(cur, ast.Attribute):
            # A property access such as ``.first`` / ``.last``.
            segs.append(_Seg(cur.attr, False, [], {}))
            cur = cur.value
        elif isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name):
            segs.reverse()
            return cur.func.id, cur.args, segs
        elif isinstance(cur, ast.Name):
            segs.reverse()
            return cur.id, None, segs
        else:
            return None


def _find_test_fn(tree: ast.Module):
    fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for n in fns:
        if n.name.startswith("test"):
            return n
    return fns[0] if fns else None


# ── Locator chain → Steppy selector ─────────────────────────────────────────


def _selector_from_chain(chain: list[_Seg]):
    """Map a Playwright locator chain to a Steppy selector string.

    Returns ``(selector, raw_token, lossy_note)``. ``selector`` is ``None`` when
    a link can't be mapped (caller degrades the whole action to ``custom``).
    ``raw_token`` is the human-ish label behind the locator (for titles + secret
    detection). Positional links (``.nth``/``.first``/``.last``) fold into a
    Playwright ``>> nth=`` suffix - uniform and always valid for a draft.
    """
    parts: list[str] = []
    token = None
    lossy = None
    for seg in chain:
        if not seg.is_call:
            if seg.name == "first":
                parts.append("nth=0")
            elif seg.name == "last":
                parts.append("nth=-1")
            # Any other bare property (e.g. ``.keyboard``) contributes no selector.
            continue
        name = seg.name
        a0 = seg.args[0] if seg.args else None
        if name == "nth":
            if a0 is _MISSING or a0 is None:
                return None, None, None
            parts.append(f"nth={a0}")
        elif name == "get_by_test_id":
            if a0 is _MISSING:
                return None, None, None
            parts.append(f'[data-testid="{a0}"]')
            token = token or str(a0)
        elif name == "locator":
            if a0 is _MISSING:
                return None, None, None
            parts.append(str(a0))
            token = token or str(a0)
        elif name == "get_by_role":
            if a0 is _MISSING:
                return None, None, None
            sel = f"role={a0}"
            nm = seg.kwargs.get("name")
            if nm is not None and nm is not _MISSING:
                sel += f'[name="{nm}"]'
                token = token or str(nm)
            else:
                token = token or str(a0)
            parts.append(sel)
        elif name == "get_by_placeholder":
            if a0 is _MISSING:
                return None, None, None
            parts.append(f'[placeholder="{a0}"]')
            token = token or str(a0)
        elif name == "get_by_alt_text":
            if a0 is _MISSING:
                return None, None, None
            parts.append(f'[alt="{a0}"]')
            token = token or str(a0)
        elif name == "get_by_title":
            if a0 is _MISSING:
                return None, None, None
            parts.append(f'[title="{a0}"]')
            token = token or str(a0)
        elif name == "get_by_text":
            if a0 is _MISSING:
                return None, None, None
            parts.append(f"text={a0}")
            token = token or str(a0)
            lossy = "recorded via get_by_text - a text= selector breaks on i18n/wording; prefer a data-testid"
        elif name == "get_by_label":
            if a0 is _MISSING:
                return None, None, None
            parts.append(f'[aria-label="{a0}"]')
            token = token or str(a0)
            lossy = "recorded via get_by_label - emitted an [aria-label] guess; verify the selector"
        elif name == "filter":
            ht = seg.kwargs.get("has_text")
            if ht is None or ht is _MISSING:
                return None, None, None
            parts.append(f':has-text("{ht}")')
            lossy = "recorded a .filter(has_text=...) - folded into :has-text(); verify the selector"
        else:
            return None, None, None
    return " >> ".join(parts), token, lossy


def _humanize_token(token) -> Optional[str]:
    if not token:
        return None
    t = re.sub(r"[-_]+", " ", str(token)).strip()
    return t or None


def _strip_target(url, base_url: Optional[str]) -> str:
    """Strip the origin from ``url`` when it matches the role's base URL → a path."""
    if url is _MISSING or not isinstance(url, str):
        return str(url)
    if not base_url:
        return url
    try:
        u, b = urlsplit(url), urlsplit(base_url)
    except ValueError:
        return url
    if u.scheme == b.scheme and u.netloc == b.netloc and u.netloc:
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        return path
    return url


# ── Statement → step dict ───────────────────────────────────────────────────


def _custom_step(stmt: ast.stmt, role: str, notes: list) -> dict:
    code = ast.unparse(stmt) if hasattr(ast, "unparse") else "# (unrecognized recorded action)"
    first = code.splitlines()[0] if code else ""
    notes.append(f"kept an unrecognized recorded statement as a custom step: {first[:80]}")
    return {"role": role, "action": "custom", "code": code, "_token": None}


def _page_step(chain, terminal, role, base_url, notes) -> Optional[dict]:
    name = terminal.name
    args = terminal.args

    if name == "goto":
        if not args or args[0] is _MISSING:
            return None
        return {"role": role, "action": "navigate", "target": _strip_target(args[0], base_url), "_token": None}

    selector, token, lossy = _selector_from_chain(chain)
    if not selector:
        return None
    tokh = _humanize_token(token)

    if name == "click":
        if lossy:
            notes.append(lossy)
        return {"role": role, "action": "click", "selector": selector, "_token": tokh}
    if name in ("fill", "press_sequentially", "type"):
        if not args or args[0] is _MISSING:
            return None
        if lossy:
            notes.append(lossy)
        return {"role": role, "action": "fill", "selector": selector, "value": args[0], "_token": tokh, "_field": token}
    if name == "select_option":
        val = args[0] if (args and args[0] is not _MISSING) else None
        if val is None:
            val = terminal.kwargs.get("value") or terminal.kwargs.get("label")
        if val is None or val is _MISSING:
            return None
        if lossy:
            notes.append(lossy)
        return {"role": role, "action": "select", "selector": selector, "value": val, "_token": tokh, "_field": token}
    if name == "press":
        if not args or args[0] is _MISSING:
            return None
        return {"role": role, "action": "keyboard", "value": args[0], "_token": _humanize_token(args[0])}
    if name in ("check", "uncheck", "set_checked"):
        if lossy:
            notes.append(lossy)
        notes.append(f"recorded {name}() emitted as a click - verify it toggles as expected")
        return {"role": role, "action": "click", "selector": selector, "_token": tokh}
    if name == "hover":
        if lossy:
            notes.append(lossy)
        return {"role": role, "action": "hover", "selector": selector, "_token": tokh}
    if name == "set_input_files":
        if not args or args[0] is _MISSING:
            return None
        return {"role": role, "action": "upload", "selector": selector, "value": args[0], "_token": tokh}
    return None


def _expect_step(base_args, segs, role, base_url, notes) -> Optional[dict]:
    if not segs:
        return None
    matcher = segs[-1]
    inner_sel = None
    tokh = None
    if base_args:
        w = _walk(base_args[0])
        if w:
            b2, _, inner_segs = w
            if b2 == "page":
                inner_sel, tok, lossy = _selector_from_chain(inner_segs)
                tokh = _humanize_token(tok)
                if lossy:
                    notes.append(lossy)

    mname = matcher.name
    margs = matcher.args
    have_text = margs and margs[0] is not _MISSING
    if mname == "to_be_visible" and inner_sel:
        expect = f"visible({inner_sel})"
    elif mname == "to_be_hidden" and inner_sel:
        expect = f"hidden({inner_sel})"
    elif mname == "to_contain_text" and inner_sel and have_text:
        expect = f'contains({inner_sel}, "{margs[0]}")'
    elif mname == "to_have_text" and inner_sel and have_text:
        expect = f'text({inner_sel}, "{margs[0]}")'
    elif mname == "to_have_url" and have_text:
        expect = f'url-contains("{_strip_target(margs[0], base_url)}")'
    else:
        notes.append(f"dropped a recorded assertion (.{mname}) - add an expect: manually if you need it")
        return None
    return {"role": role, "action": "assert", "expect": expect, "_token": tokh}


def _call_to_step(call: ast.Call, role, base_url, notes) -> Optional[dict]:
    walked = _walk(call)
    if walked is None:
        return None
    base, base_args, segs = walked
    if base == "page":
        if not segs:
            return None
        terminal = segs[-1]
        chain = segs[:-1]
        return _page_step(chain, terminal, role, base_url, notes)
    if base == "expect":
        return _expect_step(base_args, segs, role, base_url, notes)
    return None


def _stmt_to_step(stmt: ast.stmt, role, base_url, notes) -> Optional[dict]:
    if isinstance(stmt, ast.Expr):
        if isinstance(stmt.value, ast.Constant):
            return None  # a docstring
        if isinstance(stmt.value, ast.Call):
            return _call_to_step(stmt.value, role, base_url, notes) or _custom_step(stmt, role, notes)
        return None
    if isinstance(stmt, ast.Pass):
        return None
    # with / assign / if / ... - keep verbatim for the reviewer.
    return _custom_step(stmt, role, notes)


# ── Login collapse + placeholderize ─────────────────────────────────────────


def _collapse_login(steps: list[dict], role: str, notes: list) -> bool:
    """Collapse a username/password/submit sequence into ``- action: login``.

    Returns True when a collapse happened (⇒ scenario frontmatter ``auth: false``).
    """
    pi = next(
        (
            i
            for i, s in enumerate(steps)
            if s["action"] == "fill" and s.get("_field") and _PASSWORD_RE.search(str(s["_field"]))
        ),
        None,
    )
    if pi is None:
        return False
    remove = {pi}
    ui = next((i for i in range(pi - 1, -1, -1) if steps[i]["action"] == "fill"), None)
    if ui is not None:
        remove.add(ui)
    si = next((i for i in range(pi + 1, len(steps)) if steps[i]["action"] == "click"), None)
    if si is not None:
        remove.add(si)
    insert_at = min(remove)
    new = [s for i, s in enumerate(steps) if i not in remove]
    new.insert(insert_at, {"role": role, "action": "login", "_token": None})
    steps[:] = new
    notes.append(
        "collapsed the recorded username/password/submit into `- action: login` - "
        "credentials come from config.yaml (${STEPPY_%s_USERNAME/PASSWORD}), never the recording" % _envify(role)
    )
    return True


def _envify(text) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "_", str(text)).strip("_").upper()
    return s or "VALUE"


def _placeholderize(steps, role, placeholderize_all, env_vars: dict, mask: set, notes, login_collapsed):
    """Replace secret (or, with ``--placeholderize-all``, every) fill/select value
    with a ``${STEPPY_<ROLE>_<FIELD>}`` env ref; collect env names + mask selectors."""
    saw_password = login_collapsed
    for s in steps:
        if s["action"] not in ("fill", "select"):
            continue
        f = s.get("_field")
        if f and _PASSWORD_RE.search(str(f)):
            saw_password = True
        if placeholderize_all or (f and _SECRET_RE.search(str(f))):
            base_env = f"STEPPY_{_envify(role)}_{_envify(f or 'value')}"
            env = base_env
            n = 2
            while env in env_vars:
                env = f"{base_env}_{n}"
                n += 1
            s["value"] = "${" + env + "}"
            env_vars[env] = True
            if s.get("selector"):
                mask.add(s["selector"])
    if saw_password:
        mask.add("input[type='password']")
    if placeholderize_all:
        notes.append("--placeholderize-all: every recorded value is a ${VAR} ref - set them before running")


# ── Titles + rendering ──────────────────────────────────────────────────────


def _step_title(step: dict) -> str:
    action = step["action"]
    tok = step.get("_token")
    if action == "navigate":
        return f"Open {step.get('target', 'page')}"
    if action == "login":
        return f"Sign in as {step['role']}"
    if action == "keyboard":
        return f"Press {step.get('value', 'key')}"
    if action == "custom":
        return "Review recorded action"
    verbs = {
        "click": "Click",
        "fill": "Fill",
        "select": "Select",
        "hover": "Hover",
        "upload": "Upload",
        "assert": "Verify",
    }
    verb = verbs.get(action, action.capitalize())
    return f"{verb} {tok}" if tok else verb


def _emit_value(v: str) -> str:
    """Wrap a step value so the parser's surrounding-quote strip preserves it."""
    if '"' in v and "'" not in v:
        return "'" + v + "'"
    return '"' + v + '"'


def _render(title: str, auth_false: bool, steps: list[dict]) -> str:
    fm: dict = {"title": title, "description": "Recorded with steppy record", "tags": ["e2e"]}
    if auth_false:
        fm["auth"] = False
    out: list[str] = ["---", yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip(), "---", ""]
    for i, s in enumerate(steps, 1):
        out.append(f"### {i}. {s['_title']}")
        out.append(f"- role: {s['role']}")
        out.append(f"- action: {s['action']}")
        if s.get("target") is not None:
            out.append(f"- target: {s['target']}")
        if s.get("selector"):
            out.append(f"- selector: {s['selector']}")
        if s.get("value") is not None:
            out.append(f"- value: {_emit_value(str(s['value']))}")
        if s.get("expect"):
            out.append(f"- expect: {s['expect']}")
        if s.get("state"):
            out.append(f"- state: {s['state']}")
        if s.get("note"):
            out.append(f"- note: {s['note']}")
        if s.get("code"):
            out.append("- code: |")
            for line in s["code"].splitlines():
                out.append(f"    {line}")
        out.append("")
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _slugify(text: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "-", text.strip().lower()).strip("-")
    return s or "recording"


def _humanize(text: str) -> str:
    s = re.sub(r"[-_]+", " ", text).strip()
    return (s[:1].upper() + s[1:]) if s else "Recorded flow"


# ── The pure transform (unit-tested without a browser) ──────────────────────


def codegen_source_to_scenario(
    source: str,
    *,
    role: str,
    title: str,
    base_url: Optional[str] = None,
    auth_mode: Optional[str] = None,
    has_credentials: bool = False,
    placeholderize_all: bool = False,
) -> RecordResult:
    """Transform a ``playwright codegen --target python-pytest`` source into a
    Steppy scenario Markdown draft. Pure and deterministic (no browser, no clock)."""
    tree = ast.parse(source)
    fn = _find_test_fn(tree)
    notes: list = []
    steps: list[dict] = []
    if fn is not None:
        for stmt in fn.body:
            st = _stmt_to_step(stmt, role, base_url, notes)
            if st:
                steps.append(st)

    auth_false = False
    if auth_mode == "form" and has_credentials:
        auth_false = _collapse_login(steps, role, notes)

    env_vars: dict = {}
    mask: set = set()
    _placeholderize(steps, role, placeholderize_all, env_vars, mask, notes, auth_false)

    for s in steps:
        s["_title"] = _step_title(s)

    markdown = _render(title, auth_false, steps)
    return RecordResult(
        markdown=markdown,
        step_count=len(steps),
        notes=notes,
        env_vars=sorted(env_vars),
        mask_selectors=sorted(mask),
    )


# ── Command ─────────────────────────────────────────────────────────────────


def _print_report(result: RecordResult, out_path: Path) -> None:
    if result.notes:
        console.print("\n[bold]Review before committing:[/bold]")
        for note in result.notes:
            console.print(f"  [dim]note[/dim] {escape(note)}")
    if result.mask_selectors:
        console.print(
            "\n[bold]Redact secrets in screenshots[/bold] - add to config.yaml "
            "(comments there are preserved only if you edit by hand):"
        )
        console.print("  screenshot:")
        console.print("    mask:")
        for sel in result.mask_selectors:
            console.print(f'      - "{escape(sel)}"')
    if result.env_vars:
        console.print("\n[bold]Set these before running the generated test:[/bold]")
        for name in result.env_vars:
            console.print(f"  export {name}=...")
    console.print(f"\n[bold]Next:[/bold] steppy validate {out_path.as_posix()} && steppy generate")
    console.print(
        "Refine the draft with the [bold]steppy-scenario-author[/bold] skill "
        "(installed by `steppy init` in .claude/skills/)."
    )


def _run_record(
    config,
    url: Optional[str],
    role: Optional[str],
    scenarios_dir: Path,
    slug: Optional[str],
    title: Optional[str],
    placeholderize_all: bool,
    browser: str,
    channel: Optional[str],
    no_auth: bool,
    test_id_attribute: str,
) -> None:
    apps = config.apps
    if not apps:
        _error("no apps configured in config.yaml - nothing to record against")
        raise typer.Exit(1)
    if role is None:
        role = next(iter(apps))
    if role not in apps:
        _error(f"unknown role '{role}' - configured roles: {', '.join(apps)}")
        raise typer.Exit(1)
    if url is None:
        url = apps[role].url

    if slug is None and title is None:
        _error("provide --slug or --title so the draft has a filename (scenarios/<slug>.md)")
        raise typer.Exit(1)
    if title is None:
        title = _humanize(slug)
    if slug is None:
        slug = _slugify(title)
    out_path = scenarios_dir / f"{slug}.md"
    if out_path.exists():
        _error(f"refusing to overwrite {out_path.as_posix()} - choose a different --slug")
        raise typer.Exit(1)

    fd, tmp = tempfile.mkstemp(suffix="_codegen.py", prefix="steppy_record_")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        argv = [
            sys.executable,
            "-m",
            "playwright",
            "codegen",
            url,
            "--target",
            "python-pytest",
            "-o",
            str(tmp_path),
            "--test-id-attribute",
            test_id_attribute,
            "-b",
            browser,
        ]
        if channel:
            argv += ["--channel", channel]
        storage = Path(config.auth.storage_state_dir) / f"{role}.json"
        if not no_auth:
            if storage.exists():
                argv += ["--load-storage", str(storage)]
                console.print(f"[dim]Using cached auth for '{role}' ({storage.as_posix()}).[/dim]")
            else:
                console.print(
                    f"[yellow]note[/yellow] no cached auth for '{role}' "
                    f"({storage.as_posix()}); recording unauthenticated. Run "
                    f"`steppy auth login {role}` first to record a logged-in flow."
                )
        console.print(f"\n[bold]Recording[/bold] as '{role}' at {url}")
        console.print("A browser will open - perform your flow, then close the window to finish.\n")
        try:
            proc = subprocess.run(argv)
        except FileNotFoundError:
            _error("could not launch Playwright - is it installed? Try: pip install playwright && playwright install")
            raise typer.Exit(1)
        if proc.returncode != 0:
            _error(
                f"playwright codegen exited with code {proc.returncode}. "
                f"If browsers are missing, run: playwright install {browser}"
            )
            raise typer.Exit(1)
        source = tmp_path.read_text(encoding="utf-8") if tmp_path.exists() else ""
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        result = codegen_source_to_scenario(
            source,
            role=role,
            title=title,
            base_url=apps[role].url,
            auth_mode=apps[role].auth.mode,
            has_credentials=apps[role].auth.credentials is not None,
            placeholderize_all=placeholderize_all,
        )
    except SyntaxError:
        _error("could not parse the recorded output - no scenario written")
        raise typer.Exit(1)

    if result.step_count == 0:
        console.print(
            "[yellow]WARNING[/yellow] nothing recorded (interact before closing the window) - no scenario written."
        )
        raise typer.Exit(0)

    if out_path.exists():
        _error(f"refusing to overwrite {out_path.as_posix()}")
        raise typer.Exit(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.markdown, encoding="utf-8", newline="\n")
    console.print(f"  [green]create[/green] {out_path.as_posix()}")

    # Selector lint (best-effort - a validation hiccup degrades to a warning, the
    # draft is already written for review).
    try:
        scenario = parse_scenario_file(out_path)
        for warn in lint_scenario_selectors(scenario):
            console.print(f"  [yellow]WARNING[/yellow] {escape(str(warn))}")
    except ParseError as e:
        console.print(
            f"  [yellow]WARNING[/yellow] the draft did not validate cleanly - run "
            f"`steppy validate {out_path.as_posix()}` to see why"
        )
        if _debug_enabled():
            console.print(escape(str(e)))

    _print_report(result, out_path)


def register_record(app: typer.Typer) -> None:
    """Register the ``record`` command on the root Typer app."""

    @app.command("record")
    def record(
        url: Optional[str] = typer.Argument(None, help="Start URL (default: the role's configured app URL)."),
        role: Optional[str] = typer.Option(
            None,
            "--role",
            "-r",
            help="Role to record as (default: first app in config.yaml). Stamped on "
            "every step; also selects .auth/<role>.json for an authenticated session.",
        ),
        scenarios_dir: Path = typer.Option(
            Path("scenarios"),
            "--scenarios-dir",
            "-d",
            help="Directory to write the draft scenario into.",
        ),
        slug: Optional[str] = typer.Option(
            None,
            "--slug",
            help="Output filename stem → scenarios/<slug>.md (default: derived from --title).",
        ),
        title: Optional[str] = typer.Option(None, "--title", help="Scenario title (default: derived from --slug)."),
        placeholderize_all: bool = typer.Option(
            False,
            "--placeholderize-all/--no-placeholderize-all",
            help="Replace EVERY recorded value with a ${VAR} env ref (default: only "
            "detected secrets are placeholderized; other values stay literal).",
        ),
        browser: str = typer.Option(
            "chromium", "--browser", "-b", help="Browser to record in (chromium|firefox|webkit)."
        ),
        channel: Optional[str] = typer.Option(None, "--channel", help="Browser channel (e.g. chrome, msedge)."),
        no_auth: bool = typer.Option(
            False,
            "--no-auth",
            help="Do not load .auth/<role>.json - record a fresh, unauthenticated session "
            "(use when recording the login flow itself).",
        ),
        test_id_attribute: str = typer.Option(
            "data-testid",
            "--test-id-attribute",
            help="Attribute Playwright codegen prefers for locators.",
        ),
    ):
        """Record a browser session into a draft Steppy scenario.

        Wraps `playwright codegen`: a headed browser opens; perform your flow, then
        close the window. The recorded actions become a draft `scenarios/<slug>.md`
        - a reviewable starting point, not a finished scenario. Detected secrets are
        collapsed to `- action: login` or placeholderized to ${VAR} refs, and a
        password screenshot mask is recommended. Refine the draft with the
        steppy-scenario-author skill, then `steppy validate` / `steppy generate`.

        V1 (CLI plumbing) - extended when Steppy lands on the OSS showcases.
        """
        with _cli_errors():
            config = _load_config_or_exit()
            _run_record(
                config,
                url,
                role,
                scenarios_dir,
                slug,
                title,
                placeholderize_all,
                browser,
                channel,
                no_auth,
                test_id_attribute,
            )
