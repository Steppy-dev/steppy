"""Code generator for Steppy - transforms scenarios into pytest files."""

import re
from pathlib import Path
from typing import Optional

from .assertions import parse_assertion
from .durations import parse_ms
from ._templating import make_template_env, template_search_paths
from .models import Scenario, SteppyConfig
from .presets import IDP_PRESETS


class SteppyGenerator:
    """Generates pytest test files from parsed scenarios."""

    def __init__(
        self,
        config: SteppyConfig,
        templates_dir: Optional[Path] = None,
        project_root: Optional[Path] = None,
    ):
        """
        Initialize the generator.

        Args:
            config: Validated Steppy configuration
            templates_dir: Explicit template directory (error if missing).
            project_root: Root whose ``templates/`` overrides the bundled ones.
                          Defaults to the current working directory.

        See ``_templating.template_search_paths`` for the precedence rule,
        which is shared with the guide and build assemblers.
        """
        self.config = config

        self._search_paths = [str(p) for p in template_search_paths(templates_dir, project_root)]
        self.env = make_template_env(templates_dir, project_root)

        # Add custom filters
        self.env.filters["format_wait_duration"] = self._format_wait_duration
        self.env.filters["format_assertion"] = self._format_assertion
        self.env.filters["format_assertions"] = self._format_assertions
        self.env.filters["bracket_step"] = self._bracket_step
        self.env.filters["apply_step_guard"] = self._apply_step_guard
        self.env.filters["apply_optional"] = self._apply_optional
        self.env.filters["format_dropdown_option"] = self._format_dropdown_option
        self.env.filters["format_match_suffix"] = self._format_match_suffix
        self.env.filters["disambig_selector"] = self._disambig_selector
        self.env.filters["resolve_timeout"] = self._resolve_timeout
        self.env.filters["timeout_kwarg"] = self._timeout_kwarg
        self.env.filters["call_kwargs"] = self._call_kwargs
        self.env.filters["timeout_ms"] = parse_ms
        self.env.filters["pystr"] = self._py_str
        self.env.filters["emits_screenshot"] = self._emits_screenshot
        self.env.filters["docstring"] = self._docstring_safe
        self.env.filters["tour_section"] = self._tour_section

    @staticmethod
    def _py_str(value) -> str:
        """Render a value as a safe Python string literal via ``repr()``.

        Produces single-quoted output for simple strings — byte-identical to the
        historical hand-quoted templates (``'{{ x }}'``) — while correctly
        escaping apostrophes, quotes, backslashes, newlines, and non-ASCII. The
        result always ``eval``s back to the original string, so a scenario value
        like ``L'adresse`` or a toast ``L'élément a été créé`` no longer
        breaks the generated Python with a ``SyntaxError``.

        ``None`` is coerced to the literal ``'None'`` to match Jinja's own
        ``{{ None }}`` → ``None`` rendering, preserving byte-identity for the
        (validation-caught) missing-field case.
        """
        return repr("None" if value is None else str(value))

    @staticmethod
    def _emits_screenshot(step) -> bool:
        """True when this step emits a screenshot for its own role (FLAWS §8.2).

        Replaces the screenshot-emit predicate that was copy-pasted 36× across
        ``test_scenario.py.j2``. Mirrors the historical inline expression exactly
        so codegen stays byte-identical: ``screenshot`` is truthy, is not the
        literal ``False``, and is either ``True``, the step's own ``role``, or the
        ``"all"`` sentinel. The role is read from the step itself (there is no bare
        ``role`` in template scope), so the template calls ``step | emits_screenshot``
        with no argument; timing/cursor clauses (``screenshot_when``, ``guide_cursor``)
        stay at the call site since they vary per branch.
        """
        ss = getattr(step, "screenshot", None)
        return bool(ss) and ss is not False and (ss is True or ss == step.role or ss == "all")

    @staticmethod
    def _docstring_safe(value) -> str:
        """Escape a value for safe interpolation inside a triple-quoted docstring
        (FLAWS §8.1).

        Frontmatter ``title``/``description`` are emitted into the generated test
        function's ``\"\"\"...\"\"\"`` docstring. A value containing a ``\"`` or a
        trailing backslash could terminate or corrupt that literal (a ``SyntaxError``
        at pytest collection, exactly the §1.1 failure mode). Escaping ``\\`` → ``\\\\``
        and ``\"`` → ``\\\"`` closes the hole. Values with no quotes/backslashes (every
        real title so far) render unchanged, so existing goldens stay byte-identical.
        """
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _bracket_step(body: str, step) -> str:
        """Wrap a step's emitted body in the per-step event markers.

        Done in Python rather than with two lines in the template, because
        whether the captured body ends with a newline **depends on which action
        branch rendered**: a branch whose last literal is followed by a
        ``{%- elif %}`` has its trailing newline stripped at lex time, and one
        that ends inside a nested ``{% if %}`` does not. A template-level marker
        therefore glues onto the previous statement for *some* actions only —
        which is a syntax error in the generated file for those scenarios and
        nothing at all for the rest. Normalising here removes the whole class of
        bug, and puts it beside ``_apply_step_guard``, which already owns the
        body's final shape.

        Applied BEFORE the guard filter so the markers land inside
        ``if _step_runs(...)`` — a step skipped by ``only-in``/``layouts`` must
        emit neither, or ``failed_step`` could name a step that never ran.
        """
        number = step.step_number
        title = SteppyGenerator._py_str(step.step_title)
        action = SteppyGenerator._py_str(step.action)
        inner = body.strip("\n")
        opening = f"    _steppy_mark_step({number}, {title}, {action})"
        closing = f"    _steppy_end_step({number})"
        if not inner.strip():
            return f"{opening}\n{closing}\n"
        return f"{opening}\n{inner}\n{closing}\n"

    @staticmethod
    def _apply_optional(body: str, step) -> str:
        """Wrap a step's body in ``try/except`` when it declares ``optional: true``.

        For the step that may legitimately not be there — the confirmation panel
        that only appears the first time, the banner that has already cleared.
        The corpus spells this as a bare ``try:``/``except Exception: pass``
        around a custom block, 93 times in one project and 9 in the other.

        Applied BEFORE ``bracket_step``, so the ``try`` sits *inside* the step
        markers: the step really did run, and ``_steppy_end_step`` must still be
        reached or ``failed_step`` would blame a step that merely opted out.

        No-op when the flag is absent, so unflagged steps stay byte-identical.
        """
        if not getattr(step, "optional", None):
            return body
        inner = body.strip("\n")
        if not inner.strip():
            return body
        indented = "\n".join(("    " + line) if line.strip() else line for line in inner.split("\n"))
        number = step.step_number
        return (
            "    try:\n"
            + indented
            + "\n    except Exception as _optional_err:\n"
            + f'        print(f"STEPPY: optional step {number} skipped: {{_optional_err}}", flush=True)\n'
        )

    @staticmethod
    def _apply_step_guard(body: str, step) -> str:
        """Wrap a step's emitted body in ``if _step_runs(only_in, layouts):``
        when the step declares ``only-in`` or ``layouts`` filters. No-op for
        unfiltered steps so the common path stays byte-identical to the
        pre-feature output.

        The captured ``body`` already has the standard 4-space test-function
        indent. The wrap adds the guard line at that indent and re-indents
        every non-blank body line by 4 more spaces so it sits inside the
        ``if`` block."""
        when = getattr(step, "when", None)
        if not getattr(step, "only_in", None) and not getattr(step, "layouts", None) and not when:
            return body
        only_in_arg = repr(step.only_in) if step.only_in else "None"
        layouts_arg = repr(step.layouts) if step.layouts else "None"
        conditions = [f"_step_runs({only_in_arg}, {layouts_arg})"]
        if when:
            # Evaluated before the step markers, exactly like `_step_runs`: a
            # step whose condition is false did not run, so it must emit
            # neither marker.
            conditions.append(f"_when({step.role}_page, {SteppyGenerator._py_str(when)}, _vars)")
        guard = " and ".join(conditions)
        lines = body.split("\n")
        indented = []
        for line in lines:
            if line.strip():
                indented.append("    " + line)
            else:
                indented.append(line)
        return f"    if {guard}:\n" + "\n".join(indented)

    def _format_wait_duration(self, duration: str) -> float:
        """
        Convert duration string (e.g., '5s', '3000ms') to seconds as float.

        Args:
            duration: Duration string like "5s" or "3000ms"

        Returns:
            Duration in seconds as a float
        """
        duration = duration.strip().lower()

        if duration.endswith("ms"):
            return float(duration[:-2]) / 1000
        elif duration.endswith("s"):
            return float(duration[:-1])
        else:
            # Assume milliseconds if no unit
            return float(duration) / 1000

    @staticmethod
    def _call_kwargs(step, scenario, config, has_args: bool = True) -> str:
        """The extra keyword arguments this step adds to its Playwright call.

        Returns ``", timeout=25000, force=True"``, or ``"timeout=25000"`` when
        the call has no positional arguments to comma-follow (``locator.click()``
        versus ``page.click(sel)``), or ``""`` when the step asks for neither.

        The empty case is load-bearing: ``click``/``fill``/``hover``/``upload``/
        ``scroll``/``select``/``navigate`` emit no timeout at all today and sit
        on Playwright's own 30s default, not on ``config.timing.step_timeout``.
        Emitting a resolved default for them would change both the generated
        bytes and the runtime behaviour of every existing scenario.
        """
        parts = []
        if getattr(step, "timeout", None) is not None or getattr(scenario, "timeout", None) is not None:
            parts.append(f"timeout={SteppyGenerator._resolve_timeout(step, scenario, config)}")
        if getattr(step, "force", None):
            parts.append("force=True")
        if not parts:
            return ""
        joined = ", ".join(parts)
        return f", {joined}" if has_args else joined

    @staticmethod
    def _timeout_kwarg(step, scenario, config, lead: str = ", ") -> str:
        """``", timeout=25000"`` when this step asks for one, ``""`` otherwise.

        For the actions that emit no timeout at all today — ``click``, ``fill``,
        ``hover``, ``upload``, ``scroll``, ``select``, ``navigate`` — the kwarg
        has to be absent unless asked for, or every existing scenario's
        generated file changes. Emitting the resolved default instead would be
        a behaviour change too: those calls currently sit on Playwright's own
        30s, not on ``config.timing.step_timeout``.
        """
        if getattr(step, "timeout", None) is None and getattr(scenario, "timeout", None) is None:
            return ""
        return f"{lead}timeout={SteppyGenerator._resolve_timeout(step, scenario, config)}"

    @staticmethod
    def _resolve_timeout(step, scenario, config, default: Optional[int] = None) -> int:
        """Milliseconds this step should wait, most specific source winning.

        ``step.timeout`` -> ``scenario.timeout`` -> ``config.timing.step_timeout``.

        The scenario level is not new surface: ``Scenario.timeout`` has existed
        since the first release and was read by nothing — this is the wiring it
        was always missing.

        Returns an int so the template can emit ``timeout=25000`` directly. A
        step that sets nothing resolves to the global default, which is what
        every call site baked before, so unflagged steps stay byte-identical.
        """
        raw = getattr(step, "timeout", None)
        if raw is None:
            raw = getattr(scenario, "timeout", None)
        if raw is None:
            return config.timing.step_timeout if default is None else default
        return parse_ms(raw)

    def _format_assertion(self, expect: str) -> dict[str, str]:
        """Parse an assertion string into components for code generation.

        Delegates to :mod:`steppy.assertions`, which is also what
        ``Step.validate_expect_grammar`` calls — so ``steppy validate`` and
        codegen can never disagree about what an assertion means. The grammar
        used to live here alone, and an unrecognised form returned
        ``{"type": "unknown"}`` that ``assert_body`` rendered as ``pass``: a
        green test asserting nothing. It is now a ``ValidationError`` raised at
        parse time, long before this method runs.
        """
        return parse_assertion(expect)

    def _format_dropdown_option(self, option: str) -> dict[str, object]:
        """Parse a ``select-option`` ``option:`` spec into a codegen descriptor.

        Forms:
            first | last        -> {"kind": "first"|"last"}
            N (integer)         -> {"kind": "nth", "index": N}
            text:"After lunch"  -> {"kind": "text", "text": "After lunch"}
        """
        option = (option or "").strip()
        if option == "first":
            return {"kind": "first"}
        if option == "last":
            return {"kind": "last"}
        if option.startswith("text:"):
            text = option[5:].strip().strip('"').strip("'")
            return {"kind": "text", "text": text}
        if re.match(r"^\d+$", option):
            return {"kind": "nth", "index": int(option)}
        # Fallback: treat as a literal substring match.
        return {"kind": "text", "text": option}

    @staticmethod
    def _format_assertions(expect) -> list[dict]:
        """Every assertion on a step, as descriptors carrying their message.

        Always a list, so the template has one shape to render whether the
        author wrote a scalar or a list. A scalar with no message produces a
        single descriptor whose ``message`` is ``None``, which the macro renders
        exactly as it did before this field grew.
        """
        out: list[dict] = []
        if expect is None:
            return out
        entries = [expect] if isinstance(expect, str) else list(expect)
        for entry in entries:
            if isinstance(entry, str):
                check, message = entry, None
            else:
                check, message = entry.check, entry.message
            # `fail_message`, not `message`: the `toast(...)` descriptor already
            # owns a `message` key (the toast text), and clobbering it silently
            # asserted on the string "None".
            out.append({**parse_assertion(check), "fail_message": message, "raw": check})
        return out

    @staticmethod
    def _disambig_selector(step) -> str:
        """``within``/``match`` folded back into a single selector string.

        For the two actions that hand a *selector* to a conftest helper rather
        than building a locator — ``pick-date`` and ``pick-time``, which pass one
        into ``_pick_date``/``_pick_time`` — a locator chain cannot be threaded
        without changing those signatures. Playwright's selector engine already
        composes the same thing with ``>>``, so the chain is expressed there
        instead. Returns the bare selector unchanged when neither field is set,
        keeping existing output byte-identical.
        """
        parts = []
        if getattr(step, "within", None):
            parts.append(str(step.within))
        parts.append(str(step.selector))
        match = getattr(step, "match", None)
        if match is not None:
            spec = str(match).strip().lower()
            if spec == "first":
                parts.append("nth=0")
            elif spec == "last":
                parts.append("nth=-1")
            else:
                parts.append(f"nth={int(spec)}")
        return " >> ".join(parts)

    def _format_match_suffix(self, match: object) -> str:
        """Convert a ``match:`` spec into the Playwright locator suffix it maps to.

        Forms:
            first        -> ".first"
            last         -> ".last"
            N (integer)  -> ".nth(N)"
            None/empty   -> ""  (no disambiguation)
        """
        if match is None:
            return ""
        m = str(match).strip().lower()
        if m == "":
            return ""
        if m == "first":
            return ".first"
        if m == "last":
            return ".last"
        if re.match(r"^\d+$", m):
            return f".nth({int(m)})"
        # Validator already rejected anything else; be defensive.
        return ""

    def generate_test_file(self, scenario: Scenario, tour_plan=None) -> str:
        """
        Generate a pytest .py file from a parsed scenario.

        Args:
            scenario: Parsed and validated scenario
            tour_plan: The scenario's in-app tour ``ReplayPlan`` (from
                ``steppy.tours``), or ``None``. With a plan, the file also gets
                the ``test_<slug>__tour`` replay; without one it is byte-identical
                to a tours-free build.

        Returns:
            Python code as a string
        """
        template = self.env.get_template("test_scenario.py.j2")

        roles_needed = sorted(scenario.get_roles())

        if tour_plan is None:
            # ``tour_replay`` is passed explicitly rather than left undefined: an
            # undefined name resolves to Jinja's ``missing`` sentinel, and a
            # re-imported jinja2 (tests purge sys.modules) makes the compiled
            # template compare it against a different sentinel object.
            return template.render(
                scenario=scenario,
                config=self.config,
                roles_needed=roles_needed,
                tour_replay=None,
            )

        from .tours.replay import build_replay

        replay = build_replay(scenario, tour_plan, self.config)
        code = template.render(
            scenario=scenario,
            config=self.config,
            roles_needed=roles_needed,
            tour_replay=replay,
        )
        if f"def {replay.function_name}(" not in code:
            # A project-local templates/test_scenario.py.j2 that predates in-app
            # tours renders no replay — refuse rather than ship a tour CI never checks.
            raise ValueError(
                "your templates/test_scenario.py.j2 override predates in-app tours (no step_body macro / tour "
                "replay section): copy the bundled template's changes into it, or remove the override"
            )
        return code

    def _tour_section(self, replay, step_body) -> str:
        """The ``test_<slug>__tour`` replay function, composed from ``step_body`` bodies.

        Composed in Python for the same reason ``_bracket_step`` is: whether a
        captured body ends with a newline depends on the action branch, and the
        tour checks must sit on their own lines around it.
        """
        from pprint import pformat

        role = replay.role
        if replay.auth:
            params = ", ".join(f"{r}_page: Page" for r in replay.roles)
            aliases = []
        else:
            params = ", ".join(f"{r}_page_noauth: Page" for r in replay.roles)
            aliases = [f"    {r}_page = {r}_page_noauth" for r in replay.roles]
        tour_id = replay.spec["id"]
        lines = [
            "",
            "",
            "# The in-app tour this scenario compiles to, as the replay checks it.",
            f"TOUR = {pformat(replay.spec, width=110, sort_dicts=False)}",
            "",
            "",
            f"def {replay.function_name}({params}, request):",
            f'    """In-app tour replay of {self._py_str(tour_id)}: the scenario, followed the way a user '
            'follows the tour."""',
            "    _vars: dict[str, str] = {}",
            *aliases,
            f"    _tour = TourProbe({role}_page, request, TOUR, step_runs=_step_runs)",
        ]
        out = "\n".join(lines) + "\n"
        for item in replay.items:
            out += self._tour_item(item, step_body, replay)
        out += f"\n\n{replay.function_name}._steppy_tour_role = {self._py_str(role)}\n"
        return out

    def _tour_item(self, item, step_body, replay) -> str:
        if item.kind == "deferred-ensure":
            body = f"    _tour_ensure_advanced(_tour, {self._py_str(item.step_id)})\n"
            return self._apply_step_guard(body, item.step)
        rule = "─" * max(3, 74 - len(item.comment))
        head = f"\n    # ── {item.comment} {rule}\n"
        if item.kind == "omitted":
            return head
        if item.kind == "start":
            args = ", ".join(f"{self._py_str(p)}: _interp({self._py_str(t)}, _vars)" for p, t in replay.params)
            return head + f"    _tour_start(_tour, {{{args}}})\n"
        if item.kind == "complete":
            return head + "    _tour_expect_complete(_tour)\n"
        if item.kind in ("setup", "plain"):
            body = self._apply_optional(str(step_body(item.step)), item.step)
            return head + self._apply_step_guard(self._bracket_step(body, item.step), item.step)
        chunks = []
        for part, check in item.parts:
            target = f", _interp({self._py_str(check.selector)}, _vars)" if check.selector else ""
            chunks.append(f"    _tour_expect_step(_tour, {self._py_str(check.step_id)}{target})")
            chunks.append(str(step_body(part)))
            if check.ensure == "inline":
                chunks.append(f"    _tour_ensure_advanced(_tour, {self._py_str(check.step_id)})")
        body = "\n".join(c.strip("\n") for c in chunks if c.strip()) + "\n"
        return head + self._apply_step_guard(self._bracket_step(body, item.step), item.step)

    def tours_helper_source(self) -> str:
        """The static ``_steppy_tours.py`` replay helper (written only when tours are on)."""
        return self._read_static_template("_steppy_tours.py")

    def _read_static_template(self, filename: str) -> str:
        """Read a static (non-Jinja) template file from the configured search
        paths. The first match wins — same precedence as the Jinja loader, so
        a user-customised file in ``templates/`` overrides the bundled copy."""
        for path_str in self._search_paths:
            candidate = Path(path_str) / filename
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Static template not found in search paths: {filename}")

    def generate_conftest(self, all_scenarios: list[Scenario]) -> str:
        """
        Generate the shared conftest.py with auth fixtures.

        Args:
            all_scenarios: List of all scenarios to determine which role fixtures are needed

        Returns:
            Python code as a string
        """
        # Collect all roles used across all scenarios
        all_roles = set()
        for scenario in all_scenarios:
            all_roles.update(scenario.get_roles())

        template = self.env.get_template("conftest.py.j2")

        return template.render(
            config=self.config,
            roles=sorted(all_roles),
            tours_enabled=self._tours_enabled(),
            # The conftest reads config.yaml raw at run time, so it carries the
            # preset table itself to resolve `auth.form.preset`.
            idp_form_presets={name: kit["form"] for name, kit in IDP_PRESETS.items()},
        )

    def _tours_enabled(self) -> bool:
        tours = getattr(self.config, "tours", None)
        return bool(tours is not None and tours.enabled)

    def generate_all(
        self,
        scenarios: list[Scenario],
        output_dir: Path,
        force: bool = False,
        *,
        tour_plans=None,
    ) -> dict[str, str]:
        """
        Generate all test files + conftest.

        Args:
            scenarios: List of parsed scenarios
            output_dir: Output directory (e.g., tests/e2e/)
            force: If True, overwrite existing files without asking

        Returns:
            Dict mapping file paths to status ("generated", "updated", "unchanged")
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        results = {}

        # Ensure __init__.py files exist to make it a package
        tests_init = output_dir.parent / "__init__.py"
        if not tests_init.exists():
            tests_init.write_text("# This file makes tests a package\n", encoding="utf-8")
            results[str(tests_init)] = "generated"

        e2e_init = output_dir / "__init__.py"
        if not e2e_init.exists():
            e2e_init.write_text("# This file makes tests.e2e a package\n", encoding="utf-8")
            results[str(e2e_init)] = "generated"

        # Generate conftest.py
        conftest_path = output_dir / "conftest.py"
        conftest_code = self.generate_conftest(scenarios)

        if conftest_path.exists() and not force:
            existing_content = conftest_path.read_text(encoding="utf-8")
            if existing_content == conftest_code:
                results[str(conftest_path)] = "unchanged"
            else:
                conftest_path.write_text(conftest_code, encoding="utf-8")
                results[str(conftest_path)] = "updated"
        else:
            conftest_path.write_text(conftest_code, encoding="utf-8")
            results[str(conftest_path)] = "generated"

        # Drop the parallel-mode events plugin next to conftest. The conftest
        # gates the import on STEPPY_EMIT_EVENTS, so this file is inert in
        # single-worker runs. Plain Python, no Jinja substitution.
        events_path = output_dir / "_steppy_events.py"
        events_code = self._read_static_template("_steppy_events.py")
        if events_path.exists() and not force:
            existing_events = events_path.read_text(encoding="utf-8")
            if existing_events == events_code:
                results[str(events_path)] = "unchanged"
            else:
                events_path.write_text(events_code, encoding="utf-8")
                results[str(events_path)] = "updated"
        else:
            events_path.write_text(events_code, encoding="utf-8")
            results[str(events_path)] = "generated"

        # The in-app tour replay helper, only when tours are on (a config-level
        # gate, never per scenario: a partial generate must not drop it).
        if self._tours_enabled():
            if tour_plans is None and self.config.tours.replay:
                raise ValueError("tours are enabled: generate_all needs the compiled tour plans (tour_plans=...)")
            helper_path = output_dir / "_steppy_tours.py"
            helper_code = self.tours_helper_source()
            if helper_path.exists() and not force:
                same = helper_path.read_text(encoding="utf-8") == helper_code
                if not same:
                    helper_path.write_text(helper_code, encoding="utf-8")
                results[str(helper_path)] = "unchanged" if same else "updated"
            else:
                helper_path.write_text(helper_code, encoding="utf-8")
                results[str(helper_path)] = "generated"

        # Generate test files
        for scenario in scenarios:
            filename = f"test_{scenario.slug}.py"
            test_path = output_dir / filename
            test_code = self.generate_test_file(scenario, tour_plan=(tour_plans or {}).get(scenario.slug))

            if test_path.exists() and not force:
                existing_content = test_path.read_text(encoding="utf-8")
                if existing_content == test_code:
                    results[str(test_path)] = "unchanged"
                else:
                    test_path.write_text(test_code, encoding="utf-8")
                    results[str(test_path)] = "updated"
            else:
                test_path.write_text(test_code, encoding="utf-8")
                results[str(test_path)] = "generated"

        return results
