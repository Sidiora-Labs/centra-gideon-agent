"""Shorter parameterized test identifiers, for the one job that runs the suite whole.

The sharded jobs (``test-shard``, ``matrix-shard``) split by *duration per node-id* —
``pytest-split`` keys its durations on the id string — so silently rewriting ids would make
every recorded duration miss and re-balance the shards for reasons nobody asked for. The
unsharded coverage leg has no such tie: it collects everything in one process, and it is the
run whose log is long enough for truncation to be a question at all.

So the shortening is OPT-IN, off unless ``GIDEON_SHORT_TEST_IDS`` is set to a true value,
and only ``.github/workflows/full.yml``'s ``coverage`` job sets it. Nothing about that job's
topology changes: same single ``pytest --cov`` command, same tests, same order.

The rules, and why each one is what it is:

* **Anything that is not a string is left alone.** pytest already names those
  ``<argname><index>`` — short by construction, and a hook that returned something for them
  would be inventing noise.
* **A string at or under** :data:`LIMIT` **is left alone**, so the ids a reader recognises
  stay byte-identical between this job and the sharded ones.
* **A longer string keeps its last path segment**, because a parametrized path's meaning is
  in the leaf (``.../fixtures/deep/nested/case-seventeen.json``), never in the shared prefix.
* **…then squeezes runs of non-alphanumerics to one dash and truncates to** :data:`KEEP`.
* **…and appends a short digest of the WHOLE original value.** Truncation collides, and two
  parameters that render to one id are two rows a failure cannot be attributed between.
  pytest would de-duplicate them with a positional suffix, which is exactly the id nobody
  can read. The digest keeps distinct values distinct and is deterministic across runs, so
  a failing id from a CI log still selects the same case locally.
"""

from __future__ import annotations

import hashlib
import os
import re

ENV_VAR = "GIDEON_SHORT_TEST_IDS"

LIMIT = 24
KEEP = 16
DIGEST = 4

_TRUE = frozenset({"1", "true", "yes", "on"})
_SQUEEZE = re.compile(r"[^A-Za-z0-9]+")


def enabled(environ: "os._Environ[str] | dict[str, str] | None" = None) -> bool:
    """True when this run opted into short ids. Anything unset/empty/false is off."""
    env = os.environ if environ is None else environ
    return str(env.get(ENV_VAR, "")).strip().lower() in _TRUE


def shorten(val: object) -> str | None:
    """A short, deterministic id for ``val``, or ``None`` to leave pytest's own id alone."""
    if isinstance(val, (bytes, bytearray)):
        source: bytes = bytes(val)
        text = source.decode("utf-8", "replace")
    elif isinstance(val, str):
        text = val
        source = text.encode("utf-8", "backslashreplace")
    else:
        return None

    if len(text) <= LIMIT:
        return None

    tail = text.rsplit("/", 1)[-1]
    squeezed = _SQUEEZE.sub("-", tail or text).strip("-")
    head = squeezed[:KEEP].rstrip("-") or "id"
    digest = hashlib.blake2b(source, digest_size=DIGEST).hexdigest()[:DIGEST]
    return f"{head}~{digest}"
