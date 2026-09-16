"""The provider-registration invariant: no execution without a policy check (§7 item 6 / R3 am.5).

The plan asks for "a test asserting no execution without a policy check". Measured before writing
it — and the honest result is that the invariant HOLDS today, so this file exists to keep it holding
rather than to fix a defect:

    hooks._run_provider (lifecycle)                  incident_active   + enforce_action
    gateway._fire_store_trigger (clock/file/event)   incident_active   + enforce_action (AG-12)
    event_triggers.execute_event_action              incident_active   + enforce_action
    handlers/triggers._dispatch_store_action (manual) manual_refusal
    handlers/hooks                                   -- reads metadata only, never executes

The `enforce_action` column is AG-12's addition, and it is a SECOND invariant over the same sites:
`POLICY_CHECKS` below is satisfied by ANY one check, which is right for its question ("does this
site consult policy at all?") but blind to a specific control going missing at a specific seam.
That is precisely what happened — the gateway seam kept the kill switch and gained the rung ladder
while the denylist §1.2 promises at all three seams was 0 there — so `DENYLIST_SEAMS` names that
one control and requires it everywhere it was declared.

That last line is the reason this is a source-level test rather than a behavioural one. The
property is *structural*: "every site that reaches a provider passes a policy check first". A
behavioural test can only prove the sites it knows about, so it cannot fail when someone adds a
FIFTH execution site — the exact regression this invariant is written against. The failure mode
prevented is not "the check is wrong", it is "a new call path skipped the check entirely".

🔴 What is deliberately NOT asserted: the plan also describes providers each *declaring* their
enforcement chokepoint as an attribute. Measured: none of the 16 shipped providers declares one.
That is left alone rather than half-built, because an attribute nothing reads is exactly the
inert-control defect this program keeps finding — enforcement lives at the call sites, and this
test guards the call sites. Recorded so the next author knows it was a decision, not an oversight.
"""

from __future__ import annotations

import inspect

import pytest

EXECUTION_SITES: tuple[tuple[str, str], ...] = (
    ("gideon.engine.hooks", "the lifecycle-hook fire path"),
    ("gideon.engine.trigger_dispatch", "the clock/file trigger fire path"),
    ("gideon.automation.event_triggers", "the data-event fire path"),
    ("gideon.interfaces.dashboard.handlers.triggers", "the manual Run path"),
    ("gideon.cognition.proposals_contract", "the inbox proposal apply path"),
    ("gideon.interfaces.dashboard.tile_refresh", "the chatless tile-refresh path"),
    (
        "gideon.interfaces.dashboard.handlers.research_reports",
        "the manual report Run path",
    ),
    ("gideon.cognition.proactive.autoexec", "the triage auto-execution path"),
)


REVERSAL_SITE = "gideon.security.guardrails.ladder"

POLICY_CHECKS: tuple[str, ...] = (
    "enforce_action",
    "incident_active",
    "manual_refusal",
    "capability_allows",
    "unfenced_actions",
    "requested_capabilities",
    "path_allowed",
    "firepath",
)


DENYLIST_SEAMS: tuple[tuple[str, str], ...] = (
    ("gideon.engine.hooks", "script hooks"),
    ("gideon.engine.trigger_dispatch", "clock / file / webhook / chained triggers"),
    ("gideon.automation.event_triggers", "memory-event triggers"),
    ("gideon.interfaces.dashboard.tile_refresh", "TTL dashboard tiles"),
    ("gideon.cognition.proactive.autoexec", "trivial-tier triage auto-execution"),
)

MANUAL_SEAM = "gideon.interfaces.dashboard.handlers.triggers"

USER_CLICKED_SEAMS: tuple[str, ...] = (
    MANUAL_SEAM,
    "gideon.cognition.proposals_contract",
    "gideon.interfaces.dashboard.handlers.research_reports",
)


def _source(module_name: str) -> str:
    import importlib

    return inspect.getsource(importlib.import_module(module_name))


def _enforce_action_calls(module_name: str) -> list:
    """Every `enforce_action(...)` CALL node in a module, found via AST.

    AST rather than a substring search because the property under test is a property of the
    CALL — that it passes `session_key=` — and the three seams spell the call across one, four
    and five lines. A regex that happened to match today's formatting would stop seeing the call
    the moment someone reflowed it, and a rail that matches nothing reads exactly like a pass.
    """
    import ast

    tree = ast.parse(_source(module_name))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "enforce_action"
    ]


@pytest.mark.parametrize("module_name,label", DENYLIST_SEAMS)
def test_every_unattended_seam_enforces_the_denylist(module_name, label):
    """🔴 THE §1.2 INVARIANT. The denylist's whole promise is that "an app-contributed provider
    inherits the denylist without knowing it exists" — which holds only if EVERY unattended
    dispatch seam calls it. Two of three is the same shape as none, because an author only needs
    to reach the unguarded one."""
    calls = _enforce_action_calls(module_name)
    assert calls, (
        f"the {label} seam ({module_name}) dispatches an action provider without calling "
        "guardrails.denylist.enforce_action. §1.2 requires it at all three dispatch seams."
    )


@pytest.mark.parametrize("module_name,label", DENYLIST_SEAMS)
def test_every_seam_threads_the_session_key(module_name, label):
    """The call SHAPE, not just its presence. `session_key=""` classifies as ATTENDED, so the
    run's `SafetyProfile` (its `denylist_extra` globs and its `path_allowlist` confinement) is
    skipped entirely — the PHF-8 defect. A seam that calls `enforce_action` without threading a
    session key enforces only the built-ins, which is a quieter version of not enforcing.
    """
    for call in _enforce_action_calls(module_name):
        assert any(kw.arg == "session_key" for kw in call.keywords), (
            f"the {label} seam ({module_name}) calls enforce_action without session_key=; "
            "the SafetyProfile layer is silently skipped."
        )


def test_the_denylist_seam_list_covers_every_unattended_execution_site():
    """🔴 The rail that makes `DENYLIST_SEAMS` trustworthy, and the one that catches a FOURTH seam.

    Derived from `EXECUTION_SITES` (itself verified against the tree by
    `test_the_site_list_is_not_STALE`) minus the documented manual exemption, so a new
    provider-execution path cannot be added without either carrying the denylist or being
    argued into an exemption here.
    """
    unattended = {m for m, _ in EXECUTION_SITES} - set(USER_CLICKED_SEAMS)
    declared = {m for m, _ in DENYLIST_SEAMS}
    assert unattended == declared, (
        "the denylist seam list drifted from the execution-site list: "
        f"missing {sorted(unattended - declared)}, stale {sorted(declared - unattended)}"
    )


@pytest.mark.parametrize("module_name", USER_CLICKED_SEAMS)
def test_the_manual_run_path_is_the_documented_denylist_exemption(module_name):
    """The exemption asserted rather than assumed: it must still be gated by `manual_refusal`.

    If that check ever disappears, this path becomes an unattended-equivalent execution site with
    no policy gate at all — so the exemption is only valid while its own gate is present.
    """
    src = _source(module_name)
    assert "manual_refusal" in src, (
        "the manual Run path is exempt from the denylist because a human initiates it and "
        "`manual_refusal` gates it. That gate is gone, so the exemption no longer holds."
    )


@pytest.mark.parametrize("module_name,label", EXECUTION_SITES)
def test_every_execution_site_has_a_policy_check(module_name, label):
    """🔴 THE INVARIANT. A new provider-execution path that forgot its policy check is how an
    automation surface quietly stops being fenced — the defect S117 found for the kill switch, where
    three unattended entry points existed and only one checked the flag."""
    src = _source(module_name)
    found = [c for c in POLICY_CHECKS if c in src]
    assert found, (
        f"{label} ({module_name}) executes an action provider with no policy check. "
        f"Expected one of: {', '.join(POLICY_CHECKS)}"
    )


def test_the_catalog_site_does_not_execute():
    """The one `get_action_provider` caller exempt from the invariant, and why.

    `dashboard/handlers/hooks.py` resolves every provider to read `display_name`/`supports_blocking`
    for the catalog. If it ever gained an `execute` call it would become an unfenced execution path,
    so the exemption is asserted rather than assumed.
    """
    src = _source("gideon.interfaces.dashboard.handlers.hooks")
    assert (
        "get_action_provider(" in src
    ), "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "the catalog site must never execute a provider"


def test_the_reversal_site_undoes_and_never_executes():
    """The second exemption from the execution invariant, and the properties that earn it.

    `guardrails.ladder` resolves a provider so a user can take an `auto_with_undo` action BACK.
    That is the opposite direction from every site in `EXECUTION_SITES`, so the kill-switch check
    they share would be wrong here — but "it's different" is not an exemption, so the difference
    is asserted: it must never execute, and it must resolve its provider through the declaration
    (`reversal_kinds`) rather than accept whatever name a caller supplies.
    """
    src = _source(REVERSAL_SITE)
    assert (
        "get_action_provider(" in src
    ), "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "the reversal site must never execute a provider"
    assert ".reverse(" in src, "the reversal site must reach the provider's own undo"
    assert (
        "reversal_kinds" in src
    ), "resolution must be bounded by what the provider claims"


def test_the_would_execute_preview_site_only_reads_the_declaration():
    """The third exemption, and the properties that earn it (PLATFORM-RESILIENCE §3.3 — PR2-7).

    `dashboard/handlers/doctor.py`'s would-execute simulator resolves a provider to read ONE
    declaration — `supports_dry_run` — because that is the T9 honesty rule: only the spawn-based
    LLM providers have a real observe mode, and a panel that labelled a deterministic provider's
    description "observe-mode result" would promise a safety property the provider does not have.

    The kill-switch check every `EXECUTION_SITES` entry shares would be wrong here, because this
    site is not an entry point at all: the dry fire it renders returns before AUTOMATION-SUBSTRATE
    consults a runner. "It's different" is not an exemption, so the difference is asserted —
    it must never execute, and it must never dispatch a fire with a runner attached.
    """
    src = _source("gideon.interfaces.dashboard.handlers.doctor")
    assert (
        "get_action_provider(" in src
    ), "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "the preview site must never execute a provider"
    assert (
        "supports_dry_run" in src
    ), "the only reason to resolve here is the T9 declaration"
    assert "runner=None" in src, "the dry fire must be dispatched with no runner"


def test_the_site_list_is_not_STALE():
    """🔴 The test that makes the list above trustworthy.

    A hardcoded list of call sites rots the moment someone adds one — and a rotted list reads as
    "all sites are checked" while silently covering fewer. So the list is verified against the tree:
    every module that calls `get_action_provider(` must be either an execution site or the
    documented catalog exemption.
    """
    import pathlib
    import re

    root = pathlib.Path(inspect.getfile(__import__("gideon"))).parent
    callers: set[str] = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"[^f]get_action_provider\(", text):
            rel = path.relative_to(root).with_suffix("")
            callers.add("gideon." + str(rel).replace("/", "."))

    known = {m for m, _ in EXECUTION_SITES} | {
        REVERSAL_SITE,
        "gideon.interfaces.dashboard.handlers.hooks",
        "gideon.interfaces.dashboard.handlers.doctor",
        "gideon.automation.triggers.tools",
        "gideon.integrations.action_providers.selfqa_watch_provider",
        "gideon.integrations.action_providers.registry",
        "gideon.integrations.action_providers",
    }
    unaccounted = callers - known
    assert not unaccounted, (
        "these modules reach an action provider but are not in EXECUTION_SITES: "
        f"{sorted(unaccounted)}. Add them (with a policy check) or document the exemption."
    )


def test_the_delegating_provider_only_hands_off_to_a_frozen_name():
    """The properties that earn `selfqa_watch_provider`'s exemption (SV-11).

    It is the only resolve in the known set that happens inside a PROVIDER. A provider cannot
    be an entry point: something already gated -- here the gateway's `file`-trigger fire path,
    which carries the denylist and the kill switch -- has to dispatch it first, so the policy
    check every `EXECUTION_SITES` entry shares has already run upstream by the time this code
    executes. "It's downstream" is not an exemption on its own, so the two properties that make
    it safe are asserted: it resolves a FROZEN literal name (never one a caller supplies, the
    hole the reversal-site exemption also closes), and it delegates the start rather than
    re-implementing it, so the dedupe and origin stamping stay single-writer.

    If this provider ever resolves a name off its `action_config`, it becomes a
    caller-steerable dispatcher and must argue its way into `EXECUTION_SITES` with a real
    policy gate instead.
    """
    import re

    src = _source("gideon.integrations.action_providers.selfqa_watch_provider")
    calls = re.findall(r"get_action_provider\(([^)]*)\)", src)
    assert calls, "the exemption is stale if the provider no longer delegates"
    assert all(c.strip() in {'"run-workflow"', "'run-workflow'"} for c in calls), (
        "every resolve must be the frozen `run-workflow` literal; a name read from "
        f"action_config would make this a caller-steerable dispatcher. found: {calls}"
    )
    assert "action_config" not in "".join(
        calls
    ), "the delegate name must not come from the caller"


def test_the_create_time_provider_check_only_asks_existence():
    """The properties that earn `triggers.tools`'s exemption (#779).

    `create` refuses an unregistered action provider BEFORE the row exists — the
    green-row-silent-failure-loop this repo's BA-7 rule exists to prevent. That takes one
    registry question, "is this name registered?", and nothing more: the resolved provider
    is never bound to a name, never handed to a runner, and never executed. "It's different"
    is not an exemption, so the difference is asserted here — if `create` ever starts USING
    the provider it resolves, this test fails and the module must argue its way into
    `EXECUTION_SITES` with a real policy gate instead.
    """
    import ast

    src = _source("gideon.automation.triggers.tools")
    tree = ast.parse(src)
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_action_provider"
    ]
    assert calls, "the exemption is stale if create no longer resolves a provider"
    for call in calls:
        parent = parents[call]
        assert isinstance(parent, ast.Compare) and parent.left is call
        assert len(parent.ops) == 1 and isinstance(parent.ops[0], (ast.Is, ast.IsNot))
        assert len(parent.comparators) == 1
        assert (
            isinstance(parent.comparators[0], ast.Constant)
            and parent.comparators[0].value is None
        )
    assert "_ensure_default_providers_registered()" in src, (
        "the existence check must register the built-ins first, or startup order would "
        "make it refuse real providers"
    )


def test_no_shipped_provider_declares_a_chokepoint_attribute():
    """Pins the measured state the docstring records, so the next author sees it as a decision.

    If someone later adds a `chokepoint` attribute to providers, this test fails and they must
    either wire something that READS it or drop it — which is the point. An attribute nothing reads
    is the inert-control defect, and a security-shaped one is worse than none.
    """
    from gideon.integrations.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
        list_action_providers,
    )

    _ensure_default_providers_registered()
    declaring = [
        name
        for name in list_action_providers()
        if any(
            hasattr(get_action_provider(name), attr)
            for attr in ("chokepoint", "requires_policy_check")
        )
    ]
    assert not declaring, (
        f"{declaring} declare a chokepoint attribute. Either wire a consumer that ENFORCES it, or "
        "remove it — a declared-but-unread security attribute is worse than none."
    )
