"""Context decision for an existing due commitment before delivery."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitmentPlan:
    action: str
    reason: str
    channel: str = ""


def plan_commitment(item: dict, *, posture: str, dashboard_available: bool, context: dict | None = None) -> CommitmentPlan:
    text = str(item.get("text") or "").strip()
    if not text:
        return CommitmentPlan("silence", "empty commitment")
    recent = (context or {}).get("recent_sessions") or []
    if any(str(row.get("text") or "").strip().casefold() == text.casefold()
           and str(row.get("created_at") or "") >= str(item.get("due_window") or "")
           for row in recent if isinstance(row, dict)):
        return CommitmentPlan("silence", "already covered in a recent session")
    if posture != "allowed":
        return CommitmentPlan("defer", "notification settings or quiet hours")
    channel = str(item.get("channel") or "dashboard").strip()
    if channel == "background":
        return CommitmentPlan("defer", "background work requires a reviewed proposal")
    if channel == "dashboard" or channel.startswith("dashboard:"):
        if not dashboard_available:
            return CommitmentPlan("defer", "dashboard unavailable")
        return CommitmentPlan("contribute", "deliver to the dashboard conversation", channel)
    return CommitmentPlan("notify", "deliver to the selected channel", channel)
