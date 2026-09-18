"""Count wording for diagnostics — the one place a rendered count grows its plural.

Every probe, fix and remediation job that used to interpolate ``"{n} thing(s)"`` calls
:func:`count_noun` instead. The placeholder plural is a defect of the diagnosis, not a
style preference: ``"1 unclaimed path(s) and 0 undeclared database(s)"`` reads as
machine output about an unknown quantity, which is exactly the wrong impression for a
line whose whole job is to tell an operator how much is wrong.

One helper rather than a per-site ternary so a new probe cannot reintroduce the form:
the tests render real probe output for zero, one and many findings, and a site that
skipped this function would show up there.
"""

from __future__ import annotations


def count_noun(value: float, singular: str, plural: str = "", *, fmt: str = "") -> str:
    """``value`` and a noun that agrees with it: ``"1 stale lock"`` / ``"3 stale locks"``.

    *plural* overrides the default ``singular + "s"`` for a noun that does not take it.
    *fmt* is a format spec for the number, for a count that is not a whole integer
    (``fmt="g"`` renders ``5.5`` as ``5.5`` and ``7.0`` as ``7``).
    """
    rendered = format(value, fmt) if fmt else str(value)
    word = singular if value == 1 else (plural or f"{singular}s")
    return f"{rendered} {word}"
