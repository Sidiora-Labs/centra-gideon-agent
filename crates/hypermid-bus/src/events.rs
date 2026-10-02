use std::collections::{BTreeMap, HashMap};
use std::path::Path;

use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace};
use hypermid_protocol::Principal;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::dead_letter::DeadLetterRecord;
use crate::journal::{Journal, JournalError};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventDraft {
    pub event_id: Id,
    pub topic: String,
    pub scope: Scope,
    pub at_ms: u64,
    pub schema_name: String,
    pub schema_version: u64,
    pub trace: Option<Trace>,
    pub payload: Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventRecord {
    pub event_id: Id,
    pub topic: String,
    pub producer: Principal,
    pub scope: Scope,
    pub at_ms: u64,
    pub schema_name: String,
    pub schema_version: u64,
    pub payload_digest: Digest,
    pub trace: Option<Trace>,
    pub cursor: Cursor,
    pub payload: Value,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Snapshot {
    pub cursor: Cursor,
    pub replay: Vec<EventRecord>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Delivery {
    pub event: EventRecord,
    pub delivery_count: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ConsumerState {
    consumer_id: Id,
    scope: Scope,
    topic_filter: String,
    cursor: Cursor,
    outstanding: Option<(Id, u32)>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
enum EventJournalEntry {
    Published(EventRecord),
    Consumer(ConsumerState),
    DeadLetter(DeadLetterRecord),
}

#[derive(Debug, thiserror::Error)]
pub enum EventBusError {
    #[error(transparent)]
    Journal(#[from] JournalError),
    #[error("principal lacks {0}")]
    ScopeDenied(String),
    #[error("topic is invalid")]
    InvalidTopic,
    #[error("event id was reused with different content")]
    DivergentDuplicate,
    #[error(
        "cursor belongs to another epoch or is outside retained history; recover at {recovery:?}"
    )]
    CursorGap { recovery: Cursor },
    #[error("consumer does not exist")]
    UnknownConsumer,
    #[error("durable consumer resume does not match its persisted scope, filter, or cursor")]
    ConsumerResumeMismatch,
    #[error("acknowledgement does not match the in-flight event")]
    InvalidAcknowledgement,
    #[error("contract violation: {0}")]
    Contract(#[from] hypermid_contracts::ContractViolation),
}

pub struct DurableEventBus {
    journal: Journal<EventJournalEntry>,
    epoch: u64,
    max_retained: usize,
    max_deliveries: u32,
    events: Vec<EventRecord>,
    published: BTreeMap<Id, (Digest, EventRecord)>,
    consumers: HashMap<Id, ConsumerState>,
    dead_letters: Vec<DeadLetterRecord>,
}

impl DurableEventBus {
    pub fn open(
        path: impl AsRef<Path>,
        epoch: u64,
        max_retained: usize,
        max_deliveries: u32,
    ) -> Result<Self, EventBusError> {
        let journal = Journal::open(path)?;
        let mut events = Vec::new();
        let mut published = BTreeMap::new();
        let mut consumers = HashMap::new();
        let mut dead_letters = Vec::new();
        for (_, entry) in journal.entries() {
            match entry {
                EventJournalEntry::Published(event) => {
                    if event.cursor.epoch != epoch {
                        return Err(EventBusError::CursorGap {
                            recovery: Cursor::new(epoch, 0)?,
                        });
                    }
                    published.insert(
                        event.event_id.clone(),
                        (event_fingerprint_from_record(event)?, event.clone()),
                    );
                    events.push(event.clone());
                }
                EventJournalEntry::Consumer(state) => {
                    consumers.insert(state.consumer_id.clone(), state.clone());
                }
                EventJournalEntry::DeadLetter(record) => dead_letters.push(record.clone()),
            }
        }
        if events.len() > max_retained {
            events.drain(..events.len() - max_retained);
        }
        Ok(Self {
            journal,
            epoch,
            max_retained,
            max_deliveries: max_deliveries.max(1),
            events,
            published,
            consumers,
            dead_letters,
        })
    }

    pub fn publish(
        &mut self,
        principal: &Principal,
        draft: EventDraft,
    ) -> Result<EventRecord, EventBusError> {
        validate_topic(&draft.topic)?;
        require_scope(principal, "events.publish", &draft.topic)?;
        let payload_bytes =
            serde_json::to_vec(&draft.payload).map_err(JournalError::Serialization)?;
        let payload_digest = Digest::sha256(payload_bytes);
        let fingerprint = event_fingerprint(principal, &draft)?;
        if let Some((existing_fingerprint, existing)) = self.published.get(&draft.event_id) {
            if *existing_fingerprint != fingerprint {
                return Err(EventBusError::DivergentDuplicate);
            }
            return Ok(existing.clone());
        }
        let cursor = self.head_cursor()?.next()?;
        let event = EventRecord {
            event_id: draft.event_id,
            topic: draft.topic,
            producer: principal.clone(),
            scope: draft.scope,
            at_ms: draft.at_ms,
            schema_name: draft.schema_name,
            schema_version: draft.schema_version,
            payload_digest,
            trace: draft.trace,
            cursor,
            payload: draft.payload,
        };
        self.journal
            .append(EventJournalEntry::Published(event.clone()))?;
        self.published
            .insert(event.event_id.clone(), (fingerprint, event.clone()));
        self.events.push(event.clone());
        if self.events.len() > self.max_retained {
            self.events.remove(0);
        }
        Ok(event)
    }

    pub fn subscribe(
        &mut self,
        principal: &Principal,
        consumer_id: Id,
        scope: Scope,
        topic_filter: String,
        after: Option<Cursor>,
    ) -> Result<Snapshot, EventBusError> {
        validate_filter(&topic_filter)?;
        require_scope(principal, "events.subscribe", &topic_filter)?;
        let after = after.unwrap_or(Cursor::new(self.epoch, 0)?);
        if let Some(state) = self.consumers.get(&consumer_id) {
            if state.scope != scope || state.topic_filter != topic_filter || state.cursor != after {
                return Err(EventBusError::ConsumerResumeMismatch);
            }
            self.validate_cursor(after)?;
            return self.snapshot(&scope, &topic_filter, after);
        }
        self.validate_cursor(after)?;
        let state = ConsumerState {
            consumer_id: consumer_id.clone(),
            scope: scope.clone(),
            topic_filter: topic_filter.clone(),
            cursor: after,
            outstanding: None,
        };
        self.journal
            .append(EventJournalEntry::Consumer(state.clone()))?;
        self.consumers.insert(consumer_id, state);
        self.snapshot(&scope, &topic_filter, after)
    }

    pub fn next_delivery(
        &mut self,
        consumer_id: &Id,
        now_ms: u64,
    ) -> Result<Option<Delivery>, EventBusError> {
        let state = self
            .consumers
            .get(consumer_id)
            .cloned()
            .ok_or(EventBusError::UnknownConsumer)?;
        if let Some((event_id, delivery_count)) = state.outstanding.clone() {
            if delivery_count >= self.max_deliveries {
                let event = self
                    .events
                    .iter()
                    .find(|event| event.event_id == event_id)
                    .cloned()
                    .ok_or(EventBusError::CursorGap {
                        recovery: self.recovery_cursor()?,
                    })?;
                let dead_letter = DeadLetterRecord {
                    original_event_id: event.event_id.clone(),
                    topic: event.topic.clone(),
                    scope: event.scope.clone(),
                    payload_digest: event.payload_digest,
                    delivery_count,
                    reason: "durable consumer exhausted its delivery bound".into(),
                    recorded_ms: now_ms,
                };
                self.journal
                    .append(EventJournalEntry::DeadLetter(dead_letter.clone()))?;
                self.dead_letters.push(dead_letter);
                let mut advanced = state;
                advanced.cursor = event.cursor;
                advanced.outstanding = None;
                self.persist_consumer(advanced)?;
                return self.next_delivery(consumer_id, now_ms);
            }
            let event = self
                .events
                .iter()
                .find(|event| event.event_id == event_id)
                .cloned()
                .ok_or(EventBusError::CursorGap {
                    recovery: self.recovery_cursor()?,
                })?;
            let mut redelivered = state;
            redelivered.outstanding = Some((event_id, delivery_count + 1));
            self.persist_consumer(redelivered)?;
            return Ok(Some(Delivery {
                event,
                delivery_count: delivery_count + 1,
            }));
        }
        let next = self
            .events
            .iter()
            .find(|event| {
                event.cursor.sequence > state.cursor.sequence
                    && event.scope == state.scope
                    && topic_matches(&state.topic_filter, &event.topic)
            })
            .cloned();
        let Some(event) = next else {
            return Ok(None);
        };
        let mut delivered = state;
        delivered.outstanding = Some((event.event_id.clone(), 1));
        self.persist_consumer(delivered)?;
        Ok(Some(Delivery {
            event,
            delivery_count: 1,
        }))
    }

    pub fn acknowledge(&mut self, consumer_id: &Id, event_id: &Id) -> Result<(), EventBusError> {
        let mut state = self
            .consumers
            .get(consumer_id)
            .cloned()
            .ok_or(EventBusError::UnknownConsumer)?;
        if state.outstanding.as_ref().map(|(id, _)| id) != Some(event_id) {
            return Err(EventBusError::InvalidAcknowledgement);
        }
        let event = self
            .events
            .iter()
            .find(|event| &event.event_id == event_id)
            .ok_or(EventBusError::CursorGap {
                recovery: self.recovery_cursor()?,
            })?;
        state.cursor = event.cursor;
        state.outstanding = None;
        self.persist_consumer(state)
    }

    pub fn dead_letters(&self) -> &[DeadLetterRecord] {
        &self.dead_letters
    }

    pub fn head_cursor(&self) -> Result<Cursor, EventBusError> {
        Ok(self
            .events
            .last()
            .map(|event| event.cursor)
            .unwrap_or(Cursor::new(self.epoch, 0)?))
    }

    fn validate_cursor(&self, cursor: Cursor) -> Result<(), EventBusError> {
        let recovery = self.recovery_cursor()?;
        if cursor.epoch != self.epoch
            || cursor.sequence > self.head_cursor()?.sequence
            || (recovery.sequence > 0 && cursor.sequence < recovery.sequence.saturating_sub(1))
        {
            return Err(EventBusError::CursorGap { recovery });
        }
        Ok(())
    }

    fn recovery_cursor(&self) -> Result<Cursor, EventBusError> {
        Ok(self
            .events
            .first()
            .map(|event| event.cursor)
            .unwrap_or(Cursor::new(self.epoch, 0)?))
    }

    fn snapshot(
        &self,
        scope: &Scope,
        topic_filter: &str,
        after: Cursor,
    ) -> Result<Snapshot, EventBusError> {
        let snapshot_cursor = self.head_cursor()?;
        let replay = self
            .events
            .iter()
            .filter(|event| {
                &event.scope == scope
                    && event.cursor.sequence > after.sequence
                    && event.cursor.sequence <= snapshot_cursor.sequence
                    && topic_matches(topic_filter, &event.topic)
            })
            .cloned()
            .collect();
        Ok(Snapshot {
            cursor: snapshot_cursor,
            replay,
        })
    }

    fn persist_consumer(&mut self, state: ConsumerState) -> Result<(), EventBusError> {
        self.journal
            .append(EventJournalEntry::Consumer(state.clone()))?;
        self.consumers.insert(state.consumer_id.clone(), state);
        Ok(())
    }
}

fn require_scope(principal: &Principal, action: &str, topic: &str) -> Result<(), EventBusError> {
    let exact = format!("{action}:{topic}");
    let wildcard = format!("{action}:*");
    if principal
        .scopes
        .iter()
        .any(|scope| scope == &exact || scope == &wildcard)
    {
        Ok(())
    } else {
        Err(EventBusError::ScopeDenied(exact))
    }
}

fn validate_topic(topic: &str) -> Result<(), EventBusError> {
    if topic.is_empty()
        || topic.len() > 256
        || !topic.bytes().enumerate().all(|(index, byte)| {
            if index == 0 {
                byte.is_ascii_lowercase()
            } else {
                byte.is_ascii_lowercase()
                    || byte.is_ascii_digit()
                    || matches!(byte, b'.' | b'_' | b'-')
            }
        })
    {
        return Err(EventBusError::InvalidTopic);
    }
    Ok(())
}

fn validate_filter(filter: &str) -> Result<(), EventBusError> {
    if let Some(prefix) = filter.strip_suffix(".*") {
        validate_topic(prefix)
    } else {
        validate_topic(filter)
    }
}

fn topic_matches(filter: &str, topic: &str) -> bool {
    filter == topic
        || filter
            .strip_suffix(".*")
            .is_some_and(|prefix| topic.starts_with(&format!("{prefix}.")))
}

fn event_fingerprint(principal: &Principal, draft: &EventDraft) -> Result<Digest, EventBusError> {
    let bytes = serde_json::to_vec(&(principal, draft)).map_err(JournalError::Serialization)?;
    Ok(Digest::sha256(bytes))
}

fn event_fingerprint_from_record(event: &EventRecord) -> Result<Digest, EventBusError> {
    event_fingerprint(
        &event.producer,
        &EventDraft {
            event_id: event.event_id.clone(),
            topic: event.topic.clone(),
            scope: event.scope.clone(),
            at_ms: event.at_ms,
            schema_name: event.schema_name.clone(),
            schema_version: event.schema_version,
            trace: event.trace.clone(),
            payload: event.payload.clone(),
        },
    )
}
