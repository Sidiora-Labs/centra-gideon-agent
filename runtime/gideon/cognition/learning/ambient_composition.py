"""Build ambient candidates and manage their shared rendering allowance."""

from __future__ import annotations

from collections import Counter
from itertools import takewhile


class AmbientBindings:
    def __init__(self):
        from . import ambient

        self.api = ambient


class SkillSections:
    @staticmethod
    def entries(block):
        return list(
            filter(
                lambda line: line.strip().startswith("- **"), (block or "").split("\n")
            )
        )

    @staticmethod
    def hint(line, limit):
        separator = line.find(": ")
        if separator < 0 or len(line) - separator - 2 <= limit:
            return line
        description = line[separator + 2 :]
        return line[:separator] + ": " + description[:limit].rstrip() + "…"

    @staticmethod
    def bodies(block):
        lines = (block or "").split("\n")
        opening = None
        retained = []
        for offset, line in enumerate(lines):
            if line.startswith("### Skill: "):
                if opening is None:
                    opening = offset
            elif line.startswith(("## Available Skills", "[End of skills]")):
                if opening is not None:
                    retained.extend(lines[opening:offset])
                    opening = None
        if opening is not None:
            retained.extend(lines[opening:])
        end = len(retained)
        for line in reversed(retained):
            if line.strip() not in ("", "---"):
                break
            end -= 1
        return "\n".join(retained[:end]).strip()

    @staticmethod
    def header(block):
        prefix = takewhile(
            lambda line: not line.strip().startswith("- "), (block or "").split("\n")
        )
        return "\n".join(line for line in prefix if line.strip())


class AmbientCandidates(AmbientBindings):
    def lessons(self, block):
        bullets = []
        for line in (block or "").split("\n"):
            normalized = line.strip()
            if normalized.startswith("- "):
                content = normalized[2:].strip()
                if content:
                    bullets.append("- " + content)
        return [
            self.api.Candidate(
                kind=self.api.SLOT_KINDS["lessons"],
                key=f"lesson:{index}",
                score=1.0,
                l0=text,
                l1=text,
                arm="lesson_store",
            )
            for index, text in enumerate(bullets)
        ]

    def whole(self, name, block, score, *, strict=False):
        normalized = (block or "").strip()
        if not normalized:
            return None
        levels = dict.fromkeys(("l0", "l1", "l2"), normalized)
        kind = (
            self.api.SLOT_KINDS[name]
            if strict
            else self.api.SLOT_KINDS.get(name, "memory")
        )
        return self.api.Candidate(kind=kind, key=name, score=score, arm=name, **levels)

    def index(self, block):
        api = self.api
        entries = api._index_entries(block)
        if not entries:
            return None

        def frame(lines):
            return "\n".join([api._SKILL_HEADER, *lines, api._SKILL_FOOTER])

        levels = {
            "l0": frame([line.partition(": ")[0] for line in entries]),
            "l1": frame([api._hint(line) for line in entries]),
            "l2": (block or "").strip(),
        }
        return api.Candidate(
            kind=api.SLOT_KINDS["skill_index"],
            key="skill_index",
            score=0.9,
            arm="skill_index",
            **levels,
        )

    def pool(self, blocks):
        api = self.api
        result = {}
        lessons = api.lesson_candidates(blocks["lessons"])
        if lessons:
            result["lessons"] = lessons
        procedural = api.procedural_candidate(blocks["procedural"])
        if procedural is not None:
            result["procedural"] = [procedural]
        skills = []
        always = api.always_body(blocks["skill_index"])
        if always:
            framing = "\n".join((api._SKILL_HEADER, always, api._SKILL_FOOTER))
            fixed = api.block_candidate("skill_index", framing, score=0.95)
            if fixed is not None:
                fixed.key = "skills_always"
                skills.append(fixed)
        index = api.index_candidate(blocks["skill_index"])
        if index is not None:
            skills.append(index)
        template = api.block_candidate("template", blocks["template"])
        if template is not None:
            skills.append(template)
        if skills:
            result["skills"] = skills
        memory = []
        for key, source, kind in (
            ("voice", "voice", "voice"),
            ("voice_persona", "persona", "voice"),
            ("self_model", "self_model", "self_model"),
        ):
            candidate = api.block_candidate(kind, blocks[source])
            if candidate is not None:
                candidate.key = key
                memory.append(candidate)
        if memory:
            result["memory"] = memory
        return result


class AmbientBudget(AmbientBindings):
    def __init__(self, ceiling, query):
        super().__init__()
        self.ceiling = ceiling
        self.query = query
        self.reserved = 0

    def empty(self, budget):
        return self.api.Allocation(text="", used_tokens=0, budget_tokens=budget)

    def render(self, blocks):
        api = self.api
        if self.ceiling <= 0:
            return self.empty(0)
        sources = api.sources_for(**blocks)
        lessons = sources.get("lessons", [])
        if lessons:
            self.reserved = api.count_tokens(api.lesson_header(blocks["lessons"])) + 2
            if max(0, self.ceiling - self.reserved) <= 0:
                return self.empty(self.ceiling)
        if not sources:
            return self.empty(self.ceiling)
        initial = api.allocate(
            sources, query=self.query, budget_tokens=self.ceiling - self.reserved
        )
        selected = initial
        if lessons and not api._kept_a_lesson(initial):
            unframed = api.allocate(
                sources,
                query=self.query,
                budget_tokens=self.ceiling,
                include_preamble=False,
            )
            if api._kept_a_lesson(unframed):
                selected, self.reserved = unframed, 0
        if self.reserved and api._kept_a_lesson(selected):
            selected.used_tokens += self.reserved
        selected.budget_tokens = self.ceiling
        return selected

    def frame(self, allocation, lesson_block):
        api = self.api
        text = allocation.text
        if not text:
            return ""
        if not api._kept_a_lesson(allocation):
            return text
        heading = api.lesson_header(lesson_block)
        if not heading or heading in text:
            return text
        needed = api.count_tokens(text) + api.count_tokens(heading) + 2
        if needed > allocation.budget_tokens:
            return text
        chunks = text.split("\n\n")
        insertion = next(
            (
                index
                for index, chunk in enumerate(chunks)
                if chunk.lstrip().startswith("- ")
                or api._is_lesson_block(chunk, allocation)
            ),
            len(chunks),
        )
        return "\n\n".join(chunks[:insertion] + [heading] + chunks[insertion:])

    def report(self, allocation):
        kinds = Counter(kind for kind, _key, _tier in allocation.included)
        return dict(
            used_tokens=allocation.used_tokens,
            budget_tokens=allocation.budget_tokens,
            headroom=allocation.headroom,
            by_kind=dict(sorted(kinds.items())),
            near_misses=len(allocation.near_misses),
            degraded=list(allocation.degraded),
            skipped_oversized=list(allocation.skipped_oversized),
            truncated_slot=allocation.truncated_slot,
            preamble=self.api.AUTHORITY_PREAMBLE in allocation.text,
        )
