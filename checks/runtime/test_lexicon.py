"""Tests for the Lexicon (core LEX): phonetics, store, and service behaviors."""

from __future__ import annotations

from gideon.cognition.lexicon.phonetics import (
    double_metaphone,
    phonetic_keys,
    sounds_like,
)
from gideon.cognition.lexicon.service import LexiconService
from gideon.cognition.lexicon.store import LexiconStore
from gideon.integrations.stt.provider import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)


class TestPhonetics:
    def test_returns_keys(self):
        p, s = double_metaphone("Kubernetes")
        assert p and isinstance(p, str) and len(p) <= 4

    def test_homophones_share_key(self):
        assert sounds_like("Nero", "Niro")

    def test_distinct_words_differ(self):
        assert not sounds_like("elephant", "kubernetes")

    def test_empty_is_safe(self):
        assert double_metaphone("") == ("", "")
        assert phonetic_keys("123") == []


class TestStore:
    def _store(self, tmp_path):
        return LexiconStore(str(tmp_path / "lex.db"))

    def test_upsert_and_list(self, tmp_path):
        s = self._store(tmp_path)
        s.upsert_term(
            "t1", "Kubernetes", phonetic_keys=["KPRN"], entity_type="tech", weight=3.0
        )
        terms = s.list_terms()
        assert len(terms) == 1 and terms[0].canonical == "Kubernetes"
        assert s.count_terms() == 1

    def test_manual_source_not_downgraded_by_graph(self, tmp_path):
        s = self._store(tmp_path)
        s.upsert_term("t1", "Foo", source="manual")
        s.upsert_term("t1", "Foo", source="graph")
        assert s.list_terms()[0].source == "manual"

    def test_phonetic_index_lookup(self, tmp_path):
        s = self._store(tmp_path)
        s.upsert_term("t1", "Nero", phonetic_keys=["NR"])
        hits = s.terms_for_phonetic_key("NR")
        assert len(hits) == 1 and hits[0].canonical == "Nero"

    def test_correction_count_and_auto_apply_threshold(self, tmp_path):
        s = self._store(tmp_path)
        c1 = s.upsert_correction("niro", "Nero", threshold=2)
        assert c1.count == 1 and c1.auto_apply is False
        c2 = s.upsert_correction("niro", "Nero", threshold=2)
        assert c2.count == 2 and c2.auto_apply is True
        assert s.auto_corrections() == {"niro": "Nero"}

    def test_delete_and_disable(self, tmp_path):
        s = self._store(tmp_path)
        s.upsert_term("t1", "X")
        assert s.set_enabled("t1", False) is True
        assert s.list_terms()[0].enabled is False
        assert s.delete_term("t1") is True
        assert s.count_terms() == 0


class TestService:
    def _svc(self, tmp_path):
        return LexiconService(LexiconStore(str(tmp_path / "lex.db")))

    def test_rebuild_from_graph(self, tmp_path):
        svc = self._svc(tmp_path)
        n = svc.rebuild_from_graph(
            [
                {
                    "id": "e1",
                    "name": "Kubernetes",
                    "entity_type": "tech",
                    "aliases": ["K8s"],
                },
                {"id": "e2", "name": "Gideon", "entity_type": "project", "aliases": []},
            ]
        )
        assert n == 2 and svc.store.count_terms() == 2

    def test_rebuild_prunes_stale_graph_terms(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.rebuild_from_graph(
            [{"id": "e1", "name": "Alpha"}, {"id": "e2", "name": "Beta"}]
        )
        svc.add_manual_term("Gamma")
        svc.rebuild_from_graph([{"id": "e1", "name": "Alpha"}])
        names = {t.canonical for t in svc.store.list_terms()}
        assert names == {"Alpha", "Gamma"}
        beta_keys = phonetic_keys("Beta")
        assert all(not svc.store.terms_for_phonetic_key(k) for k in beta_keys)

    def test_rebuild_keeps_user_pruned_graph_term_disabled(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.rebuild_from_graph([{"id": "e1", "name": "Alpha"}])
        tid = svc.store.list_terms()[0].id
        svc.store.set_enabled(tid, False)
        svc.rebuild_from_graph([{"id": "e1", "name": "Alpha"}])
        assert svc.store.list_terms()[0].enabled is False

    def test_learn_correction_adds_term_masked_by_superstring(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.add_manual_term("Kubernetes Cluster")
        svc.learn_correction("coober", "Kubernetes", always=True)
        names = {t.canonical for t in svc.store.list_terms()}
        assert "Kubernetes" in names
        kube = svc.store.get_term_by_canonical("Kubernetes")
        assert kube is not None and kube.weight > 2.0

    def test_select_bias_terms_context_first(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.rebuild_from_graph(
            [{"id": "e1", "name": "Alpha"}, {"id": "e2", "name": "Beta"}]
        )
        bias = svc.select_bias_terms(context_terms=["Gamma"], budget=5)
        assert bias[0] == "Gamma"
        assert "Alpha" in bias and "Beta" in bias

    def test_correct_proposes_low_confidence_phonetic_match(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.rebuild_from_graph(
            [{"id": "e1", "name": "Kubernetes", "entity_type": "tech"}]
        )
        r = TranscriptResult(
            text="deploy to kubernetis",
            segments=[
                TranscriptSegment(
                    0,
                    2,
                    "deploy to kubernetis",
                    words=[
                        TranscriptWord(0, 0.5, "deploy", 0.98),
                        TranscriptWord(0.5, 0.7, "to", 0.99),
                        TranscriptWord(0.7, 1.6, "kubernetis", 0.4),
                    ],
                )
            ],
        )
        outcome = svc.correct(r)
        assert any(c.suggested == "Kubernetes" for c in outcome.suggested)

    def test_correct_auto_applies_learned_correction(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.learn_correction("niro", "Nero", always=True)
        r = TranscriptResult(
            text="ask niro about it",
            segments=[
                TranscriptSegment(
                    0,
                    2,
                    "ask niro about it",
                    words=[
                        TranscriptWord(0, 0.3, "ask", 0.9),
                        TranscriptWord(0.3, 0.8, "niro", 0.95),
                        TranscriptWord(0.8, 1.2, "about", 0.9),
                        TranscriptWord(1.2, 1.5, "it", 0.9),
                    ],
                )
            ],
        )
        outcome = svc.correct(r)
        assert any(c.suggested == "Nero" for c in outcome.applied)
        assert "Nero" in r.text

    def test_learn_correction_flips_auto_apply_at_threshold(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.learn_correction("niro", "Nero")
        assert svc.store.auto_corrections() == {}
        svc.learn_correction("niro", "Nero")
        assert svc.store.auto_corrections() == {"niro": "Nero"}

    def test_truncating_mishearing_matched_by_prefix(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.rebuild_from_graph(
            [{"id": "e1", "name": "Kubernetes", "entity_type": "tech"}]
        )
        r = TranscriptResult(
            text="cubeer scales",
            segments=[
                TranscriptSegment(
                    0,
                    1,
                    "cubeer scales",
                    words=[
                        TranscriptWord(0, 0.5, "Cubeer", 0.27),
                        TranscriptWord(0.5, 1.0, "scales", 0.9),
                    ],
                )
            ],
        )
        outcome = svc.correct(r)
        assert any(c.suggested == "Kubernetes" for c in outcome.suggested)

    def test_stop_words_not_corrected(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.rebuild_from_graph([{"id": "e1", "name": "The", "entity_type": "x"}])
        r = TranscriptResult(
            text="the cat",
            segments=[
                TranscriptSegment(
                    0,
                    1,
                    "the cat",
                    words=[
                        TranscriptWord(0, 0.5, "the", 0.3),
                        TranscriptWord(0.5, 1, "cat", 0.3),
                    ],
                )
            ],
        )
        outcome = svc.correct(r)
        assert not any(c.heard == "the" for c in outcome.applied + outcome.suggested)
