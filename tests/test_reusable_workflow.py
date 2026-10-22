"""The reusable ``workflow_call`` workflow other projects call with ``uses:``.

Distinct from ``test_report_ci_templates.py``, which owns the *scaffolded*
pipelines ``steppy init --ci`` copies into a project. This one is not copied
anywhere — callers reference it by ref, so its contents at a tag are a public
contract with every repository that pins it, and an input renamed after the fact
breaks pipelines Steppy cannot see.

The load-bearing assertions are about ordering and failure handling: everything
worth having is produced by the steps *after* the run, so the run must not be
allowed to abort the job, and the verdict must be re-raised at the end.
"""

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "steppy-e2e.yml"


def _doc() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    """The ``on:`` block.

    YAML 1.1 resolves a bare ``on`` to the boolean ``True``, so ``doc["on"]`` is
    a ``KeyError`` under ``safe_load`` — the single most common way a workflow
    test asserts nothing at all while passing.
    """
    return doc.get("on", doc.get(True))


def _steps(doc: dict) -> list[dict]:
    return doc["jobs"]["e2e"]["steps"]


def _step(doc: dict, name: str) -> dict:
    return next(s for s in _steps(doc) if s.get("name") == name)


def test_the_workflow_parses():
    assert _WORKFLOW.is_file(), _WORKFLOW
    assert isinstance(_doc(), dict)


def test_it_only_runs_when_another_repository_calls_it():
    """Steppy's own CI is ci.yml. A stray `push:` here would run an e2e suite
    against an application this repository does not contain."""
    triggers = _triggers(_doc())
    assert "workflow_call" in triggers
    assert set(triggers) == {"workflow_call"}


# ── the contract with callers ───────────────────────────────────────────────


def test_it_declares_the_inputs_a_caller_needs():
    inputs = _triggers(_doc())["workflow_call"]["inputs"]
    for name in ("compose-file", "compose-up", "run-args", "steppy-ref", "runs-on", "working-directory"):
        assert name in inputs, f"missing input: {name}"
    # A self-hosted label is how this reaches an app that GitHub's runners cannot.
    assert inputs["runs-on"]["default"] == "ubuntu-latest"
    # Off-by-default would silently skip the app for every caller that trusted it.
    assert inputs["compose-up"]["default"] is True


def test_it_exposes_the_verdict_as_outputs():
    """This is what a reusable workflow gives that a copy-pasted one does not:
    `report.json` already knows the verdict, so a caller can gate or comment on
    it without re-parsing anything."""
    outputs = _triggers(_doc())["workflow_call"]["outputs"]
    for name in ("verdict", "exit-code", "scenarios", "scenarios-not-passed", "badge-percent"):
        assert name in outputs, f"missing output: {name}"

    # Declared at both levels or the job's values never reach the caller.
    job_outputs = _doc()["jobs"]["e2e"]["outputs"]
    assert set(job_outputs) == set(outputs)


def test_credentials_arrive_as_one_optional_secret():
    """The ${VAR} names in a consumer's config.yaml are theirs, so they cannot be
    declared as individual secrets here. Optional, because a throwaway seeded
    stack legitimately has nothing to inject."""
    secrets = _triggers(_doc())["workflow_call"]["secrets"]
    assert "steppy-env" in secrets
    assert secrets["steppy-env"].get("required") is False


# ── failure handling is the whole point of the ordering ─────────────────────


def test_the_run_step_captures_its_exit_code_instead_of_aborting():
    step = _step(_doc(), "Run scenarios")
    assert step["id"] == "run"
    assert "set +e" in step["run"]
    assert "exit-code=$code" in step["run"]


def test_the_verdict_is_re_raised_after_everything_is_produced():
    """Without this the job goes green for a red run. With it placed earlier, the
    report never gets built."""
    doc = _doc()
    gate = _step(doc, "Verdict")
    assert "exit 1" in gate["run"]
    assert "steps.run.outputs.exit-code != '0'" in gate["if"]
    # The empty guard: when an earlier step failed the run never set an exit
    # code, and a second error would point at the wrong cause.
    assert "steps.run.outputs.exit-code != ''" in gate["if"]

    names = [s.get("name", "") for s in _steps(doc)]
    assert names.index("Verdict") == len(names) - 1, "the gate must be last"
    assert names.index("Build the run report") < names.index("Verdict")


@pytest.mark.parametrize(
    "name",
    ["Build the run report", "Summarise the run", "Upload the report", "Stop the application stack", "Verdict"],
)
def test_the_steps_after_the_run_survive_a_failing_run(name: str):
    """A failing run is exactly when the report, the artifacts and the teardown
    matter most. Any one of these without `always()` is skipped precisely then."""
    assert _step(_doc(), name)["if"].startswith("always()")


def test_the_stack_is_torn_down_with_its_volumes():
    """Without `-v` the next run inherits the last one's seeded database, which
    produces failures that reproduce in CI and never locally."""
    step = _step(_doc(), "Stop the application stack")
    assert "down -v" in step["run"]


def test_every_compose_step_is_skippable_together():
    """A caller pointing apps.<role>.url at a deployed environment sets
    compose-up false; a stray unguarded compose step would then fail the job on a
    file that repository does not have."""
    for step in _steps(_doc()):
        if "docker compose" in step.get("run", ""):
            assert "inputs.compose-up" in step.get("if", ""), f"unguarded compose step: {step.get('name')}"
