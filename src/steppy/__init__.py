"""Steppy - A Python CLI for generating Playwright tests from Markdown scenarios."""

__version__ = "0.8.1"

# The name pip installs this package under. `steppy` on PyPI belongs to an
# unrelated project, so the distribution is `steppy-dev` while the import
# package and the command stay `steppy`. Code that asks importlib.metadata
# about the installed package goes through this name, never a literal.
DISTRIBUTION = "steppy-dev"
