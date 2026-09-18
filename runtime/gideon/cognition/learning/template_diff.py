"""Applying an accepted ``template_diff`` — the refiner's typed ops become a version.

The refiner FILES typed ops and never applies them; accepting is the human installing,
so THIS is where the diff lands (§3.1 "Accept → new template VERSION"). The ops ride
the change manifest's ``targeted_fix`` (the same field the inbox reads to stamp a risk
tier); they are applied to a deep copy via ``mutations.apply_batch`` and, only if the
batch is clean, saved through the writable def provider — which appends an immutable
version snapshot and pins it (``versions.record_version`` inside ``save_def``).

It lives here rather than in the dashboard handler it was written in because it is one
proposal kind's INSTALLER: :mod:`gideon.cognition.learning.installers` resolves it for
every surface that accepts a proposal, so a diff accepted from an agent tool applies
the same way one accepted from the inbox does. It used to run in the handler AFTER the
decision was recorded, which made a failed apply an accepted proposal that changed
nothing.
"""

from __future__ import annotations

from typing import Any


class TemplateDiffError(Exception):
    """An accepted diff that cannot be applied — so the accept is refused."""


async def apply_accepted_template_diff(prop) -> dict:
    """Apply *prop*'s typed ops to its target and save it as a NEW version.

    Returns ``{"applied": True, "version": n}``; raises :class:`TemplateDiffError` with
    the reason when there is nothing to apply, no target, or the batch does not hold.
    """
    manifest = getattr(prop, "change_manifest", None)
    ops_raw = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
    name = str(getattr(prop, "target", "") or "")
    if not name or not isinstance(ops_raw, list) or not ops_raw:
        raise TemplateDiffError("no typed ops on the proposal")

    from gideon.automation.workflows import defs as defs_mod
    from gideon.automation.workflows import mutations
    from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider

    spec = None
    for pname in defs_mod.list_providers():
        provider = defs_mod.get_provider(pname)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            continue
        if found is not None:
            spec = (
                found
                if isinstance(found, dict)
                else getattr(found, "to_dict", lambda: None)()
            )
            break
    if not isinstance(spec, dict):
        raise TemplateDiffError(f"no definition named {name!r}")

    try:
        ops = [mutations.Op.from_dict(o) for o in ops_raw if isinstance(o, dict)]
    except ValueError as exc:
        raise TemplateDiffError(f"unparseable op: {exc}") from exc
    candidate, issues = mutations.apply_batch(ops, spec, {})
    if issues:
        raise TemplateDiffError("; ".join(i.code for i in issues))

    writable = [
        p
        for p in (defs_mod.get_provider(n) for n in defs_mod.list_providers())
        if p is not None and not p.readonly
    ]
    target_provider = writable[0] if writable else NativeWorkflowDefProvider()
    saved = await target_provider.save_def(
        **candidate, _version_source="refiner", _version_ops=ops_raw
    )
    return {"applied": True, "version": int(getattr(saved, "version", 0) or 0)}


class _Record:
    """The attribute view :func:`apply_accepted_template_diff` reads, over a dict.

    The installer contract hands a record DICT; the applier was written against the
    stored ``Proposal``. One adapter rather than two readers of the same two fields.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self.change_manifest = data.get("change_manifest")
        self.target = data.get("target", "")


def install_accepted_template_diff(data: dict[str, Any]) -> dict:
    """The SYNC installer ``proposals.accept`` runs, driving the async apply.

    The coroutine is driven the way :func:`packs.prompt_cards._save_def_sync` drives
    its own — on a private loop in a worker thread when a loop is already running,
    rather than deadlocking the request handler that is awaiting the accept.
    """
    import asyncio

    async def _go() -> dict:
        return await apply_accepted_template_diff(_Record(data))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_go())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _go()).result()
