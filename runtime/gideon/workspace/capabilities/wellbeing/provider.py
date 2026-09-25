"""Measurement tools use the same home-scoped ledger as the dashboard."""

import asyncio
import json
from pathlib import Path

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .store import MeasurementError, MeasurementStore


class WellbeingProvider(ToolProvider):
    name = "gideon-wellbeing"
    display_name = "Wellbeing records"

    def __init__(self, home=None):
        self.home = Path(home) if home is not None else config_dir()

    async def list_tools(self):
        return [ToolDefinition(
            name="wellbeing_records", provider=self.name,
            description="List, read, export, enter or correct personal weight and blood pressure records. Corrections preserve provenance and history. Writes require a stable request_id; correct also requires revision.",
            parameters={"type": "object", "required": ["operation"], "additionalProperties": False,
                "properties": {"operation": {"type": "string", "enum": ["list", "get", "history", "export", "create", "correct", "labs_preview", "labs_commit", "labs_get", "labs_list", "labs_history", "labs_correct", "labs_trends", "apple_preview", "apple_commit", "apple_metrics", "substances_entry_create", "substances_entry_correct", "substances_entry_delete", "substances_entry_get", "substances_entry_history", "substances_entry_list", "substances_preset_create", "substances_preset_update", "substances_preset_delete", "substances_preset_list", "substances_summary", "genome_preview", "genome_commit", "genome_sources", "genome_source", "genome_original", "genome_variants", "genome_variant", "genome_annotate", "genome_history", "intervention_create_plan", "intervention_update_plan", "intervention_get_plan", "intervention_list_plans", "intervention_history_plan", "intervention_record", "intervention_correct_record", "intervention_get_record", "intervention_list_records", "intervention_history_record", "intervention_summary", "cognition_start", "cognition_get", "cognition_list", "cognition_answer", "cognition_cancel", "memory_create", "memory_update", "memory_get", "memory_list", "memory_history", "memory_practice", "life_configure", "life_config", "life_config_history", "life_projection", "life_event_create", "life_event_update", "life_events", "life_event_history", "life_reminder_check", "exports_preview", "exports_create", "exports_list", "exports_get", "exports_download", "shared_status", "shared_preview", "shared_commit", "shared_preview_file", "shared_commit_file", "shared_publish", "shared_download", "privacy_subject_create", "privacy_subjects", "privacy_subject", "privacy_consent", "privacy_consents", "privacy_facts", "privacy_fact", "privacy_fact_history", "privacy_audit", "privacy_organizations", "privacy_organization", "privacy_organization_history", "privacy_holdings", "privacy_holding_history", "privacy_changes", "privacy_change"]},
                    "id": {"type": "string"}, "payload": {"type": "object", "description": "Create: request_id, kind (body_weight/blood_pressure), observed_at with UTC offset, unit (kg/lb/mmHg), values (weight or systolic/diastolic), source, notes. Correct: request_id, revision, optional observed_at/unit/values/notes. List: optional from_date/to_date/kind/limit/offset. Labs preview: filename, format csv/json, content, source; rows analyte, observed_at with offset, value, unit, optional reference_low/reference_high/notes/external_id. Labs commit also requires preview_id and request_id. Labs correct: revision, request_id, value/reference bounds/notes. Labs trends: analyte and unit. Apple preview: filename, format xml/zip/json/fhir, content_base64 and source. Apple commit also requires preview_id and request_id. Apple metrics: optional metric/unit/from_date/to_date/limit/offset. Substance create: request_id, kind alcohol/nicotine, name, details volume_ml+abv_percent or mg_per_unit; entries additionally observed_at, count, source, notes. Optional preset_id replaces kind/name/details. Correct/update/delete require request_id and revision. Summary: timezone,days,as_of. Lists accept kind; entry list also from_date/to_date/limit/offset. Genome preview: filename, format tsv/vcf, content, source, assembly GRCh37/GRCh38, optional sample (required for multisample VCF). Commit also preview_id/request_id. Variants: id source ID, payload chromosome/rsid/limit/offset. Annotate: id variant ID, payload request_id/revision/annotation/annotation_source. Original returns base64 source bytes. Intervention create_plan: request_id,name,kind medication/supplement/activity/other,instructions,source,timezone,start_date,end_date nullable,weekdays Monday0..Sunday6. update_plan: revision/request_id/name/instructions/archived; schedule immutable. record: id plan, request_id,date,status completed/skipped,observed_at,notes. correct_record: id record, revision/request_id/status/observed_at/notes. summary: id plan,days/as_of. list_plans: include_archived boolean. Cognition start: request_id,kind arithmetic/color_word,planned_trials1..20,time_limit_seconds1..600. answer: id session,request_id,revision,answer string. cancel: id session,request_id,revision. list: limit1..500. Server-observed timing includes network latency; no clinical scoring. Memory create: request_id,front,back,source,tags. Update: id,request_id,revision,front/back/tags/archived. Practice: id,request_id,revision,grade again/hard/good/easy. List: due_only,as_of,include_archived,limit. Schedules use version1 self-grades and elapsed UTC days. Life configure: request_id,revision0initial,birth_date,horizon_years1..120,sleep_hours0..24,timezone,budgets[{name,hours_per_week}],source,reminder{enabled,time HH:MM}. Projection optional as_of offset timestamp. Event create:request_id,date,title,notes,kind recorded/planned,source; update id,request_id,revision,date/title/notes/kind/deleted. Reminder_check takes no overrides and only writes canonical local inbox if opted in and incomplete. Exports preview/list/get/download accept no payload; create requires request_id. Download returns base64 versioned JSON with referenced original attachments; no restore or external automation. Shared preview:content version1 native health JSON. Commit also preview_id/request_id. File preview/status/download no payload; file commit preview_id/request_id. Publish request_id/expected_sha256 nullable for missing file. Only bodyweight/BP, fixed home shared/wellbeing.json; no remote path or automatic cloud sync. Privacy subject_create:request_id,alias,relationship self/household/other,source. Consent:id subject,request_id,revision per scope0initial,scope vault/reveal/broker_scan/broker_submit/twin_share,granted bool,method. Other privacy operations metadata-only: id subject or fact. No private values/passphrases/reveal accepted by agent tools; use explicit owner UI."}}},
            requires_approval=True, risk_level=RiskLevel.CAUTION)]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name == "wellbeing_records" and arguments.get("operation") in ("privacy_organizations", "privacy_organization", "privacy_organization_history", "privacy_holdings", "privacy_holding_history", "privacy_changes", "privacy_change"):
                from .holdings_provider import invoke_holdings
                return ToolResult(success=True, output=await invoke_holdings(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("privacy_"):
                from .privacy_provider import invoke_privacy
                return ToolResult(success=True, output=await invoke_privacy(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("shared_"):
                from .shared_provider import invoke_shared
                return ToolResult(success=True, output=await invoke_shared(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("exports_"):
                from .exports_provider import invoke_exports
                return ToolResult(success=True, output=await invoke_exports(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("life_"):
                from .life_provider import invoke_life
                return ToolResult(success=True, output=await invoke_life(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("memory_"):
                from .memory_provider import invoke_memory
                return ToolResult(success=True, output=await invoke_memory(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("cognition_"):
                from .cognition_provider import invoke_cognition
                return ToolResult(success=True, output=await invoke_cognition(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("intervention_"):
                from .intervention_provider import invoke_intervention
                return ToolResult(success=True, output=await invoke_intervention(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("genome_"):
                from .genome_provider import invoke_genome
                return ToolResult(success=True, output=await invoke_genome(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("substances_"):
                from .substances_provider import invoke_substances
                return ToolResult(success=True, output=await invoke_substances(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("apple_"):
                from .apple_provider import invoke_apple
                return ToolResult(success=True, output=await invoke_apple(self.home, arguments))
            if tool_name == "wellbeing_records" and str(arguments.get("operation", "")).startswith("labs_"):
                from .labs_provider import invoke_labs
                return ToolResult(success=True, output=await invoke_labs(self.home, arguments))
            if tool_name != "wellbeing_records" or arguments.get("operation") not in ("list", "get", "history", "export", "create", "correct"):
                raise MeasurementError("Unknown wellbeing operation")
            store = MeasurementStore(self.home)
            operation, payload = arguments["operation"], arguments.get("payload", {})
            if operation == "list":
                value = await asyncio.to_thread(store.list, **payload)
            elif operation in ("get", "history"):
                value = await asyncio.to_thread(getattr(store, operation), arguments.get("id"))
            elif operation == "correct":
                value = await asyncio.to_thread(store.correct, arguments.get("id"), payload)
            elif operation == "create":
                value = await asyncio.to_thread(store.create, payload)
            else:
                value = await asyncio.to_thread(store.export)
            return ToolResult(success=True, output=json.dumps(value, allow_nan=False))
        except (MeasurementError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc))


def create_provider(config=None):
    return WellbeingProvider()
