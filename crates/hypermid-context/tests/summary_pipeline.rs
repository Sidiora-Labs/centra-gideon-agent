use hypermid_context::summary_queue::{SummaryQueue, SummaryQueueError};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::summary::{
    SummaryCandidate, SummaryJobSpec, SummaryOutcomeKind, SummarySource, SummaryTier, SummaryUsage,
};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope() -> Scope {
    Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")))
}

fn spec(job_id: &str, session_id: &str, body: &str, sequence: u64) -> SummaryJobSpec {
    SummaryJobSpec {
        job_id: id(job_id),
        source: SummarySource {
            scope: scope(),
            session_id: id(session_id),
            source_start: Cursor::new(1, sequence).unwrap(),
            source_end: Cursor::new(1, sequence + 2).unwrap(),
            source_digest: Digest::sha256(body),
            input_tokens: 64,
        },
        locale: "en-US".into(),
        max_input_tokens: 128,
        max_output_tokens: 64,
    }
}

fn candidate(job: &hypermid_core::summary::SummaryJob) -> SummaryCandidate {
    SummaryCandidate {
        job_id: job.job_id.clone(),
        lease_id: job.lease_id.clone(),
        fence: job.fence,
        source_digest: job.source.source_digest,
        tiers: (0..4)
            .map(|level| SummaryTier::new(level, format!("tier {level}"), 4).unwrap())
            .collect(),
        importance: 0.75,
    }
}

fn usage() -> SummaryUsage {
    SummaryUsage {
        provider: "centra".into(),
        model: "bounded-summary-model".into(),
        input_tokens: Some(64),
        output_tokens: Some(16),
        duration_ms: 20,
    }
}

#[test]
fn leased_publication_is_single_flight_fenced_atomic_and_recoverable() {
    let first = spec("job-1", "session-1", "source one", 1);
    let same_session = spec("job-2", "session-1", "source two", 4);
    let another_session = spec("job-3", "session-2", "source three", 1);
    let mut queue = SummaryQueue::new(2, 3).unwrap();
    assert!(queue.schedule(first.clone(), 1_000).unwrap());
    assert!(queue.schedule(same_session, 1_000).unwrap());
    assert!(queue.schedule(another_session, 1_000).unwrap());

    let first_lease = queue.claim_next(1_000, 100).unwrap().unwrap();
    let parallel_lease = queue.claim_next(1_000, 100).unwrap().unwrap();
    assert_ne!(
        first_lease.source.session_id,
        parallel_lease.source.session_id
    );
    assert!(queue.claim_next(1_000, 100).unwrap().is_none());

    let first_candidate = candidate(&first_lease);
    assert_eq!(
        queue.publish(
            first_candidate.clone(),
            &scope(),
            Digest::sha256("edited source"),
            usage(),
            1_050,
        ),
        Err(SummaryQueueError::StaleSource)
    );
    assert!(queue
        .records_for_session(&scope(), &id("session-1"))
        .is_empty());
    assert_eq!(
        queue.outcomes().last().unwrap().kind,
        SummaryOutcomeKind::StaleSource
    );

    let committed = queue
        .publish(
            candidate(&parallel_lease),
            &scope(),
            parallel_lease.source.source_digest,
            usage(),
            1_060,
        )
        .unwrap();
    assert_eq!(
        queue
            .usage_for(&committed.summary_id)
            .unwrap()
            .output_tokens,
        Some(16)
    );

    let state = serde_json::to_vec(&queue.state()).unwrap();
    let restored = SummaryQueue::from_state(serde_json::from_slice(&state).unwrap()).unwrap();
    assert_eq!(
        restored.records_for_session(&scope(), &parallel_lease.source.session_id),
        vec![committed]
    );

    let stale_job = spec("job-4", "session-4", "stale source", 1);
    let mut retry_queue = SummaryQueue::new(1, 3).unwrap();
    retry_queue.schedule(stale_job, 2_000).unwrap();
    let expired = retry_queue.claim_next(2_000, 10).unwrap().unwrap();
    assert!(retry_queue.claim_next(4_000, 100).unwrap().is_none());
    let replacement = retry_queue.claim_next(5_000, 100).unwrap().unwrap();
    assert_eq!(expired.job_id, replacement.job_id);
    assert!(replacement.fence > expired.fence);
    assert_eq!(
        retry_queue.publish(
            candidate(&expired),
            &scope(),
            expired.source.source_digest,
            usage(),
            5_001,
        ),
        Err(SummaryQueueError::StaleFence)
    );
}
