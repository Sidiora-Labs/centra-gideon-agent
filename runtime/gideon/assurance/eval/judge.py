"""LLM Judge — scores agent responses via a separate agent session."""

import json
import logging
from dataclasses import dataclass
from typing import Any

from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    ModelProvider,
)
from gideon.security.sel import sel

logger = logging.getLogger(__name__)


@dataclass
class JudgeVerdict:
    score: float
    reason: str
    reasoning: str = ""


class LLMJudge:
    def __init__(
        self,
        provider_factory: Any,
        prompt_template: str | None = None,
        pass_threshold: float = 3.0,
    ):
        self._factory = provider_factory
        self._prompt_template = prompt_template
        self.pass_threshold = pass_threshold
        self._provider: ModelProvider | None = None

    async def start(self) -> None:
        from gideon.integrations.llm_helpers import execute_with_fallback_chain

        async def _start(provider: ModelProvider) -> ModelProvider:
            try:
                await provider.start()
            except Exception:
                try:
                    await provider.shutdown()
                except Exception:
                    pass
                raise
            return provider

        self._provider = await execute_with_fallback_chain(
            "chat",
            _start,
            provider_factory=self._factory,
            session_key="eval_judge",
        )

    async def shutdown(self) -> None:
        if self._provider:
            await self._provider.shutdown()

    async def judge_turn(
        self, description: str, criteria: str, user_msg: str, assistant_msg: str
    ) -> JudgeVerdict:
        if self._provider is None:
            raise RuntimeError("LLMJudge.start() must be called before judge_turn()")
        values = {
            "scenario_description": description,
            "criteria": criteria,
            "user_message": user_msg,
            "assistant_response": assistant_msg,
        }
        if self._prompt_template is not None:
            prompt = self._prompt_template.format(**values)
        else:
            from gideon.integrations.prompt_providers.runtime import (
                render_use_case_prompt,
            )

            prompt = render_use_case_prompt("eval_judge", values) or ""
        chunks: list[str] = []
        async for event in self._provider.stream(prompt):
            if event.kind == EVENT_TEXT_CHUNK:
                chunks.append(event.text)
            elif event.kind == EVENT_PERMISSION_REQUEST:
                if not event.request_id:
                    logger.warning(
                        "Judge received permission request with falsy request_id for tool %s",
                        event.title,
                    )
                sel().log_tool_invocation(
                    session_key="eval_judge",
                    tool_name=event.title,
                    outcome="rejected",
                    source="eval_judge",
                )
                if event.request_id:
                    await self._provider.reject_tool(event.request_id)
            elif event.kind == EVENT_COMPLETE:
                break
        raw = "".join(chunks)
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            data = json.loads(raw[start:end])
            return JudgeVerdict(
                score=float(data["score"]),
                reason=data.get("reason", ""),
                reasoning=str(data.get("reasoning", "")).strip(),
            )
        except (ValueError, KeyError, json.JSONDecodeError):
            logger.warning("Judge returned unparseable response: %s", raw[:200])
            return JudgeVerdict(score=0, reason=f"parse_error: {raw[:100]}")
