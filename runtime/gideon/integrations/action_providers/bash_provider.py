"""Screen commands, construct a constrained environment, and own their process group."""

import asyncio
import json
import logging
import re
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import (
    ActionClock,
    CommandProcess,
    action_timeout,
)

logger = logging.getLogger(__name__)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PROTECTED_ENV_NAMES: frozenset[str] = frozenset(
    {
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "BASH_ENV",
        "ENV",
        "SHELL",
        "IFS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PERL5LIB",
        "NODE_OPTIONS",
        "NODE_PATH",
        "RUBYOPT",
        "RUBYLIB",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_EXTERNAL_DIFF",
        "HOME",
        "TMPDIR",
        "GIDEON_HOME",
        "GIDEON_WORKSPACE",
        "GIDEON_HOOK_EVENT",
        "GIDEON_HOOK_CONTEXT",
    }
)


def _payload_env(ctx: ActionContext) -> dict[str, str]:
    exported = dict(EVENT=ctx.event, CONTEXT=ctx.context)
    payload = ctx.payload or {}
    for name in payload:
        if name in PROTECTED_ENV_NAMES:
            logger.warning(
                "trigger payload key %r would override a protected environment variable; ignoring it",
                name,
            )
        elif _ENV_NAME.match(name):
            exported.update({name: str(payload[name])})
    return exported


def _sel_refusal(command: str, reason: str, ctx: ActionContext) -> None:
    try:
        import uuid
        from datetime import datetime, timezone

        from gideon.security.sel import SecurityEvent, SecurityEventLog

        fields: dict = dict(
            event_id=uuid.uuid4().hex[:16],
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="action_refused",
            caller_identity="",
            agent="gideon",
            source="action_provider",
            operation="bash_action_screened",
            tool_kind="execute_bash",
            outcome="denied",
            resources=reason,
            metadata=dict(
                provider="bash", event=getattr(ctx, "event", ""), command=command[:400]
            ),
        )
        audit = SecurityEventLog()
        audit.log(SecurityEvent(**fields))
    except Exception:
        logger.warning(
            "bash action refused (%s) but the SEL row failed", reason, exc_info=True
        )


class BashActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "bash"

    @property
    def display_name(self) -> str:
        return "Bash Command"

    @property
    def supports_blocking(self) -> bool:
        return True

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        command = (action_config.get("command") or "").strip()
        if not command:
            return ActionResult(False, error="Bash hook is missing 'command' field")
        from gideon.security import security

        denial = security.is_sensitive_bash_command(command)
        if denial:
            _sel_refusal(command, denial, ctx)
            return ActionResult(False, error=denial)
        deadline = action_timeout(action_config, timeout)
        from gideon.security.sandbox import (
            PROFILE_TOOL,
            build_child_env,
            create_subprocess_limited,
            wrap_argv,
        )

        clock = ActionClock()
        variables = _payload_env(ctx)
        variables.update(GIDEON_HOOK_EVENT=ctx.event, GIDEON_HOOK_CONTEXT=ctx.context)
        environment = build_child_env(site="bash-action", extra=variables)
        argv, disposable = wrap_argv(["/bin/sh", "-c", command])
        owned = CommandProcess(disposable)
        try:
            owned.process = await create_subprocess_limited(
                *argv,
                profile=PROFILE_TOOL,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                start_new_session=True,
                **({"cwd": ctx.execution_cwd} if ctx.execution_cwd else {}),
            )
            output = await owned.capture(json.dumps(ctx.payload).encode(), deadline)
            status = owned.process.returncode or 0
            decoded = [stream.decode(errors="replace").strip() for stream in output]
            return clock.result(
                status == 0,
                exit_code=status,
                stdout=decoded[0],
                stderr=decoded[1],
                blocked=status == 2,
            )
        except asyncio.TimeoutError:
            await owned.stop()
            return clock.result(False, error=f"Timed out after {deadline}s")
        except Exception as error:
            await owned.stop()
            return clock.result(False, error=str(error))
        finally:
            await owned.close()


def create_provider(config: dict[str, Any] | None = None) -> BashActionProvider:
    return BashActionProvider()
