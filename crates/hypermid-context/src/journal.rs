use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::history::{
    next_cursor, range_digest, ContextItem, HistoryViolation, IngestRequest, JournalRange,
    PartKind, RecoveredItem, SourceAdapter,
};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

const BINDING_FILE: &str = "binding.json";
const ENTRIES_DIR: &str = "entries";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct JournalBinding {
    scope: Scope,
    session_id: Id,
    epoch: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct JournalRecord {
    idempotency_key: Id,
    request_digest: Digest,
    source_fingerprint: Digest,
    item: ContextItem,
    #[serde(skip_serializing_if = "Option::is_none")]
    source_snapshot: Option<Vec<u8>>,
}

#[derive(Default)]
struct JournalState {
    records: Vec<JournalRecord>,
    idempotency: HashMap<Id, usize>,
    source_events: HashMap<Id, usize>,
    item_ids: HashMap<Id, usize>,
    tool_calls: HashMap<Id, bool>,
}

pub struct Journal {
    root: PathBuf,
    binding: JournalBinding,
    state: Mutex<JournalState>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct RawSourceRecord {
    scope: Scope,
    session_id: Id,
    source_event_id: Id,
    source_digest: Digest,
    source_bytes: Vec<u8>,
}

pub struct RawSourceJournal {
    root: PathBuf,
    lock: Mutex<()>,
}

impl RawSourceJournal {
    pub fn open(root: impl AsRef<Path>) -> Result<Self, JournalError> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(&root)?;
        Ok(Self {
            root,
            lock: Mutex::new(()),
        })
    }

    pub fn append(
        &self,
        scope: Scope,
        session_id: Id,
        source_event_id: Id,
        source_bytes: Vec<u8>,
    ) -> Result<Digest, JournalError> {
        let _guard = self.lock.lock().map_err(|_| JournalError::Poisoned)?;
        let source_digest = Digest::sha256(&source_bytes);
        let record = RawSourceRecord {
            scope,
            session_id,
            source_event_id,
            source_digest,
            source_bytes,
        };
        let path = self.source_path(&record.scope, &record.session_id, &record.source_event_id)?;
        if path.exists() {
            let prior: RawSourceRecord = serde_json::from_slice(&fs::read(path)?)?;
            if prior == record {
                return Ok(source_digest);
            }
            return Err(HistoryViolation::SourceIdentityConflict.into());
        }
        write_atomic(&path, &serde_json::to_vec(&record)?)?;
        Ok(source_digest)
    }

    fn source_path(
        &self,
        scope: &Scope,
        session_id: &Id,
        source_event_id: &Id,
    ) -> Result<PathBuf, JournalError> {
        let key = serde_json::to_vec(&(scope, session_id, source_event_id))?;
        Ok(self.root.join(format!("{}.json", Digest::sha256(key))))
    }
}

impl SourceAdapter for RawSourceJournal {
    fn resolve(
        &self,
        scope: &Scope,
        session_id: &Id,
        source_event_id: &Id,
    ) -> Result<Vec<u8>, HistoryViolation> {
        let path = self
            .source_path(scope, session_id, source_event_id)
            .map_err(|_| HistoryViolation::SourceUnavailable)?;
        let bytes = fs::read(path).map_err(|_| HistoryViolation::SourceUnavailable)?;
        let record: RawSourceRecord =
            serde_json::from_slice(&bytes).map_err(|_| HistoryViolation::SourceUnavailable)?;
        if record.scope != *scope
            || record.session_id != *session_id
            || record.source_event_id != *source_event_id
            || Digest::sha256(&record.source_bytes) != record.source_digest
        {
            return Err(HistoryViolation::SourceDigestMismatch);
        }
        Ok(record.source_bytes)
    }
}

impl Journal {
    pub fn open(
        root: impl AsRef<Path>,
        scope: Scope,
        session_id: Id,
        epoch: u64,
    ) -> Result<Self, JournalError> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(root.join(ENTRIES_DIR))?;
        let requested = JournalBinding {
            scope,
            session_id,
            epoch,
        };
        let binding_path = root.join(BINDING_FILE);
        if binding_path.exists() {
            let stored: JournalBinding = serde_json::from_slice(&fs::read(&binding_path)?)?;
            if stored != requested {
                return Err(HistoryViolation::ScopeMismatch.into());
            }
        } else {
            write_atomic(&binding_path, &serde_json::to_vec(&requested)?)?;
        }

        let mut entry_paths = fs::read_dir(root.join(ENTRIES_DIR))?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|value| value == "json"))
            .collect::<Vec<_>>();
        entry_paths.sort();

        let mut state = JournalState::default();
        for path in entry_paths {
            let record: JournalRecord = serde_json::from_slice(&fs::read(&path)?)?;
            validate_loaded_record(&requested, &state, &record)?;
            index_record(&mut state, record)?;
        }

        Ok(Self {
            root,
            binding: requested,
            state: Mutex::new(state),
        })
    }

    pub fn scope(&self) -> &Scope {
        &self.binding.scope
    }

    pub fn session_id(&self) -> &Id {
        &self.binding.session_id
    }

    pub fn current_cursor(&self) -> Cursor {
        let sequence = self
            .state
            .lock()
            .expect("history journal poisoned")
            .records
            .len() as u64;
        Cursor::new(self.binding.epoch, sequence).expect("validated journal cursor")
    }

    pub fn append(&self, request: IngestRequest) -> Result<ContextItem, JournalError> {
        let request_bytes = serde_json::to_vec(&request)?;
        let request_digest = Digest::sha256(&request_bytes);
        let source_fingerprint = source_fingerprint(&request)?;
        let mut state = self.state.lock().map_err(|_| JournalError::Poisoned)?;

        if let Some(index) = state.idempotency.get(&request.idempotency_key).copied() {
            let prior = &state.records[index];
            if prior.request_digest == request_digest {
                return Ok(prior.item.clone());
            }
            return Err(HistoryViolation::IdempotencyConflict.into());
        }
        if let Some(index) = state
            .source_events
            .get(&request.item.source_event_id)
            .copied()
        {
            let prior = &state.records[index];
            if prior.source_fingerprint == source_fingerprint {
                return Ok(prior.item.clone());
            }
            return Err(HistoryViolation::SourceIdentityConflict.into());
        }
        if state.item_ids.contains_key(&request.item.item_id) {
            return Err(HistoryViolation::ItemIdentityConflict.into());
        }
        let current = Cursor::new(self.binding.epoch, state.records.len() as u64)
            .map_err(|_| HistoryViolation::CursorExhausted)?;
        if request.expected_cursor != current {
            return Err(HistoryViolation::StaleCursor.into());
        }
        if request.item.scope != self.binding.scope
            || request.item.session_id != self.binding.session_id
        {
            return Err(HistoryViolation::ScopeMismatch.into());
        }
        if let Some(snapshot) = &request.source_snapshot {
            if Digest::sha256(snapshot) != request.item.source_digest {
                return Err(HistoryViolation::SourceDigestMismatch.into());
            }
        }

        let next = next_cursor(current)?;
        let item = request.item.clone().committed(next);
        item.validate_shape()?;
        validate_relations(&state, &item)?;
        validate_tool_linkage(&state, &item)?;

        let record = JournalRecord {
            idempotency_key: request.idempotency_key,
            request_digest,
            source_fingerprint,
            item: item.clone(),
            source_snapshot: request.source_snapshot,
        };
        let path = self
            .root
            .join(ENTRIES_DIR)
            .join(format!("{:020}.json", next.sequence));
        write_atomic(&path, &serde_json::to_vec(&record)?)?;
        index_record(&mut state, record)?;
        Ok(item)
    }

    pub fn all_items(&self) -> Vec<ContextItem> {
        self.state
            .lock()
            .expect("history journal poisoned")
            .records
            .iter()
            .map(|record| record.item.clone())
            .collect()
    }

    pub fn items_through(&self, cursor: Cursor) -> Result<Vec<ContextItem>, JournalError> {
        if cursor.epoch != self.binding.epoch {
            return Err(HistoryViolation::InvalidRange.into());
        }
        let state = self.state.lock().map_err(|_| JournalError::Poisoned)?;
        if cursor.sequence > state.records.len() as u64 {
            return Err(HistoryViolation::InvalidRange.into());
        }
        Ok(state.records[..cursor.sequence as usize]
            .iter()
            .map(|record| record.item.clone())
            .collect())
    }

    pub fn items_in_range(&self, range: JournalRange) -> Result<Vec<ContextItem>, JournalError> {
        let state = self.state.lock().map_err(|_| JournalError::Poisoned)?;
        let selected = select_range(&self.binding, &state, range)?;
        Ok(selected.iter().map(|record| record.item.clone()).collect())
    }

    pub fn source_digest(&self, range: JournalRange) -> Result<Digest, JournalError> {
        let items = self.items_in_range(range)?;
        Ok(range_digest(items.iter()))
    }

    pub fn recover_item_ids<A: SourceAdapter>(
        &self,
        item_ids: &[Id],
        adapter: &A,
    ) -> Result<Vec<RecoveredItem>, JournalError> {
        let state = self.state.lock().map_err(|_| JournalError::Poisoned)?;
        let mut records = Vec::with_capacity(item_ids.len());
        let mut seen = HashSet::new();
        for item_id in item_ids {
            if seen.insert(item_id.clone()) {
                let index = state
                    .item_ids
                    .get(item_id)
                    .copied()
                    .ok_or(HistoryViolation::NotFound)?;
                records.push(state.records[index].clone());
            }
        }
        records.sort_by_key(|record| record.item.cursor);
        drop(state);
        self.resolve_records(&records, adapter)
    }

    pub fn recover_tags<A: SourceAdapter>(
        &self,
        tags: &[u64],
        adapter: &A,
    ) -> Result<Vec<RecoveredItem>, JournalError> {
        let state = self.state.lock().map_err(|_| JournalError::Poisoned)?;
        let mut unique = tags.iter().copied().collect::<Vec<_>>();
        unique.sort_unstable();
        unique.dedup();
        let mut records = Vec::with_capacity(unique.len());
        for tag in unique {
            if tag == 0 || tag > state.records.len() as u64 {
                return Err(HistoryViolation::NotFound.into());
            }
            records.push(state.records[(tag - 1) as usize].clone());
        }
        drop(state);
        self.resolve_records(&records, adapter)
    }

    pub fn recover_range<A: SourceAdapter>(
        &self,
        range: JournalRange,
        adapter: &A,
    ) -> Result<Vec<RecoveredItem>, JournalError> {
        let state = self.state.lock().map_err(|_| JournalError::Poisoned)?;
        let records = select_range(&self.binding, &state, range)?
            .into_iter()
            .cloned()
            .collect::<Vec<_>>();
        drop(state);
        self.resolve_records(&records, adapter)
    }

    fn resolve_records<A: SourceAdapter>(
        &self,
        records: &[JournalRecord],
        adapter: &A,
    ) -> Result<Vec<RecoveredItem>, JournalError> {
        let mut recovered = Vec::with_capacity(records.len());
        for record in records {
            let source_bytes = adapter.resolve(
                &self.binding.scope,
                &self.binding.session_id,
                &record.item.source_event_id,
            )?;
            if Digest::sha256(&source_bytes) != record.item.source_digest {
                return Err(HistoryViolation::SourceDigestMismatch.into());
            }
            recovered.push(RecoveredItem {
                item: record.item.clone(),
                source_bytes,
            });
        }
        Ok(recovered)
    }
}

fn source_fingerprint(request: &IngestRequest) -> Result<Digest, JournalError> {
    #[derive(Serialize)]
    struct Fingerprint<'a> {
        item: &'a hypermid_core::history::PendingContextItem,
        source_snapshot: &'a Option<Vec<u8>>,
    }
    Ok(Digest::sha256(serde_json::to_vec(&Fingerprint {
        item: &request.item,
        source_snapshot: &request.source_snapshot,
    })?))
}

fn validate_loaded_record(
    binding: &JournalBinding,
    state: &JournalState,
    record: &JournalRecord,
) -> Result<(), JournalError> {
    let expected = Cursor::new(binding.epoch, state.records.len() as u64 + 1)
        .map_err(|_| HistoryViolation::CursorExhausted)?;
    if record.item.scope != binding.scope
        || record.item.session_id != binding.session_id
        || record.item.cursor != expected
    {
        return Err(HistoryViolation::ScopeMismatch.into());
    }
    record.item.validate_shape()?;
    if let Some(snapshot) = &record.source_snapshot {
        if Digest::sha256(snapshot) != record.item.source_digest {
            return Err(HistoryViolation::SourceDigestMismatch.into());
        }
    }
    validate_relations(state, &record.item)?;
    validate_tool_linkage(state, &record.item)?;
    Ok(())
}

fn validate_relations(state: &JournalState, item: &ContextItem) -> Result<(), HistoryViolation> {
    for relation in &item.relations {
        let index = state
            .item_ids
            .get(&relation.item_id)
            .copied()
            .ok_or(HistoryViolation::InvalidRelation)?;
        if relation
            .source_digest
            .is_some_and(|digest| digest != state.records[index].item.source_digest)
        {
            return Err(HistoryViolation::InvalidRelation);
        }
    }
    Ok(())
}

fn validate_tool_linkage(state: &JournalState, item: &ContextItem) -> Result<(), HistoryViolation> {
    let mut calls = state.tool_calls.clone();
    for part in &item.parts {
        match part.kind {
            PartKind::ToolCall => {
                let call_id = part.call_id.as_ref().ok_or(HistoryViolation::InvalidPart)?;
                if calls.insert(call_id.clone(), false).is_some() {
                    return Err(HistoryViolation::DuplicateToolCall);
                }
            }
            PartKind::ToolResult => {
                let call_id = part.call_id.as_ref().ok_or(HistoryViolation::InvalidPart)?;
                let resolved = calls
                    .get_mut(call_id)
                    .ok_or(HistoryViolation::OrphanToolResult)?;
                if *resolved {
                    return Err(HistoryViolation::DuplicateToolResult);
                }
                *resolved = true;
            }
            _ => {}
        }
    }
    Ok(())
}

fn index_record(state: &mut JournalState, record: JournalRecord) -> Result<(), JournalError> {
    let index = state.records.len();
    if state
        .idempotency
        .insert(record.idempotency_key.clone(), index)
        .is_some()
        || state
            .source_events
            .insert(record.item.source_event_id.clone(), index)
            .is_some()
        || state
            .item_ids
            .insert(record.item.item_id.clone(), index)
            .is_some()
    {
        return Err(JournalError::Corrupt("duplicate immutable identity"));
    }
    for part in &record.item.parts {
        match part.kind {
            PartKind::ToolCall => {
                state
                    .tool_calls
                    .insert(part.call_id.clone().unwrap(), false);
            }
            PartKind::ToolResult => {
                if let Some(resolved) = state.tool_calls.get_mut(part.call_id.as_ref().unwrap()) {
                    *resolved = true;
                }
            }
            _ => {}
        }
    }
    state.records.push(record);
    Ok(())
}

fn select_range<'a>(
    binding: &JournalBinding,
    state: &'a JournalState,
    range: JournalRange,
) -> Result<Vec<&'a JournalRecord>, JournalError> {
    if range.start.epoch != binding.epoch
        || range.end.epoch != binding.epoch
        || range.start.sequence == 0
        || range.start.sequence > range.end.sequence
        || range.end.sequence > state.records.len() as u64
    {
        return Err(HistoryViolation::InvalidRange.into());
    }
    Ok(
        state.records[(range.start.sequence - 1) as usize..range.end.sequence as usize]
            .iter()
            .collect(),
    )
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), JournalError> {
    let parent = path
        .parent()
        .ok_or(JournalError::Corrupt("missing parent"))?;
    let digest = Digest::sha256(bytes).to_hex();
    let temporary = parent.join(format!(".pending-{}", digest));
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)?;
    if let Err(error) = (|| -> std::io::Result<()> {
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    })() {
        let _ = fs::remove_file(&temporary);
        return Err(error.into());
    }
    Ok(())
}

#[derive(Debug, thiserror::Error)]
pub enum JournalError {
    #[error(transparent)]
    History(#[from] HistoryViolation),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("history journal lock is poisoned")]
    Poisoned,
    #[error("history journal is corrupt: {0}")]
    Corrupt(&'static str),
}
