"""The shipped CI templates: valid YAML, a report step, and a *commented* Pages job.

Named ``test_report_ci_templates`` so it is never confused with
``test_report_ci.py``, which owns the unrelated ``report:`` config block (JUnit,
HTML, traces, videos, reruns).

The load-bearing assertion here is that the Pages job stays **commented out**.
Publishing screenshots is a decision, and a scaffold that makes it by default —
into a public site, permanently — would be the wrong kind of helpful.
"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from steppy.cli import app

runner = CliRunner()

_CANONICAL = Path(__file__).resolve().parents[1] / "templates" / "ci"
_GITHUB = _CANONICAL / "github-actions.yml"
_GITLAB = _CANONICAL / "gitlab-ci.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── both templates are valid, runnable YAML ─────────────────────────────────


@pytest.mark.parametrize("path", [_GITHUB, _GITLAB], ids=["github", "gitlab"])
def test_the_template_parses_as_yaml(path: Path):
    """A scaffolded pipeline that does not parse is worse than none: the user finds
    out from their CI provider, not from us."""
    assert path.is_file(), path
    assert isinstance(yaml.safe_load(_text(path)), dict)


def test_the_github_workflow_has_the_expected_jobs():
    doc = yaml.safe_load(_text(_GITHUB))
    assert set(doc["jobs"]) == {"e2e"}, "the pages job must stay commented out"
    steps = doc["jobs"]["e2e"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert "Build the run report" in names
    assert "Upload the report" in names


def test_the_github_run_step_passes_bundle():
    """Without --bundle there are no event records, so the report can name a failing
    test but not the step inside it."""
    doc = yaml.safe_load(_text(_GITHUB))
    run_steps = [s for s in doc["jobs"]["e2e"]["steps"] if s.get("name") == "Run scenarios"]
    assert run_steps and "--bundle" in run_steps[0]["run"]


def test_the_github_report_step_runs_even_on_failure():
    """A failing run is exactly when the report matters."""
    doc = yaml.safe_load(_text(_GITHUB))
    step = next(s for s in doc["jobs"]["e2e"]["steps"] if s.get("name") == "Build the run report")
    assert step["if"] == "always()"


# ── the stack the scenarios run against ─────────────────────────────────────


def test_the_github_workflow_brings_the_stack_up_and_tears_it_down():
    """The scaffold has to run the app, not just describe wanting one.

    ``--wait`` rather than a bare ``up -d``: without it the scenarios start
    against containers that exist but are not serving yet. ``down -v`` rather
    than ``down``: the next run should not inherit the last one's seeds.
    """
    doc = yaml.safe_load(_text(_GITHUB))
    steps = doc["jobs"]["e2e"]["steps"]
    up = next(s for s in steps if s.get("name") == "Start the application stack")
    assert "docker compose up -d --wait" in up["run"]

    down = next(s for s in steps if s.get("name") == "Stop the application stack")
    assert "docker compose down -v" in down["run"]
    assert down["if"] == "always()", "a stack left running outlives the job that owns it"

    names = [s.get("name", "") for s in steps]
    assert names.index("Start the application stack") < names.index("Run scenarios")


def test_the_github_workflow_dumps_stack_logs_on_failure():
    """Red suite, and the first question is always "the tests or the stack?"."""
    doc = yaml.safe_load(_text(_GITHUB))
    step = next(s for s in doc["jobs"]["e2e"]["steps"] if s.get("name") == "Application logs")
    assert "docker compose logs" in step["run"]
    assert step["if"] == "failure()"


def test_the_compose_step_documents_the_two_ways_it_silently_does_nothing():
    """Both failure modes are properties of the user's compose file, invisible
    from here, and both present as "every scenario failed" rather than as an
    error: ``--wait`` only waits on services declaring a healthcheck, and the
    scenarios (plus seed scripts) run on the runner, not in a container."""
    text = _text(_GITHUB)
    assert "healthcheck:" in text
    assert "publish its ports" in text


# ── the install line names steppy, not the consumer's own project ───────────


@pytest.mark.parametrize("path", [_GITHUB, _GITLAB], ids=["github", "gitlab"])
def test_the_install_line_installs_steppy(path: Path):
    """``pip install '.[retry,report]'`` installs whatever the CONSUMER's repo is
    — their app, or nothing — never steppy. The templates are copied into a
    project that holds config.yaml and scenarios/, so ``.`` is never us."""
    text = _text(path)
    assert "pip install '.[" not in text, "'.' is the consumer's project, not steppy"
    assert "steppy-dev[retry,report" in text
    # Not on PyPI yet, so a git ref — pinned to a tag, because a branch means the
    # suite's own tooling can change under it between two identical commits.
    assert "git+https://github.com/Steppy-dev/steppy.git@v" in text


# ── the badge says what it needs ────────────────────────────────────────────


@pytest.mark.parametrize("path", [_GITHUB, _GITLAB], ids=["github", "gitlab"])
def test_the_badge_recipe_states_its_prerequisites(path: Path):
    """A README badge that 404s teaches the reader the project is unmaintained.

    Two of the three prerequisites are invisible from the shields URL: no
    ``--coverage`` means ``badge.json`` is never written at all (render.py only
    emits it when ``coverage.tier != "none"``), and ``steppy coverage refresh``
    exits 1 with no ``coverage:`` block — which is why these steps ship unarmed.
    """
    text = _text(path)
    assert "badge.json is written ONLY when the run recorded coverage" in text
    assert "EXITS 1" in text
    assert "n/a" in text


@pytest.mark.parametrize("path", [_GITHUB, _GITLAB], ids=["github", "gitlab"])
def test_the_pages_recipe_warns_about_the_single_site_per_repo(path: Path):
    """Report and guides both want the Pages root. Whoever loses moves, and
    moving the report also moves badge.json — the one URL a README pins."""
    text = _text(path)
    assert "ONE Pages site" in text
    assert "badge.json" in text


def test_the_gitlab_pipeline_runs_the_report_after_the_run():
    doc = yaml.safe_load(_text(_GITLAB))
    job = doc["e2e"]
    assert "--bundle" in " ".join(job["script"])
    # after_script, so it also runs when the run failed — and `|| true` so a
    # rendering hiccup never rewrites the job's real status.
    assert any("steppy report" in line for line in job["after_script"])
    assert "output/report/" in job["artifacts"]["paths"]
    assert job["artifacts"]["when"] == "always"


def test_the_gitlab_pipeline_declares_no_pages_job():
    doc = yaml.safe_load(_text(_GITLAB))
    assert "pages" not in doc, "the pages job must stay commented out"


# ── the Pages job is documented but not armed ──────────────────────────────


@pytest.mark.parametrize("path", [_GITHUB, _GITLAB], ids=["github", "gitlab"])
def test_the_pages_recipe_is_present_as_commented_guidance(path: Path):
    text = _text(path)
    assert "Pages" in text
    # Present as a recipe...
    assert "deploy-pages" in text or "mv output/report public" in text
    # ...and every line of it commented.
    for line in text.splitlines():
        if "deploy-pages" in line or line.strip().startswith("pages:"):
            assert line.lstrip().startswith("#"), f"armed Pages line: {line!r}"


@pytest.mark.parametrize("path", [_GITHUB, _GITLAB], ids=["github", "gitlab"])
def test_the_pages_recipe_warns_that_publishing_is_permanent(path: Path):
    """ADR 0013 is emphatic about this: `screenshot.mask` exists, but a leak is
    permanent and indexed. The scaffold has to say so where the decision is made."""
    text = _text(path)
    assert "screenshot.mask" in text
    assert "permanent" in text


def test_the_github_recipe_says_artifact_never_a_branch():
    """Screenshots committed to gh-pages on every run grow the repository's history
    forever and cannot be pruned without a rewrite."""
    text = _text(_GITHUB)
    assert "upload-pages-artifact" in text
    assert "gh-pages branch" in text


def test_the_gitlab_recipe_mentions_its_access_control_advantage():
    """The one place GitLab Pages beats GitHub's for this: it can be restricted to
    project members, which makes a published run report viable for client work."""
    assert "Access Control" in _text(_GITLAB)


@pytest.mark.parametrize("path", [_GITHUB, _GITLAB], ids=["github", "gitlab"])
def test_the_coverage_recipe_points_at_the_non_ui_prerequisite(path: Path):
    """Triage flags live in the gitignored .steppy/coverage.db, so a badge built in
    CI without `coverage.non_ui` reads artificially low — and a badge like that
    gets deleted."""
    text = _text(path)
    assert "coverage.non_ui" in text
    assert "gitignored" in text


# ── scaffolding ─────────────────────────────────────────────────────────────


def test_init_ci_writes_only_the_github_workflow(tmp_path: Path):
    """`--ci` is a boolean people already type; it could not grow a value without
    breaking every existing invocation."""
    assert runner.invoke(app, ["init", str(tmp_path), "--ci", "--no-skills"]).exit_code == 0
    assert (tmp_path / ".github" / "workflows" / "steppy.yml").is_file()
    assert not (tmp_path / ".gitlab-ci.yml").exists()


def test_init_ci_gitlab_writes_only_the_gitlab_pipeline(tmp_path: Path):
    assert runner.invoke(app, ["init", str(tmp_path), "--ci-gitlab", "--no-skills"]).exit_code == 0
    assert (tmp_path / ".gitlab-ci.yml").is_file()
    assert not (tmp_path / ".github").exists()


def test_both_flags_together_write_both(tmp_path: Path):
    result = runner.invoke(app, ["init", str(tmp_path), "--ci", "--ci-gitlab", "--no-skills"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".github" / "workflows" / "steppy.yml").is_file()
    assert (tmp_path / ".gitlab-ci.yml").is_file()


def test_the_gitlab_pipeline_lands_in_the_scaffold_manifest(tmp_path: Path):
    """Without a manifest entry, `steppy update` cannot tell a pristine scaffold
    file from one the user edited, so it would never refresh it."""
    import json

    assert runner.invoke(app, ["init", str(tmp_path), "--ci-gitlab", "--no-skills"]).exit_code == 0
    manifest = json.loads((tmp_path / ".steppy" / "scaffold.json").read_text(encoding="utf-8"))
    assert ".gitlab-ci.yml" in manifest["files"]


def test_update_refreshes_the_gitlab_pipeline(tmp_path: Path):
    """The other half: `_refreshable_content` has to recognise the path, or the
    file is managed on the way in and orphaned forever after."""
    from steppy.update_cli import _refreshable_content

    assert _refreshable_content(".gitlab-ci.yml") == _text(_GITLAB)
    assert _refreshable_content(".github/workflows/steppy.yml") == _text(_GITHUB)
    assert _refreshable_content("config.yaml") is None  # the user owns this one
