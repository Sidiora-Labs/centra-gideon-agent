"""Core handlers — page serving, branding, STT transcribe, config, SEL, auth, session workspace."""

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

import gideon.assurance.validation as _validation_mod
from gideon.core.atomic_write import atomic_write
from gideon.core.config.edit_spec import ConfigValueError, coerce_edit_value
from gideon.core.config.loader import MEMORY_VAULT_MODES, PUSH_BACKENDS, AppConfig
from gideon.core.layout import package_path
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import (
    MAX_SESSION_TTL_SECS,
    generate_token,
    parse_duration,
)
from gideon.security.security import SUSPICIOUS_BASH_PATTERNS

logger = logging.getLogger(__name__)

_DIST_DIR = package_path("static", "dist")

_STT_MIC_CAP_BYTES = 25 * 1024 * 1024


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import gideon.interfaces.dashboard.handlers as _pkg  # noqa: F811 — circular import

    return _pkg.sel()


_UNBUNDLED_PAGE = """\
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gideon — Build the dashboard</title>
<style>
*{box-sizing:border-box;margin:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(145deg,#0f172a 0%,#1e293b 50%,#0f172a 100%);
  color:#e2e8f0;padding:32px}
.card{max-width:540px;width:100%;background:rgba(30,41,59,.85);
  border:1px solid rgba(148,163,184,.15);border-radius:20px;
  padding:48px 40px;backdrop-filter:blur(12px);
  box-shadow:0 25px 50px -12px rgba(0,0,0,.5)}
.icon{width:64px;height:64px;margin:0 auto 24px;display:flex;
  align-items:center;justify-content:center;
  background:linear-gradient(135deg,#6366f1,#8b5cf6);
  border-radius:16px;box-shadow:0 8px 24px rgba(99,102,241,.3)}
.icon svg{width:32px;height:32px;fill:none;stroke:#fff;stroke-width:2;
  stroke-linecap:round;stroke-linejoin:round}
h1{font-size:1.5rem;font-weight:700;text-align:center;margin-bottom:8px;
  background:linear-gradient(135deg,#c7d2fe,#e0e7ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{text-align:center;color:#94a3b8;font-size:.925rem;margin-bottom:32px}
.steps{display:flex;flex-direction:column;gap:12px}
.step{display:flex;align-items:flex-start;gap:12px;
  background:rgba(15,23,42,.6);border:1px solid rgba(148,163,184,.1);
  border-radius:12px;padding:14px 16px;transition:border-color .2s}
.step:hover{border-color:rgba(99,102,241,.4)}
.num{width:24px;height:24px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;font-size:.75rem;
  font-weight:700;background:rgba(99,102,241,.2);color:#a5b4fc;flex-shrink:0}
.step-body{flex:1;min-width:0}
.step-title{font-weight:600;font-size:.875rem;margin-bottom:2px}
.step-cmd{font-family:'SF Mono',Menlo,monospace;font-size:.8rem;
  color:#a5b4fc;background:rgba(99,102,241,.08);border-radius:6px;
  padding:6px 10px;margin-top:6px;display:inline-block;letter-spacing:-.01em}
.note{text-align:center;color:#64748b;font-size:.8rem;margin-top:28px}
.note a{color:#818cf8;text-decoration:none}
.note a:hover{text-decoration:underline}
.pulse{animation:pulse 2s cubic-bezier(.4,0,.6,1) infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
</style></head><body>
<div class="card">
  <div class="icon">
    <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5z"/>
    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
  </div>
  <h1>Gideon dashboard isn't built yet</h1>
  <p class="sub">The gateway is running <span class="pulse">●</span> &mdash;
  build the web UI to get started.</p>
  <div class="steps">
    <div class="step">
      <div class="num">1</div>
      <div class="step-body">
        <div class="step-title">Install dependencies</div>
        <code class="step-cmd">npm install</code>
      </div>
    </div>
    <div class="step">
      <div class="num">2</div>
      <div class="step-body">
        <div class="step-title">Build the dashboard</div>
        <code class="step-cmd">npm run build</code>
      </div>
    </div>
    <div class="step">
      <div class="num">3</div>
      <div class="step-body">
        <div class="step-title">Reload this page</div>
        <code class="step-cmd">⌘R or F5</code>
      </div>
    </div>
  </div>
  <p class="note">Your administrator can also provide a Gideon package with the dashboard included.</p>
</div>
</body></html>"""


async def index(request: web.Request) -> web.Response:
    """Serve the React dashboard HTML."""
    react_index = _DIST_DIR / "index.html"
    if not react_index.is_file():
        return web.Response(
            text=_UNBUNDLED_PAGE,
            content_type="text/html",
            status=503,
        )
    html = react_index.read_text(encoding="utf-8")
    from gideon.workspace.surface_layers import inject_safe_meta

    return web.Response(text=inject_safe_meta(html), content_type="text/html")


async def favicon(request: web.Request) -> web.StreamResponse:
    """Serve /gideon.svg — the favicon index.html declares. Dist-root files have no
    static route (only /assets, /fonts, …), so without this the request fell
    through to the SPA fallback and the "icon" came back as index.html HTML."""
    path = _DIST_DIR / "gideon.svg"
    if path.is_file():
        return web.FileResponse(path)
    raise web.HTTPNotFound()


def _dist_root_file(name: str, content_type: str) -> web.StreamResponse:
    """Serve a dist-ROOT file with an explicit content type.

    Two things here are load-bearing for the PWA (MOBILE-COMPANION T3.1):

    * **The content type is stated, never guessed.** ``.webmanifest`` is absent from
      Python's ``mimetypes`` table on a stock install, so ``FileResponse`` would send
      ``application/octet-stream`` and the browser would discard the manifest.
    * **A missing file returns a 404 *response*, it does not raise.** ``spa_fallback``
      converts a raised ``HTTPNotFound`` into ``index.html``, and HTML served for
      ``/sw.js`` fails registration on a MIME check while HTML served for the manifest
      fails to parse — both silently, with the app still looking fine. Returning the
      status directly bypasses that middleware entirely.
    """
    path = _DIST_DIR / name
    if path.is_file():
        return web.FileResponse(path, headers={"Content-Type": content_type})
    return web.Response(status=404, text=f"{name} not built", content_type="text/plain")


async def manifest_webmanifest(request: web.Request) -> web.StreamResponse:
    """Serve /manifest.webmanifest — the PWA manifest index.html declares.

    Stays behind session auth (it is not in ``token_auth._BYPASS_*``), which is why
    index.html declares the link with ``crossorigin="use-credentials"``.
    """
    return _dist_root_file("manifest.webmanifest", "application/manifest+json")


async def service_worker(request: web.Request) -> web.StreamResponse:
    """Serve /sw.js — the service worker, from the dist ROOT.

    The path is the scope: a worker served from ``/assets/`` could only control
    ``/assets/``, so this one must stay at the origin root to control the SPA.
    """
    return _dist_root_file("sw.js", "text/javascript")


async def api_stt_transcribe(request: web.Request) -> web.Response:
    """POST /api/stt/transcribe — transcribe uploaded audio via the active STT model.

    Two duplex-loop behaviors ride on this endpoint (MULTIMODAL-IO §4). Both keyed
    off the query string, because the body is a streamed multipart upload whose
    first part must stay the audio:

    * ``?duplex=true&session=<key>`` — a hands-free capture. The transcript is
      checked against the last text spoken for that session; speaker bleed comes
      back as ``{"text": "", "filtered": "echo"}`` so the dashboard can say why
      nothing happened instead of looking deaf.
    * The response carries ``input_origin: "voice"`` and, when the disclaimer is
      enabled, the line the frontend submits with the turn (§4.4).
    """
    import tempfile  # noqa: F811

    from gideon.integrations.transcribe import transcribe_audio  # noqa: F811
    from gideon.integrations.transcribe import is_available
    from gideon.integrations.voice.duplex import VOICE_DISCLAIMER, is_echo

    if not await is_available():
        return web.json_response({"error": "STT not available"}, status=503)

    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return web.json_response(
            {"error": "multipart/form-data with an 'audio' field is required"},
            status=400,
        )
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return web.json_response(
            {"error": f"failed to parse multipart body: {exc}"},
            status=400,
        )
    field = await reader.next()
    if field is None or not hasattr(field, "name") or field.name != "audio":  # type: ignore[union-attr]  # noqa: E501
        return web.json_response({"error": "missing audio field"}, status=400)

    fname = getattr(field, "filename", None) or "recording.webm"
    ext = os.path.splitext(fname)[1] or ".webm"
    from gideon.workspace.uploads import check_upload

    _stt_cap = _STT_MIC_CAP_BYTES
    field_mime = (getattr(field, "headers", {}) or {}).get("Content-Type") or None
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        os.close(fd)
        size = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = await field.read_chunk(8192)  # type: ignore[union-attr]
                if not chunk:
                    break
                size += len(chunk)
                if size > _stt_cap:
                    return web.json_response(
                        {
                            "error": check_upload(
                                fname, field_mime, size=size, override_limit=_stt_cap
                            ).reason
                        },
                        status=413,
                    )
                f.write(chunk)

        text = await transcribe_audio(tmp)
        if text:
            from gideon.security.security import (
                redact_credentials,
                redact_exfiltration_urls,
            )

            text, _ = redact_exfiltration_urls(text)
            text, _ = redact_credentials(text)
        text = text or ""

        cfg = AppConfig.load().voice
        duplex = str(request.query.get("duplex", "")).strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if duplex and text and cfg.echo_filter_enabled:
            spoken = request.app["state"].last_spoken(request.query.get("session", ""))
            if spoken and is_echo(text, spoken):
                return web.json_response({"text": "", "filtered": "echo"})

        payload: dict[str, object] = {"text": text, "input_origin": "voice"}
        if text and cfg.voice_disclaimer_enabled:
            payload["disclaimer"] = VOICE_DISCLAIMER
        return web.json_response(payload)
    except Exception:
        logger.exception("STT transcribe failed")
        return web.json_response({"error": "transcription failed"}, status=500)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


async def api_sel_rotate(request: web.Request) -> web.Response:
    """POST /api/sel/rotate — archive existing SEL log and start a fresh chain.

    Recovers from a broken HMAC chain. The previous log file is renamed with
    a UTC timestamp suffix unless ``{"archive": false}`` is sent.
    """
    archive = True
    if request.can_read_body:
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("archive") is False:
                archive = False
        except Exception:
            pass
    result = _sel().rotate(archive=archive)
    return web.json_response(result)


async def api_security_stats(_request: web.Request) -> web.Response:
    """GET /api/security/stats — live security feature counts."""
    from gideon.security.security import denied_command_patterns

    denied = len(denied_command_patterns())

    schemas = sum(
        1
        for name in dir(_validation_mod)
        if name.endswith("_SCHEMA") and name.isupper()
    )

    return web.json_response(
        {
            "denied_commands": denied,
            "suspicious_patterns": len(SUSPICIOUS_BASH_PATTERNS),
            "tool_schemas": schemas,
            "redaction_paths": 5,
        }
    )


async def api_security_denied_commands(_request: web.Request) -> web.Response:
    """GET /api/security/denied-commands — the bash denylist for the Security panel.

    ``builtin`` is the packaged baseline: always-on, read-only, and served with the
    ``baseline`` block the panel needs to say *which* baseline is in force — its
    ``version``, the ``sha256`` captured at import, how many patterns that covers, and
    whether the packaged file on disk still matches (``verified``). A file that has
    diverged is reported, not adopted, so ``count`` stays the number actually enforced.

    ``user_additions`` is the number of user patterns that genuinely *widen* the
    effective set, derived as ``len(effective) - len(baseline)`` rather than by
    counting the config list, because a user entry equal to a built-in is deduped away
    by :func:`denied_command_patterns` and adds nothing. ``user`` is still the raw
    editable list, persisted at ``security.denied_commands`` (edit via PATCH
    /api/config/gideon); the baseline has no write path at all.

    Reading this re-verifies the baseline, so viewing the panel while the packaged file
    is diverged writes the same SEL ``baseline_denylist_tamper_attempt`` the periodic
    doctor probe writes. That is deliberate: an owner looking at a diverged baseline is
    an auditable event.
    """
    from gideon.security.security import (
        baseline_denied_command_patterns,
        denied_command_patterns,
        verify_baseline_denylist,
    )

    user = list(AppConfig.load().security.denied_commands)
    report = verify_baseline_denylist()
    baseline = list(baseline_denied_command_patterns())
    effective = denied_command_patterns()
    return web.json_response(
        {
            "builtin": baseline,
            "user": user,
            "baseline": {
                "version": report["version"],
                "sha256": report["sha256"],
                "count": report["count"],
                "verified": report["file_verified"],
                "detail": report["detail"],
            },
            "user_additions": len(effective) - len(baseline),
        }
    )


async def api_security_egress(_request: web.Request) -> web.Response:
    """GET /api/security/egress — the operator's outbound-egress overrides for the
    Security panel. Defaults (public-only, no allow/deny) are enforced in code; these
    are the self-hoster's relaxations, edited via PATCH /api/config/gideon
    ``security.egress``."""
    eg = AppConfig.load().security.egress
    return web.json_response(
        {
            "allow_hosts": list(eg.allow_hosts),
            "deny_hosts": list(eg.deny_hosts),
            "allow_private": bool(eg.allow_private),
        }
    )


async def api_gideon_config(request: web.Request) -> web.Response:
    """GET/PUT /api/config/gideon — read or update Gideon config."""
    from gideon.core.config.loader import config_path  # noqa: F811

    if request.method == "PUT":
        caller = request.get("user", "dashboard")

        def _deny(error: str, status: int = 400) -> web.Response:
            _sel().log_api_access(
                caller=caller,
                operation="config.update",
                outcome="denied",
                error=error,
            )
            return web.json_response({"error": error}, status=status)

        try:
            body = await request.json()
        except Exception:
            return _deny("invalid JSON")
        if not isinstance(body, dict):
            return _deny("JSON body must be an object")
        agent_settings = body.get("agent")
        if not isinstance(agent_settings, dict):
            return _deny("agent must be an object")
        agent_fields = ("subagent_max_turns", "max_subagents", "orchestrator_skill")
        unknown = sorted(k for k in agent_settings if k not in agent_fields)
        if unknown:
            return _deny(
                f"unknown agent settings: {', '.join(unknown)} "
                f"(writable: {', '.join(agent_fields)})"
            )
        staged: dict[str, object] = {}
        for key in agent_fields:
            if key not in agent_settings:
                continue
            try:
                staged[key] = coerce_edit_value(
                    f"agent.{key}",
                    agent_settings[key],
                    _EDITABLE_CONFIG[f"agent.{key}"],
                )
            except ConfigValueError as exc:
                return _deny(f"{key} {exc}", exc.status)
        if not staged:
            return _deny("no recognized settings provided")
        applied = list(staged)

        from gideon.interfaces.dashboard.handlers.agents import (  # noqa: F811
            _get_config_lock,
        )

        path = config_path()
        async with _get_config_lock():
            try:
                data = (
                    json.loads(path.read_text(encoding="utf-8"))
                    if path.exists()
                    else {}
                )
            except Exception:
                _sel().log_api_access(
                    caller=caller,
                    operation="config.update",
                    outcome="error",
                    error="config.json is corrupt",
                )
                return web.json_response(
                    {"error": "config.json is corrupt"}, status=500
                )
            if not isinstance(data.get("agent"), dict):
                data["agent"] = {}
            agent = data["agent"]
            agent.update(staged)
            atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)
        _sel().log_api_access(
            caller=caller,
            operation="config.update",
            outcome="ok",
            resources=",".join(applied),
        )
        if "orchestrator_skill" in applied:
            if agent.get("orchestrator_skill"):
                from gideon.interfaces.dashboard.handlers.agents import (  # noqa: F811
                    _regen_orchestrator,
                )

                _regen_orchestrator()
            else:
                try:
                    from gideon.extensions.skills import ProcedureLibrary  # noqa: F811

                    for legacy in ("orchestrator", "conductor"):
                        p = ProcedureLibrary()._dir / legacy / "SKILL.md"
                        if p.exists():
                            p.unlink()
                except Exception:
                    logger.exception("Failed to clean up orchestrator skill")
        return web.json_response({"ok": True})

    cfg = AppConfig.load()
    return web.json_response(cfg.to_dict())


def _agent_values() -> set[str]:
    """Return allowed pool_agent values: empty string + all configured agent names."""
    from gideon.core.config.loader import AppConfig

    return {"", *AppConfig.load().agents}


def _bot_name_sanitizer(value: str) -> str:
    """The loader's bot_name sanitizer (single source of truth)."""
    from gideon.core.config.loader import _sanitize_bot_name

    return _sanitize_bot_name(value)


def _push_to_talk_chord_sanitizer(value: str) -> str:
    """Normalize a push-to-talk accelerator at the WRITE boundary (DC-3 T3.1).

    ``" Command + Shift + Space "`` and ``"Command+Shift+Space"`` are the same chord to
    a user and different strings to `globalShortcut.register`. Collapsing the spacing
    here means the value the shell is handed is byte-identical to the value stored and
    to the value the Settings control redisplays — otherwise a chord round-trips as
    "saved" while binding nothing.

    Empty stays empty: `load()` turns that into the shipped default, which is the one
    place that decision belongs.
    """
    return "+".join(part.strip() for part in value.split("+") if part.strip())


def _scratchpad_path_sanitizer(value: str) -> str:
    """Canonicalize the watched-scratchpad path at the WRITE boundary.

    `pathguard.canonicalize` is the same realpath+expanduser the trigger capability fence uses, so
    a stored path can never differ from the one a fence would compare — a config file holding
    ``~/notes/../.ssh/id_rsa`` while the runtime resolved something else is the split-brain S118
    documented. Empty stays empty: "" is how the feature is turned off.
    """
    if not value.strip():
        return ""
    from gideon.automation.triggers.pathguard import canonicalize

    return canonicalize(value.strip())


_EDITABLE_CONFIG: dict[str, dict] = {
    "agent.approval_mode": {
        "type": "enum",
        "values": ["auto", "interactive", "trust_reads"],
    },
    "agent.yolo": {"type": "bool"},
    "agent.sandbox": {"type": "enum", "values": ["auto", "off"]},
    "agent.soft_stop_budget_secs": {"type": "float", "min": 0.5, "max": 60.0},
    "agent.max_subagents": {"type": "int", "min": 0, "max": 16},
    "agent.subagent_max_turns": {"type": "int", "min": 1, "max": 200},
    "agent.subagent_timeout_secs": {"type": "int", "min": 60, "max": 7200},
    "agent.spawn_min_memory_gb": {"type": "float", "min": 0.0, "max": 64.0},
    "agent.subagent_cwd_allowed_roots": {"type": "str_list", "max_items": 20},
    "sandbox.nofile": {"type": "int", "min": 0, "max": 1_048_576},
    "sandbox.max_pids": {"type": "int", "min": 0, "max": 100_000},
    "sandbox.max_rss_mb": {"type": "int", "min": 0, "max": 1_048_576},
    "sandbox.cgroup_scopes": {"type": "bool"},
    "sandbox.env_passthrough": {"type": "str_list", "max_items": 40},
    "checkpoints.enabled": {"type": "bool"},
    "checkpoints.max_mb": {"type": "int", "min": 0, "max": 100_000},
    "checkpoints.max_turns": {"type": "int", "min": 1, "max": 1_000},
    "checkpoints.max_file_mb": {"type": "int", "min": 0, "max": 10_000},
    "security.denied_commands": {
        "type": "str_list",
        "max_items": 100,
        "each_regex": True,
    },
    "security.egress": {"type": "egress"},
    "security.credential_keychain": {"type": "bool"},
    "guardrails.budgets.max_tokens_per_run": {
        "type": "int",
        "min": 0,
        "max": 100_000_000,
    },
    "guardrails.budgets.max_tokens_per_day": {
        "type": "int",
        "min": 0,
        "max": 1_000_000_000,
    },
    "guardrails.budgets.max_dollars_per_day": {
        "type": "float",
        "min": 0.0,
        "max": 100_000.0,
    },
    "guardrails.breaker.failure_threshold": {"type": "int", "min": 1, "max": 100},
    "guardrails.breaker.recovery_secs": {"type": "float", "min": 0.0, "max": 3600.0},
    "guardrails.scan_mode": {"type": "enum", "values": ["warn", "redact", "block"]},
    "routing.enabled": {"type": "bool"},
    "routing.local_timeout_secs": {"type": "float", "min": 0.0, "max": 600.0},
    "routing.min_samples": {"type": "int", "min": 1, "max": 10_000},
    "routing.hysteresis": {"type": "float", "min": 0.0, "max": 1.0},
    "routing.cloud_quality_margin": {"type": "float", "min": 0.0, "max": 1.0},
    "routing.energy_sampling": {"type": "bool"},
    "routing.reproposal_cooldown_days": {"type": "int", "min": 0, "max": 365},
    "guardrails.autonomy.clean_approvals": {"type": "int", "min": 1, "max": 1000},
    "guardrails.autonomy.min_days": {"type": "int", "min": 0, "max": 365},
    "guardrails.autonomy.max_rejections": {"type": "int", "min": 0, "max": 100},
    "guardrails.autonomy.cooldown_days": {"type": "int", "min": 0, "max": 365},
    "guardrails.autonomy.evidence_window_days": {"type": "int", "min": 1, "max": 365},
    "voice.confirmation_phrases": {"type": "str_list", "max_items": 20},
    "voice.exit_phrases": {"type": "str_list", "max_items": 20},
    "voice.echo_filter_enabled": {"type": "bool"},
    "voice.duplex_mute_enabled": {"type": "bool"},
    "voice.clean_for_speech_enabled": {"type": "bool"},
    "voice.voice_disclaimer_enabled": {"type": "bool"},
    "voice.push_to_talk_chord": {
        "type": "str",
        "max_len": 64,
        "sanitize": _push_to_talk_chord_sanitizer,
    },
    "resilience.doctor_enabled": {"type": "bool"},
    "resilience.degraded_indicator": {"type": "bool"},
    "resilience.mid_turn_policy": {
        "type": "enum",
        "values": ["queue", "steer", "cancel_and_replace"],
    },
    "resilience.cancel_replace_min_interval_secs": {
        "type": "float",
        "min": 0.0,
        "max": 60.0,
    },
    "resilience.remediation.enabled": {"type": "bool"},
    "resilience.remediation.target_score": {"type": "int", "min": 0, "max": 100},
    "resilience.remediation.max_cost_usd": {"type": "float", "min": 0.0, "max": 100.0},
    "resilience.remediation.idle_minutes_healthy": {
        "type": "int",
        "min": 1,
        "max": 1440,
    },
    "resilience.remediation.tick_minutes_degraded": {
        "type": "int",
        "min": 1,
        "max": 1440,
    },
    "durability.auto_backup": {"type": "bool"},
    "durability.keep_daily": {"type": "int", "min": 0, "max": 365},
    "durability.keep_weekly": {"type": "int", "min": 0, "max": 260},
    "durability.keep_monthly": {"type": "int", "min": 0, "max": 120},
    "durability.restore_drills": {"type": "bool"},
    "durability.time_travel": {"type": "bool"},
    "durability.sync_enabled": {"type": "bool"},
    "durability.sync_transport": {"type": "str", "max_len": 64},
    "durability.sync_stale_after_secs": {"type": "int", "min": 30, "max": 86400},
    "durability.sync_encrypt": {
        "type": "str",
        "max_len": 8,
        "values": ("auto", "on", "off"),
    },
    "evals.enabled": {"type": "bool"},
    "evals.study_default_k": {"type": "int", "min": 1, "max": 50},
    "evals.judge_agreement_floor": {"type": "float", "min": 0.0, "max": 1.0},
    "evals.ablation_cadence_days": {"type": "int", "min": 1, "max": 365},
    "evals.default_budget_usd": {"type": "float", "min": 0.0, "max": 1000.0},
    "proactive.triage_enabled": {"type": "bool"},
    "proactive.digest_schedule": {"type": "str", "max_len": 64},
    "proactive.auto_execute_enabled": {"type": "bool"},
    "proactive.max_auto_actions_per_run": {"type": "int", "min": 0, "max": 50},
    "proactive.classifier_gate_enabled": {"type": "bool"},
    "proactive.decision_default_horizon_days": {"type": "int", "min": 1, "max": 3650},
    "tools.projection_rules": {"type": "projection_rules"},
    "tools.bg_compress_enabled": {"type": "bool"},
    "tools.bg_compress_idle_days": {"type": "float", "min": 0.0, "max": 365.0},
    "tools.groups_enabled": {"type": "bool"},
    "external_access.enabled": {"type": "bool"},
    "external_access.openai.enabled": {"type": "bool"},
    "external_access.mcp.enabled": {"type": "bool"},
    "external_access.a2a.enabled": {"type": "bool"},
    "external_access.capture.enabled": {"type": "bool"},
    "external_access.bridge.enabled": {"type": "bool"},
    "external_access.rate_rps": {"type": "float", "min": 0.01, "max": 1000.0},
    "external_access.rate_burst": {"type": "int", "min": 1, "max": 10000},
    "external_access.rate_concurrent": {"type": "int", "min": 1, "max": 256},
    "external_access.auto_disable_after_breaches": {
        "type": "int",
        "min": 0,
        "max": 10000,
    },
    "external_access.capture_retention_days": {"type": "int", "min": 0, "max": 3650},
    "external_access.capture.retention_days": {"type": "int", "min": 0, "max": 3650},
    "external_access.capture.upstream_allowlist": {"type": "str_list", "max_items": 40},
    "memory.graph_enabled": {"type": "bool"},
    "memory.push_context": {"type": "bool"},
    "memory.push_min_confidence": {"type": "float", "min": 0.0, "max": 1.0},
    "memory.graph_topology_in_context": {"type": "bool"},
    "memory.holder_attribution": {"type": "bool"},
    "memory.slot_size_cap": {"type": "int", "min": 200, "max": 4000},
    "memory.history_idle_hours": {"type": "float", "min": 0.5, "max": 8760.0},
    "memory.history_max_days": {"type": "int", "min": 7, "max": 3650},
    "memory.l1_manifest": {"type": "bool"},
    "memory.active_recall": {"type": "bool"},
    "memory.proactive_commitments": {"type": "bool"},
    "memory.vault_mode": {"type": "enum", "values": list(MEMORY_VAULT_MODES)},
    "memory.vault_path": {
        "type": "str",
        "max_len": 256,
        "sanitize": lambda v: v.strip() or "memory-vault",
    },
    "feedback.enabled": {"type": "bool"},
    "feedback.retire_threshold": {"type": "float", "min": 0.1, "max": 0.9},
    "feedback.min_n": {"type": "int", "min": 3, "max": 50},
    "feedback.window_days": {"type": "int", "min": 7, "max": 365},
    "planning.scratchpad_path": {
        "type": "str",
        "max_len": 512,
        "sanitize": _scratchpad_path_sanitizer,
    },
    "agents_routing.enabled": {"type": "bool"},
    "agents_routing.min_confidence": {"type": "float", "min": 0.3, "max": 0.95},
    "agents_routing.cooldown_hours": {"type": "float", "min": 0.0, "max": 720.0},
    "agent.orchestrator_skill": {"type": "bool"},
    "agent.acp_concurrent_sessions": {"type": "bool"},
    "agent.unattended_requires_verified_adapter": {"type": "bool"},
    "agent.runner_health_check_secs": {"type": "int", "min": 60, "max": 86_400},
    "agent.runner_idle_release_secs": {"type": "int", "min": 60, "max": 86_400},
    "agent.durable_sessions": {"type": "bool"},
    "agent.prompt_cache_enabled": {"type": "bool"},
    # template var + PromptAssembler). Sanitized at the write boundary (strip
    "agent.bot_name": {"type": "str", "max_len": 50, "sanitize": _bot_name_sanitizer},
    "agent.log_level": {
        "type": "enum",
        "values": ["DEBUG", "INFO", "WARNING", "ERROR"],
    },
    "agent.self_qa.enabled": {"type": "bool"},
    "agent.self_qa.watched_repo": {"type": "str", "max_len": 512},
    "agent.self_qa.fix_branch_enabled": {"type": "bool"},
    "agent.self_qa.max_scenarios_per_fire": {"type": "int", "min": 1, "max": 20},
    "session.timeout_secs": {"type": "int", "min": 0, "max": 86400},
    "session.autocompact_pct": {"type": "float", "min": 5.0, "max": 90.0},
    "session.pool_size": {"type": "int", "min": 0, "max": 10},
    "session.pool_agent": {"type": "str", "values_fn": _agent_values},
    "session.pool_ttl_secs": {"type": "int", "min": 0, "max": 7200},
    "session.auto_archive_days": {"type": "int", "min": 0, "max": 3650},
    "auto_update": {"type": "bool"},
    "updates.channel": {"type": "enum", "values": ["stable", "beta", "nightly"]},
    "updates.pin": {"type": "str", "max_len": 64},
    "updates.auto": {"type": "enum", "values": ["off", "staged"]},
    "updates.check_enabled": {"type": "bool"},
    "updates.check_interval_hours": {"type": "int", "min": 1, "max": 168},
    "updates.last_version": {"type": "str", "max_len": 64},
    "dashboard.mcp_probe_timeout_secs": {"type": "int", "min": 5, "max": 120},
    "dashboard.screen_share_enabled": {"type": "bool"},
    "dashboard.document_editing": {"type": "bool"},
    "dashboard.terminal.persist": {"type": "bool"},
    "loops.judge_use_case": {
        "type": "enum",
        "values": [
            "reasoning",
            "chat",
            "code_tools",
            "background",
            "orchestration",
            "loops",
        ],
    },
    "loops.stagnation_window": {"type": "int", "min": 2, "max": 50},
    "loops.check_work_stages": {"type": "bool"},
    "loops.worktree_sparse": {"type": "bool"},
    "inbox.engagement_ranking_enabled": {"type": "bool"},
    "inbox.engagement_half_life_days": {"type": "float", "min": 0.0, "max": 365.0},
    "inbox.enabled": {"type": "bool"},
    "workflows.enabled": {"type": "bool"},
    "workflows.max_active_runs": {"type": "int", "min": 1, "max": 100},
    "workflows.self_schedule_max_outstanding": {"type": "int", "min": 0, "max": 200},
    "workflows.max_concurrent_nodes": {"type": "int", "min": 1, "max": 64},
    "workflows.default_node_timeout_total_secs": {
        "type": "int",
        "min": 0,
        "max": 86400,
    },
    "workflows.default_node_timeout_stall_secs": {
        "type": "int",
        "min": 0,
        "max": 86400,
    },
    "workflows.retention_per_def": {"type": "int", "min": 1, "max": 10000},
    "workflows.max_concurrent_llm_nodes": {"type": "int", "min": 1, "max": 32},
    "workflows.max_concurrent_io_nodes": {"type": "int", "min": 1, "max": 32},
    "workflows.model_tier_reasoning": {"type": "str", "max_len": 32},
    "workflows.model_tier_standard": {"type": "str", "max_len": 32},
    "workflows.model_tier_fast": {"type": "str", "max_len": 32},
    "workflows.match_threshold": {"type": "float", "min": 0.0, "max": 1.0},
    "workflows.surface_mode_default": {
        "type": "enum",
        "values": ["off", "passive", "suggest"],
    },
    "workflows.max_materialized_per_foreach": {"type": "int", "min": 1, "max": 500},
    "workflows.confirmation_ttl_secs": {"type": "int", "min": 0, "max": 30 * 24 * 3600},
    "workflows.lease_ttl_secs": {"type": "int", "min": 30, "max": 3600},
    "workflows.default_quiet_windows": {"type": "str", "max_len": 64},
    "workflows.duty_gate_default": {"type": "str", "max_len": 64},
    "workflows.workspace_default_mode": {
        "type": "enum",
        "values": ["scratch", "worktree", "in_place", "container"],
    },
    "workflows.workspace_teardown_on_expiry": {"type": "bool"},
    "learning.min_evidence": {"type": "int", "min": 1, "max": 20},
    "learning.min_lesson_confidence": {"type": "float", "min": 0.0, "max": 1.0},
    "learning.staging_enabled": {"type": "bool"},
    "learning.self_model_enabled": {"type": "bool"},
    "learning.min_session_score": {"type": "float", "min": 0.0, "max": 1.0},
    "learning.propose_quota_per_run": {"type": "int", "min": 1, "max": 25},
    "learning.curator_enabled": {"type": "bool"},
    "learning.replay_enabled": {"type": "bool"},
    "learning.replay_max_dollars": {"type": "float", "min": 0.0, "max": 25.0},
    "learning.context_budget_tokens": {"type": "int", "min": 500, "max": 100000},
    "learning.run_end_enabled": {"type": "bool"},
    "learning.attribution_enabled": {"type": "bool"},
    "learning.identity_report_cadence": {
        "type": "enum",
        "values": ["monthly", "weekly", "off"],
    },
    "knowledge.idempotent_persist": {"type": "bool"},
    "knowledge.require_citations": {"type": "bool"},
    "knowledge.report_budget_chars": {"type": "int", "min": 1000, "max": 500000},
    "knowledge.max_mentions_per_claim": {"type": "int", "min": 1, "max": 200},
    "knowledge.synthesis_window": {"type": "int", "min": 1, "max": 200},
    "knowledge.lint_every_n_persists": {"type": "int", "min": 1, "max": 1000},
    "knowledge.maintenance_max_staleness_secs": {
        "type": "int",
        "min": 60,
        "max": 86400,
    },
    "knowledge.vault_mode": {"type": "enum", "values": ["off", "mirror", "two_way"]},
    "knowledge.vault_path": {"type": "str", "max_len": 512},
    "knowledge.similarity_min_score": {"type": "float", "min": 0.05, "max": 1.0},
    "knowledge.similarity_top_k": {"type": "int", "min": 1, "max": 64},
    "knowledge.similarity_degree_cap": {"type": "int", "min": 1, "max": 512},
    "knowledge.reranker_enabled": {"type": "bool"},
    "knowledge.reranker_model": {"type": "str", "max_len": 256},
    "knowledge.reranker_max_candidates": {
        "type": "int",
        "min": 1,
        "max": 128,
    },
    "knowledge.embed_batch_size": {"type": "int", "min": 1, "max": 2048},
    "knowledge.embed_retry_budget": {"type": "int", "min": 1, "max": 10},
    "knowledge.consolidate_min_cluster": {"type": "int", "min": 2, "max": 100},
    "knowledge.consolidate_min_hours": {"type": "int", "min": 0, "max": 720},
    "knowledge.session_brief_max_tokens": {"type": "int", "min": 0, "max": 8000},
    "knowledge.conflict_model_pass": {"type": "bool"},
    "knowledge.auto_ingest_artifacts": {"type": "bool"},
    "auth.login_enabled": {"type": "bool"},
    "auth.require_totp": {"type": "bool"},
    "auth.session_ttl": {"type": "duration"},
    "auth.lockout_threshold": {"type": "int", "min": 1, "max": 100},
    "auth.lockout_window": {"type": "duration"},
    "legibility.discover_tips": {"type": "bool"},
    "legibility.context_adapters": {"type": "bool"},
    "ambient.tiles_enabled": {"type": "bool"},
    "ambient.max_tiles": {"type": "int", "min": 1, "max": 48},
    "ambient.default_refresh_ttl_secs": {"type": "int", "min": 30, "max": 86400},
    "ambient.genui_enabled": {"type": "bool"},
    "ambient.surfaces_max_layer": {"type": "int", "min": 0, "max": 2},
    "ambient.tray_enabled": {"type": "bool"},
    "companion.discovery_enabled": {"type": "bool"},
    "companion.instance_name": {"type": "str", "max_len": 64},
    "browse.user_browser_enabled": {"type": "bool"},
    "mobile.push_backend": {"type": "enum", "values": list(PUSH_BACKENDS)},
    "mobile.ntfy_topic_url": {"type": "https_url", "max_len": 512},
    "local_models.pressure_warn_pct": {"type": "int", "min": 1, "max": 100},
    "local_models.sidecar_restart_max": {"type": "int", "min": 0, "max": 20},
    "local_models.memory_reserve_gb": {"type": "float", "min": 0.0, "max": 64.0},
    "local_models.hide_unrunnable_models": {"type": "bool"},
    "local_models.hf_whoami_ttl_secs": {"type": "int", "min": 0, "max": 86_400},
    "local_models.selftest_timeout_secs": {"type": "int", "min": 1, "max": 600},
    "sources.enabled": {"type": "bool"},
    "sources.poll_interval_default_secs": {"type": "int", "min": 300, "max": 604800},
    "sources.network_floor_secs": {"type": "int", "min": 300, "max": 604800},
    "sources.max_sources": {"type": "int", "min": 1, "max": 1000},
    "sources.max_items_per_poll": {"type": "int", "min": 1, "max": 1000},
    "sources.daily_request_budget": {"type": "int", "min": 1, "max": 100000},
    "packs.fingerprint_enabled": {"type": "bool"},
    "packs.connector_catalog_url": {"type": "str", "max_len": 512},
    "packs.skill_catalogs": {"type": "skill_catalogs"},
    "apps.registry_source_enabled": {"type": "bool"},
    "apps.bundled_source_enabled": {"type": "bool"},
}


async def api_gideon_config_patch(request: web.Request) -> web.Response:
    """PATCH /api/config/gideon — update a single config field."""
    from gideon.core.config.loader import config_path  # noqa: F811

    caller = request.get("user")
    if not caller:
        logger.warning(
            "config.patch called without authenticated user; falling back to 'dashboard'"
        )
        caller = "dashboard"

    def _log_sel(outcome: str, resources: str) -> None:
        _sel().log_api_access(
            caller=caller,
            operation="config.patch",
            outcome=outcome,
            source="dashboard",
            resources=resources,
        )

    def _deny(msg: str, resources: str = "", status: int = 400) -> web.Response:
        _log_sel("denied", resources or msg)
        return web.json_response({"error": msg}, status=status)

    try:
        body = await request.json()
    except Exception:
        return _deny("invalid JSON", "invalid JSON body")
    if not isinstance(body, dict):
        return _deny("JSON body must be an object", "non-dict body")

    path_key = body.get("path", "")
    value = body.get("value")
    spec = _EDITABLE_CONFIG.get(path_key)
    if not spec:
        return _deny(f"field not editable: {path_key}", f"{path_key}={value}")

    try:
        value = coerce_edit_value(path_key, value, spec)
    except ConfigValueError as exc:
        return _deny(str(exc), exc.resources, exc.status)

    cfg_path = config_path()
    from gideon.interfaces.dashboard.handlers.agents import (  # noqa: F811
        _get_config_lock,
    )

    async with _get_config_lock():
        try:
            data = (
                json.loads(cfg_path.read_text(encoding="utf-8"))
                if cfg_path.exists()
                else {}
            )
        except Exception:
            _log_sel("error", f"{path_key}=read_failed")
            return web.json_response(
                {"error": "failed to read config file"}, status=500
            )

        parts = path_key.split(".")
        cursor = data
        for seg in parts[:-1]:
            child = cursor.setdefault(seg, {})
            if not isinstance(child, dict):
                _log_sel("error", f"{path_key}=section_not_dict")
                return web.json_response(
                    {"error": f"config section '{seg}' is not an object"}, status=500
                )
            cursor = child
        cursor[parts[-1]] = value

        try:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(cfg_path, json.dumps(data, indent=2) + "\n", fsync=True)
        except OSError:
            _log_sel("error", f"{path_key}=write_failed")
            return web.json_response(
                {"error": "failed to write config file"}, status=500
            )

    _log_sel("success", f"{path_key}={value}")

    if path_key == "agent.log_level":
        try:
            from gideon.interfaces.dashboard.handlers.updates import (  # noqa: F811
                apply_log_level,
            )

            apply_log_level(value)
        except Exception:
            logger.warning(
                "Failed to apply log level live after config patch", exc_info=True
            )

    if path_key == "agent.yolo":
        state = request.app.get("state")
        if state is None:
            logger.warning(
                "agent.yolo patched with no dashboard state; live apply skipped"
            )
        elif value:
            try:
                from gideon.security.sel import sel

                sel().log_api_access(
                    caller="dashboard:config",
                    operation="mode_change:yolo",
                    outcome="enabled",
                    resources="config:agent.yolo",
                )
            except Exception:
                logger.error(
                    "SEL audit failed; config saved but YOLO NOT enabled live "
                    "(matches the startup path's refusal)",
                    exc_info=True,
                )
            else:
                state.enable_yolo(from_config=True)
        else:
            state.disable_yolo()
            try:
                from gideon.security.sel import sel

                sel().log_api_access(
                    caller="dashboard:config",
                    operation="mode_change:yolo",
                    outcome="disabled",
                    resources="config:agent.yolo",
                )
            except Exception:
                logger.warning(
                    "SEL audit failed for YOLO disable via config patch", exc_info=True
                )

    if path_key == "agent.orchestrator_skill":
        try:
            from gideon.extensions.skills import ProcedureLibrary  # noqa: F811

            if value:
                from gideon.interfaces.dashboard.handlers.agents import (  # noqa: F811
                    _regen_orchestrator,
                )

                _regen_orchestrator()
            else:
                for legacy in ("orchestrator", "conductor"):
                    generated = ProcedureLibrary()._dir / legacy / "SKILL.md"
                    if generated.is_file():
                        generated.unlink()
        except Exception:
            logger.exception("Failed to apply orchestrator skill toggle")

    if path_key in ("companion.discovery_enabled", "companion.instance_name"):
        try:
            from gideon.integrations.companion import (  # noqa: F811
                discovery as _discovery,
            )

            _discovery.reconcile()
        except Exception:
            logger.exception("Failed to apply the LAN discovery setting")

    if path_key.startswith("agent.self_qa."):
        try:
            from gideon.assurance.selfqa.install import reconcile as _reconcile_selfqa
            from gideon.automation.triggers.store import TriggerStore as _TriggerStore
            from gideon.core.config.loader import config_dir as _config_dir

            _reconcile_selfqa(_TriggerStore(base_dir=_config_dir()))
        except Exception:
            logger.exception("Failed to apply the Self-QA companion setting")

    if path_key == "tools.projection_rules":
        try:
            from gideon.integrations.tool_providers import projection  # noqa: F811

            projection.set_user_rules(
                [
                    projection.ProjectionRule(
                        name=r.get("name", ""),
                        match_regex=r.get("match_regex", ""),
                        strategy=r.get("strategy", "log"),
                        head=int(r.get("head", 0) or 0),
                        tail=int(r.get("tail", 0) or 0),
                        keep=str(r.get("keep", "") or ""),
                        skip=str(r.get("skip", "") or ""),
                        count=str(r.get("count", "") or ""),
                    )
                    for r in (value or [])
                ]
            )
        except Exception:
            logger.exception("Failed to live-apply projection rules")

    cfg = AppConfig.load()
    return web.json_response(cfg.to_dict())


async def api_incident(request: web.Request) -> web.Response:
    """GET /api/incident — current state; POST /api/incident — activate.

    POST body: ``{reason?: str}``. Activation is SEL-audited and suspends all
    unattended work within one poll interval; interactive chat is untouched.
    """
    from gideon.security.guardrails import incident as _incident

    if request.method == "GET":
        st = _incident.get_incident()
        return web.json_response(
            {"active": st.active, "reason": st.reason, "started_at": st.started_at}
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = str(body.get("reason", "")) if isinstance(body, dict) else ""
    st = _incident.activate(reason)
    return web.json_response(
        {"active": st.active, "reason": st.reason, "started_at": st.started_at}
    )


async def api_incident_resume(request: web.Request) -> web.Response:
    """POST /api/incident/resume — turn incident mode OFF.

    Resume is EXPLICIT: requires ``{confirm: true}`` so a stray request can't
    silently re-enable unattended work. SEL-audited.
    """
    from gideon.security.guardrails import incident as _incident

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not (isinstance(body, dict) and body.get("confirm") is True):
        return web.json_response(
            {"error": 'resume requires {"confirm": true}'}, status=400
        )
    st = _incident.resume()
    return web.json_response({"active": st.active})


async def api_project_trust(request: web.Request) -> web.Response:
    """GET /api/guardrails/project-trust — the whole store;
    POST /api/guardrails/project-trust — record a Trust/Preview decision.

    POST body: ``{dir: str, trusted: bool}``. ``trusted=true`` is the explicit **Trust**
    (the folder may run/write project scripts); ``trusted=false`` keeps **Preview**
    (read-only). Recording is SEL-audited and keyed by the RESOLVED directory.
    """
    from gideon.security.guardrails import project_trust as _pt

    if request.method == "GET":
        return web.json_response({"projects": _pt._read_store()})
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    directory = str(body.get("dir", "") or "").strip()
    if not directory:
        return web.json_response({"error": "dir is required"}, status=400)
    trusted = body.get("trusted")
    if not isinstance(trusted, bool):
        return web.json_response({"error": "trusted must be a boolean"}, status=400)
    record = _pt.record_project_trust(directory, trusted=trusted)
    return web.json_response({"dir": _pt.resolve_dir(directory), **record})


async def api_models_health(request: web.Request) -> web.Response:
    """GET /api/models/health — derived per-provider health (breaker state, latency
    percentiles, failure-mode distribution) from the model-call audit + breakers.

    Derived, not collected: reads ``model_calls.jsonl`` + in-memory breaker state,
    no telemetry infrastructure."""
    from gideon.security.guardrails.health import provider_health

    return web.json_response(await asyncio.to_thread(provider_health))


async def api_token_local(request: web.Request) -> web.Response:
    """GET /api/token/local — issue a token for local apps.

    Requires a per-session secret written to ~/.gideon/.local_secret at
    gateway startup. Only processes on the same machine can read the file.
    Secret passed via ``X-Local-Secret`` header (not query string, to avoid
    leaking in logs).
    """
    import gideon.interfaces.dashboard.handlers as _h  # noqa: F811

    if not _h.is_loopback(request.remote or ""):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="token.local",
            outcome="denied",
            source="local-bootstrap",
            resources="non-loopback",
        )
        return web.json_response({"error": "loopback only"}, status=403)

    expected = request.app.get("local_secret", "")
    if not expected:
        return web.json_response({"error": "not available"}, status=503)
    provided = request.headers.get("X-Local-Secret", "")
    if not provided or not hmac.compare_digest(expected, provided):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="token.local",
            outcome="denied",
            source="local-bootstrap",
            resources="invalid-secret",
        )
        return web.json_response({"error": "invalid secret"}, status=403)
    ttl = MAX_SESSION_TTL_SECS
    ttl_param = request.query.get("ttl", "")
    if ttl_param:
        parsed = parse_duration(ttl_param)
        if parsed:
            ttl = parsed
    token = generate_token("local-app", ttl_seconds=ttl)
    _sel().log_api_access(
        caller=request.remote or "unknown",
        operation="token.local",
        outcome="success",
        source="local-bootstrap",
        resources="token-issued",
    )
    return web.json_response({"token": token, "expires_in": ttl})


async def api_session_agents_list(request: web.Request) -> web.Response:
    """GET /api/sessions/{id}/agents — list sub-agent results for a session."""
    session_id = request.match_info["id"]
    from gideon.engine.session_workspace import list_results  # noqa: F811

    results = list_results(session_id)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="session.agents.list",
        outcome="ok",
        source="dashboard",
        resources=session_id,
    )
    return web.json_response({"results": results})


async def api_session_agent_result(request: web.Request) -> web.Response:
    """GET /api/sessions/{id}/agents/{agent_id} — read sub-agent result."""
    session_id = request.match_info["id"]
    agent_id = request.match_info["agent_id"]
    from gideon.engine.session_workspace import read_result  # noqa: F811

    content = read_result(session_id, agent_id)
    if not content:
        return web.json_response({"error": "not found"}, status=404)
    from gideon.security.security import redact_exfiltration_urls  # noqa: F811
    from gideon.security.security import redact_credentials

    content, _ = redact_exfiltration_urls(content)
    content, _ = redact_credentials(content)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="session.agent.result",
        outcome="ok",
        source="dashboard",
        resources=f"{session_id}/{agent_id}",
    )
    return web.json_response({"agent_id": agent_id, "content": content})


async def api_session_agent_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/sessions/{id}/agents/{agent_id}/stream — SSE stream of result file."""
    session_id = request.match_info["id"]
    agent_id = request.match_info["agent_id"]
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="session.agent.stream",
        outcome="ok",
        source="dashboard",
        resources=f"{session_id}/{agent_id}",
    )
    from gideon.engine.session_workspace import result_path  # noqa: F811

    path = result_path(session_id, agent_id)
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    await resp.prepare(request)

    last_pos = 0
    from gideon.security.security import redact_exfiltration_urls  # noqa: F811
    from gideon.security.security import redact_credentials

    for _ in range(1200):
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if len(content) > last_pos:
                    chunk = content[last_pos:]
                    last_pos = len(content)
                    chunk, _ = redact_exfiltration_urls(chunk)
                    chunk, _ = redact_credentials(chunk)
                    await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            state: ConsoleState = request.app["state"]
            if state.subagents:
                info = state.subagents.get(agent_id)
                if info and info.done:
                    await resp.write(b"event: done\ndata: {}\n\n")
                    break
        except (ConnectionResetError, ClientConnectionResetError):
            break
        await asyncio.sleep(1)
    return resp


async def api_logout(request: web.Request) -> web.Response:
    """POST /api/logout — revoke all active dashboard sessions.

    Called by ``gideon logout`` CLI. Requires loopback + local secret
    (same auth as /api/token/local) to prevent unauthorized revocation.
    """
    import gideon.interfaces.dashboard.handlers as _h  # noqa: F811
    from gideon.interfaces.dashboard.token_auth import revoke_all_sessions  # noqa: F811

    if not _h.is_loopback(request.remote or ""):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="logout",
            outcome="denied",
            source="cli",
            resources="non-loopback",
        )
        return web.json_response({"error": "loopback only"}, status=403)

    expected = request.app.get("local_secret", "")
    provided = request.headers.get("X-Local-Secret", "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="logout",
            outcome="denied",
            source="cli",
            resources="invalid-secret",
        )
        return web.json_response({"error": "invalid secret"}, status=403)

    revoke_all_sessions()
    _sel().log_api_access(
        caller=request.remote or "unknown",
        operation="logout",
        outcome="success",
        source="cli",
        resources="all-sessions-revoked",
    )
    return web.json_response({"ok": True})
