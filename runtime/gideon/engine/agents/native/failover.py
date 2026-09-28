"""Bounded turn-local fallback over the models a user configured."""

from dataclasses import dataclass

from gideon.integrations.llm.base import ModelSubstitution
from gideon.security.guardrails.failure import FailureMode

FAILOVER_MODES = frozenset(
    {FailureMode.PROVIDER_ERROR, FailureMode.TIMEOUT, FailureMode.CIRCUIT_OPEN}
)


@dataclass(frozen=True)
class ModelFailover:
    requested: str
    who: str
    fix: str = ""

    def substitution(
        self, served: str, failures: list[tuple[str, str]]
    ) -> ModelSubstitution:
        _first_ref, first_reason = failures[0]
        why = f"it failed before it replied ({first_reason})"
        for ref, reason in failures[1:]:
            why += f", and so did {ref} ({reason})"
        return ModelSubstitution(
            requested=self.requested,
            served=served,
            why=why,
            who=self.who,
            fix=self.fix,
        )


class NoModelAnswered(RuntimeError):
    """The preferred model and every compatible configured fallback failed."""

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        self.failures = list(failures)
        if not failures:
            message = "No configured model answered this turn. Check Settings → Models."
        else:
            ref, reason = failures[0]
            message = f"{ref} failed before it replied ({reason})"
            for fallback_ref, fallback_reason in failures[1:]:
                message += f", and so did {fallback_ref} ({fallback_reason})"
            message = (
                f"None of this chat's models answered: {message}. Try again in a moment, "
                "or check them in Settings → Models."
            )
        super().__init__(message)

    def sentence(self, *, room_member: str = "") -> str:
        text = str(self)
        if not room_member or not self.failures:
            return text
        ref, reason = self.failures[0]
        tried = f"{ref} failed before it replied ({reason})"
        for fallback_ref, fallback_reason in self.failures[1:]:
            tried += f", and so did {fallback_ref} ({fallback_reason})"
        return (
            f"None of {room_member}'s models answered: {tried}. Try again in a moment, "
            f"or give the {room_member} agent a different model on the Agents page."
        )
