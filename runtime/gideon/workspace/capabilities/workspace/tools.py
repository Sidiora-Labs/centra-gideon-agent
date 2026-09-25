"""Workspace tool consumers over the same records and process owner as the console."""

import json

from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .processes import get_registry
from .store import SnapshotStore


class WorkspaceToolProvider(ToolProvider):
    name = "workspace-tools"
    display_name = "Gideon Workspace"

    async def list_tools(self):
        definitions = []
        for name, description, fields, required, write in [
            (
                "workspace_storage_diagnosis",
                "Read bounded attributed storage for Gideon-owned state and canonical project roots.",
                {"project_id": {"type": "string"}},
                [],
                False,
            ),
            (
                "workspace_provider_terminal_profiles",
                "Read configured interactive provider engine availability.",
                {},
                [],
                False,
            ),
            (
                "workspace_external_terminals",
                "Read existing native iTerm pane metadata without taking ownership.",
                {},
                [],
                False,
            ),
            (
                "workspace_external_terminal_screen",
                "Read the visible text of one existing native iTerm pane.",
                {"id": {"type": "string"}},
                ["id"],
                False,
            ),
            (
                "workspace_desktops",
                "List isolated desktop lifecycle records.",
                {},
                [],
                False,
            ),
            (
                "workspace_desktop_get",
                "Read one isolated desktop session.",
                {"id": {"type": "string"}},
                ["id"],
                False,
            ),
            (
                "workspace_desktop_start",
                "Start an approved isolated Linux desktop and shell for a canonical project.",
                {
                    "project_id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                ["project_id", "request_id", "width", "height"],
                True,
            ),
            (
                "workspace_desktop_stop",
                "Stop only the owned isolated desktop session.",
                {"id": {"type": "string"}, "revision": {"type": "integer"}},
                ["id", "revision"],
                True,
            ),
            (
                "workspace_git_inspect",
                "Inspect a canonical project repository and initialized submodules.",
                {"project_id": {"type": "string"}},
                ["project_id"],
                False,
            ),
            (
                "workspace_git_history",
                "Read bounded durable project Git operation receipts.",
                {"project_id": {"type": "string"}},
                ["project_id"],
                False,
            ),
            (
                "workspace_git_mutate",
                "Create or switch a clean local branch, or reset an initialized submodule to its cached pinned revision.",
                {
                    key: {"type": "string"}
                    for key in (
                        "project_id",
                        "operation",
                        "target",
                        "expected_head",
                        "request_id",
                    )
                },
                ["project_id", "operation", "target", "expected_head", "request_id"],
                True,
            ),
            (
                "workspace_projects",
                "List canonical projects in allowed workspace roots.",
                {},
                [],
                False,
            ),
            (
                "workspace_project_detect",
                "Read bounded project metadata without executing scripts.",
                {"workspace": {"type": "string"}},
                ["workspace"],
                False,
            ),
            (
                "workspace_project_templates",
                "List local runnable templates and interpreter availability.",
                {},
                [],
                False,
            ),
            (
                "workspace_project_register",
                "Register an existing workspace in the canonical project store.",
                {
                    key: {"type": "string"}
                    for key in ("name", "workspace", "request_id")
                },
                ["name", "workspace", "request_id"],
                True,
            ),
            (
                "workspace_project_scaffold",
                "Create a new local service from a fixed template without overwriting files.",
                {
                    key: {"type": "string"}
                    for key in ("name", "parent", "directory", "template", "request_id")
                },
                ["name", "parent", "directory", "template", "request_id"],
                True,
            ),
            (
                "workspace_ports",
                "List durable loopback port reservation records.",
                {},
                [],
                False,
            ),
            (
                "workspace_port_inventory",
                "Inspect availability within the configured port allocation.",
                {},
                [],
                False,
            ),
            (
                "workspace_port_reserve",
                "Hold an available loopback port for a project until explicitly released.",
                {
                    "project_id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "port": {"type": "integer"},
                },
                ["project_id", "request_id"],
                True,
            ),
            (
                "workspace_port_release",
                "Release only a port socket owned by this registry.",
                {"id": {"type": "string"}, "revision": {"type": "integer"}},
                ["id", "revision"],
                True,
            ),
            ("workspace_snapshots", "List saved workspace contexts.", {}, [], False),
            (
                "workspace_snapshot_get",
                "Read one saved workspace context.",
                {"id": {"type": "string"}},
                ["id"],
                False,
            ),
            (
                "workspace_snapshot_delete",
                "Delete only a saved context; never changes its workspace.",
                {"id": {"type": "string"}, "revision": {"type": "integer"}},
                ["id", "revision"],
                True,
            ),
            (
                "workspace_process_start",
                "Start an approved command in an allowed project directory.",
                {
                    key: {"type": "string"}
                    for key in ("project_id", "workspace", "command", "request_id")
                },
                ["project_id", "workspace", "command", "request_id"],
                True,
            ),
            (
                "workspace_processes",
                "List managed process lifecycle records.",
                {},
                [],
                False,
            ),
            (
                "workspace_process_get",
                "Read current process status.",
                {"id": {"type": "string"}},
                ["id"],
                False,
            ),
            (
                "workspace_process_log_window",
                "Read a bounded redacted process log window with a durable character cursor.",
                {
                    "id": {"type": "string"},
                    "after": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                ["id"],
                False,
            ),
            (
                "workspace_process_logs",
                "Read the bounded output tail of a managed process.",
                {"id": {"type": "string"}},
                ["id"],
                False,
            ),
            (
                "workspace_process_stop",
                "Stop only a process owned by this registry.",
                {"id": {"type": "string"}, "revision": {"type": "integer"}},
                ["id", "revision"],
                True,
            ),
        ]:
            definitions.append(
                ToolDefinition(
                    name=name,
                    description=description,
                    provider=self.name,
                    parameters={
                        "type": "object",
                        "properties": fields,
                        "required": required,
                        "additionalProperties": False,
                    },
                    requires_approval=write,
                    risk_level=RiskLevel.DESTRUCTIVE if write else RiskLevel.SAFE,
                )
            )
        return definitions

    async def invoke(self, tool_name, arguments):
        from gideon.interfaces.dashboard.handlers.files import _dashboard_roots

        root = config_dir() / "capabilities" / "workspace"
        roots = [p for _, p in _dashboard_roots()]
        try:
            definitions = {t.name: t for t in await self.list_tools()}
            definition = definitions.get(tool_name)
            if definition is None or not isinstance(arguments, dict):
                raise ValueError("Unknown workspace tool or invalid arguments")
            schema = definition.parameters
            if set(arguments) - set(schema["properties"]) or set(
                schema["required"]
            ) - set(arguments):
                raise ValueError("Invalid workspace tool arguments")
            for key, value in arguments.items():
                expected = str if schema["properties"][key]["type"] == "string" else int
                if type(value) is not expected:
                    raise ValueError("Invalid argument type")
            if tool_name == "workspace_provider_terminal_profiles":
                from .provider_terminal import profiles

                result = profiles()
            elif tool_name == "workspace_storage_diagnosis":
                from .storage import StorageDiagnosis

                result = StorageDiagnosis(config_dir(), allowed_roots=roots).report(
                    arguments.get("project_id")
                )
            elif tool_name.startswith("workspace_external_terminal"):
                from .iterm import ExternalTerminalMirror

                mirror = ExternalTerminalMirror()
                result = (
                    await mirror.screen(arguments["id"])
                    if tool_name.endswith("_screen")
                    else await mirror.inventory()
                )
            elif tool_name.startswith("workspace_desktop"):
                from .desktop import get_desktop_registry

                registry = get_desktop_registry(root, allowed_roots=roots)
                if tool_name == "workspace_desktops":
                    result = registry.list()
                elif tool_name == "workspace_desktop_get":
                    result = registry.get(arguments["id"])
                elif tool_name == "workspace_desktop_start":
                    result = await registry.start(arguments)
                else:
                    result = await registry.stop(arguments["id"], arguments["revision"])
            elif tool_name.startswith("workspace_git"):
                from .git_ops import GitService

                git = GitService(root, allowed_roots=roots)
                if tool_name == "workspace_git_inspect":
                    result = await git.inspect(arguments["project_id"])
                elif tool_name == "workspace_git_history":
                    result = git.list(arguments["project_id"])
                else:
                    result = await git.mutate(arguments)
            elif tool_name.startswith("workspace_project"):
                from .projects import ProjectService

                projects = ProjectService(root, allowed_roots=roots)
                if tool_name == "workspace_projects":
                    result = projects.list()
                elif tool_name == "workspace_project_templates":
                    result = projects.templates()
                elif tool_name == "workspace_project_detect":
                    result = projects.detect(arguments["workspace"])
                elif tool_name == "workspace_project_register":
                    result = projects.register(arguments)
                else:
                    result = projects.scaffold(arguments)
            elif tool_name.startswith("workspace_port"):
                from .ports import get_port_registry

                ports = get_port_registry(root)
                if tool_name == "workspace_ports":
                    result = ports.list()
                elif tool_name == "workspace_port_inventory":
                    result = ports.inventory()
                elif tool_name == "workspace_port_reserve":
                    result = ports.reserve(arguments)
                else:
                    result = ports.release(arguments["id"], arguments["revision"])
            elif tool_name.startswith("workspace_snapshot"):
                snapshots = SnapshotStore(root, allowed_roots=roots)
                if tool_name == "workspace_snapshots":
                    result = snapshots.list()
                elif tool_name == "workspace_snapshot_get":
                    result = snapshots.get(arguments["id"])
                else:
                    result = snapshots.delete(arguments["id"], arguments["revision"])
            else:
                registry = get_registry(root, allowed_roots=roots)
                if tool_name == "workspace_process_start":
                    result = await registry.start(arguments)
                elif tool_name == "workspace_processes":
                    result = registry.list()
                elif tool_name == "workspace_process_get":
                    result = registry.get(arguments["id"])
                elif tool_name == "workspace_process_log_window":
                    result = registry.log_window(
                        arguments["id"],
                        after=arguments.get("after", 0),
                        limit=arguments.get("limit", 4096),
                    )
                elif tool_name == "workspace_process_logs":
                    result = registry.logs(arguments["id"])
                else:
                    result = await registry.stop(arguments["id"], arguments["revision"])
            return ToolResult(success=True, output=json.dumps(result))
        except (ValueError, OSError, TypeError) as error:
            return ToolResult(success=False, error=str(error))


def create_provider():
    return WorkspaceToolProvider()
