"""Resolve recurring prompt sources and admit unattended turns through live services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.cognition.autonomous_framing import with_autonomous_framing
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.services import (
    get_action_services,
    validate_spawn_cwd,
)

logger = logging.getLogger(__name__)
LOOP_MD_NAME = "loop.md"
_LOOP_MD_MAX = 16_000


@dataclass(frozen=True)
class _PromptDocument:
    content: str
    label: str

    @classmethod
    def read(cls, path: Path, label: str) -> _PromptDocument | None:
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            return None
        if not text.strip():
            return None
        bounded = text[:_LOOP_MD_MAX]
        if len(text) > _LOOP_MD_MAX:
            bounded += "\n…[loop.md truncated]"
        return cls(bounded, label)


def resolve_loop_md(cwd: str | None) -> tuple[str, str] | None:
    locations = {f"project:{cwd}": Path(cwd) / LOOP_MD_NAME} if cwd else {}
    try:
        from gideon.core.config.loader import config_dir

        locations["user"] = config_dir() / LOOP_MD_NAME
    except Exception:
        logger.debug("loop.md: config_dir lookup failed", exc_info=True)
    for label, path in locations.items():
        document = _PromptDocument.read(path, label)
        if document is not None:
            return document.content, document.label
    return None


def render_saved_prompt(prompt_id: str, values: dict[str, Any] | None) -> str:
    from gideon.integrations.prompt_providers import (
        get_default_provider,
        render_template,
    )
    from gideon.integrations.prompt_providers.base import PromptRenderError

    source = get_default_provider()
    if source is None:
        raise LookupError("no prompt provider is registered")
    template = source.get_prompt(prompt_id)
    if template is None:
        raise LookupError(f"no saved prompt named {prompt_id!r}")
    arguments = dict(resolver=source.get_snippet)
    try:
        rendered = render_template(template, values or {}, **arguments)
    except PromptRenderError as error:
        raise ValueError(str(error)) from error
    return rendered


def _select_prompt(
    prompt_id: str, cwd: str, values: dict | None
) -> _PromptDocument | ActionResult:
    if prompt_id:
        try:
            content = render_saved_prompt(prompt_id, values)
        except LookupError as error:
            return ActionResult(False, error=f"run-prompt: {error}")
        except ValueError as error:
            return ActionResult(
                False, error=f"run-prompt: failed to render {prompt_id!r}: {error}"
            )
        if not content.strip():
            return ActionResult(
                False, error=f"run-prompt: prompt {prompt_id!r} rendered empty"
            )
        return _PromptDocument(content, f"prompt {prompt_id!r}")
    local = resolve_loop_md(cwd)
    if local is None:
        return ActionResult(
            False,
            error="run-prompt has no 'prompt_id' and no loop.md was found (looked for a project loop.md in cwd, then a user loop.md)",
        )
    content, location = local
    return _PromptDocument(content, f"loop.md ({location})")


@dataclass(frozen=True)
class _PromptLaunch:
    arguments: dict[str, Any]

    @classmethod
    def prepare(
        cls, config: dict[str, Any], ctx: ActionContext, task: str, cwd: str
    ) -> _PromptLaunch:
        agent = (config.get("agent") or "").strip()
        model = (config.get("model") or "").strip() or None
        try:
            turns = int(config.get("max_turns", 0) or 0)
        except (ValueError, TypeError):
            turns = 0
        pinned = str(config.get("session") or "").strip()
        parent = pinned or str((ctx.payload or {}).get("session_key", "") or "")
        capability = str(config.get("capability") or "").strip().lower() or None
        if cwd:
            from gideon.security.guardrails.project_trust import gate_project_capability

            capability = gate_project_capability(cwd, capability)
        return cls(
            dict(
                task=task,
                parent_session_key=parent,
                agent=agent,
                max_turns=turns,
                model=model,
                cwd=cwd,
                approval_mode="auto",
                capability_class=capability,
                silent=False,
                dry_run=bool(config.get("dry_run", False)),
            )
        )

    async def dispatch(self, services: Any) -> None:
        try:
            services.subagents.spawn(**self.arguments)
        except Exception:
            logger.warning("run-prompt: spawn failed", exc_info=True)

    def schedule(self, services: Any) -> None:
        pending = self.dispatch(services)
        try:
            services.spawn_background(pending)
        except BaseException:
            pending.close()
            raise


class RunPromptActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "run-prompt"

    @property
    def display_name(self) -> str:
        return "Run Prompt"

    @property
    def supports_dry_run(self) -> bool:
        return True

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        reference = str(action_config.get("prompt_id") or "").strip()
        cwd = (action_config.get("cwd") or "").strip()
        values = action_config.get("vars")
        if values in (None, "", {}):
            values = None
        elif not isinstance(values, dict):
            return ActionResult(False, error="run-prompt 'vars' must be an object")
        document = _select_prompt(reference, cwd, values)
        if isinstance(document, ActionResult):
            return document
        framed = with_autonomous_framing(document.content)
        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(False, error="run-prompt: subagent manager unavailable")
        refused = validate_spawn_cwd(cwd)
        if refused:
            return ActionResult(False, error=f"run-prompt: {refused}")
        _PromptLaunch.prepare(action_config, ctx, framed, cwd).schedule(services)
        return ActionResult(
            True, stdout=f"launched {document.label}", outcome="launched"
        )


def create_provider(config: dict[str, Any] | None = None) -> RunPromptActionProvider:
    return RunPromptActionProvider()
