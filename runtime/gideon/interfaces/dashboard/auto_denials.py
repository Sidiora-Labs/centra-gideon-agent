"""Durable denial and owner reentry contracts for dashboard callers."""

from gideon.security import auto_denials

ReentryAttempt = auto_denials.ReentryAttempt
call_fingerprint = auto_denials.call_fingerprint
_origin_refs = auto_denials._origin_refs
record_auto_denial = auto_denials.record_auto_denial
unanswered_note = auto_denials.unanswered_note
unanswered_for_chat = auto_denials.unanswered_for_chat
_origin_matches = auto_denials._origin_matches
owner_reentry_attempt = auto_denials.owner_reentry_attempt
bind_reentry_call = auto_denials.bind_reentry_call
bind_current_reentry_call = auto_denials.bind_current_reentry_call
settle_answered_call = auto_denials.settle_answered_call
