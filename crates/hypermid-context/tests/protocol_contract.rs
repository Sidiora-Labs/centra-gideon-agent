use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use hypermid_context::engine::{ContextEngine, EngineError};
use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace};
use hypermid_core::protocol::{
    encode_frame, ChunkAssembler, FrameDecoder, Operation, ProtocolRequest, ProtocolViolation,
    RenderMode, TransferChunk, MAX_ENVELOPE_BYTES, PROTOCOL_VERSION,
};
use serde_json::json;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn scope(owner: &str, workspace: &str) -> Scope {
    Scope::new(id(owner), id("project-1"), Some(id(workspace)))
}

#[test]
fn frames_and_chunks_enforce_protocol_bounds_and_digests() {
    let request = ProtocolRequest {
        protocol_version: PROTOCOL_VERSION.to_owned(),
        operation: Operation::Bind,
        scope: scope("owner-1", "workspace-a"),
        trace: Trace::new(id("trace-1"), id("request-1")),
        session_id: id("session-1"),
        render_mode: RenderMode::HostSerialized,
        expected_cursor: None,
        writer_lease: None,
        idempotency_key: None,
        payload: json!({"mode": "pass_through"}),
    };
    request.validate().unwrap();
    let frame = encode_frame(&request).unwrap();
    let mut decoder = FrameDecoder::new();
    assert!(decoder.feed(&frame[..3]).unwrap().is_empty());
    assert_eq!(decoder.feed(&frame[3..]).unwrap().len(), 1);
    decoder.finish().unwrap();

    let mut oversized = FrameDecoder::new();
    assert_eq!(
        oversized
            .feed(&((MAX_ENVELOPE_BYTES as u32) + 1).to_be_bytes())
            .unwrap_err(),
        ProtocolViolation::EnvelopeTooLarge
    );

    let body = [vec![b'a'; 65_000], vec![b'b'; 1_000]].concat();
    let full_digest = Digest::sha256(&body);
    let parts = [&body[..65_000], &body[65_000..]];
    let chunks: Vec<_> = parts
        .iter()
        .enumerate()
        .map(|(ordinal, part)| TransferChunk {
            protocol_version: PROTOCOL_VERSION.to_owned(),
            transfer_id: id("transfer-1"),
            ordinal: ordinal as u32,
            total: 2,
            chunk_digest: Digest::sha256(part),
            full_digest,
            payload_base64: BASE64.encode(part),
            trace: Trace::new(id("trace-1"), id(&format!("chunk-{ordinal}"))),
        })
        .collect();
    let mut assembler = ChunkAssembler::new();
    assert!(assembler.add(chunks[0].clone()).unwrap().is_none());
    assert_eq!(assembler.add(chunks[1].clone()).unwrap().unwrap(), body);

    let mut out_of_order = ChunkAssembler::new();
    assert_eq!(
        out_of_order.add(chunks[1].clone()).unwrap_err(),
        ProtocolViolation::InvalidChunkOrder
    );
}

#[test]
fn ownership_scope_cursor_fence_and_idempotency_fail_closed() {
    let mut engine = ContextEngine::new();
    let session_id = id("session-1");
    let owned = scope("owner-1", "workspace-a");
    let foreign = scope("owner-2", "workspace-a");
    let initial = engine.bind(session_id.clone(), owned.clone()).unwrap();
    let lease_one = engine
        .acquire_writer(&session_id, &owned, id("lease-1"), 1_000, 1_000)
        .unwrap();

    assert_eq!(
        engine
            .commit(
                &session_id,
                &foreign,
                initial.cursor,
                &lease_one,
                id("mutation-foreign"),
                "ingest",
                json!({"accepted": 1}),
                1_100,
            )
            .unwrap_err(),
        EngineError::ScopeMismatch
    );
    assert_eq!(
        engine.state(&session_id, &owned).unwrap().cursor,
        Cursor::new(1, 0).unwrap()
    );

    let first = engine
        .commit(
            &session_id,
            &owned,
            initial.cursor,
            &lease_one,
            id("mutation-1"),
            "ingest",
            json!({"accepted": 1}),
            1_100,
        )
        .unwrap();
    let replay = engine
        .commit(
            &session_id,
            &owned,
            initial.cursor,
            &lease_one,
            id("mutation-1"),
            "ingest",
            json!({"accepted": 99}),
            1_200,
        )
        .unwrap();
    assert_eq!(first.cursor, Cursor::new(1, 1).unwrap());
    assert_eq!(replay.cursor, first.cursor);
    assert_eq!(replay.result, json!({"accepted": 1}));
    assert!(replay.replayed);

    let lease_two = engine
        .acquire_writer(&session_id, &owned, id("lease-2"), 1_300, 1_000)
        .unwrap();
    assert_eq!(
        engine
            .commit(
                &session_id,
                &owned,
                first.cursor,
                &lease_one,
                id("mutation-2"),
                "ingest",
                json!({"accepted": 1}),
                1_400,
            )
            .unwrap_err(),
        EngineError::StaleFence
    );
    assert_eq!(
        engine
            .commit(
                &session_id,
                &owned,
                Cursor::new(1, 0).unwrap(),
                &lease_two,
                id("mutation-3"),
                "ingest",
                json!({"accepted": 1}),
                1_400,
            )
            .unwrap_err(),
        EngineError::StaleCursor
    );
    assert_eq!(
        engine.state(&session_id, &owned).unwrap().cursor,
        first.cursor
    );
}

#[test]
fn workspace_rebind_is_explicit_and_owner_project_are_immutable() {
    let mut engine = ContextEngine::new();
    let session_id = id("session-1");
    let previous = scope("owner-1", "workspace-a");
    let next = scope("owner-1", "workspace-b");
    let state = engine.bind(session_id.clone(), previous.clone()).unwrap();
    let lease = engine
        .acquire_writer(&session_id, &previous, id("lease-1"), 1_000, 1_000)
        .unwrap();
    assert_eq!(
        engine.bind(session_id.clone(), next.clone()).unwrap_err(),
        EngineError::ScopeMismatch
    );
    let outcome = engine
        .rebind_workspace(
            &session_id,
            &previous,
            next.clone(),
            state.cursor,
            &lease,
            id("rebind-1"),
            1_100,
        )
        .unwrap();
    assert_eq!(outcome.cursor, Cursor::new(1, 1).unwrap());
    assert_eq!(engine.state(&session_id, &next).unwrap().scope, next);

    let next_lease = engine
        .acquire_writer(&session_id, &next, id("lease-2"), 1_200, 1_000)
        .unwrap();
    assert_eq!(
        engine
            .rebind_workspace(
                &session_id,
                &next,
                scope("owner-2", "workspace-b"),
                outcome.cursor,
                &next_lease,
                id("rebind-2"),
                1_300,
            )
            .unwrap_err(),
        EngineError::IdentityChangeRequiresNewSession
    );
}
