"""Workflow confirmation identity, progress formatting and tree inspection."""

from __future__ import annotations

import calendar

from gideon.automation.workflows.controller import Any as Any
from gideon.automation.workflows.controller import Node as Node


def _confirmation_id(run_id: str, gate_id: str, epoch: int) -> str:
    """The stable confirmation id for one (run, gate, epoch).

    Delegates to `confirmation.request_id` rather than composing a string here. Two id schemes for
    one record is the failure mode where `confirmation_pending` and `confirmation_resolved` never
    pair up in the ledger, and nobody notices until someone asks how long a gate waited.

    The EPOCH is in the key because a rewind SHOULD produce a new confirmation — the question is
    being asked about different work. Deriving from the resume token instead would break that: a
    token is single-use and rotates per poll, so pending and resolved would carry different ids for
    the same question.
    """
    from gideon.automation.workflows.confirmation import request_id

    return request_id(run_id, gate_id, epoch)


def _confirmation_kind(node_config: dict[str, Any]) -> str:
    """Which `ConfirmationType` this gate is, as its wire value.

    A destructive gate is NOT the same record as an ordinary approval: §4 gives them different
    expiry policies (auto-reject vs hold) and only the ordinary one may be muted. Reading the
    node's own declared risk keeps that classification with the author who made it, rather than
    inferring it from the prompt text at render time.
    """
    from gideon.automation.workflows.confirmation import ConfirmationType

    risk = str((node_config or {}).get("risk_category", "") or "").strip().lower()
    if risk in {"destructive", "destructive_op", "irreversible"}:
        return ConfirmationType.DESTRUCTIVE_CONFIRM.value
    kind = str((node_config or {}).get("kind", "") or "").strip().lower()
    if kind in {"input", "needs_input", "question"}:
        return ConfirmationType.NEEDS_INPUT.value
    return ConfirmationType.APPROVAL.value


def _item_label(item: Any) -> str:
    """A short, human-readable label for one foreach item.

    Prefers a NAMED field when the item is a dict, because a fan-out over records is the common
    case and `{"path": "auth.py", …}` should read as `auth.py`, not as its JSON. Falls back to
    a truncated stringification — something is always better than an index alone, which is
    what the row already shows.
    """
    if isinstance(item, dict):
        for key in ("label", "name", "title", "path", "id"):
            value = item.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return _clip(str(value))
        return _clip(", ".join(f"{k}={v}" for k, v in list(item.items())[:3]))
    if isinstance(item, (list, tuple)):
        return f"{len(item)} items"
    if item is None:
        return ""
    return _clip(str(item))


def _clip(text: str) -> str:
    from gideon.automation.workflows.controller import (
        _ITEM_LABEL_MAX,
    )

    text = " ".join(text.split())
    return text if len(text) <= _ITEM_LABEL_MAX else text[: _ITEM_LABEL_MAX - 1] + "…"


def _opt_metric(value: Any) -> float | None:
    """A metric, or None when there is not a number here (PP-12).

    Booleans are refused: `True` would read as `1.0` and pass a `metric_pass: 1.0` gate on a field
    that was never a measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_engine_install_fault(exc: BaseException) -> bool:
    """Whether `exc` says the ENGINE ITSELF could not be imported, not that a run failed.

    The distinction is the whole point: an `ImportError` naming a `gideon` module means
    this PROCESS is stale (its code was deleted or predates the run's state), so it knows
    nothing about the run and must not render a verdict on it. Every other exception — a
    provider error, a bad spec, a third-party import that a node genuinely needs — IS about
    the run and still terminally fails it. Widening this to all `ImportError`s would silently
    convert real run failures into runs that never finish.

    Keyed on `ImportError.name` rather than the message: the attribute is populated for both
    shapes that occur here (`from gideon.x import y` sets it to `gideon.x`, a
    missing module sets it to the module), and matching message text would break the moment
    CPython rewords it. `name` can be None for a hand-raised `ImportError`, which reads as
    "not attributable to the engine" — the conservative answer, since it keeps the existing
    fail-loudly behaviour for anything we cannot positively identify.
    """
    if not isinstance(exc, ImportError):
        return False
    name = getattr(exc, "name", None) or ""
    return name == "gideon" or name.startswith("gideon.")


def _now() -> str:
    from gideon.automation.workflows.controller import (
        time,
    )

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(ts: str | None) -> float:
    """Parse a UTC `...Z` stamp to a real epoch.

    `calendar.timegm`, NOT `time.mktime`: mktime reads the struct as LOCAL time, which
    shifts a UTC stamp by the machine's offset. Here it is only ever used as a DIFFERENCE
    of two stamps, so equal offsets cancelled and elapsed time came out right — except
    across a DST boundary, where the two offsets differ and the run's duration was off by
    an hour.
    """
    from gideon.automation.workflows.controller import time

    if not ts:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0


def _walk(root: Node) -> list[tuple[str, Node]]:
    from gideon.automation.workflows.models import walk

    return walk(root)


def _enclosing_parallel(path: str, tree: dict[str, Node]) -> str | None:
    """The path of the nearest enclosing `parallel`, walking OUTWARD.

    Not just the nearest `.children[N]` prefix: a watcher's synthesize stage sits at
    `…children[1].body@3.children[0]`, whose nearest prefix is the BODY SEQUENCE. Stopping
    there returned no siblings for the one node in the whole template that needs them —
    measured, and silent, because a missing `siblings` root reads as "this node has no
    siblings" rather than as an error.

    Nearest-first among genuine parallels, so a nested parallel resolves to the inner one: a
    node's siblings are the legs of ITS parallel, not an outer one's.
    """
    from gideon.automation.workflows.controller import (
        NodeKind,
        re,
        spec_path,
    )

    matches = list(re.finditer(r"\.children\[\d+\]", path))
    for match in reversed(matches):
        candidate = path[: match.start()]
        if not candidate:
            continue
        node = tree.get(spec_path(candidate))
        if node is not None and node.kind == NodeKind.PARALLEL:
            return candidate
    return None


def _natural_key(path: str) -> list[Any]:
    """Sort instance paths NUMERICALLY on their indices.

    A plain string sort puts `children[10]` before `children[2]` and `body@10` before `body@2`,
    so "oldest first" silently became wrong at the tenth iteration — the window would keep the
    wrong items and `previous.output` would return the wrong cycle. Ten cycles in is late enough
    that no short test would ever see it.
    """
    from gideon.automation.workflows.controller import (
        re,
    )

    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", path)]


def _loop_parent(path: str) -> tuple[str | None, int]:
    """`root.children[0].body@2` → `("root.children[0]", 2)`.

    The marker need not END the path. A loop whose body is a CONTAINER puts its leaf work
    deeper — `root.children[1].body@0.children[2]` — and the old form required the path to end
    at `@N`, so `int("0.children[2]")` raised, `_advance_loop` returned silently, the loop never
    advanced, and the run deadlocked after exactly one iteration. Measured live, and five
    shipped templates use container-bodied loops.

    The INNERMOST marker wins, so a loop nested inside another loop's body advances itself
    rather than its parent.
    """
    from gideon.automation.workflows.controller import (
        _LOOP_MARKER_RE,
    )

    matches = list(_LOOP_MARKER_RE.finditer(path))
    if not matches:
        return None, 0
    match = matches[-1]
    body = path[: match.start()]
    if not body.endswith(".body"):
        return None, 0
    return body[: -len(".body")], int(match.group(1))


def _is_dry(output: Any) -> bool:
    """Did an iteration surface anything new, judged by its WHOLE output?

    The rule for a loop that declares no `progress_field`. Unchanged: an empty or absent
    output is dry, anything else is progress.
    """
    if output is None:
        return True
    if isinstance(output, (list, dict, str)):
        return len(output) == 0
    return False


def _progress_reading(value: Any) -> str:
    """Classify ONE value of a loop's declared `progress_field`: dry, progress, unreadable.

    The rule, stated once: **a declared progress field is dry when its value is the field's
    own expression of "nothing"** — zero, blank, empty, false, or null. Per type, exhaustively:

    * ``None`` → dry. The body answered the question with "nothing".
    * ``bool`` → ``False`` dry, ``True`` progress. A boolean field IS the answer; checked
      before ``int`` because ``bool`` is an ``int`` subclass and would otherwise be read as
      "1 finding" / "0 findings" by accident.
    * ``int`` / ``float`` → dry iff ``== 0``. This is the shipped `new_findings_count: 0`
      case. A NEGATIVE count is progress, not dryness: a nonsensical count is not evidence
      that nothing happened, and reading it as dryness would cut the loop short.
    * ``str`` → dry iff blank after ``strip()``. A whitespace-only summary of what is new
      says nothing is new.
    * ``bytes`` / ``bytearray`` → dry iff empty.
    * ``list`` / ``tuple`` / ``set`` / ``frozenset`` / ``dict`` → dry iff empty. Nothing
      collected.
    * any other type → **unreadable**. There is no rule for it, so this refuses to call it
      dry and hands the decision back to the whole-output fallback. Not swallowed as
      "progress": the caller can tell "I read the field and it said nothing" from "I could
      not read the field", and only the first may end a loop.
    """
    from gideon.automation.workflows.controller import (
        _DRY,
        _PROGRESS,
        _UNREADABLE,
    )

    if value is None:
        return _DRY
    if isinstance(value, bool):
        return _PROGRESS if value else _DRY
    if isinstance(value, (int, float)):
        return _DRY if value == 0 else _PROGRESS
    if isinstance(value, str):
        return _DRY if not value.strip() else _PROGRESS
    if isinstance(value, (bytes, bytearray)):
        return _DRY if len(value) == 0 else _PROGRESS
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return _DRY if len(value) == 0 else _PROGRESS
    return _UNREADABLE


def _preview(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (dict, list)):
        import json

        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)[:limit]
        return text[:limit]
    return value


def _parse_revise(answer: Any) -> tuple[str, str] | None:
    """Read `revise{step_ref, comment}` out of a gate answer. Returns `(step_ref, comment)` or None.

    Recognised STRUCTURALLY, by the `revise` key rather than by a free-text prefix. A gate whose
    ask is a `text` legitimately receives prose, and sniffing for the word "revise" in it would
    hijack an answer that merely mentioned revising something.

    `answer` is untyped by contract (`WORKFLOW_RESUME_SCHEMA`), so both spellings a caller
    naturally reaches for are accepted: the nested `{"revise": {...}}` a tool emits, and the flat
    `{"revise": true, "step_ref": ..., "comment": ...}` a form posts. An `answer` with no `revise`
    key is None, which is what routes every existing answer down the unchanged approval path.
    """
    if not isinstance(answer, dict) or "revise" not in answer:
        return None
    body = answer.get("revise")
    if isinstance(body, dict):
        source: dict[str, Any] = body
    else:
        if not body:
            return None
        source = answer
    step_ref = str(source.get("step_ref", "") or source.get("step", "") or "").strip()
    comment = str(source.get("comment", "") or source.get("text", "") or "").strip()
    return step_ref, comment


def _revise_allowed(run: Any) -> tuple[bool, str]:
    """Whether this run can take a revision at all.

    A terminal run cannot: there is nothing left to re-run, so a revision would edit a spec that
    will never execute again — which would break the one promise the verb makes, that the recorded
    plan is the plan that runs.
    """
    if getattr(run, "is_terminal", False):
        return (
            False,
            f"run is already {getattr(getattr(run, 'status', None), 'value', 'finished')}",
        )
    return True, ""


def _is_approved(ask: Any, answer: Any) -> bool:
    """Did the human say yes?

    Only an `approval` ask can DENY — a text or form answer is data, not a verdict, and
    treating an empty string as a denial would fail a gate the user actually answered.
    """
    from gideon.automation.workflows.human_input import AskKind

    if ask.kind != AskKind.APPROVAL:
        return True
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, dict):
        return bool(answer.get("approved"))
    return False


def _secret_resolver(key: str) -> str:
    """Resolve `{{secret:KEY}}` from the credential store.

    Injected rather than imported at the binding layer so unit tests never touch real
    credentials, and so the resolution point is a single auditable seam.

    An unknown name returns "" rather than raising: `resolve()` treats an empty secret as
    a resolution failure and reports it with the binding's own error message, which is
    more actionable than a bare `KeyError` from two layers down.
    """
    from gideon.core.config.loader import config_dir
    from gideon.integrations.llm.credentials import CredentialStore

    try:
        cred = CredentialStore(config_dir()).resolve(key)
    except KeyError:
        return ""
    return cred.secret or ""
