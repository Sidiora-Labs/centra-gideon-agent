"""Slack spawn, automation, and recent-session commands."""

import time
from collections.abc import Callable

from slack_desk_runtime.client import SlackDeskClientOps

from gideon.sdk.channel import (
    ConversationLog,
)
from gideon.sdk.channel import DelegationSupervisor as SubagentManager
from gideon.sdk.channel import (
    TriggerStore,
    delete_all_automations,
    delete_automation,
    describe_cadence,
    redact_credentials,
    redact_exfiltration_urls,
    sel,
    set_automation_paused,
    to_schedule_row,
)


def _remove_all_jobs(store: TriggerStore) -> str:
    """Remove every automation the ASSISTANT created, and return a summary.

    Scoped to `created_by="agent"` by the SDK helper. The old version removed EVERYTHING the
    scheduler held, including the automations the user built by hand — `cron remove all` from a chat
    message could wipe them with no confirmation step. The scoped delete is what the core
    `automation_delete_all` tool enforces, and this command now inherits it rather than re-deriving a
    broader blast radius.
    """
    rows = [r for r in store.load() if r.trigger.created_by == "agent"]
    if not rows:
        return "No agent-created automations to remove."
    lines = [f"- `{r.trigger.id}` — {r.trigger.name}" for r in rows]
    result = delete_all_automations(store, created_by="agent", confirm=True)
    if not result.ok:
        return f"⚠️ {result.text}"
    return f"✅ Removed {len(lines)} automation(s):\n" + "\n".join(lines)


def _handle_spawn_command(
    text: str, manager: SubagentManager, session_key: str = ""
) -> str | None:
    """Intercept spawn/bg keyword commands. Returns reply or None."""
    t = text.strip()
    low = t.lower()

    for prefix in ("spawn ", "bg "):
        if low.startswith(prefix):
            return _do_spawn(t[len(prefix) :].strip(), manager, session_key)
    return None


def _do_spawn(task: str, manager: SubagentManager, session_key: str = "") -> str | None:
    """Execute a spawn command. Returns reply string."""
    if not task:
        return None

    # "spawn list" / "spawn status"
    if task.lower() in ("list", "status"):
        running = manager.running
        if not running:
            return "No subagents running."
        lines = ["*Running subagents:*"]
        for a in running:
            elapsed = int(time.time() - a.started)
            lines.append(f"🔹 `{a.id}` | {elapsed}s | {a.task[:60]}")
        return "\n".join(lines)

    info = manager.spawn(task, parent_session_key=session_key)
    if not info:
        return (
            f"⚠️ Subagent capacity reached ({manager.max_concurrent}). Try again later."
        )
    return f"🚀 Spawned subagent `{info.id}`\n_{task[:100]}_"


def _relative_next_run(nxt: float | None, now: float) -> str:
    """ "⏭ in 2h 15m" for a next-run timestamp, or "" when there is none."""
    if nxt is None:
        return ""
    delta = nxt - now
    if delta >= 86400:
        rel = f"in {int(delta // 86400)}d {int((delta % 86400) // 3600)}h"
    elif delta >= 3600:
        rel = f"in {int(delta // 3600)}h {int((delta % 3600) // 60)}m"
    elif delta > 0:
        minutes = int(delta // 60)
        rel = f"in {minutes}m" if minutes >= 1 else "in <1m"
    else:
        rel = "now"
    return f" | ⏭ {rel}"


def _handle_cron_command(
    text: str,
    store: TriggerStore,
    channel: str,
    thread_ts: str,
    *,
    redact_urls: Callable[[str], tuple[str, list[str]]],
    redact_secrets: Callable[[str], tuple[str, list[str]]],
) -> str | None:
    """Handle cron keyword commands against the unified trigger store. Returns reply or None.

    🔴 Re-pointed off `ScheduleService`, which core deleted (S112). That service read
    `crons.json` — a file nothing has written since core's S108 — so **every one of these commands
    was already broken**: `cron list` showed an empty list to a user with live automations, and
    remove/pause/resume answered "not found" for every real id. Measured against a store-only home
    before re-pointing.

    Reads the same store, projection and tool functions the Automations page and the `automation_*`
    chat tools use, so a `/cron` reply can no longer disagree with the UI.

    The surface is unchanged for the user, with two honest improvements the store makes possible:
    a BROKEN automation is listed with its parse error rather than silently omitted, and every kind
    is visible (a file watch or an event trigger used to be invisible here because the legacy
    scheduler only held clocks).
    """
    t = text.strip().lower()
    parts = t.split()

    if len(parts) < 2 or parts[0] != "cron":
        return None

    action = parts[1]

    if action == "list":
        rows = store.load()
        if not rows:
            return "No automations scheduled."
        lines = ["*Your automations:*"]
        now = time.time()
        for row in rows:
            trigger = row.trigger
            # A row that failed to parse is SHOWN with its reason. The legacy list could not
            # represent one at all, and silently omitting an automation the user created is how
            # "where did my automation go" happens.
            if not row.ok:
                reason = row.errors[0].message if row.errors else "invalid"
                lines.append(f"⚠️ `{trigger.id}` | {reason}")
                continue
            view = to_schedule_row(trigger)
            status = "✅" if trigger.enabled else "⏸️"
            last = ""
            if view.get("last_status") == "ok":
                last = " ✓"
            elif view.get("last_status") in ("error", "failure", "timeout"):
                last = " ❌"
            safe_msg, _ = redact_secrets(redact_urls(str(view.get("message") or ""))[0])
            next_part = _relative_next_run(view.get("next_run_ts"), now)
            lines.append(
                f"{status} `{trigger.id}` | `{describe_cadence(trigger)}` "
                f"| {safe_msg[:50]}{last}{next_part}"
            )
        return "\n".join(lines)

    if len(parts) < 3:
        return None

    job_id = parts[2]

    if action == "remove":
        if job_id == "all":
            return _remove_all_jobs(store)
        # `confirm=True`: the flag exists so a TOOL CALL cannot delete by accident. A user who typed
        # `cron remove <id>` has already expressed the intent.
        if delete_automation(store, trigger_id=job_id, confirm=True).ok:
            return f"✅ Removed automation `{job_id}`"
        return f"❌ Automation `{job_id}` not found"

    if action == "pause":
        if set_automation_paused(store, trigger_id=job_id, paused=True).ok:
            return f"⏸️ Paused automation `{job_id}`"
        return f"❌ Automation `{job_id}` not found"

    if action == "resume":
        result = set_automation_paused(store, trigger_id=job_id, paused=False)
        if result.ok:
            return f"▶️ Resumed automation `{job_id}`"
        # `set_paused` REFUSES to resume a row with a parse error and NAMES it — strictly more useful
        # than "not found", and the row does exist, so "not found" was wrong as well as unhelpful.
        return f"❌ {result.text}"

    return None


async def _handle_sessions_command(
    cmd_text: str,
    slack_desk: SlackDeskClientOps,
    channel: str,
    reply_ts: str,
    msg_ts: str,
    session_key: str,
    conversation_log: ConversationLog | None,
) -> None:
    """Handle ``!sessions`` — list recent sessions as task_card blocks with resume buttons."""
    import json as _json
    from pathlib import Path

    sess_dir = Path.home() / ".gideon" / "sessions"
    if not sess_dir.exists():
        await slack_desk.post_message(channel, "_No recent sessions._", reply_ts)
        return

    max_msg_chars = 4000
    sessions: list[dict] = []
    for jsonl in sess_dir.glob("*.jsonl"):
        if jsonl.is_symlink():
            continue
        key = jsonl.stem
        if key.startswith("dashboard_"):
            key = "dashboard:" + key[len("dashboard_") :]
        try:
            lines = jsonl.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue

        title = key
        agent = "gideon"
        msgs: list[tuple[str, str]] = []
        mtime = jsonl.stat().st_mtime

        for line in lines:
            try:
                d = _json.loads(line.strip())
            except (ValueError, _json.JSONDecodeError):
                continue
            if d.get("_type") == "metadata":
                title = d.get("title") or title
                agent = d.get("agent") or agent
                continue
            role = d.get("role", "")
            txt = (d.get("content") or "")[:max_msg_chars]
            if role in ("user", "assistant") and txt:
                msgs.append((role, txt))

        sessions.append(
            {
                "key": key,
                "title": title[:80],
                "agent": agent,
                "mtime": mtime,
                "msgs": msgs[-5:],
            }
        )

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    sessions = sessions[:10]

    sel().log_api_access(
        caller=session_key,
        operation="slack.sessions_data_access",
        outcome="allowed",
        source="slack",
        resources=f"{len(sessions)} sessions read",
    )

    if not sessions:
        await slack_desk.post_message(channel, "_No recent sessions._", reply_ts)
        return

    blocks: list[dict] = []
    for i, s in enumerate(sessions):
        rt_items: list[dict] = []
        for role, txt in s["msgs"]:
            txt, _ = redact_exfiltration_urls(txt)
            txt, _ = redact_credentials(txt)
            emoji_name = "bust_in_silhouette" if role == "user" else "robot_face"
            rt_items.append(
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "emoji", "name": emoji_name},
                        {"type": "text", "text": f" {txt}"},
                    ],
                }
            )

        _title, _ = redact_exfiltration_urls(s["title"])
        _title, _ = redact_credentials(_title)
        task: dict = {
            "type": "task_card",
            "task_id": f"session_{i}",
            "title": f"{_title} — {s['agent']} agent",
            "status": "complete",
        }
        if rt_items:
            task["details"] = {
                "type": "rich_text",
                "elements": [
                    {"type": "rich_text_list", "style": "bullet", "elements": rt_items}
                ],
            }
        blocks.append(task)
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "\u25b6\ufe0f Resume"},
                        "action_id": f"pc_session_resume_{s['key']}",
                        "value": _json.dumps({"key": s["key"], "title": s["title"]}),
                    }
                ],
            }
        )
        if i < len(sessions) - 1:
            blocks.append({"type": "divider"})

    await slack_desk.post_blocks(channel, blocks, "Recent sessions:", reply_ts)
