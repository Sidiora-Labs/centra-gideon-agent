use hypermid_context::journal::{Journal, RawSourceJournal};
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::history::{
    ContextPart, HistoryViolation, IngestRequest, JournalRange, PartKind, PendingContextItem, Role,
};
use hypermid_core::identity::{IdentityRelation, RelationKind};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn new() -> Self {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path =
            std::env::temp_dir().join(format!("hypermid-history-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn text_part(part_id: &str, text: &str) -> ContextPart {
    ContextPart {
        part_id: id(part_id),
        kind: PartKind::Text,
        content_digest: Digest::sha256(text.as_bytes()),
        text: Some(text.to_owned()),
        call_id: None,
        tool_name: None,
        arguments_json: None,
        result_json: None,
        media_type: None,
        source_uri: None,
        width: None,
        height: None,
        metadata: None,
    }
}

fn pending(
    item_id: &str,
    event_id: &str,
    source: &[u8],
    scope: &Scope,
    session_id: &Id,
    role: Role,
    parts: Vec<ContextPart>,
    relations: Vec<IdentityRelation>,
) -> PendingContextItem {
    PendingContextItem {
        item_id: id(item_id),
        source_event_id: id(event_id),
        source_digest: Digest::sha256(source),
        scope: scope.clone(),
        session_id: session_id.clone(),
        role,
        parts,
        relations,
        created_at: "2026-10-02T12:00:00Z".into(),
        recoverable: true,
        tombstone: false,
    }
}

fn structured_digest(fields: &[&[u8]]) -> Digest {
    let mut bytes = Vec::new();
    for field in fields {
        bytes.extend_from_slice(&(field.len() as u64).to_be_bytes());
        bytes.extend_from_slice(field);
    }
    Digest::sha256(bytes)
}

#[test]
fn immutable_journal_recovers_exact_authoritative_sources_and_preserves_relations() {
    let directory = TestDirectory::new();
    let scope = Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")));
    let session_id = id("session-1");
    let sources = RawSourceJournal::open(directory.0.join("sources")).unwrap();
    let journal = Journal::open(
        directory.0.join("context"),
        scope.clone(),
        session_id.clone(),
        1,
    )
    .unwrap();

    let raw_user = br#"{"role":"user","content":"inspect the journal"}"#.to_vec();
    sources
        .append(
            scope.clone(),
            session_id.clone(),
            id("event-user"),
            raw_user.clone(),
        )
        .unwrap();
    let first_request = IngestRequest {
        expected_cursor: Cursor::new(1, 0).unwrap(),
        idempotency_key: id("append-user"),
        item: pending(
            "item-user",
            "event-user",
            &raw_user,
            &scope,
            &session_id,
            Role::User,
            vec![text_part("part-user", "inspect the journal")],
            vec![],
        ),
        source_snapshot: Some(raw_user.clone()),
    };
    let first = journal.append(first_request.clone()).unwrap();
    assert_eq!(first.cursor, Cursor::new(1, 1).unwrap());
    assert_eq!(journal.append(first_request).unwrap(), first);
    assert_eq!(journal.current_cursor(), Cursor::new(1, 1).unwrap());

    let call_id = id("call-1");
    let arguments = r#"{"path":"GOTCHA.kvx"}"#;
    let raw_call = br#"{"role":"assistant","tool":"read"}"#.to_vec();
    sources
        .append(
            scope.clone(),
            session_id.clone(),
            id("event-call"),
            raw_call.clone(),
        )
        .unwrap();
    let call_part = ContextPart {
        part_id: id("part-call"),
        kind: PartKind::ToolCall,
        content_digest: structured_digest(&[
            call_id.as_str().as_bytes(),
            b"read",
            arguments.as_bytes(),
        ]),
        text: None,
        call_id: Some(call_id.clone()),
        tool_name: Some("read".into()),
        arguments_json: Some(arguments.into()),
        result_json: None,
        media_type: None,
        source_uri: None,
        width: None,
        height: None,
        metadata: None,
    };
    let call = journal
        .append(IngestRequest {
            expected_cursor: first.cursor,
            idempotency_key: id("append-call"),
            item: pending(
                "item-call",
                "event-call",
                &raw_call,
                &scope,
                &session_id,
                Role::Assistant,
                vec![call_part],
                vec![IdentityRelation::new(
                    RelationKind::Continues,
                    first.item_id.clone(),
                    Some(first.source_digest),
                )],
            ),
            source_snapshot: None,
        })
        .unwrap();

    let result = r#"{"status":"ok"}"#;
    let raw_result = br#"{"role":"tool","content":{"status":"ok"}}"#.to_vec();
    sources
        .append(
            scope.clone(),
            session_id.clone(),
            id("event-result"),
            raw_result.clone(),
        )
        .unwrap();
    let result_part = ContextPart {
        part_id: id("part-result"),
        kind: PartKind::ToolResult,
        content_digest: structured_digest(&[call_id.as_str().as_bytes(), result.as_bytes()]),
        text: None,
        call_id: Some(call_id),
        tool_name: None,
        arguments_json: None,
        result_json: Some(result.into()),
        media_type: None,
        source_uri: None,
        width: None,
        height: None,
        metadata: None,
    };
    let tool_result = journal
        .append(IngestRequest {
            expected_cursor: call.cursor,
            idempotency_key: id("append-result"),
            item: pending(
                "item-result",
                "event-result",
                &raw_result,
                &scope,
                &session_id,
                Role::Tool,
                vec![result_part],
                vec![],
            ),
            source_snapshot: None,
        })
        .unwrap();

    let raw_edit = br#"{"role":"user","content":"inspect exact history"}"#.to_vec();
    sources
        .append(
            scope.clone(),
            session_id.clone(),
            id("event-edit"),
            raw_edit.clone(),
        )
        .unwrap();
    let edit = journal
        .append(IngestRequest {
            expected_cursor: tool_result.cursor,
            idempotency_key: id("append-edit"),
            item: pending(
                "item-edit",
                "event-edit",
                &raw_edit,
                &scope,
                &session_id,
                Role::User,
                vec![text_part("part-edit", "inspect exact history")],
                vec![IdentityRelation::new(
                    RelationKind::Supersedes,
                    first.item_id.clone(),
                    Some(first.source_digest),
                )],
            ),
            source_snapshot: Some(raw_edit.clone()),
        })
        .unwrap();
    assert_eq!(edit.cursor.sequence, 4);
    assert_eq!(journal.all_items()[0], first);

    let recovered_by_id = journal
        .recover_item_ids(&[first.item_id.clone()], &sources)
        .unwrap();
    assert_eq!(recovered_by_id[0].source_bytes, raw_user);
    let recovered_by_tag = journal
        .recover_tags(&[edit.reclaim_tag()], &sources)
        .unwrap();
    assert_eq!(recovered_by_tag[0].source_bytes, raw_edit);
    let range = JournalRange::new(first.cursor, edit.cursor).unwrap();
    assert_eq!(journal.recover_range(range, &sources).unwrap().len(), 4);
    assert_eq!(journal.source_digest(range).unwrap(), {
        let items = journal.all_items();
        hypermid_core::history::range_digest(items.iter())
    });

    let stale_source = br#"{"role":"user","content":"stale"}"#.to_vec();
    let stale = journal.append(IngestRequest {
        expected_cursor: first.cursor,
        idempotency_key: id("append-stale"),
        item: pending(
            "item-stale",
            "event-stale",
            &stale_source,
            &scope,
            &session_id,
            Role::User,
            vec![text_part("part-stale", "stale")],
            vec![],
        ),
        source_snapshot: Some(stale_source),
    });
    assert!(matches!(
        stale.unwrap_err(),
        hypermid_context::journal::JournalError::History(HistoryViolation::StaleCursor)
    ));
    assert_eq!(journal.current_cursor(), edit.cursor);

    drop(journal);
    let reopened = Journal::open(directory.0.join("context"), scope, session_id, 1).unwrap();
    assert_eq!(reopened.current_cursor(), Cursor::new(1, 4).unwrap());
    assert_eq!(reopened.all_items()[0].source_digest, first.source_digest);
}
