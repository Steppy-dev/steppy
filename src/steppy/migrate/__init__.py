"""``steppy migrate custom`` — rewrite mechanical ``custom`` blocks as steps.

New actions do not shrink the custom blocks a project already has; somebody
still has to rewrite them, and one measured project holds 4 967. This package is
that rewriter.

It is deliberately conservative. A block is converted only when **every**
statement in it maps to a rule, and only when the newly generated Python is
equivalent to what the ``custom`` body already emitted — see
:mod:`steppy.migrate.verify`. Anything else is reported, never touched. The
residue is the input to the next round of commands.

Lazy-imported: nothing here is reachable from ``import steppy.cli``
(``tests/test_no_migrate_import_on_default_path.py``).
"""

from .apply import Outcome, apply_plan, steps_to_markdown
from .plan import MigrationPlan, StepPlan, plan_file, plan_scenarios
from .rules import RULES, parse_block, statements
from .verify import VerificationResult, verify_rewrite

__all__ = [
    "RULES",
    "MigrationPlan",
    "Outcome",
    "StepPlan",
    "VerificationResult",
    "apply_plan",
    "parse_block",
    "plan_file",
    "plan_scenarios",
    "statements",
    "steps_to_markdown",
    "verify_rewrite",
]
