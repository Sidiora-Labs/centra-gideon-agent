use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct ScoreComponents {
    pub lexical: f64,
    pub semantic: f64,
    pub fusion: f64,
    pub importance: f64,
    pub recency: f64,
    pub verification: f64,
    pub provenance: f64,
    pub stability: f64,
    pub usefulness: f64,
    pub decay: f64,
    pub contradiction: f64,
    pub total: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RankInput {
    pub id: String,
    pub content_digest: String,
    pub kind: String,
    pub verification: String,
    pub importance: f64,
    pub source_time_ms: Option<i64>,
    pub provenance_count: usize,
    pub useful_count: u64,
    pub not_useful_count: u64,
    pub decay: f64,
    pub contradicted: bool,
}

pub fn cosine_similarity(left: &[f32], right: &[f32]) -> Option<f64> {
    if left.is_empty() || left.len() != right.len() {
        return None;
    }
    let mut dot = 0.0_f64;
    let mut left_norm = 0.0_f64;
    let mut right_norm = 0.0_f64;
    for (&left, &right) in left.iter().zip(right) {
        if !left.is_finite() || !right.is_finite() {
            return None;
        }
        let left = f64::from(left);
        let right = f64::from(right);
        dot += left * right;
        left_norm += left * left;
        right_norm += right * right;
    }
    if left_norm == 0.0 || right_norm == 0.0 {
        return None;
    }
    Some(dot / (left_norm.sqrt() * right_norm.sqrt()))
}

pub fn rank(
    inputs: &[RankInput],
    lexical: &HashMap<String, f64>,
    semantic: &HashMap<String, f64>,
    now_ms: i64,
) -> BTreeMap<String, ScoreComponents> {
    let lexical_ranks = ranks(lexical);
    let semantic_ranks = ranks(semantic);
    inputs
        .iter()
        .map(|input| {
            let fusion = lexical_ranks
                .get(&input.id)
                .map_or(0.0, |rank| 0.65 / (60.0 + *rank as f64))
                + semantic_ranks
                    .get(&input.id)
                    .map_or(0.0, |rank| 0.35 / (60.0 + *rank as f64));
            let importance = input.importance.clamp(0.0, 1.0) * 0.08;
            let age = input
                .source_time_ms
                .map_or(0, |time| now_ms.saturating_sub(time).max(0));
            let recency = (-(age as f64) / 2_592_000_000_f64).exp() * 0.04;
            let verification = match input.verification.as_str() {
                "supported" => 0.06,
                "disputed" => -0.04,
                "refuted" => -0.12,
                _ => 0.0,
            };
            let provenance = input.provenance_count.min(3) as f64 * 0.02;
            let stability = if input.kind == "anchor" { 0.05 } else { 0.0 };
            let usefulness = ((input.useful_count as f64 - input.not_useful_count as f64) * 0.005)
                .clamp(-0.03, 0.03);
            let decay = -input.decay.clamp(0.0, 1.0) * 0.08;
            let contradiction = if input.contradicted { -0.04 } else { 0.0 };
            let total = fusion
                + importance
                + recency
                + verification
                + provenance
                + stability
                + usefulness
                + decay
                + contradiction;
            (
                input.id.clone(),
                ScoreComponents {
                    lexical: lexical.get(&input.id).copied().unwrap_or_default(),
                    semantic: semantic.get(&input.id).copied().unwrap_or_default(),
                    fusion,
                    importance,
                    recency,
                    verification,
                    provenance,
                    stability,
                    usefulness,
                    decay,
                    contradiction,
                    total,
                },
            )
        })
        .collect()
}

fn ranks(scores: &HashMap<String, f64>) -> HashMap<String, usize> {
    let mut ordered = scores.iter().collect::<Vec<_>>();
    ordered.sort_by(|(left_id, left_score), (right_id, right_score)| {
        right_score
            .total_cmp(left_score)
            .then_with(|| left_id.cmp(right_id))
    });
    ordered
        .into_iter()
        .enumerate()
        .map(|(index, (id, _))| (id.clone(), index + 1))
        .collect()
}
