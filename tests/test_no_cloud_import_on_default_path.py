"""Guard: the default CLI path must not import steppy.cloud.

Fourth in the family alongside the parallel, coverage and bundle guards, and it
carries two separate reasons at once.

**Cost.** ``steppy.cloud.client`` imports ``httpx`` at module scope, which is
~40ms every invocation — including ``steppy --help`` — paid by the many commands
that never speak to a control plane.

**Boundary.** Steppy works with no control plane at all, and no feature here may
assume one exists. Keeping the client unreachable from the default path is the
mechanical form of that rule: if ``import steppy.cli`` ever pulls in the plane
client, something on the free path has started depending on it.
"""

import subprocess
import sys

_CLOUD_MODULE_PREFIX = "steppy.cloud"


def _purge(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            del sys.modules[name]


def _loaded(prefix: str) -> list[str]:
    return sorted(name for name in sys.modules if name == prefix or name.startswith(prefix + "."))


def test_steppy_cli_module_load_does_not_import_cloud():
    _purge(_CLOUD_MODULE_PREFIX)
    sys.modules.pop("steppy.cli", None)
    import steppy.cli  # noqa: F401

    leaked = _loaded(_CLOUD_MODULE_PREFIX)
    assert not leaked, (
        f"Importing steppy.cli pulled in cloud modules: {leaked}. "
        "Keep every steppy.cloud import lazy inside the command bodies that need one."
    )


def test_bundle_cli_module_load_does_not_import_cloud():
    """``bundle_cli`` hosts ``upload`` and is imported eagerly by cli.py, so it
    is the module most likely to leak the client."""
    _purge(_CLOUD_MODULE_PREFIX)
    sys.modules.pop("steppy.bundle_cli", None)
    import steppy.bundle_cli  # noqa: F401

    leaked = _loaded(_CLOUD_MODULE_PREFIX)
    assert not leaked, f"Importing steppy.bundle_cli pulled in: {leaked}."


_PROBE = """
import sys
import steppy.bundle_cli  # noqa: F401

leaked = sorted(n for n in sys.modules if n == "httpx" or n.startswith("httpx."))
print(",".join(leaked))
"""


def test_importing_the_bundle_cli_does_not_import_httpx():
    """A subprocess, for the same reason its sibling guard uses one: ``httpx``
    is an ordinary installed package that other tests legitimately import, so an
    in-process ``sys.modules`` check would be defeated by test ordering."""
    result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True)
    assert result.returncode == 0, f"probe failed:\n{result.stdout}\n{result.stderr}"
    assert not result.stdout.strip(), (
        f"steppy.bundle_cli pulled in httpx: {result.stdout.strip()}. "
        "`steppy bundle upload` must import steppy.cloud inside its command body."
    )
