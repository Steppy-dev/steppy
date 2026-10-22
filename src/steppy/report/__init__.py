"""``steppy report``: one results bundle rendered to a self-contained static site.

**Lazy-imported only.** Nothing here may be reached by ``import steppy.cli`` —
this package walks and hashes an ``output/`` tree, opens tar archives and builds a
Jinja environment, none of which belongs on the path a plain ``steppy run`` (or
``steppy --help``) takes. Every ``steppy.report`` import lives inside a
``report_cli`` command body. Guard:
``tests/test_no_report_import_on_default_path.py``.

**A note on the name**, because "report" already means four things here and a
fifth would be one too many. ``config.yaml``'s ``report:`` block
(:class:`steppy.models.ReportConfig`) is about *pytest* artifacts — JUnit XML, an
HTML report, traces, videos, reruns. The ``steppy-dev[report]`` extra is pytest-html.
``bundle``'s file-kind ``report`` labels those artifacts inside an archive. And
``steppy coverage report`` prints the gap buckets. This package is none of them:
it is the *run report site* — the thing you publish to GitHub or GitLab Pages.

What it is, and just as importantly what it is not. The report **transforms one
bundle**. That is a structural property, not a restriction: with a single run's
artifacts there is nothing to diff against, no history to rank flakes by, and no
baseline anyone accepted. Those need memory, and memory lives in a control plane.

Module map:

- ``source``   — three inputs (live tree / archive / unpacked dir), one protocol.
- ``model``    — the pure view-model builder. **No jinja2**, deliberately: this is
  the module a control plane imports, and it must not drag a renderer along.
- ``assets``   — which captures travel, where they land, and how they dedupe.
- ``coverage_source`` — the four-tier coverage resolution and the badge number.
- ``render``   — jinja2 → HTML, CSS, ``report.json``, ``badge.json``, robots.
- ``build``    — orchestration, :class:`ReportError`, :class:`ReportResult`.

This file re-exports **nothing**, on purpose. ``from steppy.report import
build_report`` would make importing ``steppy.report.model`` pull in the renderer
and therefore jinja2 — which is exactly what the model/render split exists to
prevent. Import the submodule you need (the same shape ``steppy.coverage`` uses).
"""
