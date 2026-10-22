"""Deterministic coverage substrate: record -> merge -> map store -> gap report.

Recording happens inside the *generated* conftest (env-gated on
``STEPPY_COVERAGE=1``); everything in this package runs steppy-side and is
lazy-imported from the CLI only — ``steppy run`` without ``--coverage`` never
imports it (guarded by ``tests/test_no_coverage_import_on_default_path.py``).

No LLM calls anywhere: recording, merge, and gap compute are deterministic.
"""
