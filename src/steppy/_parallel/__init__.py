"""Internal helpers for ``steppy run --workers N >= 2``.

This package is imported only on the parallel code path. The single-worker
path in ``steppy.cli.run`` uses lazy imports so default invocations never
load anything from here. See the lazy-import guard test in
``tests/test_no_parallel_import_on_default_path.py``.
"""
