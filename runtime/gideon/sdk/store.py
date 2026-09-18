"""SDK: the shared-store conformance kit an app's own test suite calls.

A provider-served store that more than one person can write — a team trigger repository, a
team task tracker, a shared memory or knowledge space — owes its consumers four things:
owner scoping, fencing of another contributor's text before it reaches a prompt, a
DECLARED write policy, and an ownership transfer that never leaves a row unowned.
:func:`assert_shared_store_contract` drives all four against a live provider instance.

Why a submodule of its own rather than a clause bolted onto ``gideon.sdk.memory`` /
``knowledge`` / ``prompt`` / ``triggers``: the contract is one contract across all of them,
and four copies of it drift the moment one entity is edited — the exact failure a
conformance kit exists to end. An app imports the kit here and its provider ABC from the
entity facade it already uses.

The kit ADJUDICATES NOTHING. It observes write behaviour and reuses the repo's existing
content sha to do it; divergence detection, the review queue and the proposed merge stay
in ``gideon.operations.durability.conflicts``, which is the one place a "which version
wins" answer is allowed to live.

``trigger_store_binding`` and ``task_provider_binding`` are the bundled bindings for the
two in-tree shared stores; they are also the worked examples for writing one.
"""

from gideon.assurance.testing.store_conformance import (
    FOREIGN_PROMPT_INJECTION,
    WRITE_CONFLICT_CHECKED,
    WRITE_IDEMPOTENT,
    WRITE_LAST_WRITE_WINS,
    WRITE_SEMANTICS,
    SharedStoreBinding,
    SharedStoreContractError,
    assert_shared_store_contract,
    task_provider_binding,
    trigger_store_binding,
)

__all__ = [
    "FOREIGN_PROMPT_INJECTION",
    "SharedStoreBinding",
    "SharedStoreContractError",
    "WRITE_CONFLICT_CHECKED",
    "WRITE_IDEMPOTENT",
    "WRITE_LAST_WRITE_WINS",
    "WRITE_SEMANTICS",
    "assert_shared_store_contract",
    "task_provider_binding",
    "trigger_store_binding",
]
