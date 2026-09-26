"""Reusable Block Kit builders for Gideon slash commands.

All functions return raw Block Kit dicts (no slack_sdk dependency).
Action IDs follow the pc_<command>_<action>[_<id>] convention.
"""


def command_hint_block(command: str, description: str) -> dict:
    """Single section block showing a command and its description."""
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"`{command}` — {description}"},
    }


def dashboard_link_block(url: str, link_mins: int, session_mins: int) -> list[dict]:
    """Section with clickable dashboard link and expiry info."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"🔗 <{url}|*Open Dashboard*>\n"
                    f"⏱ Click within {link_mins}m · session lasts {session_mins}m"
                ),
            },
        },
    ]


def deprecation_warning_block(old_cmd: str, new_cmd: str) -> dict:
    """Context block warning that a bang command is deprecated."""
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"⚠️ `{old_cmd}` is deprecated. Use `{new_cmd}` instead.",
            }
        ],
    }


def voice_config_modal(
    tts_enabled: bool = False,
    auto_speak: bool = False,
    length_scale: float = 1.0,
) -> dict:
    """Modal view for voice/TTS behavior (callback_id: pc_voice_config).

    Only behavior toggles + speaking speed live here. The *voice* (which Piper
    ``.onnx``) is the ``tts`` selection in Settings → Models.
    """
    _enabled_opt = {
        "text": {"type": "plain_text", "text": "Enable TTS voice replies"},
        "value": "enabled",
    }
    _auto_opt = {
        "text": {"type": "plain_text", "text": "Auto-speak every response"},
        "value": "auto_speak",
    }

    initial_checks = []
    if tts_enabled:
        initial_checks.append(_enabled_opt)
    if auto_speak:
        initial_checks.append(_auto_opt)

    blocks: list[dict] = [
        {
            "type": "input",
            "block_id": "tts_enabled_block",
            "optional": True,
            "element": {
                "type": "checkboxes",
                "action_id": "pc_voice_tts_enabled",
                "options": [_enabled_opt, _auto_opt],
                **({"initial_options": initial_checks} if initial_checks else {}),
            },
            "label": {"type": "plain_text", "text": "Text-to-Speech"},
        },
        {
            "type": "input",
            "block_id": "piper_length_scale_block",
            "optional": True,
            "element": {
                "type": "plain_text_input",
                "action_id": "pc_voice_piper_length_scale",
                "placeholder": {"type": "plain_text", "text": "1.0"},
                "initial_value": str(length_scale),
            },
            "label": {"type": "plain_text", "text": "Speaking speed (<1 faster, >1 slower)"},
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "Pick the voice model in the dashboard → Settings → Models (`tts`).",
            }],
        },
    ]

    return {
        "type": "modal",
        "callback_id": "pc_voice_config",
        "title": {"type": "plain_text", "text": "Voice Settings"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def allowlist_list_block(user_ids: list[str]) -> list[dict]:
    """List allowed users with per-user remove buttons."""
    if not user_ids:
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": "_No users on the allowlist._"}}
        ]
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Allowlist* ({len(user_ids)} users)"},
        },
    ]
    for uid in sorted(user_ids):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<@{uid}>"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🚫 Remove"},
                    "action_id": f"pc_allowlist_remove_{uid}",
                    "value": uid,
                    "style": "danger",
                },
            }
        )
    return blocks


def channel_list_block(channel_ids: list[str]) -> list[dict]:
    """List tracked channels with per-channel remove buttons."""
    if not channel_ids:
        return [{"type": "section", "text": {"type": "mrkdwn", "text": "_No tracked channels._"}}]
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Tracked Channels* ({len(channel_ids)})"},
        },
    ]
    for cid in sorted(channel_ids):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<#{cid}>"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🚫 Remove"},
                    "action_id": f"pc_channel_remove_{cid}",
                    "value": cid,
                    "style": "danger",
                },
            }
        )
    return blocks


# ---------------------------------------------------------------------------
# Channels management modal
# ---------------------------------------------------------------------------

_ACTIVATION_OPTIONS = [
    ("always", "⚡ always", "Respond to every message"),
    ("mention", "🔔 mention", "Only when @mentioned"),
    ("observe", "👀 observe", "Record all, respond when @mentioned"),
    ("review", "📋 review", "Draft for approval before posting"),
    ("off", "🔇 off", "Ignore completely"),
]


def channels_modal(
    channels: list[dict],
    agent_names: list[str] | None = None,
) -> dict:
    """Modal for managing tracked channels with per-channel activation and agent.

    *channels*: ``[{"channel_id": "C...", "activation": "mention", "agent": ""}, ...]``
    *agent_names*: available agent names from ~/.gideon/agents/
    """
    blocks: list[dict] = []
    agents = agent_names or []

    if not channels:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No tracked channels yet._"},
        })
    else:
        for ch in channels:
            cid = ch["channel_id"]
            cur = ch.get("activation", "mention")
            cur_agent = ch.get("agent", "")
            # Row 1: channel name + remove button
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<#{cid}>"},
                "accessory": {
                    "type": "button",
                    "action_id": f"pc_ch_remove_{cid}",
                    "text": {"type": "plain_text", "text": "✕ Remove"},
                    "style": "danger",
                    "value": cid,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Remove channel?"},
                        "text": {"type": "mrkdwn", "text": f"Stop tracking <#{cid}>?"},
                        "confirm": {"type": "plain_text", "text": "Remove"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
            })
            # Row 2: activation mode + agent selector
            opts = [
                {
                    "text": {"type": "plain_text", "text": label},
                    "description": {"type": "plain_text", "text": desc},
                    "value": val,
                }
                for val, label, desc in _ACTIVATION_OPTIONS
            ]
            initial = next((o for o in opts if o["value"] == cur), opts[1])
            elements: list[dict] = [{
                "type": "static_select",
                "action_id": f"pc_ch_activation_{cid}",
                "initial_option": initial,
                "options": opts,
            }]
            if agents:
                agent_opts = [
                    {"text": {"type": "plain_text", "text": "🤖 default"}, "value": "__default__"},
                ] + [
                    {"text": {"type": "plain_text", "text": n[:75]}, "value": n}
                    for n in agents
                ]
                agent_initial = next(
                    (o for o in agent_opts if o["value"] == (cur_agent or "__default__")),
                    agent_opts[0],
                )
                elements.append({
                    "type": "static_select",
                    "action_id": f"pc_ch_agent_{cid}",
                    "initial_option": agent_initial,
                    "options": agent_opts,
                })
            blocks.append({
                "type": "actions",
                "block_id": f"pc_ch_actions_{cid}",
                "elements": elements,
            })
            blocks.append({"type": "divider"})

    # Add channel picker at the bottom (conversations_select includes private channels)
    blocks.append({
        "type": "actions",
        "block_id": "pc_ch_add_block",
        "elements": [{
            "type": "conversations_select",
            "action_id": "pc_ch_add",
            "placeholder": {"type": "plain_text", "text": "➕ Add a channel…"},
            "filter": {
                "include": ["public", "private"],
                "exclude_bot_users": True,
                "exclude_external_shared_channels": True,
            },
        }],
    })

    return {
        "type": "modal",
        "callback_id": "pc_channels_modal",
        "title": {"type": "plain_text", "text": "Tracked Channels"},
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# Review mode: ephemeral draft blocks + edit modal
# ---------------------------------------------------------------------------

# Action ID prefix for review mode buttons
REVIEW_ACTION_APPROVE = "pc_review_approve"
REVIEW_ACTION_EDIT = "pc_review_edit"
REVIEW_ACTION_REVISE = "pc_review_revise"
REVIEW_ACTION_CANCEL = "pc_review_cancel"


def build_stopping_blocks(session_key: str) -> list[dict]:
    """Ephemeral 'Stopping…' message with a Kill Now escalation button."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⏹  *Stopping…*\nCooperative cancel in progress."},
        },
        {
            "type": "actions",
            "block_id": f"stop-actions-{session_key}",
            "elements": [
                {
                    "type": "button",
                    "action_id": "stop_kill_now",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Kill Now"},
                    "value": session_key,
                }
            ],
        },
    ]


def build_stopped_blocks() -> list[dict]:
    """Resolved ephemeral: cooperative cancel succeeded."""
    return [{"type": "section", "text": {"type": "mrkdwn", "text": "⏹  *[Stopped]*"}}]


def build_stop_failed_blocks() -> list[dict]:
    """Resolved ephemeral: cooperative cancel failed, session was hard-killed."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⛔  *[Stop Failed, Session Reset]*"},
        }
    ]


def review_draft_blocks(draft_text: str, draft_key: str) -> list[dict]:
    """Build Block Kit blocks for an ephemeral review-mode draft.

    The *draft_key* (channel|thread_ts|uuid) is encoded in button values so the
    interaction handler can look up the draft and know where to post.
    """
    # Slack blocks text limit is 3000 chars; truncate with indicator
    display = draft_text[:2950] + "…" if len(draft_text) > 3000 else draft_text
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "📝 *Draft response:*"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": display},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": "pc_review_actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": REVIEW_ACTION_APPROVE,
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "value": draft_key,
                },
                {
                    "type": "button",
                    "action_id": REVIEW_ACTION_EDIT,
                    "text": {"type": "plain_text", "text": "✏️ Manual Override"},
                    "value": draft_key,
                },
                {
                    "type": "button",
                    "action_id": REVIEW_ACTION_REVISE,
                    "text": {"type": "plain_text", "text": "🤖 Refine with AI"},
                    "value": draft_key,
                },
                {
                    "type": "button",
                    "action_id": REVIEW_ACTION_CANCEL,
                    "text": {"type": "plain_text", "text": "❌ Cancel"},
                    "style": "danger",
                    "value": draft_key,
                },
            ],
        },
    ]


def review_edit_modal(draft_text: str, draft_key: str) -> dict:
    """Modal for editing a review-mode draft before posting."""
    return {
        "type": "modal",
        "callback_id": "pc_review_edit_submit",
        "private_metadata": draft_key,
        "title": {"type": "plain_text", "text": "Manual Override"},
        "submit": {"type": "plain_text", "text": "Post"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "pc_review_edit_block",
                "label": {"type": "plain_text", "text": "Response"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "pc_review_edit_input",
                    "multiline": True,
                    "initial_value": draft_text[:3000],
                },
            }
        ],
    }


def review_revise_modal(draft_key: str) -> dict:
    """Modal for providing LLM feedback to revise a review-mode draft."""
    return {
        "type": "modal",
        "callback_id": "pc_review_revise_submit",
        "private_metadata": draft_key,
        "title": {"type": "plain_text", "text": "Refine with AI"},
        "submit": {"type": "plain_text", "text": "Revise"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "pc_review_revise_block",
                "label": {"type": "plain_text", "text": "What should I change?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "pc_review_revise_input",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "e.g. make it shorter, add timeline dates, softer tone...",
                    },
                },
            }
        ],
    }
