"""Validate a transient definition without publishing it to a definition provider."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

async def run_once_def(spec: dict[str, Any]) -> dict[str, Any]:
    from gideon.automation.workflows import service, macros, blocks
    checked = await service.author_def(name=str(spec.get('name') or ''), root=spec.get('root') or {},
        description=str(spec.get('description') or ''), workspace=spec.get('workspace'),
        inputs=spec.get('inputs'), strict=False, save=False, provenance='agent')
    if not checked.get('ok'):
        return checked
    # Use the same native expansion that authoring checked; no provider receives this document.
    definition = blocks.resolve_spec(macros.expand_spec(deepcopy(spec)))
    definition.update(version=1, provenance='agent')
    return {'ok': True, 'definition': definition}
