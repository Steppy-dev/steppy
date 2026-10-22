"""Allow ``python -m steppy ...`` invocation alongside the ``steppy`` script.

Used by the worker container's entrypoint so the image doesn't depend on
the script entrypoint being on PATH (it's installed via pyproject's
``[project.scripts]`` and Python 3.12 puts it in /usr/local/bin via pip,
but `python -m steppy` is more portable across base images).
"""

from .cli import app


if __name__ == "__main__":
    app()
