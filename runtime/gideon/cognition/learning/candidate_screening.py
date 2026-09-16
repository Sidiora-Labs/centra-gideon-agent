"""Deterministic plan admission and ordered failure classification."""

from __future__ import annotations

from collections import Counter


class DetectorBindings:
    def __init__(self):
        from . import detectors

        self.api = detectors


class PlanShape(DetectorBindings):
    BACK_REFERENCE = (
        r"\b(the (?:above|previous|result|output)|step \d|that (?:result|output)|it)\b"
    )

    def score(self, candidate):
        api = self.api
        steps = list(
            filter(lambda step: bool(step) and bool(step.strip()), candidate.steps)
        )
        if not steps:
            return api.Score()
        verbs = set()
        for step in steps:
            verbs.update(
                match.group(0).lower() for match in api._ACTION_VERBS.finditer(step)
            )
        diversity = min(1.0, len(verbs) / max(1, len(steps)))
        references = sum(
            bool(api.re.search(self.BACK_REFERENCE, step, api.re.IGNORECASE))
            for step in steps[1:]
        )
        dependencies = min(1.0, references / max(1, len(steps) - 1))
        slots = min(1.0, len(api._SLOT.findall(candidate.text)) / max(1, len(steps)))
        hardcoded = len(api._HARDCODED.findall(candidate.text))
        return api.Score(
            verb_diversity=diversity,
            dependencies=dependencies,
            slots=slots,
            hardcoded=hardcoded,
        )


class PlanAdmission(DetectorBindings):
    def decline(self, code, reason, score=None):
        fields = dict(
            action=self.api.Action.SKIP.value, skip_reason=code.value, reason=reason
        )
        if score is not None:
            fields["score"] = score
        return self.api.GateDecision(**fields)

    def preflight(self, candidate):
        api = self.api
        count = sum(1 for step in candidate.steps if step and step.strip())
        if count < api.MIN_PLAN_STEPS:
            return self.decline(
                api.Skip.TOO_FEW_STEPS,
                f"{count} step(s); one step is a command, not a procedure",
            )
        if candidate.template_surfaced:
            return self.decline(
                api.Skip.TEMPLATE_EXISTS,
                "a template already surfaced for this run, so there is no library gap to fill",
            )
        if candidate.budget_burn > api.MAX_BUDGET_BURN:
            return self.decline(
                api.Skip.BUDGET_BURN,
                f"burned {candidate.budget_burn:.0%} of budget; a run that flailed teaches the expensive path",
            )
        return None

    def decide(self, candidate):
        api = self.api
        refusal = self.preflight(candidate)
        if refusal is not None:
            return refusal
        score = api.structural_score(candidate)
        if score.slots <= 0:
            return self.decline(
                api.Skip.NO_SLOTS,
                "no parameterizable slot — this is a recording of one run, not a template",
                score,
            )
        if score.total >= api.AUTO_FILE_SCORE:
            action = api.Action.AUTO_FILE.value
            reason = (
                f"structural score {score.total:.2f} clears {api.AUTO_FILE_SCORE:.2f} — no "
                "model call (filing, not installing)"
            )
        elif score.total < api.DROP_SCORE:
            return self.decline(
                api.Skip.LOW_SCORE,
                f"structural score {score.total:.2f} is below {api.DROP_SCORE:.2f}",
                score,
            )
        else:
            action = api.Action.CONSULT.value
            reason = (
                f"score {score.total:.2f} sits between {api.DROP_SCORE:.2f} and {api.AUTO_FILE_SCORE:.2f} — "
                "the only band where a model call is worth paying for"
            )
        return api.GateDecision(action=action, score=score, reason=reason)

    def repeated(self, matches, threshold, min_priors, window_days):
        api = self.api
        current = sum(
            1 for row in matches or [] if row[1] >= threshold and row[2] <= window_days
        )
        expired = sum(
            1 for row in matches or [] if row[1] >= threshold and row[2] > window_days
        )
        if current >= max(1, min_priors):
            return api.GateDecision(
                action=api.Action.AUTO_FILE.value,
                reason=(
                    f"{current} plans within {window_days:g} days scored ≥{threshold:.2f} similar — "
                    "built the same thing repeatedly"
                ),
            )
        if expired:
            return self.decline(
                api.Skip.STALE_PRIORS,
                f"{expired} similar plan(s), all older than {window_days:g} days — the same plan from a project that ended",
            )
        return self.decline(
            api.Skip.TOO_FEW_PRIORS,
            f"{current} similar plan(s) in the window; {min_priors} needed before calling it a pattern",
        )


class FailureSignals(DetectorBindings):
    def classify(self, text):
        api = self.api
        if text:
            from gideon.cognition.after_turn_review import is_environment_failure_claim

            if is_environment_failure_claim(text):
                return api.FailureMode.ENVIRONMENT.value
            matches = (
                mode for mode, pattern in api._MODE_PATTERNS if pattern.search(text)
            )
            return next(matches, api.FailureMode.UNKNOWN.value)
        return api.FailureMode.UNKNOWN.value

    def distribution(self, failures):
        frequencies = Counter(
            self.api.classify_failure(str(text)) for text in failures or []
        )
        ordered = sorted(frequencies, key=lambda mode: (-frequencies[mode], mode))
        return {mode: frequencies[mode] for mode in ordered}

    def dominant(self, failures):
        api = self.api
        excluded = api.NON_LESSON_MODES | {api.FailureMode.UNKNOWN.value}
        return next(
            (
                mode
                for mode in api.failure_distribution(failures)
                if mode not in excluded
            ),
            "",
        )

    def worthiness(self, text):
        api = self.api
        mode = api.classify_failure(text)
        rejection = ""
        if mode in api.NON_LESSON_MODES:
            rejection = (
                f"{mode} is a condition of the environment; a lesson from it would teach the "
                "agent to refuse a valid action later"
            )
        elif mode == api.FailureMode.UNKNOWN.value:
            rejection = "the failure could not be classified, so there is nothing specific to learn"
        return not bool(rejection), rejection
