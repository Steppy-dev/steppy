"""In-app tours compiler: scenarios → tour files + replay plans.

Lazily imported only (guard: ``tests/test_no_tours_import_on_default_path.py``):
``steppy generate`` / ``validate`` / ``build`` reach it behind
``tours.enabled``, and ``steppy tours`` through its leaf CLI module.

Module map — each step of the pipeline is a module:

* ``settings``   resolved, immutable view of ``config.yaml``'s ``tours:`` block
* ``actions``    the per-action table (``ACTION_TOUR_KIND``) and step candidates
* ``routes``     entry-route normalisation, matching and parameter binding
* ``inference``  one scenario → kept tour steps + per-step replay dispositions
* ``text``       per-locale titles/texts, leak and link checks
* ``ids``        tour and step ids (never positional)
* ``compiler``   all opted-in scenarios → ``TourCompileResult``
* ``emit``       payloads, canonical bytes, SRI, write plans, prune
* ``pipeline``   the entry point ``generate`` and ``tours`` share
* ``publish``    the ``steppy build`` side (guide target)
* ``replay``     ``ReplayPlan`` → what the test template renders

Nothing here imports ``steppy.guide`` (markdown-it) or Playwright.
"""
