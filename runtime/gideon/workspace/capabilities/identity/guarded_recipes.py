"""Pinned recipes executed through the actual session tool and approval engine."""

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from jsonschema import Draft202012Validator

from gideon.core.config import config_dir
from gideon.engine import session_restrictions
from gideon.engine.agents.native.runtime import NativeAgentRuntime, _ToolInventory
from gideon.integrations.llm.events import (
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from gideon.integrations.tool_providers.base import ToolResult
from gideon.workspace.capabilities.identity.recipe_runtime_platform import (
    require_runtime,
)
from gideon.workspace.capabilities.identity.recipes import RecipeStore
from gideon.workspace.capabilities.identity.store import ConflictError

_ACTIVE = {}


def fingerprint(definition):
    return hashlib.sha256(
        json.dumps(asdict(definition), sort_keys=True, default=str).encode()
    ).hexdigest()


class GuardedRecipes:
    def __init__(self, home, state):
        self.home, self.state = Path(home).resolve(), state
        self.store = RecipeStore(
            self.home / "capabilities/identity/recipes.sqlite3", guarded=True
        )

    def _scope(self, session_key, *, control=False):
        if self.home != config_dir().resolve():
            raise ValueError("Recipe session belongs to a different active home")
        if (
            not isinstance(session_key, str)
            or not session_key.startswith(("dashboard:", "cli:"))
            or (not control and session_restrictions.is_restricted(session_key))
        ):
            raise ValueError("Guarded recipes require a persistent private session")

    def _runtime(self, session_key, *, admission=True):
        self._scope(session_key)
        runtime = self.state.sessions.get_provider(session_key) if self.state else None
        if (
            not isinstance(runtime, NativeAgentRuntime)
            or runtime._session_key != session_key
        ):
            raise ValueError("An existing native session is required")
        if admission:
            require_runtime(runtime)
            if runtime._dry_run:
                raise ValueError(
                    "Observe-only sessions cannot dispatch guarded actions"
                )
        return runtime

    async def _definitions(self, session_key):
        runtime = self._runtime(session_key)
        inventory = await _ToolInventory.discover(
            runtime._tool_providers, runtime._unattended
        )
        return {
            row.name: row
            for row in inventory.definitions
            if row.name in runtime._tool_index
            and not row.name.startswith(("identity_recipe_", "identity_guarded_"))
        }

    async def catalog(self, session_key):
        definitions = await self._definitions(session_key)
        return {
            "session_key": session_key,
            "tools": [
                {
                    "name": row.name,
                    "provider": row.provider,
                    "description": row.description,
                    "parameters": row.parameters,
                    "requires_approval": row.requires_approval,
                    "fingerprint": fingerprint(row),
                }
                for row in definitions.values()
            ],
        }

    async def save(self, *, session_key, **fields):
        definitions = await self._definitions(session_key)
        return RecipeStore(self.store.path, allowed_tools=definitions).save(**fields)

    async def restore(self, *, session_key, **fields):
        definitions = await self._definitions(session_key)
        return RecipeStore(self.store.path, allowed_tools=definitions).restore(**fields)

    async def begin(self, *, session_key, **fields):
        definitions = await self._definitions(session_key)
        recipe = self.store.get(fields["recipe_id"])
        if any(step["tool"] not in definitions for step in recipe["steps"]):
            raise ValueError("Recipe contains a currently unavailable tool")
        run = self.store.begin(**fields)
        with sqlite3.connect(self.store.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self.store._get(db, "runs", run["id"])
            if run.get("session_key") and run["session_key"] != session_key:
                raise ConflictError("Run belongs to another session")
            if "session_key" not in run:
                run.update(
                    session_key=session_key,
                    tool_fingerprints={
                        step["tool"]: fingerprint(definitions[step["tool"]])
                        for step in recipe["steps"]
                    },
                )
                db.execute(
                    "UPDATE runs SET body=? WHERE id=?", (json.dumps(run), run["id"])
                )
        return run

    def get_run(self, id):
        run = self.store.get_run(id)
        if not run.get("session_key"):
            raise ValueError("Run is not bound to a guarded session")
        self._scope(run["session_key"])
        return run

    def _update(self, id, **fields):
        with sqlite3.connect(self.store.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self.store._get(db, "runs", id)
            run.update(fields)
            db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(run), id))
        return run

    async def _definition(self, run, tool):
        definitions = await self._definitions(run["session_key"])
        definition = definitions.get(tool)
        if definition is None or fingerprint(definition) != run[
            "tool_fingerprints"
        ].get(tool):
            raise ValueError(
                "Tool permissions or schema changed; start a freshly reviewed run"
            )
        current = self.store.get(run["recipe_id"])
        if not current["enabled"] or current["revision"] != run["recipe_revision"]:
            raise ValueError("Recipe authority was revoked")
        return definition

    async def advance(self, *, run_id, expected_index):
        run = self.get_run(run_id)
        if type(expected_index) is not int or expected_index < 0:
            raise ValueError("Expected nonnegative step index")
        if (
            expected_index < run["next_index"]
            or run["status"] not in ("ready", "deferred")
            or (self.home, run_id) in _ACTIVE
        ):
            return run
        if expected_index != run["next_index"]:
            raise ConflictError("Run step changed")
        runtime = self._runtime(run["session_key"])
        entry = self.state.sessions._sessions.get(run["session_key"])
        if entry.semaphore.locked():
            return self._update(
                run_id,
                status="deferred",
                defer_reason="Existing session is busy; dispatch again when idle",
            )
        await entry.semaphore.acquire()
        try:
            await self._definition(
                run, run["recipe_snapshot"]["steps"][expected_index]["tool"]
            )
            if self.state.sessions.get_provider(run["session_key"]) is not runtime:
                raise ValueError("Session owner changed")
            self._update(run_id, status="ready", permission=None)
            adapter = _RuntimeStep(self, run_id, runtime)
            task = asyncio.create_task(
                self._execute(run_id, expected_index, adapter, entry)
            )
            _ACTIVE[(self.home, run_id)] = (task, adapter)
        except BaseException:
            entry.semaphore.release()
            raise
        await asyncio.sleep(0)
        return self.get_run(run_id)

    async def _execute(self, run_id, index, adapter, entry):
        try:
            run = self.get_run(run_id)
            store = RecipeStore(
                self.store.path, allowed_tools=run["tool_fingerprints"], guarded=True
            )
            await store.advance(run_id, index, adapter)
        finally:
            entry.semaphore.release()
            _ACTIVE.pop((self.home, run_id), None)

    async def decide(self, *, run_id, request_id, decision, session_key):
        if decision not in ("approve", "reject"):
            raise ValueError("Choose approve or reject")
        run = self.get_run(run_id)
        if session_key != run["session_key"]:
            raise ConflictError("Approval belongs to another session")
        permission = run.get("permission")
        active = _ACTIVE.get((self.home, run_id))
        if not permission or permission["request_id"] != request_id or not active:
            raise ConflictError("Approval is no longer active in this process")
        runtime = self._runtime(run["session_key"], admission=False)
        if runtime is not active[1].runtime:
            raise ConflictError("Session owner changed")
        if decision == "approve":
            try:
                await self._definition(run, permission["tool"])
                call = AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    title=permission["tool"],
                    tool_input=permission["arguments"],
                    tool_call_id=request_id,
                )
                reason = await runtime._policy_refusal(
                    call, permission["tool"], permission["arguments"]
                )
                if reason:
                    raise ValueError(reason)
            except Exception:
                await runtime.reject_tool(request_id)
                raise
        await (
            runtime.approve_tool(request_id)
            if decision == "approve"
            else runtime.reject_tool(request_id)
        )
        await asyncio.sleep(0)
        return self.get_run(run_id)

    async def cancel(self, *, run_id):
        run = self.store.get_run(run_id)
        self._scope(run.get("session_key"), control=True)
        with sqlite3.connect(self.store.path) as db:
            if run["status"] in ("deferred", "waiting_approval"):
                run["status"] = "cancelled"
                db.execute(
                    "UPDATE runs SET body=? WHERE id=?", (json.dumps(run), run_id)
                )
        run = self.store.cancel(run_id)
        active = _ACTIVE.get((self.home, run_id))
        if active:
            permission = run.get("permission")
            if permission:
                await active[1].runtime.reject_tool(permission["request_id"])
            else:
                active[1].runtime._cancel.request(reason="user")
        if session_restrictions.is_restricted(run["session_key"]):
            return {
                key: run[key] for key in ("id", "status", "session_key", "next_index")
            } | {"steps": [], "permission": None}
        return run


class _RuntimeStep:
    def __init__(self, service, run_id, runtime):
        self.service, self.run_id, self.runtime = service, run_id, runtime

    async def invoke(self, tool_name, arguments):
        run = self.service.get_run(self.run_id)
        definition = await self.service._definition(run, tool_name)
        Draft202012Validator(definition.parameters).validate(arguments)
        if self.runtime is not self.service._runtime(run["session_key"]):
            raise ValueError("Session owner changed")
        call = AgentEvent(
            kind=EVENT_TOOL_CALL,
            title=tool_name,
            tool_input=arguments,
            tool_call_id="recipe-" + self.run_id + "-" + str(run["next_index"]),
        )
        self.runtime._messages.append(self.runtime._assistant_msg("", [call]))
        result = ToolResult(
            success=False, error="Native tool engine returned no result"
        )
        async for event in self.runtime._execute_tool_batch([call]):
            if event.kind == EVENT_PERMISSION_REQUEST:
                self.service._update(
                    self.run_id,
                    status="waiting_approval",
                    permission={
                        "request_id": event.request_id,
                        "tool": tool_name,
                        "arguments": arguments,
                        "risk": event.risk_level,
                    },
                )
            elif event.kind == EVENT_TOOL_RESULT:
                self.service._update(self.run_id, permission=None)
                failed = (
                    event.tool_output.startswith("Error:")
                    or event.tool_meta.get("ok") is False
                )
                try:
                    output = json.loads(event.tool_output)
                except (ValueError, TypeError):
                    output = {"text": event.tool_output}
                result = ToolResult(
                    success=not failed,
                    output=json.dumps(output),
                    error=event.tool_output if failed else "",
                )
        return result
