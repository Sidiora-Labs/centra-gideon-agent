use crate::fts;
use crate::model::scope_digest;
use crate::rank::{cosine_similarity, rank, RankInput, ScoreComponents};
use crate::{error, AccessRequest, AuthContext, GrantOperation, MemoryResult, MemoryStore};
use hypermid_contracts::{Cursor, Digest, EffectState, Scope, Trace};
use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::str::FromStr;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SearchMode {
    Lexical,
    Semantic,
    Hybrid,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CandidateVector {
    pub values: Vec<f32>,
    pub fingerprint: Digest,
    pub input_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Candidate {
    pub id: String,
    #[serde(rename = "scope")]
    pub owner_scope: Scope,
    pub readable_scopes: HashSet<Scope>,
    pub source: String,
    pub kind: String,
    pub category: String,
    pub content: String,
    pub content_digest: Digest,
    pub status: String,
    pub expires_at_ms: Option<i64>,
    pub source_time_ms: Option<i64>,
    pub importance: f64,
    pub verification: String,
    pub provenance: Vec<String>,
    pub contradiction_group: Option<String>,
    pub vector: Option<CandidateVector>,
    pub decay: f64,
    pub useful_count: u64,
    pub not_useful_count: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SearchRequest {
    pub scope: Scope,
    pub mode: SearchMode,
    pub limit: usize,
    pub candidate_limit_per_source: usize,
    pub include_archived: bool,
    pub visible_digests: HashSet<Digest>,
    pub query_vector: Option<Vec<f32>>,
    pub vector_fingerprint: Option<Digest>,
    pub semantic_required: bool,
    pub now_ms: i64,
    pub from_ms: Option<i64>,
    pub to_ms: Option<i64>,
    pub sources: HashSet<String>,
    pub kinds: HashSet<String>,
    pub categories: HashSet<String>,
    pub trace: Trace,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StoredSearchRequest {
    pub query: String,
    pub scope: Scope,
    pub mode: SearchMode,
    pub limit: usize,
    pub candidate_limit_per_source: usize,
    pub include_archived: bool,
    #[serde(default)]
    pub visible_digests: HashSet<Digest>,
    pub query_vector: Option<Vec<f32>>,
    pub vector_fingerprint: Option<Digest>,
    pub semantic_required: bool,
    pub now_ms: i64,
    pub from_ms: Option<i64>,
    pub to_ms: Option<i64>,
    #[serde(default)]
    pub sources: HashSet<String>,
    #[serde(default)]
    pub kinds: HashSet<String>,
    #[serde(default)]
    pub categories: HashSet<String>,
    pub cursor: Option<Cursor>,
    pub trace: Trace,
}

impl StoredSearchRequest {
    fn ranking_request(&self) -> SearchRequest {
        SearchRequest {
            scope: self.scope.clone(),
            mode: self.mode,
            limit: self.limit,
            candidate_limit_per_source: self.candidate_limit_per_source,
            include_archived: self.include_archived,
            visible_digests: self.visible_digests.clone(),
            query_vector: self.query_vector.clone(),
            vector_fingerprint: self.vector_fingerprint,
            semantic_required: self.semantic_required,
            now_ms: self.now_ms,
            from_ms: self.from_ms,
            to_ms: self.to_ms,
            sources: self.sources.clone(),
            kinds: self.kinds.clone(),
            categories: self.categories.clone(),
            trace: self.trace.clone(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SearchHit {
    #[serde(flatten)]
    pub candidate: Candidate,
    pub scores: ScoreComponents,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct SuppressionCounts {
    pub unauthorized: usize,
    pub state: usize,
    pub stale: usize,
    pub visible: usize,
    pub duplicate: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SearchResponse {
    pub hits: Vec<SearchHit>,
    pub cursor: Cursor,
    pub suppressed: SuppressionCounts,
    pub degraded: bool,
    pub trace: Trace,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SearchFailure {
    SemanticUnavailable,
}

pub(crate) fn search_candidates(
    request: &SearchRequest,
    candidates: Vec<Candidate>,
    lexical: HashMap<String, f64>,
    cursor: Cursor,
) -> Result<SearchResponse, SearchFailure> {
    let mut suppressed = SuppressionCounts::default();
    let mut eligible = Vec::new();
    for candidate in candidates {
        if candidate.owner_scope != request.scope
            && !candidate.readable_scopes.contains(&request.scope)
        {
            suppressed.unauthorized += 1;
            continue;
        }
        if matches!(candidate.status.as_str(), "tombstoned" | "stale")
            || (candidate.status == "archived" && !request.include_archived)
            || candidate
                .expires_at_ms
                .is_some_and(|expiry| expiry <= request.now_ms)
        {
            suppressed.state += 1;
            continue;
        }
        if request
            .from_ms
            .is_some_and(|from| candidate.source_time_ms.is_some_and(|time| time < from))
            || request
                .to_ms
                .is_some_and(|to| candidate.source_time_ms.is_some_and(|time| time > to))
            || (!request.sources.is_empty() && !request.sources.contains(&candidate.source))
            || (!request.kinds.is_empty() && !request.kinds.contains(&candidate.kind))
            || (!request.categories.is_empty() && !request.categories.contains(&candidate.category))
        {
            suppressed.state += 1;
            continue;
        }
        if request.visible_digests.contains(&candidate.content_digest) {
            suppressed.visible += 1;
            continue;
        }
        eligible.push(candidate);
    }

    let degraded = request.mode != SearchMode::Lexical
        && (request.query_vector.is_none()
            || request.vector_fingerprint.is_none()
            || cosine_similarity(
                request.query_vector.as_deref().unwrap_or_default(),
                request.query_vector.as_deref().unwrap_or_default(),
            )
            .is_none());
    if degraded && (request.semantic_required || request.mode == SearchMode::Semantic) {
        return Err(SearchFailure::SemanticUnavailable);
    }
    let mut semantic = HashMap::new();
    if request.mode != SearchMode::Lexical && !degraded {
        let query = request.query_vector.as_deref().expect("checked above");
        let fingerprint = request.vector_fingerprint.expect("checked above");
        eligible.retain(|candidate| {
            let Some(vector) = &candidate.vector else {
                return true;
            };
            let similarity = cosine_similarity(query, &vector.values);
            if vector.fingerprint != fingerprint
                || vector.input_digest != candidate.content_digest
                || similarity.is_none()
            {
                suppressed.stale += 1;
                return false;
            }
            semantic.insert(
                candidate.id.clone(),
                similarity.expect("checked above").max(0.0),
            );
            true
        });
    }
    let rank_inputs = eligible
        .iter()
        .map(|candidate| RankInput {
            id: candidate.id.clone(),
            content_digest: candidate.content_digest.to_hex(),
            kind: candidate.kind.clone(),
            verification: candidate.verification.clone(),
            importance: candidate.importance,
            source_time_ms: candidate.source_time_ms,
            provenance_count: candidate.provenance.len(),
            useful_count: candidate.useful_count,
            not_useful_count: candidate.not_useful_count,
            decay: candidate.decay,
            contradicted: candidate.contradiction_group.is_some(),
        })
        .collect::<Vec<_>>();
    let scores = rank(&rank_inputs, &lexical, &semantic, request.now_ms);
    let mut hits = eligible
        .into_iter()
        .filter(|candidate| {
            lexical.contains_key(&candidate.id)
                || (request.mode != SearchMode::Lexical && semantic.contains_key(&candidate.id))
        })
        .map(|candidate| SearchHit {
            scores: scores[&candidate.id].clone(),
            candidate,
        })
        .collect::<Vec<_>>();
    hits.sort_by(|left, right| {
        right
            .scores
            .total
            .total_cmp(&left.scores.total)
            .then_with(|| {
                left.candidate
                    .content_digest
                    .cmp(&right.candidate.content_digest)
            })
            .then_with(|| left.candidate.id.cmp(&right.candidate.id))
    });
    let mut seen = HashSet::new();
    let mut source_counts = HashMap::<String, usize>::new();
    hits.retain(|hit| {
        if !seen.insert(hit.candidate.content_digest) {
            suppressed.duplicate += 1;
            return false;
        }
        let count = source_counts
            .entry(hit.candidate.source.clone())
            .or_default();
        if *count >= request.candidate_limit_per_source {
            return false;
        }
        *count += 1;
        true
    });
    hits.truncate(request.limit);
    Ok(SearchResponse {
        hits,
        cursor,
        suppressed,
        degraded,
        trace: request.trace.clone(),
    })
}

impl MemoryStore {
    pub fn search_stored(
        &mut self,
        context: &AuthContext,
        access: &AccessRequest,
        request: &StoredSearchRequest,
    ) -> MemoryResult<SearchResponse> {
        if access.operation != GrantOperation::Search
            || access.target_scope != request.scope
            || access.trace != request.trace
        {
            return Err(error(
                "AUTHORIZATION_DENIED",
                "search target scope and trace must match the authorized access request",
                EffectState::NotStarted,
            ));
        }
        validate_stored_request(request)?;
        self.immediate_access(context, access, |transaction, _authorization| {
            let cursor = transaction.cursor(&access.target_scope)?;
            if request.cursor.is_some_and(|expected| expected != cursor) {
                return Err(error(
                    "STALE_CURSOR",
                    "search cursor no longer matches durable memory state",
                    EffectState::NotStarted,
                ));
            }
            let (candidates, lexical) = stored_candidates(transaction.raw(), request)?;
            search_candidates(&request.ranking_request(), candidates, lexical, cursor).map_err(
                |failure| match failure {
                    SearchFailure::SemanticUnavailable => error(
                        "SEARCH_SEMANTIC_UNAVAILABLE",
                        "semantic retrieval requires a compatible query vector",
                        EffectState::NotStarted,
                    ),
                },
            )
        })
    }
}

fn validate_stored_request(request: &StoredSearchRequest) -> MemoryResult<()> {
    if request.query.trim().is_empty()
        || request.query.len() > 32_768
        || request.limit == 0
        || request.limit > 200
        || request.candidate_limit_per_source == 0
        || request.candidate_limit_per_source > 1_000
    {
        return Err(error(
            "INVALID_ARGUMENT",
            "search query and limits are outside the supported bounds",
            EffectState::NotStarted,
        ));
    }
    if request.query_vector.is_some() != request.vector_fingerprint.is_some()
        || request
            .query_vector
            .as_ref()
            .is_some_and(|vector| vector.is_empty() || vector.len() > 65_536)
    {
        return Err(error(
            "INVALID_ARGUMENT",
            "query vector and fingerprint must be supplied together",
            EffectState::NotStarted,
        ));
    }
    Ok(())
}

fn stored_candidates(
    connection: &Connection,
    request: &StoredSearchRequest,
) -> MemoryResult<(Vec<Candidate>, HashMap<String, f64>)> {
    let scope = scope_digest(&request.scope).to_hex();
    let bound = request
        .limit
        .saturating_mul(request.candidate_limit_per_source)
        .clamp(request.limit, 1_000);
    let mut lexical = HashMap::new();
    let mut memory_ids = HashSet::new();
    if request.mode != SearchMode::Semantic {
        for hit in fts::lexical_matches(connection, &request.query, &scope, bound)? {
            let id: Option<String> = connection
                .query_row(
                    "SELECT record_id FROM memory_fts_rows WHERE rowid=?1",
                    [hit.rowid],
                    |row| row.get(0),
                )
                .optional()
                .map_err(read_error)?;
            if let Some(id) = id {
                lexical.insert(id.clone(), hit.score);
                memory_ids.insert(id);
            }
        }
    }
    if request.mode != SearchMode::Lexical {
        if let Some(fingerprint) = request.vector_fingerprint {
            let mut statement = connection
                .prepare(
                    r#"SELECT r.record_id
                       FROM memory_records r
                       JOIN memory_embeddings e ON e.record_id=r.record_id
                                                AND e.revision_digest=r.current_revision_digest
                       JOIN embedding_registrations g ON g.registration_id=e.registration_id
                       WHERE r.owner_scope_digest=?1 AND g.owner_scope_digest=?1
                         AND g.fingerprint=?2 AND g.state='active'
                       ORDER BY r.record_id LIMIT ?3"#,
                )
                .map_err(read_error)?;
            let rows = statement
                .query_map(params![scope, fingerprint.to_hex(), bound as i64], |row| {
                    row.get::<_, String>(0)
                })
                .map_err(read_error)?;
            for id in rows {
                memory_ids.insert(id.map_err(read_error)?);
            }
        }
    }

    let mut candidates = Vec::new();
    for id in memory_ids {
        if let Some(candidate) = load_memory_candidate(connection, request, &scope, &id)? {
            candidates.push(candidate);
        }
    }
    if request.mode != SearchMode::Semantic {
        for (candidate, score) in source_candidates(connection, request, &scope, bound)? {
            lexical.insert(candidate.id.clone(), score);
            candidates.push(candidate);
        }
    }
    Ok((candidates, lexical))
}

fn load_memory_candidate(
    connection: &Connection,
    request: &StoredSearchRequest,
    scope_digest: &str,
    id: &str,
) -> MemoryResult<Option<Candidate>> {
    type MemoryRow = (
        String,
        String,
        String,
        Option<i64>,
        i64,
        f64,
        String,
        String,
        String,
        i64,
        u64,
        u64,
    );
    let row: Option<MemoryRow> = connection
        .query_row(
            r#"SELECT r.kind, r.category, r.status, r.expires_at_ms, r.updated_at_ms,
                      r.importance, r.verification_state, v.content, v.content_digest,
                      v.authored_at_ms, COALESCE(s.useful_count,0),
                      COALESCE(s.not_useful_count,0)
               FROM memory_records r
               JOIN memory_revisions v ON v.record_id=r.record_id AND v.revision=r.current_revision
               LEFT JOIN memory_retrieval_stats s ON s.record_id=r.record_id
               WHERE r.record_id=?1 AND r.owner_scope_digest=?2"#,
            params![id, scope_digest],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                    row.get(7)?,
                    row.get(8)?,
                    row.get(9)?,
                    row.get(10)?,
                    row.get(11)?,
                ))
            },
        )
        .optional()
        .map_err(read_error)?;
    let Some((
        kind,
        category,
        status,
        expires_at_ms,
        updated_at_ms,
        importance,
        verification,
        content,
        content_digest,
        authored_at_ms,
        useful_count,
        not_useful_count,
    )) = row
    else {
        return Ok(None);
    };
    let content_digest = parse_digest(&content_digest)?;
    let provenance = provenance_ids(connection, id)?;
    let contradiction_group: Option<String> = connection
        .query_row(
            r#"SELECT CASE WHEN child_record_id=?1 THEN parent_record_id ELSE child_record_id END
               FROM memory_lineage
               WHERE relation='contradicts' AND (child_record_id=?1 OR parent_record_id=?1)
               ORDER BY 1 LIMIT 1"#,
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(read_error)?;
    let vector = request.vector_fingerprint.map_or(Ok(None), |fingerprint| {
        load_candidate_vector(connection, id, scope_digest, fingerprint)
    })?;
    Ok(Some(Candidate {
        id: id.to_owned(),
        owner_scope: request.scope.clone(),
        readable_scopes: HashSet::new(),
        source: "memory".to_owned(),
        kind,
        category,
        content,
        content_digest,
        status,
        expires_at_ms,
        source_time_ms: Some(authored_at_ms.max(updated_at_ms)),
        importance,
        verification,
        provenance,
        contradiction_group,
        vector,
        decay: 0.0,
        useful_count,
        not_useful_count,
    }))
}

fn provenance_ids(connection: &Connection, id: &str) -> MemoryResult<Vec<String>> {
    let mut statement = connection
        .prepare(
            r#"SELECT DISTINCT p.source_id
               FROM memory_provenance p
               JOIN memory_records r ON r.record_id=p.record_id AND r.current_revision=p.revision
               JOIN memory_sources s ON s.source_id=p.source_id
               WHERE p.record_id=?1
               ORDER BY p.source_id"#,
        )
        .map_err(read_error)?;
    let result = statement
        .query_map([id], |row| row.get(0))
        .map_err(read_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(read_error);
    result
}

fn load_candidate_vector(
    connection: &Connection,
    id: &str,
    scope_digest: &str,
    fingerprint: Digest,
) -> MemoryResult<Option<CandidateVector>> {
    let row: Option<(String, Vec<u8>, usize)> = connection
        .query_row(
            r#"SELECT v.content_digest, e.vector_f32, e.dimensions
               FROM memory_records r
               JOIN memory_revisions v ON v.record_id=r.record_id AND v.revision=r.current_revision
               JOIN memory_embeddings e ON e.record_id=r.record_id
                                        AND e.revision_digest=r.current_revision_digest
               JOIN embedding_registrations g ON g.registration_id=e.registration_id
               WHERE r.record_id=?1 AND r.owner_scope_digest=?2
                 AND g.owner_scope_digest=?2 AND g.fingerprint=?3 AND g.state='active'
               ORDER BY e.created_at_ms DESC LIMIT 1"#,
            params![id, scope_digest, fingerprint.to_hex()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .optional()
        .map_err(read_error)?;
    row.map(|(input_digest, bytes, dimensions)| {
        if bytes.len() != dimensions.saturating_mul(4) {
            return Err(corrupt_search("stored embedding dimensions are invalid"));
        }
        let values = bytes
            .chunks_exact(4)
            .map(|chunk| f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
            .collect::<Vec<_>>();
        Ok(CandidateVector {
            values,
            fingerprint,
            input_digest: parse_digest(&input_digest)?,
        })
    })
    .transpose()
}

fn source_candidates(
    connection: &Connection,
    request: &StoredSearchRequest,
    scope_digest: &str,
    limit: usize,
) -> MemoryResult<Vec<(Candidate, f64)>> {
    let query = fts::fts_query(&request.query);
    if query.is_empty() || limit == 0 {
        return Ok(Vec::new());
    }
    type SourceRow = (
        String,
        String,
        String,
        String,
        String,
        Option<i64>,
        String,
        Option<i64>,
        f64,
    );
    let mut statement = connection
        .prepare(
            r#"SELECT d.document_id, d.source_kind, d.source_key, d.content,
                      d.content_digest, d.source_time_ms, d.metadata_json,
                      d.tombstoned_at_ms, -bm25(source_fts, 0.0, 0.0, 0.0, 1.0) AS score
               FROM source_fts
               JOIN source_fts_rows f ON f.rowid=source_fts.rowid
               JOIN source_index_documents d ON d.document_id=f.document_id
               WHERE source_fts MATCH ?1 AND d.owner_scope_digest=?2
               ORDER BY score DESC, d.document_id ASC LIMIT ?3"#,
        )
        .map_err(read_error)?;
    let rows = statement
        .query_map(params![query, scope_digest, limit as i64], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
                row.get(5)?,
                row.get(6)?,
                row.get(7)?,
                row.get(8)?,
            ))
        })
        .map_err(read_error)?;
    let mut candidates = Vec::new();
    for row in rows {
        let (
            id,
            source_kind,
            source_key,
            content,
            content_digest,
            source_time_ms,
            metadata_json,
            tombstoned_at_ms,
            score,
        ): SourceRow = row.map_err(read_error)?;
        let metadata: serde_json::Value = serde_json::from_str(&metadata_json)
            .map_err(|_| corrupt_search("source index metadata is invalid"))?;
        let category = metadata
            .get("category")
            .and_then(serde_json::Value::as_str)
            .unwrap_or(&source_kind)
            .to_owned();
        candidates.push((
            Candidate {
                id: id.clone(),
                owner_scope: request.scope.clone(),
                readable_scopes: HashSet::new(),
                source: source_kind.clone(),
                kind: source_kind,
                category,
                content,
                content_digest: parse_digest(&content_digest)?,
                status: if tombstoned_at_ms.is_some() {
                    "tombstoned"
                } else {
                    "active"
                }
                .to_owned(),
                expires_at_ms: None,
                source_time_ms,
                importance: 0.5,
                verification: "unverified".to_owned(),
                provenance: vec![source_key],
                contradiction_group: None,
                vector: None,
                decay: 0.0,
                useful_count: 0,
                not_useful_count: 0,
            },
            score,
        ));
    }
    Ok(candidates)
}

fn parse_digest(value: &str) -> MemoryResult<Digest> {
    Digest::from_str(value).map_err(|_| corrupt_search("stored digest is invalid"))
}

fn corrupt_search(message: &'static str) -> hypermid_contracts::Error {
    error("CORRUPT_STORE", message, EffectState::Unknown)
}

fn read_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "SEARCH_READ_FAILED",
        format!("search could not read authoritative memory rows: {source}"),
        EffectState::Unknown,
    )
}

pub fn record_explicit_delivery(
    counts: &mut HashMap<String, u64>,
    response: &SearchResponse,
    automatic: bool,
) {
    if automatic {
        return;
    }
    for hit in &response.hits {
        *counts.entry(hit.candidate.id.clone()).or_default() += 1;
    }
}

pub fn persist_explicit_delivery(
    transaction: &Transaction<'_>,
    response: &SearchResponse,
    now_ms: i64,
    automatic: bool,
) -> Result<(), rusqlite::Error> {
    if automatic {
        return Ok(());
    }
    for hit in response
        .hits
        .iter()
        .filter(|hit| hit.candidate.source == "memory")
    {
        transaction.execute(
            r#"INSERT INTO memory_retrieval_stats(
                 record_id, explicit_retrieval_count, last_explicit_retrieval_at_ms,
                 useful_count, not_useful_count, updated_at_ms
               ) VALUES (?1, 1, ?2, 0, 0, ?2)
               ON CONFLICT(record_id) DO UPDATE SET
                 explicit_retrieval_count=memory_retrieval_stats.explicit_retrieval_count + 1,
                 last_explicit_retrieval_at_ms=excluded.last_explicit_retrieval_at_ms,
                 updated_at_ms=excluded.updated_at_ms"#,
            params![hit.candidate.id, now_ms],
        )?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        embedding::{
            self, validate_provider_embedding, EmbeddingMode, EmbeddingRegistration,
            ProviderEmbedding,
        },
        provenance::{ProvenanceSpan, SourceKind, SourceSnapshot},
        AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant, CapabilityOperation,
        MemoryApi, MutationRequest, Operation, PrincipalKind, RecordDraft, RecordKind,
        RevisionPrecondition,
    };
    use hypermid_contracts::Id;
    use hypermid_store::authorization::put_grant;
    use rusqlite::Connection;
    use serde_json::Map;
    use std::collections::BTreeSet;

    fn scope(owner: &str) -> Scope {
        Scope::new(Id::new(owner).unwrap(), Id::new("project").unwrap(), None)
    }

    fn candidate(id: &str, owner: &str, content: &str) -> Candidate {
        let digest = Digest::sha256(content);
        Candidate {
            id: id.into(),
            owner_scope: scope(owner),
            readable_scopes: HashSet::new(),
            source: "memory".into(),
            kind: "fact".into(),
            category: "general".into(),
            content: content.into(),
            content_digest: digest,
            status: "active".into(),
            expires_at_ms: None,
            source_time_ms: Some(100),
            importance: 0.5,
            verification: "supported".into(),
            provenance: vec!["source".into()],
            contradiction_group: None,
            vector: Some(CandidateVector {
                values: vec![1.0, 0.0],
                fingerprint: Digest::sha256("model"),
                input_digest: digest,
            }),
            decay: 0.0,
            useful_count: 0,
            not_useful_count: 0,
        }
    }

    fn api_principal(owner: &str) -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: Id::new(&format!("principal-{owner}")).unwrap(),
            owner_id: Id::new(owner).unwrap(),
            kind: PrincipalKind::Foreground,
        }
    }

    fn install_api_grant(
        api: &mut MemoryApi,
        principal: &AuthenticatedPrincipal,
        owner: &Scope,
        capability_id: &Id,
    ) {
        api.store.ensure_scope(owner, 1).unwrap();
        let grant = CapabilityGrant {
            capability_id: capability_id.clone(),
            issuer_owner_id: owner.owner_id.clone(),
            principal_id: principal.principal_id.clone(),
            claimed_scope: owner.clone(),
            target_scope: owner.clone(),
            operations: BTreeSet::from([
                CapabilityOperation::Append,
                CapabilityOperation::Read,
                CapabilityOperation::Revise,
                CapabilityOperation::Delete,
            ]),
            resources: BTreeSet::from([Id::new("memory-records").unwrap()]),
            expires_at_ms: 10_000,
        };
        api.store
            .immediate(|transaction| {
                put_grant(transaction.raw(), principal, &grant).unwrap();
                Ok(())
            })
            .unwrap();
    }

    fn api_context(
        principal: &AuthenticatedPrincipal,
        owner: &Scope,
        capability_id: &Id,
        operation: CapabilityOperation,
        now_ms: u64,
    ) -> AuthContext {
        AuthContext {
            principal: principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: owner.clone(),
                target_scope: owner.clone(),
                operation,
                resource_id: Id::new("memory-records").unwrap(),
                now_ms,
            },
            capability_id: capability_id.clone(),
        }
    }

    fn api_mutation(
        owner: &Scope,
        operation: Operation,
        record_id: &Id,
        revision: RevisionPrecondition,
        suffix: &str,
    ) -> MutationRequest {
        MutationRequest {
            operation,
            actor_scope: owner.clone(),
            target_scope: owner.clone(),
            record_id: Some(record_id.clone()),
            category: (operation != Operation::Embed).then(|| "general".to_owned()),
            revision,
            trace: Trace::new(
                Id::new(&format!("trace-{suffix}")).unwrap(),
                Id::new(&format!("request-{suffix}")).unwrap(),
            ),
        }
    }

    fn api_record_draft(owner: &Scope, record_id: &Id, content: &str) -> RecordDraft {
        RecordDraft {
            id: record_id.clone(),
            scope: owner.clone(),
            kind: RecordKind::Fact,
            category: "general".to_owned(),
            content: content.to_owned(),
            metadata: Map::new(),
            importance: 0.7,
            confidence: 0.8,
            expires_at_ms: None,
            retention_until_ms: Some(20_000),
            provenance: vec![ProvenanceSpan {
                source_id: Id::new(&format!("source-{record_id}")).unwrap(),
                span_start: None,
                span_end: None,
                quoted_digest: Some(Digest::sha256(content.as_bytes())),
            }],
            lineage: Vec::new(),
            smart_predicate: None,
            summary: None,
        }
    }

    fn api_source(owner: &Scope, record_id: &Id, content: &str, now_ms: u64) -> SourceSnapshot {
        SourceSnapshot {
            source_id: Id::new(&format!("source-{record_id}")).unwrap(),
            owner_scope_digest: scope_digest(owner),
            kind: SourceKind::Message,
            source_digest: Digest::sha256(content.as_bytes()),
            locator: Some(format!("session:{record_id}")),
            captured_content: Some(content.to_owned()),
            capture_method: "canonical-test-vector".to_owned(),
            observed_at_ms: now_ms,
        }
    }

    fn create_api_record(
        api: &mut MemoryApi,
        principal: &AuthenticatedPrincipal,
        owner: &Scope,
        capability_id: &Id,
        record_id: &str,
        content: &str,
        now_ms: u64,
    ) -> crate::RecordMutation {
        let record_id = Id::new(record_id).unwrap();
        let draft = api_record_draft(owner, &record_id, content);
        let source = api_source(owner, &record_id, content, now_ms);
        api.create_record(
            &api_context(
                principal,
                owner,
                capability_id,
                CapabilityOperation::Append,
                now_ms,
            ),
            &api_mutation(
                owner,
                Operation::Create,
                &record_id,
                RevisionPrecondition::MustNotExist,
                record_id.as_str(),
            ),
            &draft,
            &[source],
            now_ms,
        )
        .unwrap()
    }

    fn register_api_embedding(
        api: &mut MemoryApi,
        principal: &AuthenticatedPrincipal,
        owner: &Scope,
        capability_id: &Id,
        registration: &EmbeddingRegistration,
        now_ms: u64,
    ) {
        api.register_embedding(
            &api_context(
                principal,
                owner,
                capability_id,
                CapabilityOperation::Revise,
                now_ms,
            ),
            &api_mutation(
                owner,
                Operation::Embed,
                &registration.registration_id,
                RevisionPrecondition::MustNotExist,
                registration.registration_id.as_str(),
            ),
            registration,
            now_ms,
        )
        .unwrap();
    }

    fn publish_api_vector(
        api: &mut MemoryApi,
        registration: &EmbeddingRegistration,
        record: &crate::MemoryRecord,
        values: Vec<f32>,
        now_ms: u64,
    ) {
        let guard = api
            .store
            .read(|connection| embedding::publication_guard(connection, &record.id, registration))
            .unwrap();
        let candidate = validate_provider_embedding(
            registration,
            record.current.content_digest,
            ProviderEmbedding {
                provider_identity: registration.provider_identity.clone(),
                model_id: registration.model_id.clone(),
                vector: values,
                input_tokens: Some(1),
                cost_units: Some(0.0),
            },
        )
        .unwrap();
        api.store
            .immediate(|transaction| {
                embedding::publish_embedding(transaction.raw(), &guard, candidate, now_ms)
                    .map(|_| ())
            })
            .unwrap();
    }

    #[test]
    fn search_filters_before_stable_hybrid_ranking_and_separates_delivery_counters() {
        let owner = scope("alice");
        let trace = Trace::new(Id::new("trace").unwrap(), Id::new("request").unwrap());
        let visible = candidate("visible", "alice", "already visible");
        let mut request = SearchRequest {
            scope: owner,
            mode: SearchMode::Hybrid,
            limit: 10,
            candidate_limit_per_source: 10,
            include_archived: false,
            visible_digests: HashSet::from([visible.content_digest]),
            query_vector: Some(vec![1.0, 0.0]),
            vector_fingerprint: Some(Digest::sha256("model")),
            semantic_required: false,
            now_ms: 200,
            from_ms: None,
            to_ms: None,
            sources: HashSet::new(),
            kinds: HashSet::new(),
            categories: HashSet::new(),
            trace,
        };
        let mut archived = candidate("archived", "alice", "old");
        archived.status = "archived".into();
        let foreign = candidate("foreign", "bob", "private");
        let first = candidate("first", "alice", "alpha");
        let stale_digest = candidate("stale-vector", "alice", "changed");
        let mut stale_vector = stale_digest.clone();
        stale_vector.vector.as_mut().unwrap().input_digest = Digest::sha256("old");
        let lexical = HashMap::from([("first".into(), 1.0), ("stale-vector".into(), 0.5)]);
        let response = search_candidates(
            &request,
            vec![foreign, archived, visible, first, stale_vector],
            lexical,
            Cursor::new(1, 7).unwrap(),
        )
        .unwrap();
        assert_eq!(response.hits[0].candidate.id, "first");
        assert_eq!(
            response.suppressed,
            SuppressionCounts {
                unauthorized: 1,
                state: 1,
                stale: 1,
                visible: 1,
                duplicate: 0
            }
        );
        assert!(!response.degraded);
        let mut counts = HashMap::new();
        record_explicit_delivery(&mut counts, &response, true);
        assert!(counts.is_empty());
        record_explicit_delivery(&mut counts, &response, false);
        assert_eq!(counts["first"], 1);
        let mut connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE TABLE memory_retrieval_stats(\
                record_id TEXT PRIMARY KEY, explicit_retrieval_count INTEGER NOT NULL,\
                last_explicit_retrieval_at_ms INTEGER, useful_count INTEGER NOT NULL,\
                not_useful_count INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);",
            )
            .unwrap();
        let transaction = connection.transaction().unwrap();
        persist_explicit_delivery(&transaction, &response, 300, true).unwrap();
        assert_eq!(
            transaction
                .query_row("SELECT count(*) FROM memory_retrieval_stats", [], |row| row
                    .get::<_, i64>(0))
                .unwrap(),
            0
        );
        persist_explicit_delivery(&transaction, &response, 300, false).unwrap();
        assert_eq!(transaction.query_row("SELECT explicit_retrieval_count FROM memory_retrieval_stats WHERE record_id='first'", [], |row| row.get::<_, i64>(0)).unwrap(), 1);
        request.mode = SearchMode::Lexical;
        let wire = serde_json::to_string(&request).unwrap();
        let decoded: SearchRequest = serde_json::from_str(&wire).unwrap();
        assert_eq!(decoded.mode, SearchMode::Lexical);
    }

    #[test]
    fn search_storage_boundary_requires_matching_live_access() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let owner = scope("alice");
        store.ensure_scope(&owner, 100).unwrap();
        let resource_id = Id::new("memory-search").unwrap();
        let capability_id = Id::new("cap-search").unwrap();
        let principal = AuthenticatedPrincipal {
            principal_id: Id::new("principal").unwrap(),
            owner_id: Id::new("alice").unwrap(),
            kind: PrincipalKind::Foreground,
        };
        let trace = Trace::new(Id::new("trace").unwrap(), Id::new("request").unwrap());
        let access = AccessRequest {
            operation: GrantOperation::Search,
            actor_scope: owner.clone(),
            target_scope: owner.clone(),
            resource_id: resource_id.clone(),
            category: None,
            trace: trace.clone(),
        };
        let context = AuthContext {
            principal: principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: owner.clone(),
                target_scope: owner.clone(),
                operation: CapabilityOperation::Read,
                resource_id: resource_id.clone(),
                now_ms: 100,
            },
            capability_id: capability_id.clone(),
        };
        let grant = CapabilityGrant {
            capability_id,
            issuer_owner_id: owner.owner_id.clone(),
            principal_id: principal.principal_id.clone(),
            claimed_scope: owner.clone(),
            target_scope: owner.clone(),
            operations: BTreeSet::from([CapabilityOperation::Read]),
            resources: BTreeSet::from([resource_id]),
            expires_at_ms: 200,
        };
        store
            .immediate(|transaction| {
                put_grant(transaction.raw(), &principal, &grant).unwrap();
                let owner_digest = scope_digest(&owner).to_hex();
                let content_digest = Digest::sha256("launch").to_hex();
                let revision_digest = Digest::sha256("revision").to_hex();
                transaction
                    .raw()
                    .execute(
                        r#"INSERT INTO memory_records(
                         record_id, owner_scope_digest, kind, category, status,
                         current_revision, current_revision_digest, normalized_content_digest,
                         importance, confidence, verification_state, expires_at_ms,
                         retention_until_ms, created_at_ms, updated_at_ms, deleted_at_ms
                       ) VALUES ('record', ?1, 'fact', 'general', 'active', 1, ?2, ?3,
                                 0.5, 0.5, 'supported', NULL, NULL, 100, 100, NULL)"#,
                        params![owner_digest, revision_digest, content_digest],
                    )
                    .unwrap();
                transaction
                    .raw()
                    .execute(
                        r#"INSERT INTO memory_revisions(
                         record_id, revision, revision_digest, parent_revision_digest, content,
                         content_digest, metadata_json, smart_predicate_json,
                         author_scope_digest, authored_at_ms, immutable_anchor
                       ) VALUES ('record', 1, ?1, NULL, 'launch', ?2, '{}', NULL, ?3, 100, 0)"#,
                        params![revision_digest, content_digest, owner_digest],
                    )
                    .unwrap();
                fts::index_memory_record(
                    transaction.raw(),
                    "record",
                    &owner_digest,
                    "general",
                    "launch",
                    &revision_digest,
                )
                .unwrap();
                Ok(())
            })
            .unwrap();
        let request = StoredSearchRequest {
            query: "launch".to_owned(),
            scope: owner.clone(),
            mode: SearchMode::Lexical,
            limit: 1,
            candidate_limit_per_source: 1,
            include_archived: false,
            visible_digests: HashSet::new(),
            query_vector: None,
            vector_fingerprint: None,
            semantic_required: false,
            now_ms: 100,
            from_ms: None,
            to_ms: None,
            sources: HashSet::new(),
            kinds: HashSet::new(),
            categories: HashSet::new(),
            cursor: None,
            trace,
        };
        let response = store.search_stored(&context, &access, &request).unwrap();
        assert_eq!(response.hits[0].candidate.id, "record");
        let mut forged = request.clone();
        forged.scope = scope("mallory");
        assert_eq!(
            store
                .search_stored(&context, &access, &forged)
                .unwrap_err()
                .code,
            "AUTHORIZATION_DENIED"
        );
    }

    #[test]
    fn memory_api_sqlite_hybrid_search_fences_scope_fingerprint_digest_and_tombstones() {
        let directory = tempfile::tempdir().unwrap();
        let mut api = MemoryApi::open(directory.path().join("memory.sqlite3")).unwrap();
        let alice = scope("alice");
        let bob = scope("bob");
        let alice_principal = api_principal("alice");
        let bob_principal = api_principal("bob");
        let alice_capability = Id::new("cap-alice-search").unwrap();
        let bob_capability = Id::new("cap-bob-search").unwrap();
        install_api_grant(&mut api, &alice_principal, &alice, &alice_capability);
        install_api_grant(&mut api, &bob_principal, &bob, &bob_capability);

        let retired_registration = EmbeddingRegistration::new(
            Id::new("embedding-retired").unwrap(),
            alice.clone(),
            EmbeddingMode::Local,
            "canonical-test-vector",
            "retired-axis-2d",
            2,
            false,
        )
        .unwrap();
        register_api_embedding(
            &mut api,
            &alice_principal,
            &alice,
            &alice_capability,
            &retired_registration,
            100,
        );
        let wrong_fingerprint = create_api_record(
            &mut api,
            &alice_principal,
            &alice,
            &alice_capability,
            "wrong-fingerprint",
            "retired vector only",
            110,
        );
        publish_api_vector(
            &mut api,
            &retired_registration,
            &wrong_fingerprint.record,
            vec![1.0, 0.0],
            120,
        );

        let active_registration = EmbeddingRegistration::new(
            Id::new("embedding-active").unwrap(),
            alice.clone(),
            EmbeddingMode::Local,
            "canonical-test-vector",
            "active-axis-2d",
            2,
            false,
        )
        .unwrap();
        register_api_embedding(
            &mut api,
            &alice_principal,
            &alice,
            &alice_capability,
            &active_registration,
            130,
        );

        let alpha = create_api_record(
            &mut api,
            &alice_principal,
            &alice,
            &alice_capability,
            "alpha",
            "launch checklist alpha",
            140,
        );
        publish_api_vector(
            &mut api,
            &active_registration,
            &alpha.record,
            vec![1.0, 0.0],
            150,
        );
        let beta = create_api_record(
            &mut api,
            &alice_principal,
            &alice,
            &alice_capability,
            "beta",
            "launch checklist beta",
            160,
        );
        publish_api_vector(
            &mut api,
            &active_registration,
            &beta.record,
            vec![0.8, 0.2],
            170,
        );

        let stale = create_api_record(
            &mut api,
            &alice_principal,
            &alice,
            &alice_capability,
            "stale-digest",
            "stale vector only",
            180,
        );
        publish_api_vector(
            &mut api,
            &active_registration,
            &stale.record,
            vec![1.0, 0.0],
            190,
        );
        let stale_update = "updated content without the old vector";
        let stale_source_id = Id::new("source-stale-update").unwrap();
        let mut stale_draft = api_record_draft(&alice, &stale.record.id, stale_update);
        stale_draft.provenance[0].source_id = stale_source_id.clone();
        let mut stale_source = api_source(&alice, &stale.record.id, stale_update, 200);
        stale_source.source_id = stale_source_id;
        stale_source.locator = Some("session:stale-update".to_owned());
        api.update_record(
            &api_context(
                &alice_principal,
                &alice,
                &alice_capability,
                CapabilityOperation::Revise,
                200,
            ),
            &api_mutation(
                &alice,
                Operation::Update,
                &stale.record.id,
                RevisionPrecondition::Match(stale.record.current.digest),
                "stale-update",
            ),
            &stale_draft,
            &[stale_source],
            200,
        )
        .unwrap();

        let tombstoned = create_api_record(
            &mut api,
            &alice_principal,
            &alice,
            &alice_capability,
            "tombstoned",
            "deleted vector only",
            210,
        );
        publish_api_vector(
            &mut api,
            &active_registration,
            &tombstoned.record,
            vec![1.0, 0.0],
            220,
        );
        api.delete_record(
            &api_context(
                &alice_principal,
                &alice,
                &alice_capability,
                CapabilityOperation::Delete,
                230,
            ),
            &api_mutation(
                &alice,
                Operation::Delete,
                &tombstoned.record.id,
                RevisionPrecondition::Match(tombstoned.record.current.digest),
                "tombstone",
            ),
            230,
        )
        .unwrap();

        let bob_registration = EmbeddingRegistration::new(
            Id::new("embedding-bob").unwrap(),
            bob.clone(),
            EmbeddingMode::Local,
            "canonical-test-vector",
            "active-axis-2d",
            2,
            false,
        )
        .unwrap();
        register_api_embedding(
            &mut api,
            &bob_principal,
            &bob,
            &bob_capability,
            &bob_registration,
            240,
        );
        assert_eq!(
            bob_registration.fingerprint,
            active_registration.fingerprint
        );
        let foreign = create_api_record(
            &mut api,
            &bob_principal,
            &bob,
            &bob_capability,
            "foreign",
            "launch checklist foreign",
            250,
        );
        publish_api_vector(
            &mut api,
            &bob_registration,
            &foreign.record,
            vec![1.0, 0.0],
            260,
        );

        let trace = Trace::new(
            Id::new("trace-api-search").unwrap(),
            Id::new("request-api-search").unwrap(),
        );
        let access = AccessRequest {
            operation: GrantOperation::Search,
            actor_scope: alice.clone(),
            target_scope: alice.clone(),
            resource_id: Id::new("memory-records").unwrap(),
            category: None,
            trace: trace.clone(),
        };
        let search_context = api_context(
            &alice_principal,
            &alice,
            &alice_capability,
            CapabilityOperation::Read,
            300,
        );
        let request = StoredSearchRequest {
            query: "launch checklist".to_owned(),
            scope: alice,
            mode: SearchMode::Hybrid,
            limit: 10,
            candidate_limit_per_source: 10,
            include_archived: false,
            visible_digests: HashSet::new(),
            query_vector: Some(vec![1.0, 0.0]),
            vector_fingerprint: Some(active_registration.fingerprint),
            semantic_required: true,
            now_ms: 300,
            from_ms: None,
            to_ms: None,
            sources: HashSet::new(),
            kinds: HashSet::new(),
            categories: HashSet::new(),
            cursor: None,
            trace,
        };

        let first = api.search(&search_context, &access, &request).unwrap();
        let second = api.search(&search_context, &access, &request).unwrap();
        let first_ids = first
            .hits
            .iter()
            .map(|hit| hit.candidate.id.as_str())
            .collect::<Vec<_>>();
        let second_ids = second
            .hits
            .iter()
            .map(|hit| hit.candidate.id.as_str())
            .collect::<Vec<_>>();
        assert_eq!(first_ids, vec!["alpha", "beta"]);
        assert_eq!(second_ids, first_ids);
        assert_eq!(first.cursor, second.cursor);
        assert!(!first.degraded);
        assert!(first.hits.iter().all(|hit| {
            hit.scores.lexical > 0.0
                && hit.scores.semantic > 0.0
                && hit.scores.total.is_finite()
                && hit.scores.provenance > 0.0
                && hit.candidate.provenance == vec![format!("source-{}", hit.candidate.id)]
        }));
        assert_eq!(
            first
                .hits
                .iter()
                .map(|hit| hit.scores.clone())
                .collect::<Vec<_>>(),
            second
                .hits
                .iter()
                .map(|hit| hit.scores.clone())
                .collect::<Vec<_>>()
        );
        let rendered = format!("{first:?}");
        assert!(!rendered.contains("foreign"));
        assert!(!rendered.contains("wrong-fingerprint"));
        assert!(!rendered.contains("stale-digest"));
        assert!(!rendered.contains("tombstoned"));

        let mut semantic_request = request.clone();
        semantic_request.mode = SearchMode::Semantic;
        semantic_request.query = "semantic-only".to_owned();
        let semantic = api
            .search(&search_context, &access, &semantic_request)
            .unwrap();
        assert_eq!(
            semantic
                .hits
                .iter()
                .map(|hit| hit.candidate.id.as_str())
                .collect::<Vec<_>>(),
            vec!["alpha", "beta"]
        );
    }
}
