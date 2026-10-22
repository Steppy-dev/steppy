"""Golden-file test for the compose override renderer.

We don't run docker — we just snapshot the rendered YAML and assert the
critical pieces are present (and absent when they shouldn't be).
"""

from pathlib import Path

import yaml

from steppy._parallel.compose import ComposeIntrospection, render_override


def _render(tmp_path: Path, **overrides) -> str:
    introspection = overrides.pop(
        "introspection",
        ComposeIntrospection(services_with_ports=["app"], networks=[]),
    )
    return render_override(
        introspection=introspection,
        worker_image="steppy-worker",
        image_tag="0.1.0",
        worker_index=1,
        run_id="abc12345",
        project_root=tmp_path / "proj",
        tmp_dir=tmp_path / "run",
        base_url_override='{"editor": "http://editor-app:3000"}',
        locales="fr,nl",
        layouts="desktop,mobile",
        guide_mode="1",
        no_asserts="1",
        screenshots="1",
        **overrides,
    )


def test_renders_steppy_worker_service(tmp_path):
    yml = _render(tmp_path)
    assert "steppy-worker:" in yml
    assert "image: steppy-worker:0.1.0" in yml


def test_resets_ports_for_user_services(tmp_path):
    yml = _render(
        tmp_path,
        introspection=ComposeIntrospection(services_with_ports=["app", "redis"], networks=[]),
    )
    # !reset is the load-bearing line — without it, Compose would APPEND
    # to the inherited ports list, defeating the host-port nullification.
    assert "  app:" in yml
    assert "    ports: !reset []" in yml
    assert "  redis:" in yml


def test_no_reset_block_when_no_user_ports(tmp_path):
    yml = _render(
        tmp_path,
        introspection=ComposeIntrospection(services_with_ports=[], networks=[]),
    )
    assert "!reset" not in yml
    # Should still have the steppy-worker service.
    assert "steppy-worker:" in yml


def test_coverage_env_passthrough(tmp_path):
    """STEPPY_COVERAGE flows into the worker env: '1' when --coverage was
    passed, '' otherwise (the generated conftest's `or "0"` pattern tolerates
    the empty string)."""
    assert "STEPPY_COVERAGE: '1'" in _render(tmp_path, coverage="1")
    assert "STEPPY_COVERAGE: ''" in _render(tmp_path)


def test_passes_env_vars(tmp_path):
    yml = _render(tmp_path)
    assert "STEPPY_WORKER_ID: 'w1'" in yml
    assert "STEPPY_RUN_ID: 'abc12345'" in yml
    assert "STEPPY_LOCALES: 'fr,nl'" in yml
    assert "STEPPY_LAYOUTS: 'desktop,mobile'" in yml
    assert "STEPPY_GUIDE_MODE: '1'" in yml
    assert 'STEPPY_BASE_URL_OVERRIDE: \'{"editor": "http://editor-app:3000"}\'' in yml


def test_enables_event_emission_and_unbuffered_io(tmp_path):
    """Parallel mode opts the worker into the structured-events plugin
    (STEPPY_EMIT_EVENTS=1) and disables Python stdout buffering so events
    appear in real time over ``docker compose logs -f``."""
    yml = _render(tmp_path)
    assert "STEPPY_EMIT_EVENTS: '1'" in yml
    assert "PYTHONUNBUFFERED: '1'" in yml


def test_mounts_project_and_coord(tmp_path):
    yml = _render(tmp_path)
    parsed = yaml.safe_load(_strip_reset_tags(yml))
    volumes = parsed["services"]["steppy-worker"]["volumes"]
    # Project dir mounted RO; .auth + output overlaid RW; coord dir RW.
    assert any(v.endswith(":/steppy/project:ro") for v in volumes), volumes
    assert any(v.endswith(":/steppy/project/.auth") for v in volumes), volumes
    assert any(v.endswith(":/steppy/project/output") for v in volumes), volumes
    assert any(v.endswith(":/steppy/coord") for v in volumes), volumes


def test_default_network_when_introspection_empty(tmp_path):
    yml = _render(
        tmp_path,
        introspection=ComposeIntrospection(services_with_ports=["app"], networks=[]),
    )
    parsed = yaml.safe_load(_strip_reset_tags(yml))
    assert parsed["services"]["steppy-worker"]["networks"] == ["default"]


def test_custom_networks_propagated(tmp_path):
    yml = _render(
        tmp_path,
        introspection=ComposeIntrospection(services_with_ports=["app"], networks=["app-net", "db-net"]),
    )
    parsed = yaml.safe_load(_strip_reset_tags(yml))
    nets = parsed["services"]["steppy-worker"]["networks"]
    assert "app-net" in nets
    assert "db-net" in nets


def _strip_reset_tags(yml: str) -> str:
    """PyYAML doesn't know !reset. Strip it for parsing — tests above cover
    the literal !reset lines via substring assertions."""
    return yml.replace("!reset ", "")
