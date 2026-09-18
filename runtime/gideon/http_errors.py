"""The ONE wire error emitter, and the append-only registry of wire codes.

`AGENTS.md` §"Shared conventions" → **Error envelope (HTTP)** owns this shape:
a failed API route answers ``{"error": {"code": "<stable_snake_code>", "message":
"<human>"}}``, and ``code`` is *append-only and never reworded once shipped* — a
stable surface an agent (or a saved SOP, or a browser client) branches on.

**Why this module exists.** The declaration above shipped long before anything
could check it: thirteen module-local ``_err``/``_error``/``_bad_request``
helpers each re-derived the envelope, four of them emitted a *flat*
``{"error": "<prose>"}`` carrying no code at all, and their signatures disagreed
in argument ORDER (``_err(code, message, status)``, ``_err(message, code,
status)`` and ``_err(code, status)`` all shipped), so a copy-paste between two
handlers silently swapped the machine code and the human message. There was also
no registry of wire codes and no rail, so "append-only" was unverifiable.
:func:`json_error` is the single emitter that replaced all thirteen, and
:data:`HTTP_ERROR_CODES` is the registry the rail
(``tests/test_http_error_codes_append_only.py``) checks.

**No positional ambiguity, by construction.** ``code`` is the ONLY positional
parameter. ``message``, ``status`` and everything else are keyword-only, so the
copy-paste hazard that motivated this module cannot recur: there is no second
positional slot for a code and a message to swap between.

**Two envelopes, deliberately distinct — do not merge them.** This module owns
the *wire* envelope (``lowercase_snake`` codes; what a branching HTTP client
reads). :mod:`gideon.core.errors` owns :class:`~gideon.core.errors.AgentError`
(``ERR_UPPER_SNAKE`` codes; the carrier *into an LLM session*, on
``ToolResult``/``ActionResult``). The two vocabularies never overlap by
construction — the case of the code tells you which surface you are on — and the
rails on both registries assert that disjointness. A third, transport-independent
vocabulary (``WF_UPPER_SNAKE``, the workflows *service-result* codes) is
translated into this one by ``workflows/handlers.py``'s ``_STATUS_MAP``; it is not
a wire vocabulary and does not belong here.

**Success envelopes are out of scope.** The same convention says success bodies
imitate the neighboring handler and are *not* standardized retroactively.
"""

from __future__ import annotations

from typing import Any, Mapping

from aiohttp import web

HTTP_ERROR_CODES: dict[str, str] = {
    "bad_request": "The request was malformed or carried an unusable parameter.",
    "invalid_request": "The request was well-formed JSON but failed validation.",
    "invalid_json": "The request body is not valid JSON.",
    "invalid_body": "The request body is valid JSON but not the expected object.",
    "invalid_id": "A record id is not a single path segment (separators, '..' or over-long).",
    "invalid_path": "The path is not one the dashboard may touch.",
    "invalid_name": "A file or directory name is not a single safe path segment "
    "(separators, '..' or over-long).",
    "not_found": "The addressed resource does not exist.",
    "forbidden": "The caller is not permitted to touch this resource.",
    "confirmation_required": "The operation is destructive and needs an explicit confirm.",
    "model_unresolved": (
        "No model provider resolves for the use case this route needs — no provider is "
        "configured, or the bound one is absent. Connect a model in Settings → Models."
    ),
    "doctor_disabled": "The Doctor surface is turned off on this instance.",
    "unknown_capability": "No capability with that name is registered with the Doctor.",
    "confirm_required": 'The operation needs an explicit {"confirm": true} in the body.',
    "unknown_fix": "No Doctor fix with that id exists.",
    "text_required": "A query text is required.",
    "import_failed": "A snapshot import failed. The gateway log carries the failure detail.",
    "restore_failed": "A restore attempt failed. The gateway log carries the failure detail.",
    "api_version_unsupported": (
        "The client's declared API version is outside the window this gateway supports."
    ),
    "auth_not_enabled": "Owner authentication is not enabled on this instance.",
    "auth_invalid_credentials": "The submitted credential did not verify.",
    "auth_origin_not_allowed": "The request origin is not allowed on this instance.",
    "auth_locked_out": "Too many failed attempts from this address; try again later.",
    "auth_totp_required": "A second factor is required to finish this login.",
    "auth_enroll_code_invalid": "The enrollment code did not verify.",
    "device_pair_code_invalid": "The pairing code did not verify.",
    "device_pair_expired": "The pairing code has expired.",
    "device_pair_origin_rejected": "The request origin is not allowed to pair a device.",
    "device_pair_locked_out": "Too many failed pairing attempts; try again later.",
    "device_unknown": "No such paired device.",
    "browse_connector_loopback_only": "The browse connector is reachable over loopback only.",
    "browse_connector_unpaired": "Only a paired device may attach as the browse connector.",
    "browse_connector_endpoint_invalid": "The announced CDP page-target endpoint is missing "
    "or is not a loopback ws(s) URL.",
    "channel_trust_sender_unknown": "That sender is not on this channel's allowlist.",
    "push_subscription_invalid": "The push subscription is missing an https endpoint or its keys.",
    "push_not_subscribed": "That device has no push subscription.",
    "push_relay_registration_invalid": "The relay registration lacks a device id or token.",
    "push_relay_not_registered": "That device has no relay token registered.",
    "invalid_turn": "The addressed turn does not exist or is not rewindable.",
    "turn_running": "The turn is still executing; it cannot be rewound yet.",
    "session_not_found": "No such chat session.",
    "plan_session_missing": "The session has no plan in progress.",
    "step_id_required": "A step id is required.",
    "step_not_awaiting_review": "That plan step is not awaiting review.",
    "markdown_required": "A non-empty markdown body is required.",
    "comment_text_required": "A non-empty comment body is required.",
    "invalid_reasoning_effort": "The reasoning effort is not a short lowercase token.",
    "reasoning_effort_not_declared": (
        "The bound runtime did not declare that reasoning effort, so it cannot be honored."
    ),
    "audit_owner_only": "The audit trail is owner-only; an app-scoped token may not read it.",
    "invalid_cursor": "The pagination cursor is malformed.",
    "invalid_limit": "The limit parameter is out of range or not an integer.",
    "invalid_time_filter": "A since/until filter is not a recognized timestamp.",
    "unknown_filter": "The request names a filter this endpoint does not support.",
    "credentials_owner_only": "The credential store is owner-only; an app-scoped token may "
    "neither read where secrets live nor move them.",
    "migration_refused": "The keychain is not the active credential backend, so moving "
    "secrets out of .env would leave them nowhere. Nothing was changed.",
    "rollback_refused": "There is no pre-migration .env snapshot to roll back to.",
    "evals_disabled": "The evals surface is switched off in config.",
    "learning_disabled": "Learning is switched off in config, so there is nothing learned to "
    "report on.",
    "judge_bench_absent": "No judge benchmark artifact has been produced yet.",
    "judge_bench_unreadable": "The judge benchmark artifacts could not be read.",
    "studies_unreadable": "The pre-registered study artifacts could not be read.",
    "study_absent": "No study is registered under that id.",
    "ablation_absent": "No harness-ablation report has been produced yet.",
    "ablation_unreadable": "The ablation artifacts could not be read.",
    "retrieval_absent": "No retrieval-ablation report has been produced yet.",
    "retrieval_unreadable": "The retrieval benchmark artifacts could not be read.",
    "learning_benchmark_absent": "No skill-impact benchmark report has been produced yet.",
    "learning_benchmark_unreadable": "The skill-impact benchmark report could not be read.",
    "store_required": "The request must name one retrieval store (knowledge or memory).",
    "card_unavailable": "That retrieval store could not be read for labelling.",
    "store_mutated": "A read-only harness pass wrote to a store and was refused.",
    "labels_required": "The request carries no qrels label mapping.",
    "labels_rejected": "The submitted qrels labels were refused.",
    "field_metrics_unreadable": "The lab/field metric sources could not be read.",
    "pack_not_installed": "No such installed pack.",
    "pack_not_bundled": "The pack is not bundled with this build.",
    "pack_build_failed": "Building the pack artifact failed.",
    "pack_update_refused": "The pack update was refused.",
    "pack_has_no_roster": "The pack declares no agent roster.",
    "binding_key_required": "A binding key is required.",
    "binding_rejected": "The submitted binding was rejected.",
    "one_link_required": "A one-link target is required.",
    "one_link_rejected": "The submitted one-link target was rejected.",
    "project_not_found": "No such project.",
    "prompt_card_failed": "Rendering the prompt card failed.",
    "prompt_card_rejected": "The submitted prompt card was rejected.",
    "rejection_incomplete": "A rejection must carry a reason.",
    "triage_digest_unreadable": "The triage digest could not be read.",
    "triage_digest_expired": "That digest is no longer the current one, so its item numbers no longer address the items it listed.",  # noqa: E501
    "triage_schedule_write_failed": "The triage digest schedule could not be written.",
    "decision_journal_unreadable": (
        "The decision journal could not be read, so neither your decisions nor the calibration "
        "strip are shown. This is a failed read, NOT an empty journal — nothing you logged was "
        "lost, and the read changes nothing, so retrying is safe. Fix: the message names the "
        "underlying fault; `gideon doctor` checks the sqlite build the store needs."
    ),
    "research_reports_unavailable": "Scheduled research reports are not available in this build.",
    "consent_required": "The cloned voice's consent record is not verified.",
    "artifact_missing": "The addressed voice artifact does not exist.",
    "artifact_not_readable": "The voice artifact exists but is not readable.",
    "invalid_artifact": "The artifact path is not a recognized artifact reference.",
    "invalid_profile_id": "The voice profile id is malformed.",
    "path_escape": "The submitted path escapes the voice_profiles directory.",
    "invalid_seed": "The seed must be an integer.",
    "invalid_speed": "The speed must be a number.",
    "invalid_kind": "The voice profile kind is not one of the supported kinds.",
    "kind_immutable": "A voice profile's kind cannot be changed after creation.",
    "invalid_extension": "The audio file extension is not supported.",
    "name_required": "A non-empty name is required.",
    "consent_text_required": "Consent text is required for a cloned voice.",
    "empty_history": "The profile has no generation history.",
    "history_audio_missing": "That generation's audio file is gone.",
    "no_active_voice": "There is no active TTS voice selection to migrate.",
    "unauthorized": "The request carried no usable bearer credential.",
    "upstream_unavailable": "No upstream base URL could be resolved for this dialect.",
    "upstream_denied": "The resolved upstream is not on the operator's egress allow-list.",
    "upstream_failed": "The upstream call was attempted and did not complete.",
    "upstream_redirected": (
        "The allow-listed upstream answered a redirect. It is not followed, because the "
        "egress guard evaluated the original host and never saw the redirect target."
    ),
    "service_unavailable": "The service is temporarily suspended; retry later.",
    "unknown_action": "The named control-bridge action does not exist.",
    "action_not_bound": "The calling client's bindings do not include this action.",
    "action_failed": "The control-bridge action raised while running.",
    "confirm_token_invalid": "The confirmation token is unknown, already used, or expired.",
    "unknown_agent": "The requested model does not name an agent on this instance.",
    "agent_binding_violation": "This client is pinned to a different agent than it requested.",
    "empty_messages": "The request carried no message with content.",
    "turn_timeout": "The agent did not finish the turn within this surface's deadline.",
    "no_bound_voice": "No text-to-speech voice is selected on this instance.",
    "synthesis_failed": "Speech synthesis was attempted and raised.",
    "synthesis_empty": "Speech synthesis completed but produced no audio.",
    "missing_input": "The synthesis request carried no input text.",
    "stt_unavailable": "No speech-to-text model is installed on this instance.",
    "transcription_failed": "Transcription was attempted and raised.",
    "missing_file": "The upload carried no file field.",
    "invalid_content_type": "The request's Content-Type is not supported on this route.",
    "invalid_upload": "The multipart upload could not be parsed.",
    "method_not_allowed": "The HTTP method is not supported on this route.",
    "rate_limited": "The caller exceeded its request rate cap; see Retry-After.",
    "too_many_concurrent_requests": (
        "The caller already has the maximum number of requests in flight."
    ),
    "request_too_large": "The request body exceeds this surface's size cap.",
    "a2a_catalog_unavailable": "The published-workflow catalog could not be read.",
    "computer_use_refused": (
        "A computer-use call was refused — the out-of-band keystone is off, the target "
        "application is not on the operator's allowlist, the destination is a secure field, "
        "the element index is stale, or the request named no valid tool."
    ),
    "computer_use_unavailable": (
        "The computer-use call was permitted but no accessibility driver could run it on this "
        "platform, or the ceilinged driver subprocess failed. Nothing was changed on the "
        "desktop."
    ),
    "computer_use_view_owner_only": (
        "The desktop live view is readable by the owner only — an app-scoped token cannot "
        "watch what the agent is doing on the operator's desktop."
    ),
    "artifact_build_failed": (
        "Bundling the artifact's React source failed, so nothing was published. Fix: read the "
        "build message — it names the file and the reason."
    ),
    "artifact_slug_invalid": (
        "The artifact slug is not a usable directory name, so no served path could be built for "
        "it. Fix: rename the artifact to something slug-safe."
    ),
    "kind_not_binary": (
        "The artifact's kind stores its body as text, so it has no binary body to replace. "
        "Fix: PATCH the artifact instead."
    ),
    "if_match_required": (
        "A whole-body write must declare the version it is replacing via `If-Match`. Fix: read "
        "the artifact, then resend with `If-Match: <version>`."
    ),
    "if_match_malformed": (
        "The `If-Match` header is not an artifact version number. Fix: send the integer "
        "`version` the artifact reported."
    ),
    "version_conflict": (
        "The artifact moved since it was read, so the write would have destroyed somebody "
        "else's edit. Fix: reload and re-apply. The `error` object names the current version."
    ),
    "intent_id_taken": (
        "Another intent already covers this goal. Fix: edit that intent, or reword this "
        "goal so the two are distinguishable."
    ),
    "unknown_tag_id": "The request names a tag id that does not exist.",
    "unknown_folder_id": "The request names a folder id that does not exist.",
    "collection_name_taken": (
        "A shelf with that name already exists. Fix: open that shelf, or pick another name."
    ),
    "content_length_required": (
        "The request declared no `Content-Length`, so its size cannot be checked before the "
        "body is read. Fix: send a length-delimited body, not a chunked one."
    ),
    "mime_kind_mismatch": (
        "The body's `Content-Type` belongs to a different artifact kind than the addressed "
        "artifact. Fix: send the format this artifact already is, or create a new artifact."
    ),
    "unsupported_media_type": (
        "The body's `Content-Type` is not a binary artifact format this build can store."
    ),
    "model_unavailable": (
        "No document parser or writer ships for this artifact's kind, so it has no editable "
        "model. Fix: read the bytes via the raw route instead."
    ),
    "model_parse_failed": (
        "The stored bytes could not be parsed as a document of this artifact's kind."
    ),
    "invalid_model": (
        "The posted document model is not a valid model. The message names the offending path."
    ),
    "render_failed": "The document model was valid but the writer could not render it.",
    "document_editing_off": (
        "In-place document editing is off, so this document cannot be re-rendered. Fix: turn "
        "on Settings › Documents › 'Edit documents in place'."
    ),
    "trigger_id_required": "A trigger id is required to describe what an automation would do.",
    "unknown_trigger": "No automation exists with that id.",
    "onboarding_import_failed": "Scanning for or importing from another agent tool failed.",
    "local_model_scan_refused": (
        "The private-network scan was refused before anything was probed — a target is "
        "not a loopback/private address or CIDR, names too wide a range, or the request "
        "asks for more addresses than one scan may touch."
    ),
    "local_model_probe_failed": (
        "The endpoint did not answer as a live local model service, so no binding was "
        "offered or created."
    ),
    "local_model_bind_failed": "Writing the key-less local-model provider row failed.",
    "tool_disabled": "The tool is disabled on the Tools page and will not be executed.",
    "tts_disabled": (
        "Text-to-speech is switched off. Fix: turn on “Speak replies aloud” in "
        "Settings → Speech & Transcription."
    ),
    "capture_import_failed": "Staging an exported agent log into the capture store failed.",
    "secret_name_invalid": "A secret's name must look like an environment variable — letters, "
    "digits and underscores, not starting with a digit.",
    "secret_value_required": "Storing a secret needs a non-empty value; to remove one, "
    "use DELETE instead.",
    "secret_project_invalid": "The project id cannot be used to scope a secret — it must be a "
    "plain id without '__' in it.",
    "secret_absent": "No secret is stored under that name in the scope you asked for.",
    "secret_host_readonly": "That row is inherited from the host environment, so the vault "
    "cannot change or remove it — unset it where the gateway's environment is defined.",
    "note_text_empty": "A note needs some text. Type what you want to remember, then save.",
    "note_too_long": "That note is longer than the capture limit. Shorten it and save again.",
    "note_not_saved": (
        "The note could not be written to the inbox, so it was not kept. Your text is "
        "still in the compose box — try saving again."
    ),
    "invalid_field_type": "A request field carries a value of the wrong type for that field.",
    "workspace_dir_unsafe": (
        "The project's bound workspace directory is not a safe place to write generated agent "
        "files (a relative path, the home directory itself, a credential directory, or an "
        "OS/system root)."
    ),
    "provider_unreachable": (
        "A provider instance's endpoint or server could not be reached, so its connection "
        "test did not pass."
    ),
    "provider_config_invalid": (
        "A provider instance's configuration is incomplete or invalid, so it could not be tested."
    ),
    "provider_test_failed": (
        "A provider instance's connection test failed unexpectedly; the underlying error is "
        "in the server log."
    ),
    "run_not_prelaunch": (
        "The run has launched, so its policy overrides are frozen; edit them before launch."
    ),
    "loop_not_prelaunch": (
        "The loop has launched, so its plan is frozen; planning actions only apply "
        "before launch."
    ),
    "unknown_policy_key": (
        "A policy override key is not in the overridable set; the detail names the "
        "offending keys and the keys a run may override."
    ),
    "config_path_required": (
        "The config write names no field: the request body carries no `path`. Fix: send "
        '{"path": "<section.field>", "value": <new value>}.'
    ),
    "config_path_blank": (
        "The config write's `path` is present but empty, so it selects no field. Fix: name "
        "the dotted field to change, such as `agent.approval_mode`."
    ),
    "config_path_type_invalid": (
        "The config write's `path` is not a string, so it cannot select a field. Fix: send "
        "`path` as a dotted string, not a list, object, number or boolean."
    ),
}


def json_error(
    code: str,
    *,
    message: str | None = None,
    status: int,
    headers: Mapping[str, str] | None = None,
    error_extra: Mapping[str, Any] | None = None,
    **extra: Any,
) -> web.Response:
    """The one wire error envelope: ``{"error": {"code", "message"}}``.

    :param code: The stable ``lowercase_snake`` code a client branches on. The ONLY
        positional parameter, so it can never be confused with ``message``.
    :param message: The human sentence. Omit it to use the :data:`HTTP_ERROR_CODES`
        meaning — which is what the auth and device-pairing routes do, because a
        fixed message per code is what keeps those responses from being usable to
        enumerate users or devices.
    :param status: The HTTP status. Keyword-only and required: an error envelope
        served with an accidental 200 is worse than no envelope.
    :param headers: Response headers (``Retry-After`` on a lockout, for instance).
    :param error_extra: Extra keys merged INSIDE the ``error`` object — the
        actionable half of a failure (a preflight's findings, a service code).
    :param extra: Extra keys merged at the TOP level, beside ``error``.

    An unregistered ``code`` still emits (falling back to the code as its own
    message) rather than raising: a typo must not turn a 400 into a 500. The
    append-only rail is what catches the typo, statically, before it ships.
    """
    err: dict[str, Any] = {
        "code": code,
        "message": message if message is not None else HTTP_ERROR_CODES.get(code, code),
    }
    if error_extra:
        err.update(error_extra)
    return web.json_response(
        {"error": err, **extra}, status=status, headers=dict(headers or {})
    )
