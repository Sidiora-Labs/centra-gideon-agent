use hypermid_context::cache_store::CacheStore;
use hypermid_context::durable_writer::{DurableWriterAuthority, ScopedWriterLease};
use hypermid_context::journal::{Journal, RawSourceJournal};
use hypermid_context::projector::{
    DeterministicProjector, JournalItemSelection, JournalProjectionRequest, SummarySelection,
};
use hypermid_context::reclaimer::Reclaimer;
use hypermid_context::serializer::HostSerializer;
use hypermid_context::tier_selector::TierSelector;
use hypermid_contracts::{Cursor, Digest, EffectState, Error, Id, Scope, Trace};
use hypermid_core::budget::{HistoryBudget, ProtectedReservations};
use hypermid_core::cache::{
    CacheGeneration, CacheOutcomeKind, CacheTransition, CachedChangeKind, CachedRegion,
    HardBoundaryReason,
};
use hypermid_core::capability::{AuthorizationRequest, CapabilityOperation};
use hypermid_core::decay::{HistoricalSpan, TierSelectionRequest};
use hypermid_core::history::{IngestRequest, JournalRange, PendingContextItem};
use hypermid_core::projection::{ContextMode, ProjectionBudgetInputs, RegionKind};
use hypermid_core::provider::{ProviderGeneration, ProviderProfile};
use hypermid_core::reclaim::{PressureInput, PressurePolicy, ReclaimCandidate, ReclaimDisposition};
use hypermid_core::reduction::ReductionBoundary;
use hypermid_core::summary::{SummaryRecord, SummaryTier, SummaryUsage};
use hypermid_memory::{
    AccessRequest, GrantOperation, MemoryApi, MemoryRecord, RecordKind, RecordStatus,
};
use hypermid_protocol::Envelope;
use hypermid_transport::AuthenticatedSession;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

pub const CONTEXT_OPERATIONS: &[&str] = &["context.primary_project", "context.expand"];
#[derive(Clone, Debug)]
pub struct ContextRouteResponse {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

pub struct ContextRoutes {
    root: PathBuf,
    authority: Arc<DurableWriterAuthority>,
    memory_api: Arc<Mutex<MemoryApi>>,
    state: Mutex<ContextRouteState>,
}

#[derive(Debug, Default)]
struct ContextRouteState {
    caches: HashMap<(Scope, Id), CacheStore>,
    reclaimers: HashMap<(Scope, Id), Reclaimer>,
    tiers: HashMap<(Scope, Id), TierSelector>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrimaryProjectRequest {
    pub session_id: Id,
    pub writer_lease: ScopedWriterLease,
    #[serde(default)]
    pub new_items: Vec<PrimarySourceItem>,
    pub expected_source_cursor: Option<Cursor>,
    pub baseline_through: Option<Cursor>,
    pub delta_through: Option<Cursor>,
    pub generation: u64,
    pub policy_revision: u64,
    pub provider_profile: ProviderProfile,
    pub budget_inputs: ProjectionBudgetInputs,
    pub created_at: String,
    pub cache_boundary: Option<HardBoundaryReason>,
    #[serde(default = "no_cached_change")]
    pub cached_change: CachedChangeKind,
    #[serde(default)]
    pub overflow_requires_cached_change: bool,
    pub reduction_boundary: ReductionBoundary,
    #[serde(default)]
    pub reclaim_policy: PressurePolicy,
    #[serde(default)]
    pub reclaim_candidates: Vec<ReclaimCandidate>,
    pub summary_access: Option<SummaryAccessRequest>,
    #[serde(default)]
    pub summary_sources: Vec<SummaryAccessRequest>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryAccessRequest {
    pub capability_id: Id,
    pub request: AccessRequest,
    pub limit: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrimarySourceItem {
    pub idempotency_key: Id,
    pub item: PendingContextItem,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_snapshot: Option<Vec<u8>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ExpandRequest {
    pub session_id: Id,
    #[serde(default)]
    pub item_ids: Vec<Id>,
    #[serde(default)]
    pub reclaim_tags: Vec<u64>,
}

impl ContextRoutes {
    pub fn open(
        root: impl AsRef<Path>,
        authority: Arc<DurableWriterAuthority>,
        memory_api: Arc<Mutex<MemoryApi>>,
    ) -> Result<Self, std::io::Error> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(&root)?;
        Ok(Self {
            root,
            authority,
            memory_api,
            state: Mutex::new(ContextRouteState::default()),
        })
    }

    pub fn dispatch(
        &self,
        session: &AuthenticatedSession,
        request: &Envelope,
        now_ms: u64,
    ) -> ContextRouteResponse {
        if request.scope.as_ref() != Some(&session.bound_scope) {
            return failure(
                "SCOPE_DENIED",
                "request scope does not match authenticated scope",
                false,
            );
        }
        let Some(payload) = request.payload.clone() else {
            return failure(
                "INVALID_REQUEST",
                "primary projection payload is required",
                false,
            );
        };
        let result = match request.operation.as_deref() {
            Some("context.primary_project") => serde_json::from_value(payload)
                .map_err(|_| RouteFailure::invalid("invalid primary projection payload"))
                .and_then(|parsed| {
                    self.primary_project(session, request.trace.as_ref(), parsed, now_ms)
                }),
            Some("context.expand") => serde_json::from_value(payload)
                .map_err(|_| RouteFailure::invalid("invalid expansion payload"))
                .and_then(|parsed| self.expand(&session.bound_scope, parsed)),
            _ => {
                return failure(
                    "UNKNOWN_OPERATION",
                    "context operation is not available",
                    false,
                )
            }
        };
        match result {
            Ok(payload) => ContextRouteResponse {
                payload: Some(payload),
                error: None,
            },
            Err(error) => failure(error.code, error.message, error.retryable),
        }
    }

    fn primary_project(
        &self,
        session: &AuthenticatedSession,
        trace: Option<&Trace>,
        request: PrimaryProjectRequest,
        now_ms: u64,
    ) -> Result<Value, RouteFailure> {
        let scope = &session.bound_scope;
        if request.new_items.len() > 100_000 || request.created_at.is_empty() {
            return Err(RouteFailure::invalid(
                "new_items or created_at is outside bounds",
            ));
        }
        request
            .provider_profile
            .validate()
            .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        let validated = self
            .authority
            .validate(scope, &request.writer_lease, now_ms)
            .map_err(|_| RouteFailure::fence("writer lease is absent, stale, or uncommitted"))?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| RouteFailure::internal("context route lock is poisoned"))?;
        let journal_root = self.journal_root(scope, &request.session_id)?;
        let epoch = request
            .expected_source_cursor
            .map_or(1, |cursor| cursor.epoch);
        let journal = Journal::open(
            journal_root,
            scope.clone(),
            request.session_id.clone(),
            epoch,
        )
        .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        let source_journal = RawSourceJournal::open(self.source_root(scope, &request.session_id)?)
            .map_err(|error| RouteFailure::invalid(&error.to_string()))?;

        for source in request.new_items {
            if source.item.scope != *scope || source.item.session_id != request.session_id {
                return Err(RouteFailure::denied(
                    "ingest item scope or session does not match authenticated request",
                ));
            }
            let before = journal.current_cursor();
            let snapshot = source
                .source_snapshot
                .ok_or_else(|| RouteFailure::invalid("source snapshot is required"))?;
            source_journal
                .append(
                    scope.clone(),
                    request.session_id.clone(),
                    source.item.source_event_id.clone(),
                    snapshot.clone(),
                )
                .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
            let ingest = IngestRequest {
                expected_cursor: before,
                idempotency_key: route_ingest_id(&source.idempotency_key, before)?,
                item: source.item,
                source_snapshot: Some(snapshot),
            };
            let _committed = journal
                .append(ingest)
                .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        }
        let source_cursor = journal.current_cursor();
        if request
            .expected_source_cursor
            .is_some_and(|expected| expected != source_cursor)
        {
            return Err(RouteFailure::retry(
                "requested source cursor is not the committed journal cursor",
            ));
        }
        let baseline_through = request.baseline_through.unwrap_or(
            Cursor::new(source_cursor.epoch, 0)
                .map_err(|_| RouteFailure::invalid("invalid baseline cursor"))?,
        );
        let delta_through = request.delta_through.unwrap_or(baseline_through);
        if baseline_through.epoch != source_cursor.epoch
            || delta_through.epoch != source_cursor.epoch
            || baseline_through > delta_through
            || delta_through > source_cursor
        {
            return Err(RouteFailure::invalid(
                "projection region cursors are inconsistent",
            ));
        }

        if request.summary_sources.len() > 1 || (!request.summary_sources.is_empty()
            && request.summary_access.as_ref().is_none_or(|access| access.request.target_scope == session.bound_scope)) {
            return Err(RouteFailure::authorization("summary union requires an app primary source"));
        }
        let mut summary_page = match request.summary_access.as_ref() {
            Some(access) => self.read_summaries(session, trace, access, now_ms)?,
            None => SummaryPage::default(),
        };
        for access in &request.summary_sources {
            if access.request.target_scope != session.bound_scope {
                return Err(RouteFailure::authorization("additional summary source must be the bound owner scope"));
            }
            let additional = self.read_summaries(session, trace, access, now_ms)?;
            summary_page.authorized_records += additional.authorized_records;
            summary_page.records.extend(additional.records);
            summary_page.source_cursors.extend(additional.source_cursors);
        }
        let summary_candidates = eligible_summaries(
            &journal,
            scope,
            &request.session_id,
            source_cursor,
            baseline_through,
            delta_through,
            summary_page.records,
        );
        let summary_coverage = summary_candidates
            .iter()
            .flat_map(|selection| {
                selection.record.source_start.sequence..=selection.record.source_end.sequence
            })
            .collect::<BTreeSet<_>>();

        let normalized_items = journal
            .all_items()
            .into_iter()
            .map(|item| {
                let region = if item.cursor <= baseline_through {
                    RegionKind::Baseline
                } else if item.cursor <= delta_through {
                    RegionKind::Delta
                } else {
                    RegionKind::Tail
                };
                let bytes = serde_json::to_vec(&item)
                    .map_err(|_| RouteFailure::internal("journal item serialization failed"))?;
                let token_mass = u64::try_from(bytes.len())
                    .unwrap_or(u64::MAX)
                    .saturating_add(3)
                    / 4;
                Ok((item, region, token_mass.max(1)))
            })
            .collect::<Result<Vec<_>, RouteFailure>>()?;
        let raw_mass = normalized_items
            .iter()
            .filter(|(item, _, _)| !summary_coverage.contains(&item.cursor.sequence))
            .try_fold(0_u64, |total, (_, _, mass)| total.checked_add(*mass))
            .ok_or_else(|| RouteFailure::invalid("raw history mass overflowed"))?;
        let summaries = if summary_candidates.is_empty() {
            Vec::new()
        } else {
            let spans = summary_candidates
                .iter()
                .map(|selection| HistoricalSpan {
                    summary_id: selection.record.summary_id.clone(),
                    tiers: selection
                        .record
                        .tiers
                        .clone()
                        .try_into()
                        .expect("eligible summaries have four tiers"),
                    age_rank: source_cursor
                        .sequence
                        .saturating_sub(selection.record.source_end.sequence),
                    importance_basis_points: (selection.record.importance * 10_000.0)
                        .round()
                        .clamp(0.0, 10_000.0) as u16,
                    protected: false,
                    recurrence: 0,
                    dependency_reach: 0,
                })
                .collect();
            let selection = state
                .tiers
                .entry((scope.clone(), request.session_id.clone()))
                .or_default()
                .select(&TierSelectionRequest {
                    generation: request.generation,
                    budget: HistoryBudget {
                        context_window: request.budget_inputs.max_input_tokens,
                        reservations: ProtectedReservations {
                            provider_output: 0,
                            live_tail: raw_mass,
                            active_tool_pairs: 0,
                            latest_user_request: 0,
                            unresolved_approvals: 0,
                            protected_window: 0,
                        },
                    },
                    spans,
                })
                .map_err(|_| RouteFailure::pressure("summary tiers exceed history budget"))?;
            let levels = selection
                .decisions
                .into_iter()
                .map(|decision| (decision.summary_id, decision.level))
                .collect::<BTreeMap<_, _>>();
            summary_candidates
                .into_iter()
                .map(|mut candidate| {
                    candidate.level = *levels
                        .get(&candidate.record.summary_id)
                        .expect("tier selector returns one decision per summary");
                    candidate
                })
                .collect()
        };
        let item_selections = normalized_items
            .into_iter()
            .filter(|(item, _, _)| !summary_coverage.contains(&item.cursor.sequence))
            .map(|(item, region, token_mass)| JournalItemSelection {
                item_id: item.item_id,
                region,
                token_mass,
            })
            .collect();
        let projection = DeterministicProjector
            .project_journal(
                &journal,
                JournalProjectionRequest {
                    scope: scope.clone(),
                    session_id: request.session_id.clone(),
                    source_cursor,
                    generation: request.generation,
                    policy_revision: request.policy_revision,
                    mode: ContextMode::Primary,
                    provider_profile_digest: request.provider_profile.profile_digest,
                    budget_inputs: request.budget_inputs,
                    created_at: request.created_at,
                    items: item_selections,
                    summaries,
                },
            )
            .map_err(|error| RouteFailure::invalid(&format!("projection refused: {error:?}")))?;

        let key = (scope.clone(), request.session_id.clone());
        let previous_cache = state.caches.get(&key).and_then(CacheStore::snapshot);
        let previous_provider = previous_cache.as_ref().map(|cache| ProviderGeneration {
            generation: cache.generation,
            profile_digest: cache.provider_profile_digest,
        });
        let serialized = HostSerializer
            .serialize(
                &projection,
                &request.provider_profile,
                previous_provider.as_ref(),
            )
            .map_err(|error| {
                RouteFailure::invalid(&format!("provider serialization refused: {error}"))
            })?;

        let pressure = PressureInput {
            calibrated_mass: serialized.model_budget.baseline_tokens
                + serialized.model_budget.delta_tokens
                + serialized.model_budget.tail_tokens,
            safe_input_mass: serialized.model_budget.max_input_tokens,
            cache_generation: projection.generation,
            projection_digest: projection.output_digest,
            policy_revision: projection.policy_revision,
        };
        let reclaim = state
            .reclaimers
            .entry(key.clone())
            .or_default()
            .decide(
                &pressure,
                &request.reclaim_policy,
                request.reduction_boundary,
                &request.reclaim_candidates,
            )
            .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        if reclaim.plan.disposition == ReclaimDisposition::Refused
            || !reclaim.plan.chosen.is_empty()
        {
            return Err(RouteFailure::pressure(
                "projection requires reclaim before dispatch",
            ));
        }

        let next_cache = CacheGeneration {
            generation: projection.generation,
            provider_profile_digest: projection.provider_profile_digest,
            policy_revision: projection.policy_revision,
            baseline: cached_region(&projection.baseline),
            delta: cached_region(&projection.delta),
            live_tail: cached_region(&projection.tail),
            boundary_reason: request.cache_boundary,
        };
        let cache = state.caches.entry(key).or_default();
        let cache_outcome = cache
            .apply(CacheTransition {
                next: next_cache,
                cached_change: request.cached_change,
                boundary: request.cache_boundary,
                overflow_requires_cached_change: request.overflow_requires_cached_change,
            })
            .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        if cache_outcome.kind != CacheOutcomeKind::Applied {
            return Err(RouteFailure::pressure(&cache_outcome.reason_code));
        }

        Ok(json!({
            "projection": projection,
            "serialized": serialized,
            "cache": cache_outcome,
            "reclaim": reclaim.plan,
            "cursor": journal.current_cursor(),
            "writer_fence": validated.lease.fence_token,
            "writer_fence_epoch": validated.lease.fence_epoch,
            "writer_journal_digest": validated.journal_digest,
            "summary": {
                "memory_cursor": summary_page.cursor,
                "source_cursors": summary_page.source_cursors,
                "authorized_records": summary_page.authorized_records,
                "selected_records": projection.baseline.summary_ids.len()
                    + projection.delta.summary_ids.len()
                    + projection.tail.summary_ids.len(),
            },
        }))
    }

    fn read_summaries(
        &self,
        session: &AuthenticatedSession,
        trace: Option<&Trace>,
        access: &SummaryAccessRequest,
        now_ms: u64,
    ) -> Result<SummaryPage, RouteFailure> {
        let trace = trace.ok_or_else(|| RouteFailure::invalid("summary reads require a trace"))?;
        if access.limit == 0
            || access.limit > 1_000
            || access.request.operation != GrantOperation::Read
            || access.request.actor_scope != session.bound_scope
            || access.request.resource_id.as_str() != "memory-list"
            || access.request.category.as_deref() != Some("context_summary")
            || &access.request.trace != trace
        {
            return Err(RouteFailure::authorization(
                "summary access binding is invalid",
            ));
        }
        let context = crate::dispatch::Dispatcher
            .context_for_session(
                session,
                AuthorizationRequest {
                    claimed_scope: session.bound_scope.clone(),
                    target_scope: access.request.target_scope.clone(),
                    operation: CapabilityOperation::Read,
                    resource_id: access.request.resource_id.clone(),
                    now_ms,
                },
                access.capability_id.clone(),
            )
            .map_err(|_| RouteFailure::authorization("summary access was denied"))?;
        let mut api = self
            .memory_api
            .lock()
            .map_err(|_| RouteFailure::internal("memory service lock is poisoned"))?;
        if access.request.target_scope != session.bound_scope {
            if access.request.target_scope.owner_id != session.bound_scope.owner_id
                || access.request.target_scope.project_id != session.bound_scope.project_id {
                return Err(RouteFailure::authorization("summary app scope binding is invalid"));
            }
            let scope_id = access.request.target_scope.workspace_id.clone()
                .ok_or_else(|| RouteFailure::authorization("summary app scope binding is invalid"))?;
            let mut resolve_context = context.clone();
            resolve_context.request.resource_id = Id::new("memory-records")
                .map_err(|_| RouteFailure::internal("summary resource is invalid"))?;
            api.resolve_app_scope(&resolve_context, scope_id)
                .map_err(|_| RouteFailure::authorization("summary app scope is inactive"))?;
        }
        match api.list_records(
            &context,
            &access.request,
            Some("context_summary"),
            Some(RecordStatus::Active),
            access.limit,
        ) {
            Ok(page) => Ok(SummaryPage {
                authorized_records: page.records.len(),
                records: page.records,
                cursor: Some(page.cursor),
                source_cursors: vec![json!({"scope": access.request.target_scope, "cursor": page.cursor})],
            }),
            Err(error) if error.code == "SCOPE_NOT_FOUND" => Ok(SummaryPage::default()),
            Err(error) if error.code == "AUTHORIZATION_DENIED" => {
                Err(RouteFailure::authorization("summary access was denied"))
            }
            Err(_) => Err(RouteFailure::internal("summary records could not be read")),
        }
    }

    fn expand(&self, scope: &Scope, request: ExpandRequest) -> Result<Value, RouteFailure> {
        let selections = request
            .item_ids
            .len()
            .saturating_add(request.reclaim_tags.len());
        if selections == 0
            || selections > 100_000
            || (!request.item_ids.is_empty() && !request.reclaim_tags.is_empty())
        {
            return Err(RouteFailure::invalid(
                "expansion requires exactly one bounded selector",
            ));
        }
        let journal = Journal::open(
            self.journal_root(scope, &request.session_id)?,
            scope.clone(),
            request.session_id.clone(),
            1,
        )
        .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        let source_journal = RawSourceJournal::open(self.source_root(scope, &request.session_id)?)
            .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        let recovered = if request.item_ids.is_empty() {
            journal.recover_tags(&request.reclaim_tags, &source_journal)
        } else {
            journal.recover_item_ids(&request.item_ids, &source_journal)
        }
        .map_err(|error| RouteFailure::invalid(&error.to_string()))?;
        let items = recovered
            .into_iter()
            .map(|entry| {
                let source_digest = Digest::sha256(&entry.source_bytes);
                json!({
                    "item": entry.item,
                    "source_bytes": entry.source_bytes,
                    "source_digest": source_digest,
                })
            })
            .collect::<Vec<_>>();
        Ok(json!({"session_id": request.session_id, "items": items}))
    }

    fn journal_root(&self, scope: &Scope, session_id: &Id) -> Result<PathBuf, RouteFailure> {
        let binding = serde_json::to_vec(&(scope, session_id))
            .map_err(|_| RouteFailure::internal("scope serialization failed"))?;
        Ok(self.root.join(Digest::sha256(binding).to_string()))
    }

    fn source_root(&self, scope: &Scope, session_id: &Id) -> Result<PathBuf, RouteFailure> {
        Ok(self.journal_root(scope, session_id)?.join("raw_sources"))
    }
}

fn cached_region(region: &hypermid_core::projection::ProjectionRegion) -> CachedRegion {
    CachedRegion {
        digest: region.digest,
        bytes: region.bytes.clone(),
        item_ids: region.item_ids.clone(),
        summary_ids: region.summary_ids.clone(),
        token_mass: region.token_mass,
    }
}

#[derive(Default)]
struct SummaryPage {
    source_cursors: Vec<Value>,
    records: Vec<MemoryRecord>,
    cursor: Option<Cursor>,
    authorized_records: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SummaryMetadata {
    session_id: Id,
    source_start: Cursor,
    source_end: Cursor,
    source_digest: Digest,
    tiers: Vec<SummaryTier>,
    usage: SummaryUsage,
}

fn eligible_summaries(
    journal: &Journal,
    scope: &Scope,
    session_id: &Id,
    source_cursor: Cursor,
    baseline_through: Cursor,
    delta_through: Cursor,
    records: Vec<MemoryRecord>,
) -> Vec<SummarySelection> {
    let mut candidates = records
        .into_iter()
        .filter_map(|record| {
            if record.kind != RecordKind::Summary || record.category != "context_summary" {
                return None;
            }
            let metadata: SummaryMetadata =
                serde_json::from_value(Value::Object(record.current.metadata.clone())).ok()?;
            if metadata.session_id != *session_id
                || metadata.usage.validate().is_err()
                || metadata.tiers.first().is_none_or(|tier| {
                    tier.content != record.current.content
                        || tier.content_digest != record.current.content_digest
                })
            {
                return None;
            }
            let summary = SummaryRecord {
                summary_id: record.id,
                scope: scope.clone(),
                session_id: metadata.session_id,
                source_start: metadata.source_start,
                source_end: metadata.source_end,
                source_digest: metadata.source_digest,
                tiers: metadata.tiers,
                importance: record.importance,
                created_at_ms: record.current.authored_at_ms,
            };
            if summary.validate().is_err() || summary.source_end > source_cursor {
                return None;
            }
            let range = JournalRange::new(summary.source_start, summary.source_end).ok()?;
            if journal.source_digest(range).ok()? != summary.source_digest {
                return None;
            }
            let region = summary_region(&summary, baseline_through, delta_through)?;
            Some(SummarySelection {
                record: summary,
                level: 3,
                region,
            })
        })
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| {
        left.record
            .source_start
            .cmp(&right.record.source_start)
            .then_with(|| right.record.source_end.cmp(&left.record.source_end))
            .then_with(|| left.record.summary_id.cmp(&right.record.summary_id))
    });
    let mut occupied = BTreeSet::new();
    candidates
        .into_iter()
        .filter(|candidate| {
            let range =
                candidate.record.source_start.sequence..=candidate.record.source_end.sequence;
            if range.clone().any(|sequence| occupied.contains(&sequence)) {
                return false;
            }
            occupied.extend(range);
            true
        })
        .collect()
}

fn summary_region(
    summary: &SummaryRecord,
    baseline_through: Cursor,
    delta_through: Cursor,
) -> Option<RegionKind> {
    let region = |cursor: Cursor| {
        if cursor <= baseline_through {
            RegionKind::Baseline
        } else if cursor <= delta_through {
            RegionKind::Delta
        } else {
            RegionKind::Tail
        }
    };
    let start = region(summary.source_start);
    (start == region(summary.source_end)).then_some(start)
}

fn no_cached_change() -> CachedChangeKind {
    CachedChangeKind::None
}

fn route_ingest_id(value: &Id, cursor: Cursor) -> Result<Id, RouteFailure> {
    let material = serde_json::to_vec(&(value, cursor))
        .map_err(|_| RouteFailure::internal("could not serialize ingest identity"))?;
    Id::new(format!("route-ingest:{}", Digest::sha256(material)))
        .map_err(|_| RouteFailure::internal("could not create ingest identity"))
}

#[derive(Clone, Copy, Debug)]
struct RouteFailure {
    code: &'static str,
    message: &'static str,
    retryable: bool,
}
impl RouteFailure {
    fn invalid(_message: &str) -> Self {
        Self {
            code: "INVALID_CONTEXT_REQUEST",
            message: "primary context request is invalid",
            retryable: false,
        }
    }
    fn denied(_message: &str) -> Self {
        Self {
            code: "SCOPE_DENIED",
            message: "primary context scope was denied",
            retryable: false,
        }
    }
    fn retry(_message: &str) -> Self {
        Self {
            code: "STALE_CONTEXT_CURSOR",
            message: "primary context cursor is stale",
            retryable: true,
        }
    }
    fn pressure(_message: &str) -> Self {
        Self {
            code: "CONTEXT_PRESSURE_REFUSED",
            message: "primary context projection was refused under pressure",
            retryable: false,
        }
    }
    fn fence(_message: &str) -> Self {
        Self {
            code: "WRITER_FENCE_REJECTED",
            message: "primary context writer lease is not active",
            retryable: false,
        }
    }
    fn authorization(_message: &str) -> Self {
        Self {
            code: "AUTHORIZATION_DENIED",
            message: "context summary access was denied",
            retryable: false,
        }
    }
    fn internal(_message: &str) -> Self {
        Self {
            code: "CONTEXT_ENGINE_FAILED",
            message: "primary context engine failed",
            retryable: true,
        }
    }
}

fn failure(code: &str, message: &str, retryable: bool) -> ContextRouteResponse {
    ContextRouteResponse {
        payload: None,
        error: Some(
            Error::new(
                code,
                message,
                retryable,
                None,
                Some(EffectState::NotStarted),
            )
            .expect("context route errors are static and valid"),
        ),
    }
}
