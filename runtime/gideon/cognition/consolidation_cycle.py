"""Consolidation task accounting, extraction rounds, and bounded record application."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("gideon.cognition.history")


def _history():
    from gideon.cognition import history

    return history


def memory_setting(name):
    from gideon.core.config.loader import AppConfig

    return getattr(AppConfig.load().memory, name)


def redact_text(value):
    if not isinstance(value, str):
        return ""
    api = _history()
    result, _ = api.redact_exfiltration_urls(value)
    result, _ = api.redact_credentials(result)
    return result


class ConsolidationTasks:
    def __init__(self, owner):
        self.owner = owner
        self.running = set()
        self.tasks = set()
        self.activity = {}
        self.history = {}
        self.preferences = {}
        self.capacity = 64
        self.concurrent = asyncio.Semaphore(2)
        self.inflight = set()
        self.deferred = set()

    def start(self, key, include_history, destination, stamp):
        owner = self.owner
        if key in owner._running:
            return False
        if len(owner._tasks) >= self.capacity:
            self.deferred.add(key)
            return False
        owner._running.add(key)
        self.deferred.discard(key)
        initial_count = len(owner._log._read_messages(key))

        async def ordered():
            async with self.concurrent:
                self.inflight.add(key)
                try:
                    for attempt in range(2):
                        try:
                            await owner._consolidate(key, include_history=include_history)
                            return
                        except Exception:
                            if attempt:
                                raise
                            await asyncio.sleep(0.5)
                finally:
                    self.inflight.discard(key)
                    owner._running.discard(key)

        task = asyncio.create_task(ordered(), name=f"memory-extract:{key}")
        owner._tasks.add(task)

        def settled(completed):
            owner._tasks.discard(completed)
            if not completed.cancelled() and completed.exception() is None:
                destination[key] = stamp
                current_count = len(owner._log._read_messages(key))
                if current_count > initial_count and owner._log.unconsolidated_count(key) >= 1:
                    self.start(key, include_history, destination, current_count)
            else:
                self.deferred.add(key)

        task.add_done_callback(settled)
        return True

    def recover(self):
        for session in self.owner._log.list_sessions():
            key = session.get("key")
            if key and self.owner._log.unconsolidated_count(key):
                self.activity.setdefault(key, float(session.get("modified") or 0))

    async def drain(self, timeout=3.0):
        tasks = list(self.tasks)
        if tasks:
            _, unfinished = await asyncio.wait(tasks, timeout=max(0.0, timeout))
        else:
            unfinished = set()
        outcome = {"queued": len(self.tasks - self.inflight_tasks()),
                   "in_flight": len(unfinished & self.inflight_tasks()),
                   "deferred": len(self.deferred)}
        for task in unfinished:
            task.cancel()
        from gideon.core.atomic_write import atomic_write
        from gideon.core.config.loader import config_dir

        path = config_dir() / "memory" / "extraction_shutdown.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(outcome, sort_keys=True) + "\n")
        return outcome

    def inflight_tasks(self):
        return {task for task in self.tasks if task.get_name().removeprefix("memory-extract:") in self.inflight}

    def idle(self, now):
        owner = self.owner
        for key, latest in list(owner._last_activity.items()):
            if now - latest < owner._history_idle_secs:
                continue
            if owner._log.unconsolidated_count(key) < 1:
                continue
            if now - owner._history_consolidated.get(key, 0) < owner._history_idle_secs:
                continue
            if key not in owner._running:
                yield key


class ConsolidationRound:
    def __init__(self, owner, key, include_history):
        self.owner, self.key, self.include_history = owner, key, include_history

    def load(self):
        self.messages, self.total = self.owner._log.get_unconsolidated(self.key)
        if not self.messages:
            return False
        self.metadata = self.owner._log.get_metadata(self.key)
        workspace = self.metadata.get("workspace")
        if workspace:
            from gideon.cognition.context import PromptAssembler

            self.memory = PromptAssembler.get_memory_for(workspace)
        else:
            self.memory = self.owner._memory
        transcript = []
        for message in self.messages:
            tools = (
                " [tools: " + ", ".join(message["tools"]) + "]"
                if message.get("tools")
                else ""
            )
            transcript.append(
                f"[{message.get('ts', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        self.conversation = "\n".join(transcript)
        self.preferences = self.memory.read_preferences()
        self.projects = self.memory.read_projects()
        return True

    def render(self):
        from gideon.integrations.prompt_providers.runtime import (
            render_snippet_block,
            render_use_case_prompt,
        )

        owner, history = self.owner, self.include_history
        instructions = []

        def request(name, values=None):
            snippet = "consolidation-key-" + name
            instructions.append(
                render_snippet_block(snippet, values)
                if values is not None
                else render_snippet_block(snippet)
            )

        if history:
            request("history")
        semantic = ""
        has_vector = owner._svc.has_vector
        if has_vector:
            records = owner._svc.get_all_semantic()
            values = [
                {name: record[name] for name in ("key", "value_json", "confidence")}
                for record in records
            ]
            semantic = "\n\n## Current Semantic Memory\n" + (
                json.dumps(values, indent=1) if records else "[]"
            )
            request("semantic")
            request("episodic")
            if owner._holder_attribution:
                request("claims")
        if not owner._migrated:
            request("preferences")
            request("projects")
        if history:
            request("lessons")
        if history and has_vector:
            request("self-persona")
            if owner._proactive_commitments:
                request(
                    "commitments", {"max_commitments": owner._proactive_commitments_max}
                )
        api = _history()
        self.skills_eligible = (
            history
            and owner._auto_skills_enabled
            and owner._skills_loader is not None
            and api._count_tool_call_messages(self.messages)
            >= owner._auto_min_tool_calls
            and not api._session_touched_sensitive(self.messages)
        )
        if self.skills_eligible:
            request("new-skill")
            if owner._auto_refine_enabled:
                request("refined-skill")
        markdown = (
            ""
            if owner._migrated
            else (
                f"\n\n## Current Preferences\n{self.preferences or '(empty)'}"
                f"\n\n## Current Projects\n{self.projects or '(empty)'}"
            )
        )
        return (
            render_use_case_prompt(
                "memory_consolidation",
                {
                    "numbered_keys": "\n\n".join(
                        f"{index}. {text}" for index, text in enumerate(instructions, 1)
                    ),
                    "semantic_block": semantic,
                    "markdown_blocks": markdown,
                    "conversation": self.conversation,
                },
            )
            or ""
        )

    async def run(self):
        if self.load():
            result = await self.owner._call_llm(self.render())
            if result:
                await self.apply(result)

    async def apply(self, result):
        owner, key = self.owner, self.key
        entry = result.get("history_entry")
        if entry:
            self.memory.append_history(entry)
            logger.info("Consolidated %d messages for %s", len(self.messages), key)
            try:
                owner._svc.write_working_memory(key, entry)
            except Exception:
                logger.debug("working-memory write failed for %s", key, exc_info=True)
        if owner._svc.has_vector:
            await owner._form_semantic_memory(result, key)
            owner._write_episodic_memory(result, key)
        if not owner._migrated:
            for field, previous, write in (
                ("preferences_update", self.preferences, self.memory.write_preferences),
                ("projects_update", self.projects, self.memory.write_projects),
            ):
                replacement = result.get(field)
                if replacement and replacement.strip() != previous.strip():
                    write(replacement)
        if owner._svc.has_vector and result.get("lessons"):
            owner._save_lessons(result["lessons"])
        if owner._svc.has_vector and self.include_history:
            from gideon.engine.agents.defaults import normalize_agent_name

            agent = normalize_agent_name(self.metadata.get("agent"))
            owner._write_self_persona(result, agent)
            if owner._proactive_commitments:
                owner._write_commitments(result, agent, key)
        if self.skills_eligible:
            try:
                owner._process_auto_skills(result, key)
            except Exception:
                logger.warning(
                    "Auto-skill processing failed for %s", key, exc_info=True
                )
        if self.include_history:
            owner._log.mark_consolidated(key, self.total)
            await self.maintain()

    async def maintain(self):
        owner, key = self.owner, self.key
        try:
            owner._maybe_promote_episodic(self.memory)
        except Exception:
            logger.warning("Episodic promotion failed for %s", key, exc_info=True)
        operations: tuple[tuple[str, dict[str, Any], str, str], ...] = (
            (
                "expire_by_category",
                {},
                "Category-TTL expired %d memory record(s)",
                "Category-TTL sweep",
            ),
            (
                "promote_by_heat",
                {},
                "Heat-promoted %d record(s) to global scope",
                "Heat promotion",
            ),
            (
                "synthesize_failures",
                {},
                "Synthesized %d procedural failure prior(s)",
                "Failure synthesis",
            ),
            (
                "build_daily_digest",
                {},
                "Built %d daily-digest node(s)",
                "Daily-digest build",
            ),
            (
                "prune_volunteer_events",
                {"keep_days": 90},
                "Pruned %d volunteer event(s)",
                "Volunteer-log prune",
            ),
        )
        for method, arguments, message, label in operations:
            try:
                changed = getattr(owner._svc, method)(**arguments)
                if changed:
                    logger.info(message, changed)
            except Exception:
                logger.debug("%s failed for %s", label, key, exc_info=True)
        try:
            from gideon.integrations.inbound import capture_store

            removed = capture_store.prune()
            if removed:
                logger.info("Pruned %d expired capture file(s)", removed)
        except Exception:
            logger.debug("Capture prune failed for %s", key, exc_info=True)
        try:
            changed = owner._svc.refresh_topology()
            if changed:
                logger.info("Topology: assigned %d entity communit(ies)", changed)
        except Exception:
            logger.debug("Topology refresh failed for %s", key, exc_info=True)
        try:
            note = owner._run_learning_curator()
            if note:
                logger.info("Learning curator: %s", note)
        except Exception:
            logger.debug("Learning curator failed for %s", key, exc_info=True)
        try:
            from gideon.cognition.learning import replay

            note = replay.summarize_pass(await replay.run_pass())
            if note:
                logger.info("Learning replay: %s", note)
        except Exception:
            logger.debug("Learning replay pass failed for %s", key, exc_info=True)


class CuratorNotes:
    def __init__(self):
        self.outcomes = self.liveness = self.attribution = ""

    def measure(self, owner):
        try:
            from gideon.cognition.learning import outcome_resolver

            result = outcome_resolver.resolve(owner._svc)
            if result.get("resolved") or result.get("inconclusive"):
                self.outcomes = f"outcomes resolved={result['resolved']} inconclusive={result['inconclusive']}"
        except Exception:
            logger.debug("Outcome resolver failed", exc_info=True)
        try:
            from gideon.cognition.learning import consumer_liveness

            result = consumer_liveness.sweep()
            if result.get("dormant") or result.get("proposed"):
                self.liveness = f"consumer liveness dormant={result['dormant']} proposed={result['proposed']}"
        except Exception:
            logger.debug("Consumer-liveness sweep failed", exc_info=True)
        try:
            from gideon.cognition.learning import attribution

            result = attribution.grade_accepted_changes()
            if result.get("graded") or result.get("reverts"):
                self.attribution = (
                    f"attribution graded={result['graded']} reverts={result['reverts']}"
                )
        except Exception:
            logger.debug("Attribution grading failed", exc_info=True)

    def render(self, summary="", promotions=""):
        return "; ".join(
            filter(
                None,
                (summary, promotions, self.outcomes, self.liveness, self.attribution),
            )
        )


class SemanticFormationBatch:
    def __init__(self, owner, key):
        self.owner, self.key = owner, key

    async def apply(self, result):
        from gideon.cognition import memory_formation
        from gideon.cognition.vector_memory import _MAX_SEMANTIC_PER_CONSOLIDATION

        owner = self.owner
        items = result.get("semantic")
        if not isinstance(items, list) or not items:
            return
        candidates = memory_formation.candidates_from_extract(
            items,
            holder_attribution=owner._holder_attribution,
            limit=_MAX_SEMANTIC_PER_CONSOLIDATION,
        )
        if not candidates:
            return
        verdicts, degraded = {}, False
        try:
            memory_formation.gather(owner._vector_store, candidates)
            prompt = memory_formation.build_decide_prompt(candidates)
            if prompt:
                answer = await owner._call_llm(prompt)
                verdicts = memory_formation.parse_decisions(answer, candidates)
                degraded = not verdicts
        except Exception:
            logger.warning(
                "Memory formation degraded for %s — writing as ADD",
                self.key,
                exc_info=True,
            )
            verdicts, degraded = {}, True
        report = memory_formation.apply_decisions(
            owner._vector_store,
            candidates,
            verdicts,
            source=f"consolidation:{self.key}",
            holder_attribution=owner._holder_attribution,
        )
        report.degraded = report.degraded or degraded
        logger.info("Memory formation for %s: %s", self.key, report.summary())


class ExtractedRecords:
    def __init__(self, owner):
        self.owner = owner

    def lessons(self, items):
        written = sum(
            bool(
                self.owner._svc.write_lesson(
                    rule=item["rule"],
                    category=item.get("category", "knowledge"),
                    negative=item.get("negative"),
                    source="consolidation",
                )
            )
            for item in items
            if isinstance(item, dict) and item.get("rule")
        )
        if written:
            logger.info("Extracted %d lesson(s) from chat (record store)", written)

    def episodes(self, items, key):
        from gideon.cognition.vector_memory import _MAX_EPISODIC_PER_CONSOLIDATION

        written = 0
        for item in items[:_MAX_EPISODIC_PER_CONSOLIDATION]:
            if isinstance(item, dict) and "text" in item:
                written += bool(
                    self.owner._svc.write_episodic(
                        item["text"],
                        conversation_id=key,
                        tags=item.get("tags", []),
                        importance=float(item.get("importance", 0.5)),
                        source=f"consolidation:{key}",
                    )
                )
        if written:
            logger.info("Wrote %d episodic entries from consolidation", written)

    def persona(self, traits, agent):
        written = 0
        for trait in traits[:4]:
            if not isinstance(trait, str):
                continue
            safe = redact_text(trait.strip()[:120])
            if safe:
                try:
                    written += bool(
                        self.owner._svc.record_persona(agent=agent, trait=safe)
                    )
                except Exception:
                    logger.debug("self_persona write failed", exc_info=True)
        if written:
            logger.info("Wrote %d self-persona trait(s) for agent %s", written, agent)

    def commitments(self, items, agent, key):
        channel = "dashboard:" + key.removeprefix("dashboard:").removeprefix(
            "dashboard_"
        )
        limit = self.owner._proactive_commitments_max
        written = 0
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            text, due = item.get("text", ""), item.get("due_window", "")
            if not isinstance(text, str) or not isinstance(due, str):
                continue
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            safe = redact_text(text.strip()[:300])
            if safe and due:
                try:
                    written += bool(
                        self.owner._svc.record_commitment(
                            agent=agent,
                            channel=channel,
                            text=safe,
                            due_window=due,
                            confidence=confidence,
                            enabled=True,
                            max_per_day=limit,
                        )
                    )
                except Exception:
                    logger.debug("commitment write failed", exc_info=True)
        if written:
            logger.info(
                "Recorded %d proactive commitment(s) for agent %s", written, agent
            )


class SkillExtraction:
    def __init__(self, owner, session):
        self.owner, self.session = owner, session
        self.loader = owner._skills_loader

    def audit(self, operation, outcome, **metadata):
        _history().sel().log_tool_invocation(
            session_key=self.session,
            tool_name=operation,
            tool_kind="skills",
            outcome=outcome,
            metadata=metadata,
        )

    @staticmethod
    def fields(record):
        return {
            name: redact_text(record.get(name, ""))
            for name in ("description", "triggers", "procedure_md")
        }

    def propose(self, record):
        slug = str(record.get("slug", "")).strip()
        fields = self.fields(record)
        if not slug or not fields["description"] or not fields["procedure_md"]:
            logger.info(
                "Auto-skill create skipped: empty slug/description/procedure after redaction (slug=%r)",
                slug,
            )
            self.audit(
                "auto_skill_create",
                "rejected",
                slug=slug or "(empty)",
                reason="empty_after_redaction",
            )
            return
        similar = self.loader.find_similar(
            fields["description"], threshold=self.owner._auto_similarity_threshold
        )
        if similar:
            logger.info(
                "Auto-skill synthesis skipped: '%s' overlaps existing skill '%s'",
                slug,
                similar,
            )
            self.audit(
                "auto_skill_create",
                "rejected",
                slug=slug,
                reason="similar_exists",
                existing=similar,
            )
            return
        from gideon.extensions.skills import proposals
        from gideon.extensions.skills.loader import AUTO_SKILL_NAMESPACE

        existing = f"{AUTO_SKILL_NAMESPACE}/{slug}"
        installed = self.loader.load_skill(existing) is not None
        proposal = proposals.enqueue(
            slug=slug,
            kind="refine" if installed else "new",
            refine_target=existing if installed else "",
            **fields,
            session_key=self.session,
            created_at=_history().AutoSkillProvenance.now_iso(),
            source_excerpt=fields["procedure_md"],
        )
        if proposal is None:
            logger.info(
                "Auto-skill proposal rejected for slug '%s' (queue full/invalid)", slug
            )
            self.audit(
                "auto_skill_propose",
                "rejected",
                slug=slug,
                reason="queue_full_or_invalid",
            )
        else:
            logger.info(
                "Queued skill proposal %s from session %s", proposal.id, self.session
            )
            self.audit(
                "auto_skill_propose", "invoked", proposal_id=proposal.id, slug=slug
            )

    def refine(self, record):
        name = str(record.get("name", "")).strip()
        if not self.loader.is_auto_generated(name):
            logger.info(
                "Auto-skill refine rejected for %s: not in auto namespace", name
            )
            self.audit(
                "auto_skill_refine", "rejected", name=name, reason="not_auto_namespace"
            )
            return
        fields = self.fields(record)
        if not fields["description"] or not fields["procedure_md"]:
            logger.info(
                "Auto-skill refine skipped for %s: empty description/procedure after redaction",
                name,
            )
            self.audit(
                "auto_skill_refine",
                "rejected",
                name=name,
                reason="empty_after_redaction",
            )
            return
        try:
            from gideon.extensions.skills.usage import SkillUsageStore

            reuse = SkillUsageStore().get(name).count
        except Exception:
            reuse = 0
        provenance_type = _history().AutoSkillProvenance
        provenance = provenance_type(
            session_key=self.session,
            created_at=provenance_type.now_iso(),
            refined_at=provenance_type.now_iso(),
            reuse_count=reuse,
        )
        if self.loader.update_auto_skill(name, **fields, provenance=provenance):
            logger.info("Auto-refined skill %s from session %s", name, self.session)
            self.audit("auto_skill_refine", "invoked", name=name)
        else:
            logger.info("Auto-skill refine rejected for %s (update_failed)", name)
            self.audit(
                "auto_skill_refine", "rejected", name=name, reason="update_failed"
            )
