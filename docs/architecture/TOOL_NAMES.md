# Tool names on the wire

A registered tool has a canonical name. Model protocol adapters send that name in their
tool schema; a returned call must resolve to the current admitted tool before invocation.
Provider APIs can impose their own name constraints, so successful local registration
is not proof that every provider accepts the same spelling.

## Resolution

Schema construction lives in `runtime/gideon/engine/agents/native/tools.py`. Shared model
adapters live under `runtime/gideon/integrations/llm/`. Native runtime resolution is in
`runtime/gideon/engine/agents/native/runtime.py`, including `_sanitized_tool_key`,
`build_sanitized_index`, and `_resolve_name`.

Resolution prefers an exact canonical or meta-tool name. A non-exact name can consult
the sanitized alias index only when it maps unambiguously to a current real tool.
Colliding aliases and aliases that shadow exact names are not guessed. If two names
collapse to one provider spelling, the returned spelling cannot identify both safely.

History may retain the spelling used in an earlier model call. That does not make a
retired or currently forbidden tool available. Current profile grants, app limits,
provider inventory, approval, and invocation risk still constrain dispatch after name
resolution. Recovering a name is not recovering permission.

## Contributor checks

Choose names compatible with the intended provider protocols and avoid sanitized
collisions. Preserve canonical identity across schema, metadata, result records, and
provider invocation. Surface unknown or ambiguous names as failures instead of calling
a similarly named tool.

`checks/runtime/test_tool_name_wire_fidelity.py` covers the repository's naming contract.
Its existence is not a current full-provider qualification result; exercise the actual
adapter and provider when introducing a new wire behavior.
