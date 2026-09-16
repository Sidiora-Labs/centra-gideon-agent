"""Message lifecycle records with validated, timestamped transitions."""

import enum
import time
from dataclasses import dataclass, field


class TaskState(enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
_TRANSITIONS: "dict[TaskState, set[TaskState]]" = {
    TaskState.PENDING: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
    TaskState.IN_PROGRESS: {
        TaskState.AWAITING_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.AWAITING_APPROVAL: {
        TaskState.IN_PROGRESS,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
}


class InvalidTransition(Exception):  # noqa: N818
    """A requested edge is absent from the message lifecycle."""


@dataclass(frozen=True)
class _TransitionPlan:
    destination: TaskState
    starts_clock: bool
    ends_clock: bool

    @classmethod
    def prepare(cls, task: "Task", destination: TaskState) -> "_TransitionPlan":
        if destination not in _TRANSITIONS.get(task.state, ()):
            raise InvalidTransition(f"{task.state.value} -> {destination.value}")
        return cls(
            destination,
            destination is TaskState.IN_PROGRESS and task.started_at is None,
            destination in _TERMINAL,
        )

    def apply(self, task: "Task") -> None:
        task.state = self.destination
        instant = time.monotonic()
        for field_name, enabled in (
            ("started_at", self.starts_clock),
            ("finished_at", self.ends_clock),
        ):
            if enabled:
                setattr(task, field_name, instant)


@dataclass
class Task:
    id: str
    state: "TaskState" = TaskState.PENDING
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL

    def transition(self, to: "TaskState") -> None:
        _TransitionPlan.prepare(self, to).apply(self)

    def start(self) -> None:
        self.transition(TaskState.IN_PROGRESS)

    def complete(self) -> None:
        self.transition(TaskState.COMPLETED)

    def fail(self, error: str = "") -> None:
        self.transition(TaskState.FAILED)
        self.error = error

    def cancel(self) -> None:
        self.transition(TaskState.CANCELLED)

    def await_approval(self) -> None:
        self.transition(TaskState.AWAITING_APPROVAL)

    def resume(self) -> None:
        self.transition(TaskState.IN_PROGRESS)
