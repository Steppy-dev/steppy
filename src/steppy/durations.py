"""Duration parsing, shared by the model validator and codegen.

``timeout:`` is written by a scenario author and consumed by the generator, so
the grammar has to be the same in both places or ``steppy validate`` accepts a
value the generator then mangles — the shape of bug ``steppy.assertions`` was
extracted to close. Stdlib only: ``models.py`` is on the eager CLI path.

The grammar matches the ``wait`` action's ``duration:`` exactly, including its
bare-number-means-milliseconds rule, so an author never has to remember two.
"""

from __future__ import annotations

import re
from typing import Union

__all__ = ["DurationSyntaxError", "parse_ms"]


class DurationSyntaxError(ValueError):
    """Raised when a duration string is not ``<number>[ms|s]``."""


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)?\s*$")


def parse_ms(value: Union[str, int, float]) -> int:
    """Return ``value`` in whole milliseconds.

    ``"25s"`` -> 25000, ``"500ms"`` -> 500, ``25000`` -> 25000. A bare number is
    milliseconds — the same rule ``generator._format_wait_duration`` applies to
    ``wait``'s ``duration:``.
    """
    if isinstance(value, bool):  # bool is an int subclass; a timeout of True is a typo
        raise DurationSyntaxError(f"duration must be a number or a string, got {value!r}")
    if isinstance(value, (int, float)):
        ms = float(value)
    else:
        match = _DURATION.match(str(value))
        if not match:
            raise DurationSyntaxError(
                f'duration {value!r} is not understood. Use e.g. "25s", "500ms", or a bare number of milliseconds'
            )
        number, unit = float(match.group(1)), match.group(2)
        ms = number * 1000 if unit == "s" else number
    if ms <= 0:
        raise DurationSyntaxError(f"duration must be positive, got {value!r}")
    return int(round(ms))
