# Worker image for `steppy run --workers N >= 2`, built from a SOURCE CHECKOUT.
#
# Coordinator probes for `steppy-worker:<package_version>-<src_hash>` and
# auto-builds via this Dockerfile if missing. The image inherits Microsoft's
# Playwright/Python base (chromium pre-installed, Python 3.12) and adds the
# steppy package itself.
#
# Build context: the steppy repo root (so we can COPY src/ and pyproject.toml).
# Manual build (matches what coordinator runs):
#   docker build -t steppy-worker:dev -f Dockerfile .
#
# This file only works when a checkout is present. When steppy is installed as
# a wheel with no source tree on the machine, the coordinator instead builds
# `src/steppy/_docker/Dockerfile.installed` against a context synthesized from
# the installed package — see `steppy/_parallel/image.py`.

# Keep in lockstep with the playwright pin in pyproject.toml, with
# `_docker/Dockerfile.installed`, and with DEFAULT_PLAYWRIGHT_BASE in
# `steppy/_parallel/image.py` (guard: tests/test_parallel_worker_image.py).
ARG PLAYWRIGHT_BASE=mcr.microsoft.com/playwright/python:v1.60.0-noble
FROM ${PLAYWRIGHT_BASE}

# Workdir matches the coordinator's compose override (`working_dir:
# /steppy/project`) so cwd-relative paths work for pytest exactly like the
# host single-worker path.
WORKDIR /steppy/project

# Install steppy. Copy the build-essential bits (the user's project code is
# bind-mounted at runtime, not baked in). The custom hatchling build hook
# (hatch_build.py, wired in pyproject.toml) regenerates the bundled
# src/steppy/{templates,skills} + src/steppy/CHANGELOG.md from the canonical
# repo-root templates/, skills/, and CHANGELOG.md at build time — so ALL of
# those inputs must be in the context, not just pyproject + src. Omitting
# hatch_build.py makes `pip install` fail before the wheel build even starts
# ("Build script does not exist: hatch_build.py").
COPY pyproject.toml /tmp/steppy/pyproject.toml
COPY hatch_build.py /tmp/steppy/hatch_build.py
COPY src /tmp/steppy/src
COPY templates /tmp/steppy/templates
COPY skills /tmp/steppy/skills
COPY CHANGELOG.md /tmp/steppy/CHANGELOG.md

# The [retry,report] extras are baked in on purpose: `steppy run --workers N
# --retries K` resolves host-side but the `--reruns` flag executes INSIDE this
# container, so pytest-rerunfailures has to be here or the worker warns and
# silently skips retries. pytest-html is the same story for `report.html`.
# Adding an extra does NOT change any *.py under the package, so it does not
# move the src hash — WORKER_IMAGE_CONTRACT in _parallel/image.py is what busts
# stale cached images (guard: tests/test_parallel_worker_image.py).
RUN pip install --no-cache-dir '/tmp/steppy[retry,report]' && rm -rf /tmp/steppy

# Worker entrypoint reads STEPPY_WORKER_ID, STEPPY_COORD_DIR, etc. from the
# coordinator-injected env and loops claiming scenarios until the queue is
# empty, then exits with 0 (success) or 1 (any scenario failed).
ENTRYPOINT ["python", "-m", "steppy", "worker"]
