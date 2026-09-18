"""The fast-tier contradiction judge, wired into the persist path — one call, bounded, metered.

``contradiction.py`` has shipped the model tier's primitives (``shortlist``,
``conflict_prompt``, ``memo_key``, ``parse_model_verdict``) and unit tests for all four
since it was written, and nothing in ``runtime/`` called any of them: the persist provider
ran the deterministic tier and stopped. Unit-tested primitives with no caller are the exact
shape of a capability that reads as present and is inert, so every test here drives the REAL
``KnowledgePersistActionProvider.execute`` and asserts an effect a caller could observe.

The four properties, and why each needs its own assertion:

* **Exactly one call, on the fast background tier.** Not "at least one" — an unbounded
  judging pass on the write path is a cost that compounds with every claim in every write,
  and the axis decides which model pays it. Both are asserted from what the seam RECEIVED.
* **A bounded prompt.** The shortlist cap is what keeps the marginal cost of a write
  independent of store size; without the assertion, a pass that sent the whole store would
  still make exactly one call.
* **The verdict becomes edges.** Parsed into ``item_relations`` rows carrying
  ``provenance='inferred'``, so the graph can still tell a proof from an opinion.
* **Degradation is graceful.** With no model configured the deterministic findings and their
  edges must be exactly what they were — the model tier's absence costs its own findings and
  nothing else.
"""

from __future__ import annotations

import json

import pytest

from gideon.cognition.knowledge.contradiction import MAX_CONFLICT_CANDIDATES
from gideon.integrations.action_providers import knowledge_persist_provider as persist
from gideon.integrations.action_providers.base import ActionContext


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _forget_verdicts():
    """A memo that survived a test would turn "exactly one call" into "one call, once, ever"
    — the pass would look budgeted while actually never running again."""
    persist.reset_conflict_memo()
    yield
    persist.reset_conflict_memo()


@pytest.fixture
def ctx():
    return ActionContext(event="workflow_node", payload={"node_id": "n-1"})


def _open():
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


class _Judge:
    """A controlled stand-in for the model seam, recording what it was actually asked.

    Records the prompt and the USE CASE rather than only counting, because "one call" and
    "one call on the intended tier" are different claims and only the second is the
    requirement.
    """

    def __init__(self, verdict=None):
        self.verdict = verdict if verdict is not None else {"conflicts": []}
        self.prompts: list[str] = []
        self.use_cases: list[str] = []

    async def __call__(self, prompt: str, *, use_case: str):
        self.prompts.append(prompt)
        self.use_cases.append(use_case)
        return self.verdict


async def _persist(ctx, *, title: str, statement: str, judge=None, origin="external"):
    return await persist.KnowledgePersistActionProvider().execute(
        {
            "kind": "fact",
            "title": title,
            "content": f"{title} body",
            "claims": [{"id": title, "statement": statement, "origin": origin}],
        },
        ctx,
        judge=judge,
    )


async def _seed(ctx, judge=None) -> None:
    """One stored claim the deterministic tier provably cannot conflict with the next one:
    no shared subject+predicate with a different object, no opposed predicate. Without that
    the model tier would never be the thing under test."""
    await _persist(
        ctx,
        title="Rollout note",
        statement="The March rollout shipped to every region",
        judge=judge,
    )


class TestTheCallIsBoundedAndOnTheFastTier:
    async def test_exactly_one_call_on_the_background_axis_per_persist(self, home, ctx):
        judge = _Judge()
        await _seed(ctx, judge)
        assert (
            judge.use_cases == []
        ), "a first write has nothing stored to judge against"

        await _persist(
            ctx,
            title="Region note",
            statement="The March rollout skipped the Frankfurt region",
            judge=judge,
        )

        assert len(judge.prompts) == 1
        assert judge.use_cases == [persist.CONFLICT_JUDGE_USE_CASE]
        assert persist.CONFLICT_JUDGE_USE_CASE == "background"

    async def test_many_claims_in_one_write_still_cost_one_call(self, home, ctx):
        """The budget is per PERSIST, not per claim. A write carrying ten claims must cost
        what a write carrying one costs."""
        judge = _Judge()
        await _seed(ctx, judge)

        await persist.KnowledgePersistActionProvider().execute(
            {
                "kind": "fact",
                "title": "Bulk note",
                "content": "many claims",
                "claims": [
                    {
                        "id": f"c{i}",
                        "statement": f"Observation {i} was recorded in March",
                    }
                    for i in range(10)
                ],
            },
            ctx,
            judge=judge,
        )

        assert len(judge.prompts) == 1

    async def test_the_prompt_is_capped_by_the_shortlist(self, home, ctx):
        """The cap is what makes the cost graph-size-independent. Forty stored claims, one
        prompt, never more candidates than ``MAX_CONFLICT_CANDIDATES``."""
        judge = _Judge()
        for index in range(40):
            await _persist(
                ctx,
                title=f"March note {index}",
                statement=f"The March rollout recorded observation {index}",
                judge=judge,
            )
        judge.prompts.clear()
        judge.use_cases.clear()

        await _persist(
            ctx,
            title="Final note",
            statement="The March rollout recorded observation forty",
            judge=judge,
        )

        assert len(judge.prompts) == 1
        candidates = judge.prompts[0].count("<untrusted_content source=knowledge>") - 1
        assert (
            candidates == MAX_CONFLICT_CANDIDATES
        ), "forty stored claims must still buy exactly the capped shortlist"

    async def test_a_deterministic_proof_is_never_re_asked_of_the_model(
        self, home, ctx
    ):
        """Paying a model to re-derive what the cheap tier proved is the one call with no
        possible upside."""
        judge = _Judge()
        await _persist(
            ctx,
            title="Latency A",
            statement="Cold start latency is 4.2 seconds",
            judge=judge,
        )
        judge.prompts.clear()

        result = await _persist(
            ctx,
            title="Latency B",
            statement="Cold start latency is 9.1 seconds",
            judge=judge,
            origin="user",
        )

        conflicts = json.loads(result.stdout)["conflicts"]
        assert [c["basis"] for c in conflicts] == ["deterministic"]
        assert judge.prompts == [], "the settled claim was sent to the model anyway"


class TestTheVerdictBecomesEdges:
    async def test_a_model_verdict_is_parsed_into_conflicts_and_inferred_edges(
        self, home, ctx
    ):
        await _seed(ctx)
        judge = _Judge(
            {
                "conflicts": [
                    {
                        "index": 0,
                        "kind": "value",
                        "reason": "one says every region, the other excludes Frankfurt",
                        "confidence": 0.8,
                    }
                ]
            }
        )

        result = await _persist(
            ctx,
            title="Region note",
            statement="The March rollout skipped the Frankfurt region",
            judge=judge,
        )

        conflicts = json.loads(result.stdout)["conflicts"]
        assert [c["basis"] for c in conflicts] == ["model"]
        assert conflicts[0]["confidence"] == pytest.approx(0.8)

        store = _open()
        rows = [dict(r) for r in store.db.execute("SELECT * FROM item_relations")]
        assert len(rows) == 1
        assert (
            rows[0]["provenance"] == "inferred"
        ), "a model's opinion must not be indistinguishable from a proof in the graph"
        assert rows[0]["relation_type"] in ("contradicts", "supersedes")

    async def test_an_unparseable_answer_invents_nothing(self, home, ctx):
        """Garbled means "we do not know". Inventing a conflict from noise is the one
        outcome worse than missing one."""
        await _seed(ctx)
        judge = _Judge("not json at all")

        result = await _persist(
            ctx,
            title="Region note",
            statement="The March rollout skipped the Frankfurt region",
            judge=judge,
        )

        assert json.loads(result.stdout)["conflicts"] == []
        store = _open()
        assert (
            list(store.db.execute("SELECT COUNT(*) AS n FROM item_relations"))[0]["n"]
            == 0
        )

    async def test_an_out_of_range_index_is_refused(self, home, ctx):
        await _seed(ctx)
        judge = _Judge({"conflicts": [{"index": 99, "kind": "value"}]})

        result = await _persist(
            ctx,
            title="Region note",
            statement="The March rollout skipped the Frankfurt region",
            judge=judge,
        )

        assert json.loads(result.stdout)["conflicts"] == []


class TestDegradation:
    async def test_a_failing_model_keeps_the_deterministic_edges(self, home, ctx):
        """The order is the contract: the deterministic findings and their edges are written
        before the model is reached, so an exception inside the judge costs only the model's
        own findings."""

        async def _explodes(prompt: str, *, use_case: str):
            raise RuntimeError("provider down")

        await _persist(
            ctx,
            title="Latency A",
            statement="Cold start latency is 4.2 seconds",
            judge=_explodes,
        )
        result = await _persist(
            ctx,
            title="Latency B",
            statement="Cold start latency is 9.1 seconds",
            judge=_explodes,
            origin="user",
        )

        conflicts = json.loads(result.stdout)["conflicts"]
        assert conflicts and conflicts[0]["basis"] == "deterministic"
        store = _open()
        rows = [dict(r) for r in store.db.execute("SELECT * FROM item_relations")]
        assert len(rows) == 1
        assert rows[0]["provenance"] == "extracted"

    async def test_no_model_configured_still_persists_the_write(self, home, ctx):
        """The real default seam with no provider registry behind it: ``one_shot_completion``
        raises, and the write must land anyway."""
        await _seed(ctx)

        result = await _persist(
            ctx,
            title="Region note",
            statement="The March rollout skipped the Frankfurt region",
        )

        assert result.success
        assert json.loads(result.stdout)["conflicts"] == []
        store = _open()
        titles = {row["title"] for row in store.db.execute("SELECT title FROM items")}
        assert titles == {"Rollout note", "Region note"}


class TestTheDefaultSeam:
    async def test_the_default_judge_asks_the_background_axis_for_a_dict(
        self, home, monkeypatch
    ):
        """The default is what makes the call METERED: ``one_shot_completion`` is the seam
        where ``ModelCallGuard`` applies the breaker, the timeout and the spend meter, so a
        judge that resolved its own provider would be an unmetered inference on every write.
        """
        seen: dict = {}

        async def _capture(prompt, *, use_case, output_type=None, **rest):
            seen.update(prompt=prompt, use_case=use_case, output_type=output_type)
            return '{"conflicts": []}'

        monkeypatch.setattr(
            "gideon.integrations.llm_helpers.one_shot_completion", _capture
        )

        answer = await persist._default_conflict_judge(
            "question", use_case=persist.CONFLICT_JUDGE_USE_CASE
        )

        assert answer == '{"conflicts": []}'
        assert seen["use_case"] == "background"
        assert seen["output_type"] is dict
