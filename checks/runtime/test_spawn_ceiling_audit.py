"""Spawn-ceiling audit tripwire (PLATFORM-HARDENING-FLOORS §1, SH1.3a).

Every process-spawning call site in ``runtime/gideon`` must be *accounted for*: either
it is **ceiling-wrapped** (routed through the post-exec shim via ``create_subprocess_limited``
/ ``spawn_shim_argv``, so an agent-influenced child carries a resource ceiling) or it is
**operator-exempt** (an operator-initiated spawn — the frontend build, the service/update
machinery, the interactive terminal, host-fact probes — which must NOT be constrained).

The census is an AST walk (no import side effects) over every ``subprocess.Popen/run/call/
check_output/check_call``, ``asyncio.create_subprocess_exec/shell``, ``os.execv*``, and
``StdioServerParameters`` site. Each is keyed by ``file::qualname::callee``. The keys are
partitioned across two hardcoded allowlists below. **A new, unmapped spawn site reds this
test naming its file:line** — the author must consciously classify it. That conscious step
is the control: an agent-influenced spawn that forgets the ceiling cannot slip in silently.

Adding a site → add its key to exactly one allowlist with a one-line reason. Removing a
site → drop its key. The test fails if the census and the union of the allowlists disagree
in either direction, so a stale allowlist entry is caught too.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SPAWN_CALLEES = {
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_output",
    "subprocess.check_call",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.execv",
    "os.execvp",
    "os.execve",
    "StdioServerParameters",
    "create_subprocess_limited",
}

_CEILING_WRAPPED: dict[str, str] = {
    "engine/tmux_substrate.py::TmuxCommand.status::asyncio.create_subprocess_exec": (
        "tmux worker command is ceiling-wrapped by provisioning._DurableSetupJob.run before new_session; other verbs are fixed session controls"
    ),
    "automation/workflows/container_env.py::_run_cli::create_subprocess_limited": (
        "container backend CLI verbs — manifest-derived (agent-authorable) argv → tool ceiling"
    ),
    "security/sandbox.py::create_subprocess_limited::asyncio.create_subprocess_exec": (
        "the ceiling helper — spawns the shim-prepended argv for every routed async seam"
    ),
    "engine/_spawn_exec_shim.py::main::os.execvp": (
        "the shim's post-exec handoff to the real target (limits already applied)"
    ),
    "engine/agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::"
    "create_subprocess_limited": "native bash tool → tool ceiling via create_subprocess_limited",
    "integrations/action_providers/bash_provider.py::BashActionProvider.execute::"
    "create_subprocess_limited": "bash action provider → tool ceiling",
    "integrations/computer_use/service.py::_run_driver::create_subprocess_limited": (
        "desktop computer-use driver → tool ceiling via create_subprocess_limited"
    ),
    "automation/loop/gates.py::run_verify_command::create_subprocess_limited": (
        "loop verify command → tool ceiling (was create_subprocess_shell)"
    ),
    "automation/loop/worktree.py::_git::subprocess.run": (
        "loop worktree git → build ceiling via spawn_shim_argv"
    ),
    "assurance/selfqa/fix_branch.py::_git::subprocess.run": (
        "selfqa fix-branch git → build ceiling via spawn_shim_argv"
    ),
    "workspace/artifacts/build.py::_run_esbuild::create_subprocess_limited": (
        "react artifact bundle → build ceiling via create_subprocess_limited"
    ),
    "extensions/apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen": (
        "app backend → tool ceiling via spawn_shim_argv (argv-prepend; NOT preexec_fn)"
    ),
    "extensions/apps/worker_runtime.py::WorkerSupervisor._spawn::subprocess.Popen": (
        "app background worker → tool ceiling via spawn_shim_argv (argv-prepend; NOT preexec_fn)"
    ),
    "integrations/mcp_discovery.py::probe_server::create_subprocess_limited": (
        "MCP probe → tool ceiling via create_subprocess_limited"
    ),
    "integrations/mcp_client.py::McpServerConn._open_transport::StdioServerParameters": (
        "MCP stdio client → tool ceiling via spawn_shim_argv baked into StdioServerParameters"
    ),
    "automation/workflows/effects.py::_TeardownInvocation.run::create_subprocess_limited": (
        "workflow BYOI teardown → tool ceiling via create_subprocess_limited"
    ),
    "automation/workflows/provisioning.py::_StepExecution.subprocess::create_subprocess_limited": (
        "workspace setup/teardown step → tool ceiling via create_subprocess_limited"
    ),
    "integrations/sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited": (
        "none sandbox provider → profile ceiling via create_subprocess_limited (post-exec shim); "
        "the single routed-spawn seam (subsumes the former AcpProcess.spawn session_host site)"
    ),
    "integrations/sandbox_providers/docker.py::_DockerHandle.exec::create_subprocess_limited": (
        "docker sandbox provider → profile ceiling via create_subprocess_limited on the docker "
        "client (mirrors the none seam); container itself bounded by native --pids-limit/--memory"
    ),
    "integrations/sandbox_providers/lima.py::_LimaHandle.exec::create_subprocess_limited": (
        "lima sandbox provider → profile ceiling via create_subprocess_limited on the limactl "
        "client (mirrors the none/docker seam); guest VM bounded by instance-creation config"
    ),
    "automation/schedule_script.py::run_script_sandboxed::subprocess.run": (
        "cron/scheduled script → tool ceiling via spawn_shim_argv, prepended outside the "
        "OS-sandbox wrap (sync site; rlimits inherit through exec)"
    ),
    "interfaces/dashboard/handlers/terminal.py::api_terminal_ws::create_subprocess_limited": (
        "interactive terminal → none profile (user's own shell; helper is a no-op)"
    ),
    "integrations/knowledge_providers/pack_parse.py::run_parse_script::subprocess.run": (
        "connector-pack parse script → tool ceiling via spawn_shim_argv, prepended outside "
        "the OS-sandbox wrap (sync site; rlimits inherit through exec)"
    ),
    "integrations/local_models/sidecar.py::SidecarRunner._spawn::subprocess.Popen": (
        "model sidecar child → tool ceiling via spawn_shim_argv (argv-prepend)"
    ),
    "integrations/local_models/sidecar.py::SidecarInstall._run::subprocess.run": (
        "sidecar venv/pip install → build ceiling via spawn_shim_argv"
    ),
}

_OPERATOR_EXEMPT: dict[str, str] = {
    "engine/tmux_substrate.py::TmuxCommand.lines::asyncio.create_subprocess_exec": "operator: tmux session listing",
    "engine/tmux_substrate.py::TmuxCommand.status_sync::subprocess.run": "operator: tmux session existence probe",
    "engine/tmux_substrate.py::TmuxCommand.output_sync::subprocess.run": "operator: tmux pane cwd listing",
    "engine/gateway_maintenance.py::run_command::asyncio.create_subprocess_exec": "service: gateway git/pip update commands",
    "engine/gateway_maintenance.py::RuntimeUpdates.restart::os.execv": "service: re-exec gateway after update",
    "engine/gateway_maintenance.py::DependencyRepair.run::subprocess.run": "operator: install missing gateway dependencies",
    "interfaces/cli/doctor.py::_doctor_node::subprocess.run": "host-fact: node version probe",
    "interfaces/cli/doctor.py::_git_repo_state::subprocess.run": "host-fact: git branch and worktree status",
    "interfaces/dashboard/handlers/updates.py::_run_rollback._rollback::asyncio.create_subprocess_exec": "operator: explicit package/git rollback",
    "integrations/acp/cli_resolve.py::_npm_root_global_bin::subprocess.run": "operator: npm prefix probe",
    "integrations/acp/cli_resolve.py::resolve_node_ge::subprocess.run": "operator: node version probe",
    "integrations/acp/cli_resolve.py::_AdapterInstall.execute::subprocess.run": "operator: ACP adapter install",
    "integrations/acp/transport.py::_direct_children::subprocess.check_output": "host-fact: child PID probe",
    "integrations/acp/transport.py::_get_start_time::subprocess.check_output": (
        "host-fact: process start-time probe"
    ),
    "integrations/acp/transport.py::_is_our_child::subprocess.check_output": "host-fact: PID-recycle probe",
    "integrations/acp/transport.py::_kill_escaped_children::subprocess.check_output": (
        "host-fact: pgid membership scan for escaped children"
    ),
    "engine/agents/runners.py::probe_runner::subprocess.run": "host-fact: runner --version probe",
    "automation/workflows/web_preview.py::_run::subprocess.run": "host-fact: listening-port/cwd probe",
    "operations/durability/state_history.py::_git::subprocess.run": "operator: state-history git runner",
    "assurance/selfqa/triage.py::_git::subprocess.run": "host-fact: read-only git commit inspection",
    "assurance/selfqa/watch.py::_git::subprocess.run": (
        "host-fact: read-only git HEAD/rev-list probe (SV-11 — the retired sandbox "
        "script's delta logic, moved in-process; same fixed argv, no shell, 30s timeout)"
    ),
    "assurance/selfqa/evidence.py::_ffmpeg_ping::subprocess.run": (
        "host-fact: ffmpeg availability probe (fixed `ffmpeg -version` argv)"
    ),
    "assurance/selfqa/evidence.py::_run_ffmpeg::subprocess.run": (
        "host tool: ffmpeg contact-sheet/GIF derivation "
        "(fixed filter argv, paths in the bundle dir)"
    ),
    "operations/durability/state_history.py::ensure_repo::subprocess.run": "operator: state-history repo init",
    "operations/durability/state_history.py::_repo_usable::subprocess.run": "operator: repo usability probe",
    "operations/durability/state_history.py::git_available::subprocess.run": "host-fact: git presence probe",
    "extensions/apps/app_manager.py::_run_hook::subprocess.run": "operator: app install setup hook",
    "extensions/apps/app_manager.py::_install_python_deps::subprocess.run": "operator: app dep install",
    "extensions/apps/catalog.py::_read_git_registry::subprocess.run": "operator: git app registry read",
    "extensions/apps/catalog.py::_scan_git_source::subprocess.run": "operator: git app source scan",
    "extensions/apps/source.py::_clone_git::subprocess.run": "operator: git app clone",
    "interfaces/cli/config.py::_config_cmd::os.execvp": "operator: opens $EDITOR on config",
    "interfaces/cli/doctor.py::_doctor::subprocess.run": "operator: doctor host probes",
    "interfaces/cli/server.py::_stop::subprocess.check_output": "operator: stop — pid lookup",
    "interfaces/cli/server.py::_is_gideon_process::subprocess.check_output": (
        "operator: pid identity probe"
    ),
    "interfaces/cli/server.py::_spawn_detached_gateway::subprocess.Popen": (
        "operator: launch the gateway itself"
    ),
    "interfaces/cli/run.py::start_transient_gateway::subprocess.Popen": (
        "operator: launch the gateway itself (headless `run` bootstrap)"
    ),
    "interfaces/cli/server.py::_install::subprocess.run": "operator: self-update package install",
    "interfaces/cli/server.py::_refresh_agent_config::subprocess.run": (
        "operator: post-update `setup --agent-only` re-run"
    ),
    "interfaces/cli/server.py::_logs_cmd::subprocess.run": "operator: logs source probe",
    "interfaces/cli/server.py::_logs_cmd::os.execvp": "operator: exec journalctl/tail for `logs`",
    "interfaces/dashboard/handlers/_shared.py::_list_marketplace_skills::asyncio.create_subprocess_exec": (
        "operator: `gideon skills list`"
    ),
    "interfaces/dashboard/handlers/mcp.py::api_mcp_remove::asyncio.create_subprocess_exec": (
        "operator: `gideon skills mcp uninstall`"
    ),
    "interfaces/dashboard/handlers/files.py::_content_search_rg::asyncio.create_subprocess_exec": (
        "operator: file search (rg)"
    ),
    "interfaces/dashboard/handlers/files.py::_git::asyncio.create_subprocess_exec": (
        "operator: file browser git read"
    ),
    "automation/workflows/review_service.py::_git::asyncio.create_subprocess_exec": (
        "operator: run workspace git diff read"
    ),
    "interfaces/dashboard/handlers/files.py::api_reveal_path::subprocess.Popen": (
        "operator: reveal in Finder/xdg-open"
    ),
    "interfaces/dashboard/handlers/files.py::api_screenshot::asyncio.create_subprocess_exec": (
        "operator: screencapture"
    ),
    "interfaces/dashboard/handlers/files.py::api_upload::asyncio.create_subprocess_exec": (
        "operator: native file picker"
    ),
    "interfaces/dashboard/handlers/terminal.py::_kill_tmux_session::asyncio.create_subprocess_exec": (
        "operator: kill user's tmux session"
    ),
    "interfaces/dashboard/handlers/terminal.py::_list_tmux_sessions::asyncio.create_subprocess_exec": (
        "operator: list user's tmux sessions"
    ),
    "operations/self_update.py::_run_git::subprocess.run": (
        "service: the one git seam every sync self-update probe funnels through"
    ),
    "operations/self_update.py::_git_output::asyncio.create_subprocess_exec": (
        "service: update git fetch + rev-list"
    ),
    "interfaces/dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec": (
        "service: update check git"
    ),
    "interfaces/dashboard/handlers/updates.py::_apply_pip_update._apply::asyncio.create_subprocess_exec": (
        "service: self pip update"
    ),
    "interfaces/dashboard/handlers/updates.py::api_update_apply::asyncio.create_subprocess_exec": (
        "service: update git"
    ),
    "interfaces/dashboard/handlers/updates.py::api_update_apply._apply::asyncio.create_subprocess_exec": (
        "service: update git/pip"
    ),
    "interfaces/dashboard/handlers/updates.py::_graceful_reexec::os.execve": (
        "service: re-exec the gateway itself"
    ),
    "interfaces/dashboard/handlers_system.py::_get_static_system_info::subprocess.check_output": (
        "host-fact: static sysinfo"
    ),
    "interfaces/dashboard/handlers_system.py::_collect_gpu_metrics::subprocess.check_output": (
        "host-fact: GPU metrics"
    ),
    "integrations/local_models/fit.py::_probe_gpu::subprocess.check_output": (
        "host-fact: GPU/VRAM capacity"
    ),
    "interfaces/dashboard/handlers_system.py::_collect_system_metrics::subprocess.check_output": (
        "host-fact: system metrics"
    ),
    "integrations/local_models/residency.py::_darwin_memory::subprocess.check_output": (
        "host-fact: macOS memory-pressure probe (sysctl/vm_stat, static argv)"
    ),
    "assurance/evals/runner.py::_spawn_cell::subprocess.run": (
        "operator: evals matrix cell (own env isolation)"
    ),
    "operations/frontend.py::build_frontend_sync::subprocess.run": "operator: frontend npm build",
    "operations/frontend.py::build_frontend_async::asyncio.create_subprocess_exec": (
        "operator: frontend npm build"
    ),
    "engine/gateway.py::_wslview_open::subprocess.run": "operator: open browser on WSL",
    "cognition/knowledge/pipeline/executor.py::PipelineExecutor._media_duration::subprocess.run": (
        "host tool: ffprobe"
    ),
    "cognition/knowledge/pipeline/nodes/media_nodes.py::VideoClassifyNode._dense_regions::subprocess.run": (
        "host tool: ffprobe scene detect"
    ),
    "cognition/knowledge/pipeline/nodes/media_nodes.py::_run_cmd::asyncio.create_subprocess_exec": (
        "host tool: ffmpeg"
    ),
    "integrations/mcp_core.py::_get_ppid::subprocess.check_output": "host-fact: ppid probe",
    "integrations/mcp_shared.py::_resolve_excluded_tools._get_ppid::subprocess.check_output": (
        "host-fact: ppid probe"
    ),
    "engine/session_pid.py::_is_managed_agent_process::subprocess.check_output": (
        "host-fact: managed-process probe"
    ),
    "engine/subagent.py::_total_memory_gb::subprocess.check_output": "host-fact: total RAM probe",
    "security/sandbox.py::_probe_sandbox_exec::subprocess.run": (
        "host-fact: sandbox-exec availability probe"
    ),
    "security/sandbox.py::_ssh_supports_accept_new::subprocess.run": "host-fact: ssh version probe",
    "integrations/sandbox_providers/docker.py::_daemon_ping::subprocess.run": (
        "host-fact: docker daemon availability probe (fixed docker version argv)"
    ),
    "integrations/sandbox_providers/lima.py::_probe::subprocess.run": (
        "host-fact: lima instance status probe (fixed limactl list argv)"
    ),
    "integrations/sandbox_providers/docker.py::_DockerHandle.cleanup::subprocess.run": (
        "operator: docker rm -f of our own ephemeral container (fixed argv, self-generated name)"
    ),
    "operations/service/linux.py::_current_group::subprocess.run": "operator: service install id probe",
    "operations/service/linux.py::_sudo_run::subprocess.run": "operator: service install sudo",
    "operations/service/linux.py::_systemctl::subprocess.run": "operator: systemctl control",
    "operations/service/linux.py::_write_unit_via_sudo::subprocess.run": "operator: write systemd unit",
    "operations/service/macos.py::_launchctl::subprocess.run": "operator: launchctl control",
    "automation/triggers/liveness.py::ActivityProbe.git_changes::subprocess.run": (
        "host-fact: workspace git-dirty probe"
    ),
    "integrations/computer_use/macos_tcc.py::_probe::subprocess.run": (
        "host-fact: tccd responsible-process probe (fixed argv, read-only, own timeout)"
    ),
    "integrations/transcribe.py::_transcribe_segmented::asyncio.create_subprocess_exec": (
        "host tool: transcription ffmpeg"
    ),
    "integrations/transcribe.py::_transcribe_segmented_detailed::asyncio.create_subprocess_exec": (
        "host tool: transcription ffmpeg"
    ),
    "integrations/voice_reply.py::stitch_wavs::asyncio.create_subprocess_exec": "host tool: wav stitch ffmpeg",
    "extensions/apps/quality.py::run_bundle_tests::subprocess.run": (
        "CI tool: app-bundle pytest for a quality declaration (no gateway call site)"
    ),
}


def _src_root() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime" / "gideon"


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        parts = [f.attr]
        v = f.value
        while isinstance(v, ast.Attribute):
            parts.append(v.attr)
            v = v.value
        if isinstance(v, ast.Name):
            parts.append(v.id)
        return ".".join(reversed(parts))
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _normalize(callee: str) -> str:
    tail = callee.split(".")[-1]
    if tail in {"create_subprocess_exec", "create_subprocess_shell"}:
        return "asyncio." + tail
    if tail == "create_subprocess_limited":
        return "create_subprocess_limited"
    if (
        tail in {"Popen", "run", "call", "check_output", "check_call"}
        and "subprocess" in callee
    ):
        return "subprocess." + tail
    if tail in {"execv", "execvp", "execve"}:
        return "os." + tail
    if tail == "StdioServerParameters":
        return "StdioServerParameters"
    return callee


def _census() -> dict[str, list[int]]:
    """Map ``file::qualname::callee`` → sorted line numbers for every spawn site."""
    out: dict[str, list[int]] = {}
    root = _src_root()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(root).as_posix()

        class V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.q: list[str] = []

            def visit_FunctionDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.generic_visit(n)
                self.q.pop()

            visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

            def visit_ClassDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.generic_visit(n)
                self.q.pop()

            def visit_Call(self, n: ast.Call) -> None:
                c = _normalize(_callee(n))
                if c in _SPAWN_CALLEES:
                    key = f"{rel}::{'.'.join(self.q) or '<module>'}::{c}"
                    out.setdefault(key, []).append(n.lineno)
                self.generic_visit(n)

        V().visit(tree)
    return out


def test_every_spawn_site_is_classified():
    """Every spawn site is in exactly one allowlist; a new/unmapped site reds CI by name."""
    census = _census()
    allow = set(_CEILING_WRAPPED) | set(_OPERATOR_EXEMPT)

    unmapped = sorted(set(census) - allow)

    def lines_for(k):
        return ", ".join(f"{k.split('::')[0]}:{ln}" for ln in census[k])  # noqa: E731

    assert not unmapped, (
        "Unmapped spawn site(s) — classify each in checks/runtime/test_spawn_ceiling_audit.py as "
        "ceiling-wrapped (agent-influenced → route through create_subprocess_limited/"
        "spawn_shim_argv) or operator-exempt:\n"
        + "\n".join(f"  {k}  ({lines_for(k)})" for k in unmapped)
    )

    stale = sorted(allow - set(census))
    assert not stale, (
        "Stale allowlist entr(y/ies) — no such spawn site exists anymore; remove from the "
        "allowlist:\n" + "\n".join(f"  {k}" for k in stale)
    )


def test_ceiling_wrapped_and_operator_exempt_are_disjoint():
    """A site cannot be both wrapped and exempt."""
    both = set(_CEILING_WRAPPED) & set(_OPERATOR_EXEMPT)
    assert not both, f"sites in both allowlists: {sorted(both)}"


def test_agent_influenced_seams_are_all_ceiling_wrapped():
    """The named agent-influenced seams from PLATFORM-HARDENING-FLOORS §1 are each present
    in the ceiling-wrapped set (a regression guard so one cannot be quietly re-exempted).
    """
    required = {
        "engine/agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::"
        "create_subprocess_limited",
        "integrations/action_providers/bash_provider.py::BashActionProvider.execute::"
        "create_subprocess_limited",
        "extensions/apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen",
        "integrations/mcp_discovery.py::probe_server::create_subprocess_limited",
        "integrations/mcp_client.py::McpServerConn._open_transport::StdioServerParameters",
        "integrations/sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited",
        "automation/loop/gates.py::run_verify_command::create_subprocess_limited",
        "automation/loop/worktree.py::_git::subprocess.run",
        "automation/schedule_script.py::run_script_sandboxed::subprocess.run",
        "integrations/knowledge_providers/pack_parse.py::run_parse_script::subprocess.run",
        "workspace/artifacts/build.py::_run_esbuild::create_subprocess_limited",
    }
    missing = sorted(required - set(_CEILING_WRAPPED))
    assert not missing, f"agent seams not ceiling-wrapped: {missing}"
