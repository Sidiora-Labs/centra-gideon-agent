"""The SEL audit step for desktop computer use (DESKTOP-COMPUTER-USE §3 floor 5, `DCU-2`).

**This module has no opinion.** The plan places it as step 5 of the dispatch chain —
``gate.require_computer_use`` — and is emphatic about what that step is: *"SEL audit
(records, does not decide)"* and *"Every action is SEL-audited (``gate.require_computer_use``
records, doesn't decide)"*. The decisions happened already, upstream: the keystone
(:func:`gideon.integrations.computer_use.enable_state.is_enabled`, step 1) and the target policy
(``policy.check_app`` / ``policy.check_input_target``, steps 2 and 4). By the time control
reaches here the verdict exists; the only remaining obligation is that it is *written down*.

**The name is the plan's, and the veto is deliberately absent.** ``require_*`` reads like a
gate everywhere else in this codebase, and that is exactly the trap: the next reader will
want to add an ``if not allowed: raise`` here, because the name invites it. Do not. The
refusal a caller needs is :func:`enable_state.disabled_error` (or the policy's), raised by
the layer that made the call; duplicating it here would give one attempt two refusal sites
that can drift, and would make the audit step able to block an operation the policy allowed.
The signature returns ``None`` and the body raises nothing, on any input.

**Why "never decides" implies "never fails".** An audit step that can raise is a
decision-maker by accident: a full disk, a read-only home or a corrupt HMAC key file would
turn "record this" into "refuse this", and a capability the operator armed would collapse for
a reason that has nothing to do with safety. So every failure path here is swallowed —
:func:`require_computer_use` fails **open**.

**Why a swallowed failure is still loud.** A silently total swallow is indistinguishable from
a module that never ran, which would make the atom's clause ("every attempt, allowed or
refused, produces a SEL record") unfalsifiable in production. So a dropped record emits
``logger.warning`` naming the tool and the outcome that did NOT reach the log. WARNING is a
deliberate step up from :func:`enable_state.ensure_computer_use_boot`'s ``logger.debug``:
that record is once-per-run posture evidence, whereas this one is per-attempt, so a
systematically broken audit here is a *silent, ongoing* hole in the security record and must
be visible at the level an operator actually runs.

**What goes in the record, and why ``metadata`` carries no free text.**
:func:`gideon.security.sel.redact_event` exists because *"the log stores a truncated summary of
real tool arguments, so a record can carry a secret a user pasted into a command"* — but two
properties of it matter here. First, it runs only on the way **out** (the forward callback and
the audit read surface); :meth:`SecurityEventLog.log` writes ``asdict(event)`` to disk
unredacted, so anything placed in a field is on disk in the clear regardless. Second, it
delegates to :func:`gideon.security.security.redact`, which recognises *credential*-shaped
strings — not personal data. A computer-use attempt's natural payload is the worst possible
fit for both: a window title ("Bank of America — Checking"), a field label, or the text about
to be typed is personal data that is not credential-shaped, so it would pass ``redact_event``
untouched and sit in the audit log forever. Hence :data:`_safe_metadata`: string and container
values are replaced by a type+length shape summary, so a caller cannot leak user text into the
audit log through this module even by mistake. Keys survive (they are developer-authored
literals) and so do plain scalars, which is all the audit signal an attempt needs — *which*
tool, on *which* app, with *what* verdict. The two fields that do carry prose, ``resources``
and ``error``, take the target app and the refusal's stable code/reason (never tool
arguments), truncated at :data:`gideon.security.sel._MAX_ARG_LEN` like every other SEL writer.
"""

import logging
import uuid
from datetime import datetime, timezone

from gideon.security.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)

SEL_EVENT_TYPE = "computer_use"

SEL_TOOL_KIND = "computer_use"

_DEFAULT_SOURCE = "background"

_SHAPE_KEY = "metadata_shape"

_MAX_LEN = 500


def _shape(value: object) -> str:
    """A leak-proof summary of one value: its type, and its length when it has one."""
    try:
        return f"<{type(value).__name__} len={len(value)}>"  # type: ignore[arg-type]
    except Exception:
        return f"<{type(value).__name__}>"


def _safe_metadata(metadata: object) -> dict:
    """Reduce caller ``metadata`` to values that structurally cannot carry user text.

    Scalars (``bool``/``int``/``float``/``None``) survive verbatim — a count, a flag or an
    element index is exactly the audit signal an attempt needs and cannot be a window title.
    Every other value, including any string and any nested container, is replaced *wholesale*
    by :func:`_shape`. Replacing wholesale rather than walking is the point: there is no depth
    at which a string can survive, so no recursion is needed and none can be got wrong.

    A non-dict ``metadata`` becomes a one-key dict recording its shape, so a caller bug is
    visible in the row itself instead of vanishing.
    """
    if not isinstance(metadata, dict):
        if metadata is None:
            return {}
        return {_SHAPE_KEY: _shape(metadata)}

    def _keep(value: object) -> object:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _shape(value)

    return {str(key): _keep(value) for key, value in metadata.items()}


def require_computer_use(
    *,
    tool: str,
    app: str = "",
    outcome: str,
    caller_identity: str = "",
    agent: str = "gideon",
    source: str = _DEFAULT_SOURCE,
    error: str = "",
    metadata: dict | None = None,
) -> None:
    """Record one computer-use attempt. RECORDS; never decides, never raises.

    The name is the plan's (§3 step 5) even though ``require_*`` reads like a gate — see this
    module's docstring. There is deliberately **no veto**: an ``if`` here that refused
    anything would make the audit step a second decision site behind the keystone and the
    policy, so callers pass the verdict they already reached and this function writes it down.

    ``outcome`` is passed through **verbatim, unvalidated**, even when it is outside
    :class:`gideon.security.sel.SecurityEvent`'s vocabulary (``approved``/``rejected``/``denied``/
    ``completed``/``failed``). Coercing an unrecognised outcome to a known one would record a
    *different verdict than the one that happened*, which corrupts the audit far worse than an
    odd string does; and rejecting it would be a decision. A refused attempt is
    ``outcome="denied"`` with the refusal's stable code in ``error`` (e.g.
    :data:`enable_state.ERR_DISABLED`), so allowed and refused rows are one query apart.

    Returns ``None`` on every input, including a failed SEL write — see the module docstring
    on failing open and on why the drop is logged at WARNING.
    """
    try:
        target = str(app)
        SecurityEventLog().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type=SEL_EVENT_TYPE,
                caller_identity=str(caller_identity),
                agent=str(agent),
                source=str(source) or _DEFAULT_SOURCE,
                operation=str(tool),
                tool_kind=SEL_TOOL_KIND,
                outcome=str(outcome),
                resources=f"app={target}"[:_MAX_LEN] if target else "",
                error=str(error)[:_MAX_LEN],
                metadata=_safe_metadata(metadata),
            )
        )
    except Exception:
        logger.warning(
            "computer_use SEL audit DROPPED — attempt is NOT in the audit log "
            "(tool=%r outcome=%r app=%r)",
            tool,
            outcome,
            app,
            exc_info=True,
        )
