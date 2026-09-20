"""CLI doctor subcommand — verify Gideon setup and diagnose issues."""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from gideon import __version__ as _pc_version
from gideon.core.config import AppConfig
from gideon.core.config import loader as config_loader
from gideon.core.config.loader import env_path
from gideon.core.layout import package_path
from gideon.engine.agent import AGENT_FILENAME, AGENTS_DIR
from gideon.integrations.transcribe import ensure_ffmpeg_in_path
from gideon.interfaces.dashboard.origin import (
    auth_is_off,
    is_local_bind,
    loopback_requires_token,
    machine_hostname,
    parse_dashboard_url,
    resolve_bind_host,
    tailnet_ip,
)


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


_MIN_NODE_VERSION = 18


def _doctor_node(node: str) -> None:
    try:
        result = subprocess.run([node, "-v"], capture_output=True, text=True, timeout=5)
        major = int(result.stdout.strip().lstrip("v").split(".")[0])
        if major >= _MIN_NODE_VERSION:
            print(f"  node:        ✅ {node} (v{major})")
        else:
            print(
                f"  node:        ⚠️  v{major} < {_MIN_NODE_VERSION} "
                f"(frontend needs Node {_MIN_NODE_VERSION}+)"
            )
            print(f"               Fix: install Node.js >= {_MIN_NODE_VERSION}")
    except Exception:
        print(f"  node:        ⚠️  {node} (version unknown)")


def _git_repo_state(project: Path, git: str | None) -> bool | None:
    try:
        result = subprocess.run(
            [git or "git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    return result.returncode == 0 and result.stdout.strip() == "true"


def _doctor_providers() -> list[str]:
    """Run a health probe for each registered ProviderEntry.

    For ``acp_agent`` entries, spawns the configured command and completes
    the ACP ``initialize`` handshake.  For other entries, performs
    a lightweight capability check (import test + credential presence).
    Returns a list of issue strings for any entry that fails.
    """
    issues: list[str] = []
    try:
        import gideon.integrations.llm.acp_agent  # noqa: F401
        from gideon.integrations.llm.registry import get_default_registry

        registry = get_default_registry()
        entries = registry.list_entries()
    except Exception as exc:
        print(f"  registry:    ⚠️  could not load ({exc})")
        return issues

    if not entries:
        print("  entries:     ⏹  no provider entries configured")
        return issues

    for entry in entries:
        label = f"{entry.name} ({entry.type})"
        if entry.type == "acp_agent":
            _probe_acp_agent(entry, label, issues)
        else:
            print(f"  {label}: ✅ registered")

    return issues


def _probe_acp_agent(entry: object, label: str, issues: list[str]) -> None:
    """Probe the acp_agent entry's readiness via the shared readiness probe."""
    import asyncio

    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    options = getattr(entry, "options", {}) or {}

    try:
        status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    except Exception as exc:
        print(f"  {label}: ⚠️  could not probe ({exc})")
        return

    icon = {
        "ready": "✅",
        "not_found": "❌",
        "needs_login": "🔑",
        "timeout": "⏳",
        "error": "❌",
    }.get(status.state, "⚠️")
    print(f"  {label}: {icon} {status.detail}")
    if not status.ready:
        issues.append(f"{label}: {status.state}")


def _doctor_paths() -> None:
    """Print the resolved install paths as machine-friendly ``key<TAB>path`` lines.

    The ``doctor get install-dir`` pattern (PLATFORM-LEGIBILITY §3.1): an external
    agent driving Gideon locates the offline API reference — and the config,
    skills, and install dirs — from the binary alone, without knowing whether this
    is a wheel, an editable install, or a source checkout. Tab-separated so it
    parses trivially; the ``reference`` path is the one an agent reads for exact
    tool/route signatures (see ``reference/index.md``).
    """
    from gideon.extensions.manifest_reference import reference_dir
    from gideon.extensions.skills.loader import skills_dir

    paths: list[tuple[str, Path]] = [
        ("reference", reference_dir()),
        ("config", config_dir()),
        ("skills", skills_dir()),
        ("install", Path(__file__).resolve().parent),
    ]
    for label, path in paths:
        print(f"{label}\t{path}")


def _doctor_rebuild_routing_stats() -> None:
    """Refold ``routing_stats.json`` from the model-call audit — the §1.3 rebuild path.

    🔑 THIS IS THE FLAG `routing/stats.py` ALREADY NAMED. Its :func:`~gideon.engine.routing.
    stats.rebuild` docstring calls itself "the ``--rebuild-routing-stats`` maintenance path" and
    `routing/usage.py` notes in as many words that no argument implemented it — so the recovery
    function shipped tested and unreachable, with the audit rows it needs sitting on disk.

    That mattered because the two folds recover differently. The usage fold self-heals: every
    ``GET /api/usage`` calls ``usage.refresh``, which refolds. The routing fold has no such read
    path — ``GET /api/models/telemetry`` calls ``load_stats`` only, and a missing file reads as an
    empty fold rather than an error (correct, and never fatal). So a deleted or truncated
    ``routing_stats.json`` left the Routing & Efficiency view permanently blank and dropped the
    learned policy's per-ref sample counts below its ``n >= 5`` floor, silently stopping it from
    proposing — while ``model_calls.jsonl`` still held everything needed to restore both.

    Reports the row count, because the number is the finding: the audit JSONL is capped and
    rotated, so a rebuild recovers the retained tail rather than all history, and a caller who is
    not told how many rows were folded cannot tell a successful rebuild from an empty one.
    """
    from gideon.engine.routing.stats import _stats_path, rebuild

    home = config_dir()
    folded = rebuild(home)
    print(f"routing stats: refolded {folded} attempt row(s) → {_stats_path(home)}")
    if not folded:
        print("  (no attempt rows in the audit log — the fold is empty, not broken)")


def _doctor_credentials() -> list[str]:
    """Print which credential store is holding the secrets; return any issues (SH-1).

    Reports the RESOLVED backend, never the requested one. That distinction is the
    reason this line exists: an install that asks for a keychain on a box with no OS
    secret service keeps its credentials in ``.env`` at 0600, and echoing the request
    would tell that operator their secrets are somewhere they are not.
    """
    from gideon.operations.resilience.doctor import credential_store_state

    state = credential_store_state()
    if state["backend"] == "keychain":
        print("  credentials: 🔐 OS keychain (keyring)")
    elif state["env_exists"] is False:
        print(f"  credentials: 🔐 .env not created — {env_path()}")
    elif state["env_exists"] is None:
        print(f"  credentials: ⚠️  .env state unknown — {env_path()}")
    elif not state["env_readable"]:
        print(
            f"  credentials: ⚠️  .env unreadable (mode {state['env_mode']}) — {env_path()}"
        )
    else:
        print(f"  credentials: 🔐 .env {state['env_mode']} — {env_path()}")
    warning = state["warning"]
    if not warning:
        return []
    print(f"               ⚠️  {warning}")
    return ["credential backend: keychain requested but unavailable"]


def _doctor_timezone() -> list[str]:
    """Print the zone timed triggers resolve to; return an issue on a UTC fallback (#2520).

    Same rule as the credentials line above: report the RESOLVED zone, not the requested one.
    Before this, no surface answered "which zone will my 08:30 reminder fire at" — `server_tz`
    said `UTC` on a PDT host, and an 08:30 trigger fired at 01:30 with nothing warning.

    The fallback is a ⚠️  that names the consequence in hours, not an informational line: a user
    who reads "timezone source: utc-fallback" has no reason to act, and one who reads "timed
    triggers will fire 7 hours off your local time" does.
    """
    from gideon.core.timezones import zone_report

    facts = zone_report()
    print(
        f"  timezone:    🕐 {facts['resolved']} (from {facts['source']}, "
        f"UTC{facts['utc_offset_hours']:+g})"
    )
    if not facts["warning"]:
        return []
    print(f"               ⚠️  {facts['warning']}")
    return [f"timezone: unresolved — schedules fall back to {facts['resolved']}"]


def _doctor_auth_mode() -> list[str]:
    """Report requested and effective auth, including selector availability."""
    from gideon.security.auth.modes import AuthConfig

    auth_cfg = AuthConfig.from_env()
    print(f"  auth mode:   {auth_cfg.mode_state()}")
    if not auth_cfg.fell_back_from_unauthorable_mode:
        return []
    print(
        "               ⚠️  requested auth mode is not authorable; "
        "runtime fell back to local_token"
    )
    return [
        f"auth mode: {auth_cfg.requested_mode} requested but runtime uses local_token"
    ]


def _doctor_proxy_bypass(cfg: AppConfig) -> list[str]:
    """Name the local-network auth bypass and flag public reverse-proxy exposure."""
    from gideon.security.exposure import (
        local_network_bypass_enabled,
        public_proxy_bypass_warning,
    )

    if not local_network_bypass_enabled():
        return []

    warning = public_proxy_bypass_warning(cfg)
    if warning:
        print("  proxy bypass: ❌ admits public internet without authentication")
        print(f"                ⚠️  {warning}")
        return ["proxy bypass: public internet may skip authentication"]

    print(
        "  proxy bypass: ⚠️  GIDEON_BYPASS_LOCAL_NETWORKS=1 "
        "(private clients skip auth)"
    )
    return []


def _doctor() -> None:
    """Verify Gideon setup — check dependencies, config, credentials, connectivity."""

    print("Gideon Doctor\n")
    issues: list[str] = []

    print("Dependencies")

    git = shutil.which("git")
    if git:
        print(f"  git:         ✅ {git}")
    else:
        print("  git:         ❌ not found (needed for gideon update)")
        issues.append("git")

    node = shutil.which("node")
    if node:
        _doctor_node(node)
    else:
        print(
            f"  node:        ⚠️  not found (frontend needs Node {_MIN_NODE_VERSION}+)"
        )
        print(f"               Fix: install Node.js >= {_MIN_NODE_VERSION}")

    from gideon.core.sqlite_compat import probe as _sqlite_probe

    _sq = _sqlite_probe()
    _fts = "✅" if _sq.fts5 else "❌"
    _json1 = "✅" if _sq.json1 else "❌"
    print(f"  SQLite:      {_sq.driver} {_sq.version}, FTS5 {_fts}, JSON1 {_json1}")
    if not _sq.fts5:
        print("               Fix: pip install pysqlite3-binary (bundles FTS5)")
        issues.append("sqlite fts5")

    venv_py = package_path().parent.parent / ".venv" / "bin" / "python3"
    is_venv_install = venv_py.is_file()

    print("\nProject")
    proj = os.environ.get("GIDEON_PROJECT_DIR", "")
    stale_project = False
    if not proj:
        saved_proj = config_dir() / "project_dir"
        if saved_proj.is_file():
            saved = saved_proj.read_text(encoding="utf-8").strip()
            if saved and Path(saved).is_dir():
                proj = saved
            else:
                print(f"  project dir: ❌ stale — points to deleted {saved}")
                print(f"               Fix: rm {config_dir() / 'project_dir'}")
                issues.append("stale project_dir")
                stale_project = True
    if proj and Path(proj).is_dir():
        print(f"  project dir: ✅ {proj}")
        git_repo = _git_repo_state(Path(proj), git)
        if git_repo is True:
            print("  git repo:    ✅")
        elif git_repo is False:
            print("  git repo:    ⚠️  not a git repo")
        else:
            print("  git repo:    ⚠️  state unknown")
    elif not stale_project:
        print("  project dir: ⏹  not set (source checkouts only — not needed here)")

    print("\nAgent")
    agent_path = AGENTS_DIR / AGENT_FILENAME
    if agent_path.exists():
        print(f"  config:      ✅ {agent_path}")
    else:
        print("  config:      ❌ not found (run gideon setup)")
        issues.append("agent config")

    print("\nConfiguration")
    cfg_dir = config_dir()
    cfg = AppConfig.load()
    if cfg_dir.exists():
        print(f"  config dir:  ✅ {cfg_dir}")
    else:
        print(f"  config dir:  📁 {cfg_dir} (will be created)")
    print(f"  provider:    {cfg.agent.provider}")
    try:
        from gideon.extensions.providers.use_cases import active_model_refs

        _refs = active_model_refs("chat")
        print(f"  chat model:  {_refs[0] if _refs else '(none bound)'}")
    except Exception:
        print("  chat model:  (unresolved)")
    print(f"  approval:    {cfg.agent.approval_mode}")

    issues.extend(_doctor_auth_mode())
    issues.extend(_doctor_timezone())
    issues.extend(_doctor_credentials())
    issues.extend(_doctor_proxy_bypass(cfg))

    _host: str = ""
    _port: int | None = None
    try:
        _host, _port = parse_dashboard_url(cfg.dashboard.url)
    except Exception:
        print("  dashboard:   ⚠️  cannot parse dashboard URL from config")
        issues.append("dashboard URL misconfigured")
    _display_host = _host or "localhost"
    if _port:
        print(f"  dashboard:   http://{_display_host}:{_port}")

    creds = cfg.load_credentials()
    _has_slack = bool(creds.get("SLACK_APP_TOKEN") and creds.get("SLACK_BOT_TOKEN"))
    _bind_host = resolve_bind_host()
    _local = is_local_bind(_bind_host)
    if _local:
        print("  bind:        127.0.0.1 (local-only, SSH tunnel for remote)")
        if loopback_requires_token():
            print(
                "  auth:        🔒 token required (run: gideon token, for a signed-in link)"
            )
        else:
            print("  auth:        loopback trusted (no token required)")
    else:
        print("  bind:        0.0.0.0 (all interfaces)")
        print("  auth:        ✅ token auth required (via !dashboard)")
        if not _has_slack:
            print(
                "  auth:        ⚠️  no channel configured — token generation unavailable"
            )
            issues.append("dashboard auth: remote bind without a channel")

    _tnet = tailnet_ip()
    _remote_port = _port or 0
    if not _local and auth_is_off():
        print(
            "  remote:      ❌ bound beyond loopback with auth OFF — set a password"
            " (see docs/guides/REMOTE_ACCESS.md) or bind loopback"
        )
        issues.append("remote access: exposed beyond loopback without auth")
    elif _tnet:
        _phone_url = (
            f"http://{_tnet}:{_remote_port}" if _remote_port else f"http://{_tnet}"
        )
        print(f"  remote:      ✅ tailnet {_tnet} — open {_phone_url} on your phone")
        print("               (run: gideon token, for the signed-in link)")
    else:
        print("  remote:      local-only — see docs/guides/REMOTE_ACCESS.md")

    print("\nMCP Tools")
    if agent_path.exists():

        try:
            agent_data = json.loads(agent_path.read_text(encoding="utf-8"))
        except Exception:
            agent_data = {}
        tools = agent_data.get("tools", [])
        allowed = agent_data.get("allowedTools", [])
        mcps = agent_data.get("mcpServers", {})
        mcp_fixed = False
        mcp_cmd_fixed = False
        for ref in ("@gideon-core",):
            name = ref[1:]
            in_tools = ref in tools
            in_allowed = ref in allowed
            in_servers = name in mcps
            if in_tools and in_allowed and in_servers:
                cmd = mcps[name].get("command", "")
                exists = Path(cmd).is_file() if cmd else False
                if exists:
                    print(f"  {ref}: ✅")
                else:
                    resolved = shutil.which("gideon")
                    if resolved:
                        mcps[name]["command"] = resolved
                        mcp_cmd_fixed = True
                        print(f"  {ref}: 🔧 fixed stale path: {cmd} → {resolved}")
                    else:
                        print(f"  {ref}: ❌ binary not found: {cmd}")
                        issues.append(f"{ref} binary")
            else:
                missing: list[str] = []
                if not in_servers:
                    missing.append("mcpServers")
                if not in_tools:
                    missing.append("tools")
                if not in_allowed:
                    missing.append("allowedTools")
                print(f"  {ref}: ❌ missing from {', '.join(missing)}")
                issues.append(f"{ref} config")
                if not in_tools:
                    tools.append(ref)
                if not in_allowed:
                    allowed.append(ref)
                mcp_fixed = True
        if mcp_fixed or mcp_cmd_fixed:
            agent_data["tools"] = tools
            agent_data["allowedTools"] = allowed
            agent_path.write_text(
                json.dumps(agent_data, indent=2) + "\n", encoding="utf-8"
            )
            if mcp_fixed:
                print("  → Auto-fixed tools/allowedTools in gideon.json")
                issues = [i for i in issues if "config" not in i]
            if mcp_cmd_fixed:
                print("  → Auto-fixed stale binary path(s) in gideon.json")

    print("\nRuntime")
    print(f"  python:      ✅ {sys.executable} ({sys.version.split()[0]})")
    print(f"  backend:     ✅ {_pc_version}")
    if is_venv_install:
        try:
            py_result = subprocess.run(
                [str(venv_py), "--version"], capture_output=True, text=True, timeout=5
            )
            py_result.check_returncode()
            ver = py_result.stdout.strip().removeprefix("Python ").strip()
            print(f"  venv python: ✅ {venv_py} ({ver})")
        except Exception as exc:
            print(f"  venv python: ❌ broken: {exc}")
            issues.append("venv python")
        else:
            try:
                subprocess.run(
                    [str(venv_py), "-c", "import websockets, aiohttp"],
                    capture_output=True,
                    timeout=5,
                ).check_returncode()
                print("  deps:        ✅ websockets, aiohttp available")
            except Exception:
                print("  deps:        ❌ missing modules (websockets/aiohttp)")
                issues.append("python deps")
    else:
        sys_py = shutil.which("python3")
        if sys_py:
            py_result = subprocess.run(
                [sys_py, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            ver = py_result.stdout.strip()
            print(f"  fallback:    ⚠️  {sys_py} ({ver})")
            try:
                subprocess.run(
                    [sys_py, "-c", "import websockets, aiohttp"],
                    capture_output=True,
                    timeout=5,
                ).check_returncode()
                print("  deps:        ✅ websockets, aiohttp available")
            except Exception:
                print("  deps:        ❌ missing modules (websockets/aiohttp)")
                issues.append("python deps")
        else:
            print("  fallback:    ⚠️  python3 not found on PATH")

    try:
        from gideon.core.env import _is_wsl
        from gideon.operations.service.common import Platform, current_platform

        if _is_wsl():
            print("  platform:    🪟 WSL detected (Windows Subsystem for Linux)")
            if current_platform() is Platform.SYSTEMD:
                print(
                    "  service:     ✅ systemd active — `gideon service install` works"
                )
            else:
                print(
                    "  service:     ⚠️  systemd not active — background service won't persist"
                )
                print(
                    "               Fix: add `[boot]\\nsystemd=true` to /etc/wsl.conf, then"
                )
                print(
                    "               run `wsl --shutdown` from Windows and reopen the shell."
                )
                print(
                    "               Without it, run the gateway in a foreground shell (or via"
                )
                print(
                    "               Windows Task Scheduler). See docs/guides/PLATFORMS.md."
                )
    except Exception:
        pass

    print("\nVector Memory")
    from gideon.integrations.embedding_providers.registry import _active_embedding_spec

    if _active_embedding_spec():
        print("  embeddings:  ✅ enabled")
    else:
        print(
            "  embeddings:  ⏹ disabled (pick an embedding model in Settings → Models)"
        )

    print("\nSpeech-to-Text")
    from gideon.extensions.providers.use_cases import load_use_case_settings
    from gideon.integrations.stt.registry import active_stt

    stt_active = bool(load_use_case_settings("stt").get("enabled", True))
    stt_resolved = active_stt()

    if not stt_active:
        print("  status:      ⏹ disabled (enable in Settings → Voice)")
    elif stt_resolved is None:
        print(
            "  status:      ⏹  no STT model configured (install an STT app or bind one in Settings → Models)"  # noqa: E501
        )
    else:
        print(f"  model:       ✅ {stt_resolved[0].name}:{stt_resolved[1]}")

    ensure_ffmpeg_in_path()
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        print(f"  ffmpeg:      ✅ {ffmpeg_bin}")
    elif stt_active:
        print("  ffmpeg:      ❌ not found")
        print("               Fix: brew install ffmpeg")
        issues.append("ffmpeg")
    else:
        print("  ffmpeg:      ⏭  not installed (not needed)")

    if stt_active and stt_resolved is not None:
        try:
            import faster_whisper  # noqa: F401

            print("  faster_whisper: ✅ importable")
        except ImportError:
            print("  faster_whisper: ❌ missing")
            print("               Fix: pip install faster-whisper")
            issues.append("faster_whisper missing")

    from gideon.extensions.app_cli import run_app_doctor_probes

    issues.extend(run_app_doctor_probes())

    print("\nProvider Health")
    _provider_issues = _doctor_providers()
    issues.extend(_provider_issues)

    print("\nConnectivity")
    is_remote = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))

    if _port:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{_port}/api/status")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
            print(f"  gateway:     ✅ running (uptime {data.get('uptime', '?')})")
        except urllib.error.HTTPError as he:
            if he.code in (401, 403):
                print("  gateway:     ✅ running (token auth enabled)")
            else:
                print(f"  gateway:     ⚠️  HTTP {he.code}")
        except (urllib.error.URLError, OSError):
            print("  gateway:     ⏹  not running")
        except Exception:
            print("  gateway:     ⚠️  running but returned unexpected response")

        if is_remote:
            mh = machine_hostname() or "this-host"
            print("\n  💡 Remote access: Run on your LOCAL machine:")
            print(f"     ssh -L {_port}:localhost:{_port} {mh}")
            print("     Then run: gideon token")

    if _port and not _local:
        if not _host:
            issues.append("cannot verify dashboard auth (host unknown)")
        else:
            try:
                ext_req = urllib.request.Request(f"http://{_host}:{_port}/api/status")
                try:
                    with urllib.request.urlopen(ext_req, timeout=2) as resp:
                        print(
                            "  auth check:  ❌ external access allowed without token!"
                        )
                        issues.append(
                            "dashboard auth: no token required on external interface"
                        )
                except urllib.error.HTTPError as he:
                    if he.code in (401, 403):
                        print("  auth check:  ✅ token required on external interface")
                    else:
                        print(f"  auth check:  ⚠️  HTTP {he.code}")
            except Exception:
                print("  auth check:  ⏭  could not reach external interface")

    print()
    if issues:
        print(f"❌ Fix these issues: {', '.join(issues)}")
        sys.exit(1)
    else:
        print("✅ Gideon is ready!")
