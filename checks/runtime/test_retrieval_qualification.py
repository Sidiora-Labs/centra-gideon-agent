"""Per-arm qualification BEFORE rank fusion — the keyword floor and the graph's two tiers.

RRF is untouched here, deliberately. Fusion is a ranking rule, not a relevance judge: it
has exactly one input per arm — a position — and a position says nothing about whether the
row at it is any good. So an arm that hands fusion its weak tail is asking RRF to make a
decision it has no evidence for, and the only place that evidence exists is inside the arm.

Two arms were doing exactly that:

* **keyword.** ``_sanitize_fts5_query`` ORs the query's terms, which is what lets a
  conversational question match a document carrying only some of them — and also what
  admits a document whose sole overlap is the query's least informative word. SQLite's BM25
  clamps a non-discriminating term's IDF, so those rows arrive orders of magnitude below a
  real match and are trivially separable; nothing was separating them.
* **graph.** Direct entity matches and depth-2 neighbours were summed into ONE mention
  tally, and a count cannot say where its mentions came from. An item mentioning three
  things merely NEAR the query outranked an item mentioning what the query actually named.

Each test below asserts the effect on the real store, and each has its control: the floor
is shown keeping a genuine partial match, and the graph's neighbour tier is shown still
answering when nothing direct exists — otherwise "precision improved" is satisfied by an
arm that returns less of everything.
"""

from __future__ import annotations

import pytest

from gideon.cognition.knowledge.retrieval import (
    GRAPH_MATCH_DIRECT,
    GRAPH_MATCH_NEIGHBOR,
    HybridRetriever,
    keyword_score_cut,
)
from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store():
    store = KnowledgeStore(str(knowledge_db_path()))
    try:
        yield store
    finally:
        store.close()


def _note(store, title: str, content: str) -> str:
    item_id = store.create_typed_item(item_type="note", title=title, content=content)
    store.db.commit()
    return item_id


class TestTheKeywordFloor:
    """A relative BM25 floor, applied inside the arm, over rows already ordered by rank."""

    def test_a_cut_is_a_prefix_of_a_descending_list(self):
        assert keyword_score_cut([]) == 0
        assert keyword_score_cut([3.0, 1.0, 0.5]) == 3
        assert keyword_score_cut([3.0, 1.0, 0.01], floor=0.1) == 2
        assert keyword_score_cut([3.0, 0.05, 0.01], floor=0.1) == 1

    def test_a_signal_free_top_score_keeps_everything(self):
        """When every match is clamped there is no distance to threshold on, and cutting
        on noise would drop rows for a reason the scores cannot support."""
        assert keyword_score_cut([0.0, 0.0, 0.0]) == 3
        assert keyword_score_cut([-1.0, -2.0]) == 2

    _CORPUS = (
        (
            "Postgres vacuum runbook",
            "the autovacuum thresholds and the manual vacuum full hatch for postgres",
        ),
        ("Vacuum sealing for sous vide", "a chamber vacuum keeps the bag flat"),
        ("Sourdough hydration log", "the crumb opened at 78 percent"),
        ("Nginx TLS ciphers", "prefer the ChaCha20 suites on mobile"),
        ("Terraform state recovery", "the locked state file needs force unlock"),
        ("Kestrel field notes", "the falcon hunts the open plains"),
    )

    def _corpus(self, store) -> dict[str, str]:
        """Six documents sharing "the" and two sharing "vacuum" — enough that BM25 can tell
        a discriminating term from one it cannot score on, which a three-document corpus
        cannot: there, every term is non-discriminating and the floor correctly cuts
        nothing."""
        return {title: _note(store, title, body) for title, body in self._CORPUS}

    def test_a_common_term_only_match_never_reaches_fusion(self, store):
        """The row a term-ORed query admits and nothing else deserves.

        "the" is in all six documents, so BM25 gives it no discriminating power and the five
        that match ONLY it arrive six orders of magnitude below the one that also matches
        "postgres". Before the floor all six were handed to RRF as ranks 1-6 and the five
        noise rows scored as if they were evidence."""
        ids = self._corpus(store)
        retriever = HybridRetriever(store)

        assert [
            item_id for item_id, _ in retriever._keyword_search("the postgres")
        ] == [ids["Postgres vacuum runbook"]]
        assert {hit["id"] for hit in retriever.search("the postgres", limit=10)} == {
            ids["Postgres vacuum runbook"]
        }

    def test_a_genuine_partial_match_survives_the_floor(self, store):
        """The control. A document sharing one DISTINCTIVE term with the query sits within a
        small factor of the best match and must keep reaching fusion — a floor that took it
        would be trading recall for precision, not improving precision."""
        ids = self._corpus(store)
        retriever = HybridRetriever(store)

        keyword = [
            item_id for item_id, _ in retriever._keyword_search("postgres vacuum")
        ]
        assert keyword == [
            ids["Postgres vacuum runbook"],
            ids["Vacuum sealing for sous vide"],
        ]

    def test_an_unqualified_row_does_not_consume_a_fusion_rank(self, store):
        """Positions are handed out over the QUALIFIED rows. A dropped row that still held
        rank 3 would be scored by RRF as if it had earned that position."""
        ids = self._corpus(store)
        retriever = HybridRetriever(store)

        assert retriever._keyword_search("the vacuum") == [
            (ids["Vacuum sealing for sous vide"], 1),
            (ids["Postgres vacuum runbook"], 2),
        ]


class TestTheGraphsTwoTiers:
    """Direct entity matches and depth-2 neighbours are different evidence, ranked apart."""

    def _corpus(self, store) -> tuple[str, str]:
        """A direct match with ONE mention and a neighbour-only item with THREE.

        The counts are the whole point: under the merged tally these arms used to share,
        three beats one and the neighbour-only item took rank 1.
        """
        named = store.add_entity("Vega", "project")
        direct = _note(store, "Vega decision log", "the launch window was moved")
        store.add_mention(direct, named)

        noisy = _note(store, "Unrelated standup notes", "sprint chatter")
        for satellite in ("Vega-Alpha", "Vega-Beta", "Vega-Gamma"):
            entity = store.add_entity(satellite, "concept")
            store.add_entity_relation(named, entity, "relates_to")
            store.add_mention(noisy, entity)
        store.db.commit()
        return direct, noisy

    def test_the_premise_a_merged_tally_would_rank_the_neighbour_first(self, store):
        """Asserted, not assumed. If the fixture stopped producing 3-vs-1 the precision
        test below would pass for a reason that has nothing to do with the change."""
        direct, noisy = self._corpus(store)
        counts = {
            row["item_id"]: row["cnt"]
            for row in store.db.execute(
                "SELECT item_id, COUNT(*) AS cnt FROM mentions GROUP BY item_id"
            )
        }
        assert counts[noisy] == 3
        assert counts[direct] == 1

    def test_a_direct_match_outranks_a_noisier_neighbour(self, store):
        direct, noisy = self._corpus(store)
        retriever = HybridRetriever(store)

        assert retriever._graph_search("Vega") == [(direct, 1)]
        assert [hit["id"] for hit in retriever.search("Vega", limit=10)] == [direct]
        assert noisy not in {hit["id"] for hit in retriever.search("Vega", limit=10)}

    def test_the_results_say_which_tier_answered(self, store):
        direct, _ = self._corpus(store)
        retriever = HybridRetriever(store)

        kinds: dict[str, str] = {}
        retriever._graph_search("Vega", match_kinds=kinds)
        assert kinds == {direct: GRAPH_MATCH_DIRECT}

        hit = next(h for h in retriever.search("Vega", limit=10) if h["id"] == direct)
        assert hit["graph_match"] == GRAPH_MATCH_DIRECT

    def test_neighbours_still_answer_when_nothing_direct_exists(self, store):
        """The control for the fallback. Neighbours are demoted, not deleted: with no direct
        match the two-hop guess is the best evidence there is, and dropping it would trade
        a precision win for a recall hole."""
        named = store.add_entity("Vega", "project")
        satellite = store.add_entity("Vega-Alpha", "concept")
        store.add_entity_relation(named, satellite, "relates_to")
        reachable = _note(store, "Satellite runbook", "rotation and paging")
        store.add_mention(reachable, satellite)
        store.db.commit()
        retriever = HybridRetriever(store)

        kinds: dict[str, str] = {}
        assert retriever._graph_search("Vega", match_kinds=kinds) == [(reachable, 1)]
        assert kinds == {reachable: GRAPH_MATCH_NEIGHBOR}

        hit = next(
            h for h in retriever.search("Vega", limit=10) if h["id"] == reachable
        )
        assert hit["graph_match"] == GRAPH_MATCH_NEIGHBOR

    def test_a_keyword_hit_keeps_a_null_graph_tier(self, store):
        """``graph_match`` describes the graph arm only. A result the graph never returned
        must not be labelled with one of its tiers."""
        item_id = _note(store, "Postgres vacuum runbook", "autovacuum thresholds")
        retriever = HybridRetriever(store)

        hit = next(h for h in retriever.search("autovacuum", limit=10))
        assert hit["id"] == item_id
        assert hit["graph_match"] is None

    def test_a_direct_match_is_not_inflated_by_neighbour_mentions(self, store):
        """Ordering WITHIN the direct tier is by direct mentions alone. Counting an item's
        neighbour mentions toward its direct score reintroduces the same confusion one
        level down."""
        named = store.add_entity("Vega", "project")
        other = store.add_entity("Lyra", "project")
        satellite = store.add_entity("Vega-Alpha", "concept")
        store.add_entity_relation(named, satellite, "relates_to")

        strong = _note(store, "Vega and Lyra review", "both programmes")
        store.add_mention(strong, named)
        store.add_mention(strong, other)

        padded = _note(store, "Vega mention in passing", "one line")
        store.add_mention(padded, named)
        store.add_mention(padded, satellite)
        store.db.commit()

        retriever = HybridRetriever(store)
        ranked = retriever._graph_search("Vega Lyra")
        assert ranked[0][0] == strong
        assert {item_id for item_id, _ in ranked} == {strong, padded}
