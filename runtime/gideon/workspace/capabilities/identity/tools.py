"""Native identity operations with session policy and private-source boundaries."""
import asyncio
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from gideon.core.config import config_dir
from gideon.engine import session_restrictions
from gideon.integrations.mcp_core import get_current_session_key
from gideon.integrations.tool_providers.base import ToolProvider, ToolResult
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.fidelity import FidelityStore
from gideon.workspace.capabilities.identity.fidelity_generation import run_evaluation
from gideon.workspace.capabilities.identity.store import ConflictError, StoryStore
from gideon.workspace.capabilities.identity.twin import TwinStore
from gideon.workspace.capabilities.identity.twin_enrichment import propose_enrichment
from gideon.workspace.capabilities.identity.tool_schema import CONTRACTS, definitions


class IdentityToolProvider(ToolProvider):
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home is not None else config_dir()

    @property
    def name(self):
        return "gideon-identity"

    @property
    def display_name(self):
        return "Human Identity"

    async def list_tools(self):
        return definitions()

    async def invoke(self, tool_name, arguments):
        contract = next((tool for tool in definitions() if tool.name == tool_name), None)
        if contract is None:
            return ToolResult(success=False, error="Unknown identity operation")
        session = get_current_session_key()
        if not session.startswith(("dashboard:", "cli:")) or session_restrictions.is_temporary(session):
            return ToolResult(success=False, error="Identity tools require a non-temporary private conversation")
        if CONTRACTS[tool_name][3] and session_restrictions.is_restricted(session):
            return ToolResult(success=False, error="This conversation cannot change persistent identity records")
        problem = next(iter(Draft202012Validator(contract.parameters).iter_errors(arguments)), None)
        if problem:
            return ToolResult(success=False, error="Invalid identity arguments: " + problem.message,
                              recovery_hints=["Use the tool's declared fields and current revision."])
        try:
            if tool_name == "identity_fidelity_run":
                store = FidelityStore(self.home / "capabilities/identity/fidelity.sqlite3")
                result = await run_evaluation(store, **arguments)
                store._sources(result["case_snapshot"]["source_ids"])
            elif tool_name == "identity_twin_enrich":
                store = TwinStore(self.home / "capabilities/identity/twin.sqlite3")
                doc = self._public_document(store, arguments["document_id"])
                if not doc["enabled"]:
                    raise ValueError("Enable the source before proposing questions")
                result = {"status": "proposed", "text": await propose_enrichment(doc["text"]), "document_id": doc["id"]}
            else:
                result = await asyncio.to_thread(self._execute, tool_name, dict(arguments))
            return ToolResult(success=True, output=json.dumps(result, ensure_ascii=False, allow_nan=False),
                              metadata={"operation": tool_name})
        except ConflictError as error:
            return ToolResult(success=False, error=str(error), metadata={"status": "conflict"},
                              recovery_hints=["Reload the current record and reconcile your intended change."])
        except (ValueError, TypeError, KeyError) as error:
            return ToolResult(success=False, error=str(error))
        except Exception:
            return ToolResult(success=False, error="Identity operation unavailable; no completion is claimed",
                              metadata={"status": "unavailable"})

    @staticmethod
    def _public_document(store, identifier):
        doc = next((row for row in store.snapshot()["documents"] if row["id"] == identifier), None)
        if doc is None or doc["private"]:
            raise KeyError("Accessible identity document not found")
        return doc

    def _execute(self, name, arguments):
        directory = self.home / "capabilities/identity"
        if name.startswith("identity_goals_"):
            store = GoalStore(directory / "goals.sqlite3")
            operation = name.removeprefix("identity_goals_")
            result = getattr(store, operation)(**arguments)
            return {"calendar": result} if operation == "calendar" else result
        if name.startswith("identity_fidelity_"):
            store = FidelityStore(directory / "fidelity.sqlite3")
            operation = name.removeprefix("identity_fidelity_")
            result = getattr(store, "record_observation" if operation == "observe" else operation)(**arguments)
            def visible(row):
                try:
                    store._sources(row.get("case_snapshot", row)["source_ids"])
                    return True
                except ValueError:
                    return False
            if isinstance(result, list):
                return [row for row in result if visible(row)]
            if not visible(result):
                raise KeyError("Accessible fidelity record not found")
            return result
        if name.startswith("identity_story_"):
            store = StoryStore(directory / "stories.sqlite3")
            operation = name.removeprefix("identity_story_")
            if "story_id" in arguments:
                identifier = arguments.pop("story_id")
                result = getattr(store, operation)(identifier, **arguments)
            else:
                result = getattr(store, operation)(**arguments)
            return {"deleted": identifier} if operation == "delete" else result
        store = TwinStore(directory / "twin.sqlite3")
        operation = name.removeprefix("identity_twin_")
        if operation in ("save_document", "delete_document") and arguments.get("id"):
            self._public_document(store, arguments["id"])
        result = store.snapshot() if operation == "get" else getattr(store, "compose" if operation == "context" else operation)(**arguments)
        if "documents" in result:
            result = {**result, "documents": [doc for doc in result["documents"] if not doc["private"]]}
        return result


def create_provider(config=None):
    if config:
        raise ValueError("Identity tools use the runtime home; custom settings are not supported")
    return IdentityToolProvider()
