"""Versioned descriptive prose metrics over canonical series drafts."""

import json
import math
import re
from collections import Counter
from statistics import mean, pstdev

from .series import SeriesStore
from .store import CatalogError, integer, keys, text

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
METRICS = {
    "sentence_mean": "Sentence length mean",
    "sentence_std": "Sentence length spread",
    "sentence_cv": "Sentence length variation",
    "fragment_pct": "Sentences under five words (%)",
    "long_pct": "Sentences over thirty words (%)",
    "paragraph_mean": "Paragraph length mean",
    "paragraph_std": "Paragraph length spread",
    "dialogue_pct": "Quoted words (%)",
    "emdash_rate": "Em dashes per 1000 words",
    "abstract_rate": "English abstract suffixes per 1000 words",
    "simile_rate": "English comparison patterns per 1000 words",
    "opener_pct": "Dominant sentence opener (%)",
}
DEFAULT = {
    "revision": 0,
    "baseline": "drafted",
    "min_words": 40,
    "min_chapters": 2,
    "z_threshold": 2.0,
    "wells": {},
}


def fingerprint(source, wells=None):
    words = WORD.findall(source.casefold())
    sentences = [WORD.findall(s.casefold()) for s in re.split(r"[.!?。！？]+", source)]
    sentences = [s for s in sentences if s]
    lengths = [len(s) for s in sentences]
    paragraphs = [
        len(WORD.findall(p)) for p in re.split(r"\n\s*\n", source) if WORD.search(p)
    ]
    avg = mean(lengths) if lengths else 0
    std = pstdev(lengths) if lengths else 0
    count = len(words)

    def rate(n):
        return n * 1000 / count if count else 0

    def percentage(n):
        return n * 100 / len(lengths) if lengths else 0

    quoted = sum(len(WORD.findall(s)) for s in re.findall(r'[“"]([^“”"]*)[”"]', source))
    suffixes = (
        "tion",
        "sion",
        "ment",
        "ness",
        "ity",
        "ance",
        "ence",
        "ism",
        "ship",
        "hood",
        "dom",
        "acy",
        "ude",
        "ology",
    )
    abstract = sum(
        any(len(w) >= len(s) + 2 and w.endswith(s) for s in suffixes) for w in words
    )
    similes = len(
        re.findall(r"\blike\s+(?:a|an|the)\b|\bas\s+[a-z]+\s+as\b", source, re.I)
    )
    metrics = {
        "sentence_mean": avg,
        "sentence_std": std,
        "sentence_cv": std / avg if avg else 0,
        "fragment_pct": percentage(sum(n < 5 for n in lengths)),
        "long_pct": percentage(sum(n > 30 for n in lengths)),
        "paragraph_mean": mean(paragraphs) if paragraphs else 0,
        "paragraph_std": pstdev(paragraphs) if paragraphs else 0,
        "dialogue_pct": quoted * 100 / count if count else 0,
        "emdash_rate": rate(source.count("—")),
        "abstract_rate": rate(abstract),
        "simile_rate": rate(similes),
        "opener_pct": percentage(
            max(Counter(s[0] for s in sentences).values(), default=0)
        ),
    }
    metrics.update(
        {
            "well:" + name: rate(sum(w in terms for w in words))
            for name, terms in (wells or {}).items()
        }
    )
    return {
        "words": count,
        "sentences": len(lengths),
        "paragraphs": len(paragraphs),
        "metrics": {k: round(v, 4) for k, v in metrics.items()},
    }


class VoiceStore:
    def __init__(self, series: SeriesStore):
        self.series = series
        with series.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS voice_configs(series_id TEXT, revision INTEGER, record TEXT, PRIMARY KEY(series_id,revision))"
            )

    def _config(self, db, id):
        row = db.execute(
            "SELECT record FROM voice_configs WHERE series_id=? ORDER BY revision DESC LIMIT 1",
            (id,),
        ).fetchone()
        return json.loads(row[0]) if row else {**DEFAULT, "wells": {}}

    def configure(self, id, payload):
        keys(payload, set(DEFAULT) | {"series_revision"})
        expected = integer(payload.get("revision"), 0)
        series_revision = integer(payload.get("series_revision"))
        with self.series.connection() as db:
            series = self.series._series(db, id)
            old = self._config(db, id)
            if old["revision"] != expected or series["revision"] != series_revision:
                raise CatalogError("Series or voice configuration changed; reload", 409)
            config = {k: payload.get(k, old[k]) for k in DEFAULT}
            if config["baseline"] not in ("drafted", "exemplars", "blended"):
                raise CatalogError("Unknown voice baseline")
            integer(config["min_words"], 40, 10000)
            integer(config["min_chapters"], 2, 200)
            threshold = config["z_threshold"]
            if (
                type(threshold) not in (int, float)
                or not math.isfinite(threshold)
                or not 0.5 <= threshold <= 10
            ):
                raise CatalogError("Invalid drift threshold")
            wells = config["wells"]
            if not isinstance(wells, dict) or len(wells) > 12:
                raise CatalogError("Use at most twelve vocabulary groups")
            normalized = {}
            for name, terms in wells.items():
                name = text(name, 40, True).casefold()
                if (
                    name in normalized
                    or not isinstance(terms, list)
                    or not 1 <= len(terms) <= 100
                ):
                    raise CatalogError("Invalid vocabulary group")
                values = [text(term, 80, True).casefold() for term in terms]
                if any(WORD.fullmatch(term) is None for term in values):
                    raise CatalogError("Vocabulary terms must each be one Unicode word")
                normalized[name] = sorted(set(values))
            config.update(revision=expected + 1, wells=normalized)
            db.execute(
                "INSERT INTO voice_configs VALUES(?,?,?)",
                (id, config["revision"], json.dumps(config)),
            )
        return config

    def report(self, id):
        series = self.series.get(id)
        with self.series.connection() as db:
            config = self._config(db, id)
            history = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT record FROM voice_configs WHERE series_id=? ORDER BY revision",
                    (id,),
                )
            ]
        chapters = {c["id"]: c for v in series["volumes"] for c in v["chapters"]}
        rows = []
        for status in series["chapter_status"]:
            row = {
                "chapter_id": status["chapter_id"],
                "title": chapters[status["chapter_id"]]["title"],
                "work_id": status["work_id"],
                "draft_id": status["active_draft_id"],
                "missing": status["missing"] or status["draft_missing"],
                "eligible": False,
                "fingerprint": None,
            }
            if status["work_id"] and status["active_draft_id"] and not row["missing"]:
                draft = self.series.works.read_draft(
                    status["work_id"], status["active_draft_id"]
                )
                row.update(
                    artifact_id=draft["artifact_id"],
                    artifact_version=draft["artifact_version"],
                    characters=len(draft["text"]),
                    missing=draft["missing"],
                )
                if not draft["missing"]:
                    row["fingerprint"] = fingerprint(draft["text"], config["wells"])
                    row["eligible"] = row["fingerprint"]["words"] >= config["min_words"]
            row["gate"] = (
                "source_missing"
                if row["missing"]
                else (
                    "not_drafted"
                    if not row["draft_id"]
                    else "below_min_words" if not row["eligible"] else None
                )
            )
            rows.append(row)
        samples, passages = [], []
        author_missing = False
        if series["author_ref"]:
            try:
                author = self.series.works.authors.export(**series["author_ref"])
            except CatalogError as exc:
                if exc.status != 404:
                    raise
                author_missing = True
            else:
                for ref in author["sample_refs"]:
                    artifact = self.series.works.artifacts.get(
                        ref["artifact_id"], version=ref["artifact_version"]
                    )
                    missing = artifact is None or artifact.kind not in (
                        "text",
                        "markdown",
                    )
                    samples.append(
                        {
                            **ref,
                            "missing": missing,
                            "title": artifact.name if artifact else ref["artifact_id"],
                        }
                    )
                    if not missing:
                        passages.append(artifact.content)
        exemplar = (
            fingerprint("\n\n".join(passages), config["wells"]) if passages else None
        )
        eligible = [r for r in rows if r["eligible"]]
        gate = "below_min_chapters" if len(eligible) < config["min_chapters"] else None
        if config["baseline"] != "drafted" and (
            author_missing
            or any(s["missing"] for s in samples)
            or exemplar is None
            or exemplar["words"] < config["min_words"]
        ):
            gate = "exemplar_missing_or_small"
        baseline, findings = {}, []
        for metric in (*METRICS, *("well:" + name for name in config["wells"])):
            values = [r["fingerprint"]["metrics"][metric] for r in eligible]
            center = mean(values) if values else 0
            spread = pstdev(values) if values else 0
            target = center
            if config["baseline"] != "drafted" and exemplar is not None:
                target = (
                    exemplar["metrics"][metric]
                    if config["baseline"] == "exemplars"
                    else (center + exemplar["metrics"][metric]) / 2
                )
            baseline[metric] = {
                "drafted_mean": round(center, 4) if values else None,
                "center": (
                    round(target, 4)
                    if values and gate != "exemplar_missing_or_small"
                    else None
                ),
                "std": round(spread, 4) if values else None,
            }
            if gate:
                continue
            if spread > 1e-6:
                for row in eligible:
                    value = row["fingerprint"]["metrics"][metric]
                    z = (value - target) / spread
                    if abs(z) >= config["z_threshold"]:
                        findings.append(
                            {
                                "chapter_id": row["chapter_id"],
                                "metric": metric,
                                "value": value,
                                "center": round(target, 4),
                                "z": round(z, 4),
                                "direction": "higher" if z > 0 else "lower",
                                "kind": "chapter_drift",
                            }
                        )
            elif (
                abs(center - target) > 1e-6
                and abs(center - target) / max(abs(center), abs(target)) >= 0.5
            ):
                findings.append(
                    {
                        "chapter_id": None,
                        "metric": metric,
                        "value": round(center, 4),
                        "center": round(target, 4),
                        "z": None,
                        "direction": "higher" if center > target else "lower",
                        "kind": "uniform_corpus_difference",
                    }
                )
        return {
            "schema_version": 1,
            "formula_version": "unicode-prose-v1",
            "series_id": id,
            "series_revision": series["revision"],
            "config": config,
            "config_history": history,
            "rows": rows,
            "baseline": baseline,
            "exemplar": exemplar,
            "author_ref": series["author_ref"],
            "author_missing": author_missing,
            "samples": samples,
            "gate": gate,
            "findings": findings,
            "metric_labels": {
                **METRICS,
                **{
                    "well:" + name: "Vocabulary: " + name + " per 1000 words"
                    for name in config["wells"]
                },
            },
            "method": "Unicode letter words with internal apostrophes; punctuation sentence boundaries; blank-line paragraphs. Population spread. English suffix/comparison heuristics. Full saved draft and exemplar coverage; descriptive signals, not quality or authorship judgments.",
        }

    def export(self, id):
        return self.report(id)
