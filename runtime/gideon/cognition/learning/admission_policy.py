"""Learning admission stages, age policies and shared refusal reporting."""

from collections import Counter


class AgeProfile:
    @staticmethod
    def rate(api, kind, importance):
        multiplier = api.KIND_MULTIPLIERS.get(kind, 1.0)
        weight = max(0.0, min(1.0, importance))
        damping = 1.0 - weight * api.IMPORTANCE_DAMPING
        return (api.BASE_LAMBDA * multiplier) * max(0.05, damping)

    @staticmethod
    def verdict(
        api, level, importance, stability, pinned, source_type, linked_neighbors
    ):
        rules = (
            (lambda: pinned, (False, False, "pinned")),
            (lambda: source_type == "user", (False, False, "user_authored")),
            (
                lambda: level <= api.REVIEW_STRENGTH_MAX
                and stability >= api.REVIEW_STABILITY_MIN,
                (False, True, "decayed_but_stable"),
            ),
            (
                lambda: level < api.PRUNE_STRENGTH
                and importance < api.PRUNE_IMPORTANCE,
                None,
            ),
        )
        for eligible, result in rules:
            if not eligible():
                continue
            if result is None:
                result = (
                    (False, False, "chain_spared")
                    if linked_neighbors > 0
                    else (True, False, "low_strength_low_importance")
                )
            return api.DecayVerdict(level, *result)
        return api.DecayVerdict(level, False, False, "healthy")

    @staticmethod
    def active_days(api, dates, since, until):
        beginning = api._parse(since)
        if beginning is None:
            return 0.0
        ending = api._parse(until) if until else api.datetime.now(api.timezone.utc)
        if ending is None or ending <= beginning:
            return 0.0
        left, right = beginning.date(), ending.date()
        days = (api._parse_date(item) for item in dates)
        return float(sum(1 for day in days if day is not None and left < day <= right))


class CaptureScreen:
    @staticmethod
    def unfence(api, text):
        position, removed = 0, False
        spans = []
        while (opening := api._OPEN_TAG_RE.search(text, position)) is not None:
            removed = True
            spans.append(text[position : opening.start()])
            closing = text.find(api.UNTRUSTED_CLOSE, opening.end())
            if closing < 0:
                return "".join(spans), True
            position = closing + len(api.UNTRUSTED_CLOSE)
        if not removed:
            return text, False
        spans.append(text[position:])
        return "".join(spans), True

    @staticmethod
    def score(turns, decisions, recalls, tool_calls):
        signals = (
            (turns, 6.0, 0.20),
            (decisions, 2.0, 0.45),
            (recalls, 2.0, 0.15),
            (tool_calls, 8.0, 0.20),
        )
        total = 0.0
        for count, half, weight in signals:
            saturated = count / (count + half) if count > 0 else 0.0
            total += weight * saturated
        return round(min(1.0, total), 4)

    @staticmethod
    def apply(api, text, require_grounding):
        if not text or not text.strip():
            return api.HygieneVerdict("", False, ["empty"])
        content, unfenced = api._strip_untrusted(text)
        removals = ["untrusted_content"] if unfenced else []
        if not content.strip():
            return api.HygieneVerdict("", False, [*removals, "only_untrusted"])
        if api.is_system_injected(content):
            removals.append("system_injected")
        else:
            from gideon.cognition.after_turn_review import is_environment_failure_claim

            if is_environment_failure_claim(content):
                removals.append("environment_failure")
            elif require_grounding and not api.is_grounded(content):
                removals.append("ungrounded")
            else:
                return api.HygieneVerdict(content, True, removals)
        return api.HygieneVerdict(content, False, removals)


class SessionAdmission:
    @staticmethod
    def configure(gate, **values):
        for name, value in values.items():
            conversion = int if name == "min_tool_calls" else bool
            setattr(gate, name, conversion(value))

    @staticmethod
    def from_session(api, factory, session, cfg):
        if cfg is None:
            try:
                from gideon.core.config.loader import AppConfig

                cfg = AppConfig.load().learning
            except Exception:
                api.logger.debug(
                    "learning config load failed; gate defaults apply", exc_info=True
                )
        restricted = bool(getattr(session, "is_restricted", False))
        session_key = getattr(session, "key", None)
        if session_key:
            try:
                from gideon.engine import session_restrictions

                if not restricted:
                    restricted = session_restrictions.is_restricted(str(session_key))
            except Exception:
                api.logger.debug("session_restrictions lookup failed", exc_info=True)
        parameters = dict(
            enabled=bool(getattr(cfg, "enabled", True)),
            is_ephemeral=bool(getattr(session, "_ephemeral", False)),
            is_restricted=restricted,
            min_tool_calls=int(getattr(cfg, "min_tool_calls", 4) or 4),
            correction_heuristic=bool(getattr(cfg, "correction_heuristic", True)),
        )
        return factory(**parameters)

    @staticmethod
    def decide(
        api,
        gate,
        cadence,
        correction,
        tool_calls,
        cadence_enabled,
        session_score,
        min_session_score,
    ):
        restrictions = (
            ("enabled", False, api.GateReason.DISABLED),
            ("is_ephemeral", True, api.GateReason.EPHEMERAL),
            ("is_restricted", True, api.GateReason.RESTRICTED),
        )
        for name, denied_value, reason in restrictions:
            if bool(getattr(gate, name)) == denied_value:
                return api.GateDecision(False, False, reason, cadence)
        if not cadence_enabled:
            return api.GateDecision(True, False, api.GateReason.CADENCE_OFF, cadence)
        worthwhile = gate._worthwhile(
            cadence,
            correction=correction,
            tool_calls=tool_calls,
            session_score=session_score,
            min_session_score=min_session_score,
        )
        reason = api.GateReason.ALLOWED if worthwhile else api.GateReason.NOT_WORTHWHILE
        return api.GateDecision(True, worthwhile, reason, cadence)

    @staticmethod
    def worthwhile(
        api, gate, cadence, correction, tool_calls, session_score, min_session_score
    ):
        if cadence is api.Cadence.SESSION_END:
            return True if session_score is None else session_score >= min_session_score
        if cadence is not api.Cadence.PER_TURN:
            return True
        return (
            True
            if gate.correction_heuristic and correction
            else tool_calls >= max(1, gate.min_tool_calls)
        )


class RefusalLedger:
    @staticmethod
    def append(cadence, detail):
        from gideon.cognition.learning.staging import FlushOutcome, get_store

        get_store().record_flush(
            cadence=cadence, outcome=FlushOutcome.FLUSH_SKIPPED, detail=detail
        )
        return True

    @staticmethod
    def capture(api, decision, detail):
        if decision.allowed and decision.worthwhile:
            return False
        try:
            cadence = str(getattr(decision.cadence, "value", decision.cadence))
            reason = f"{getattr(decision.reason, 'value', decision.reason)}"
            if detail:
                reason += ": " + detail
            return RefusalLedger.append(cadence, reason)
        except Exception:
            api.logger.debug(
                "record_denial failed for %s", decision.reason, exc_info=True
            )
            return False

    @staticmethod
    def template(api, decision, detail):
        reason = decision.skip_reason
        if decision.action != api.Action.SKIP.value or not reason:
            return False
        try:
            from gideon.cognition.learning.gate import Cadence

            message = f"{api.LEDGER_PREFIX}: {reason}"
            if detail:
                message += ": " + detail
            return RefusalLedger.append(Cadence.PER_TURN.value, message)
        except Exception:
            api.logger.debug(
                "template gate: recording skip %s failed", reason, exc_info=True
            )
            return False

    @staticmethod
    def counts(api, days):
        try:
            import time

            from gideon.cognition.learning.staging import FlushOutcome, get_store

            store = get_store()
            lower_bound = time.time() - max(1, days) * 86400
            with store._cursor() as cursor:
                rows = cursor.execute(
                    "SELECT detail FROM flush_records WHERE outcome = ? AND created_ts >= ?;",
                    (FlushOutcome.FLUSH_SKIPPED.value, lower_bound),
                ).fetchall()
        except Exception:
            api.logger.debug("template gate: skip_counts unavailable", exc_info=True)
            return {}
        allowed = {value.value for value in api.detectors.Skip}
        prefix = api.LEDGER_PREFIX + ":"
        found = Counter()
        for row in rows:
            message = str(row[0] or "")
            if message.startswith(prefix):
                reason = message[len(prefix) :].strip().partition(":")[0].strip()
                if reason in allowed:
                    found[reason] += 1
        return {
            key: found[key] for key in sorted(found, key=lambda key: (-found[key], key))
        }


class TemplateFiling:
    @staticmethod
    def enqueue(api, candidate, decision, session_key, title, body):
        from gideon.cognition.learning.proposals import Kind, enqueue

        try:
            fields = dict(
                kind=Kind.TEMPLATE.value,
                title=title,
                body=body,
                provenance="inferred",
                session_key=session_key,
                run_id=candidate.run_id,
                source_excerpt=candidate.text[:2000],
                confidence=round(decision.score.total, 4),
                tags=["ad_hoc_to_template", decision.action],
            )
            _, proposal = enqueue(**fields)
        except Exception:
            api.logger.debug(
                "template gate: enqueue failed for %s", candidate.run_id, exc_info=True
            )
            return ""
        if proposal is None:
            return ""
        return proposal.id


class ConfidencePolicy:
    @staticmethod
    def support(observations, half_life):
        if observations <= 0:
            return 0.0
        excess = observations - 1
        remaining = pow(2.0, -excess / half_life)
        return 1.0 - remaining

    @staticmethod
    def derive(api, evidence, days):
        available = evidence.surviving_observations
        if available <= 0:
            return 0.0
        if evidence.human_authored:
            base, importance = 1.0, api.HUMAN_AUTHORED_IMPORTANCE
        else:
            base, importance = api.corroboration(available), 0.0
        aged = api.decay.strength(
            kind=api.DECAY_KIND, active_days_since_use=days, importance=importance
        )
        return max(0.0, min(1.0, base * aged))

    @staticmethod
    def classify(api, evidence, threshold, days):
        confidence = api.derive(evidence, active_days_idle=days)
        accepted = confidence >= threshold
        standing = (
            api.LessonStanding.INJECTED if accepted else api.LessonStanding.RETAINED
        )
        render = api._injected_reason if accepted else api._retained_reason
        return api.LessonVerdict(
            confidence, standing, render(evidence, confidence, threshold), evidence
        )

    @staticmethod
    def configured(api):
        try:
            from gideon.core.config.loader import AppConfig

            settings = AppConfig.load().learning
            threshold = float(
                getattr(settings, "min_lesson_confidence", api.DEFAULT_MIN_CONFIDENCE)
            )
        except Exception:
            api.logger.debug(
                "min_lesson_confidence read failed; using the default", exc_info=True
            )
            threshold = api.DEFAULT_MIN_CONFIDENCE
            return threshold
        return max(0.0, min(1.0, threshold))

    @staticmethod
    def injected_reason(evidence, confidence, threshold):
        explanation = (
            "you taught this directly"
            if evidence.human_authored
            else f"observed {evidence.surviving_observations}×"
        )
        return f"{explanation} — {confidence:.0%} confidence (gate {threshold:.0%})"

    @staticmethod
    def retained_reason(evidence, confidence, threshold):
        if evidence.reversals and evidence.observations <= evidence.voided:
            return (
                f"reversed — the {evidence.voided} earlier observation(s) no longer count; "
                f"held below the {threshold:.0%} gate until re-observed"
            )
        if evidence.contradictions and evidence.surviving_observations <= 0:
            cause = (
                f"contradicted {evidence.contradictions}× — no surviving corroboration"
            )
            return f"{cause}; held below the {threshold:.0%} gate"
        if evidence.observations <= 0:
            return f"no recorded observation yet — held below the {threshold:.0%} gate"
        parts = [f"observed {evidence.surviving_observations}×"]
        if evidence.contradictions:
            parts.append(f", contradicted {evidence.contradictions}×")
        parts.append(
            f" — {confidence:.0%} confidence, below the {threshold:.0%} gate; retained and still accumulating"
        )
        return "".join(parts)
