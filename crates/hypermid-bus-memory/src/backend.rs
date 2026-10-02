use async_trait::async_trait;
use hypermid_bus_contracts::{
    cursor, BusError, BusMessage, Delivery, DeliveryDisposition, PullRequest, QueueItem, QueuePull,
    Register, RegisterEntry, RegisterSnapshot, RegisterUpdate, Stream, WorkQueue,
};
use hypermid_contracts::Cursor;
use rusqlite::{params, Connection, OptionalExtension, Transaction};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const SCHEMA: &str = r#"
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS messages(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE,
 digest TEXT NOT NULL,
 subject TEXT NOT NULL,
 payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumers(
 durable TEXT PRIMARY KEY,
 ack_sequence INTEGER NOT NULL DEFAULT 0,
 inflight_sequence INTEGER,
 delivery_count INTEGER NOT NULL DEFAULT 0,
 deadline_ms INTEGER,
 available_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS register_meta(revision INTEGER NOT NULL);
INSERT INTO register_meta(revision) SELECT 0 WHERE NOT EXISTS(SELECT 1 FROM register_meta);
CREATE TABLE IF NOT EXISTS register_entries(key TEXT PRIMARY KEY, value BLOB NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS register_updates(revision INTEGER PRIMARY KEY, key TEXT NOT NULL, value BLOB, deleted INTEGER NOT NULL);
"#;

#[derive(Clone)]
pub struct LocalBus {
    connection: Arc<Mutex<Connection>>,
}

impl LocalBus {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, BusError> {
        let connection = Connection::open(path).map_err(internal)?;
        connection.execute_batch(SCHEMA).map_err(internal)?;
        Ok(Self {
            connection: Arc::new(Mutex::new(connection)),
        })
    }

    pub fn ephemeral() -> Result<Self, BusError> {
        let connection = Connection::open_in_memory().map_err(internal)?;
        connection.execute_batch(SCHEMA).map_err(internal)?;
        Ok(Self {
            connection: Arc::new(Mutex::new(connection)),
        })
    }

    fn publish_tx(tx: &Transaction<'_>, message: &BusMessage) -> Result<Cursor, BusError> {
        message.validate()?;
        let existing: Option<(u64, String)> = tx
            .query_row(
                "SELECT sequence,digest FROM messages WHERE event_id=?1",
                [message.id.as_str()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .map_err(internal)?;
        if let Some((sequence, digest)) = existing {
            if digest == message.digest.to_string() {
                return cursor(sequence);
            }
            return Err(BusError::Conflict);
        }
        let payload = serde_json::to_string(message).map_err(internal)?;
        tx.execute(
            "INSERT INTO messages(event_id,digest,subject,payload) VALUES(?1,?2,?3,?4)",
            params![
                message.id.as_str(),
                message.digest.to_string(),
                message.subject,
                payload
            ],
        )
        .map_err(internal)?;
        cursor(tx.last_insert_rowid() as u64)
    }

    fn load_message(tx: &Transaction<'_>, sequence: u64) -> Result<BusMessage, BusError> {
        let payload: String = tx
            .query_row(
                "SELECT payload FROM messages WHERE sequence=?1",
                [sequence],
                |row| row.get(0),
            )
            .map_err(internal)?;
        serde_json::from_str(&payload).map_err(internal)
    }
}

#[async_trait]
impl Stream for LocalBus {
    async fn publish(&self, message: BusMessage) -> Result<Cursor, BusError> {
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let tx = connection.transaction().map_err(internal)?;
        let result = Self::publish_tx(&tx, &message)?;
        tx.commit().map_err(internal)?;
        Ok(result)
    }

    async fn pull(&self, request: &PullRequest) -> Result<Option<Delivery>, BusError> {
        request.validate()?;
        let now = now_ms()?;
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let tx = connection.transaction().map_err(internal)?;
        tx.execute(
            "INSERT OR IGNORE INTO consumers(durable) VALUES(?1)",
            [request.durable.as_str()],
        )
        .map_err(internal)?;
        let state: (u64, Option<u64>, u32, Option<u64>, u64) = tx.query_row(
            "SELECT ack_sequence,inflight_sequence,delivery_count,deadline_ms,available_ms FROM consumers WHERE durable=?1",
            [request.durable.as_str()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
        ).map_err(internal)?;
        let (ack_sequence, inflight, count, deadline, available) = state;
        let (sequence, delivery_count) = if let Some(sequence) = inflight {
            if deadline.is_some_and(|deadline| deadline > now) || available > now {
                tx.commit().map_err(internal)?;
                return Ok(None);
            }
            (sequence, count.saturating_add(1))
        } else {
            let mut statement = tx
                .prepare(
                    "SELECT sequence,subject FROM messages WHERE sequence>?1 ORDER BY sequence",
                )
                .map_err(internal)?;
            let rows = statement
                .query_map([ack_sequence], |row| {
                    Ok((row.get::<_, u64>(0)?, row.get::<_, String>(1)?))
                })
                .map_err(internal)?;
            let mut found = None;
            for row in rows {
                let (sequence, subject) = row.map_err(internal)?;
                if subject_matches(&request.filter, &subject) {
                    found = Some(sequence);
                    break;
                }
            }
            drop(statement);
            let Some(sequence) = found else {
                tx.commit().map_err(internal)?;
                return Ok(None);
            };
            (sequence, 1)
        };
        tx.execute(
            "UPDATE consumers SET inflight_sequence=?2,delivery_count=?3,deadline_ms=?4,available_ms=0 WHERE durable=?1",
            params![request.durable.as_str(), sequence, delivery_count, now.saturating_add(request.ack_wait.as_millis() as u64)],
        ).map_err(internal)?;
        let message = Self::load_message(&tx, sequence)?;
        tx.commit().map_err(internal)?;
        Ok(Some(Delivery {
            cursor: cursor(sequence)?,
            delivery_count,
            message,
        }))
    }

    async fn dispose(
        &self,
        durable: &str,
        delivery: Cursor,
        disposition: DeliveryDisposition,
    ) -> Result<(), BusError> {
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let tx = connection.transaction().map_err(internal)?;
        let current: Option<u64> = tx
            .query_row(
                "SELECT inflight_sequence FROM consumers WHERE durable=?1",
                [durable],
                |row| row.get(0),
            )
            .optional()
            .map_err(internal)?
            .flatten();
        if current != Some(delivery.sequence) {
            return Err(BusError::Conflict);
        }
        match disposition {
            DeliveryDisposition::Ack | DeliveryDisposition::Term => {
                tx.execute("UPDATE consumers SET ack_sequence=?2,inflight_sequence=NULL,delivery_count=0,deadline_ms=NULL,available_ms=0 WHERE durable=?1", params![durable, delivery.sequence]).map_err(internal)?;
            }
            DeliveryDisposition::Nak { delay } => {
                tx.execute(
                    "UPDATE consumers SET deadline_ms=0,available_ms=?2 WHERE durable=?1",
                    params![durable, now_ms()?.saturating_add(delay.as_millis() as u64)],
                )
                .map_err(internal)?;
            }
            DeliveryDisposition::InProgress { extension } => {
                tx.execute(
                    "UPDATE consumers SET deadline_ms=?2 WHERE durable=?1",
                    params![
                        durable,
                        now_ms()?.saturating_add(extension.as_millis() as u64)
                    ],
                )
                .map_err(internal)?;
            }
        }
        tx.commit().map_err(internal)
    }
}

#[async_trait]
impl Register for LocalBus {
    async fn put(
        &self,
        key: &str,
        value: Vec<u8>,
        expected_revision: Option<u64>,
    ) -> Result<RegisterEntry, BusError> {
        validate_key(key)?;
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let tx = connection.transaction().map_err(internal)?;
        let current: Option<u64> = tx
            .query_row(
                "SELECT revision FROM register_entries WHERE key=?1",
                [key],
                |row| row.get(0),
            )
            .optional()
            .map_err(internal)?;
        if expected_revision != current {
            return Err(BusError::Conflict);
        }
        let revision = next_revision(&tx)?;
        tx.execute("INSERT INTO register_entries(key,value,revision) VALUES(?1,?2,?3) ON CONFLICT(key) DO UPDATE SET value=excluded.value,revision=excluded.revision", params![key, value, revision]).map_err(internal)?;
        tx.execute(
            "INSERT INTO register_updates(revision,key,value,deleted) VALUES(?1,?2,?3,0)",
            params![revision, key, value],
        )
        .map_err(internal)?;
        tx.commit().map_err(internal)?;
        Ok(RegisterEntry {
            key: key.into(),
            value,
            revision,
        })
    }

    async fn get(&self, key: &str) -> Result<Option<RegisterEntry>, BusError> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        connection
            .query_row(
                "SELECT value,revision FROM register_entries WHERE key=?1",
                [key],
                |row| {
                    Ok(RegisterEntry {
                        key: key.into(),
                        value: row.get(0)?,
                        revision: row.get(1)?,
                    })
                },
            )
            .optional()
            .map_err(internal)
    }

    async fn delete(&self, key: &str, expected_revision: u64) -> Result<u64, BusError> {
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let tx = connection.transaction().map_err(internal)?;
        let current: Option<u64> = tx
            .query_row(
                "SELECT revision FROM register_entries WHERE key=?1",
                [key],
                |row| row.get(0),
            )
            .optional()
            .map_err(internal)?;
        if current != Some(expected_revision) {
            return Err(BusError::Conflict);
        }
        let revision = next_revision(&tx)?;
        tx.execute("DELETE FROM register_entries WHERE key=?1", [key])
            .map_err(internal)?;
        tx.execute(
            "INSERT INTO register_updates(revision,key,value,deleted) VALUES(?1,?2,NULL,1)",
            params![revision, key],
        )
        .map_err(internal)?;
        tx.commit().map_err(internal)?;
        Ok(revision)
    }

    async fn snapshot(&self) -> Result<RegisterSnapshot, BusError> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let revision = connection
            .query_row("SELECT revision FROM register_meta", [], |row| row.get(0))
            .map_err(internal)?;
        let mut statement = connection
            .prepare("SELECT key,value,revision FROM register_entries ORDER BY key")
            .map_err(internal)?;
        let entries = statement
            .query_map([], |row| {
                Ok(RegisterEntry {
                    key: row.get(0)?,
                    value: row.get(1)?,
                    revision: row.get(2)?,
                })
            })
            .map_err(internal)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(internal)?;
        Ok(RegisterSnapshot { revision, entries })
    }

    async fn watch_after(&self, revision: u64) -> Result<Vec<RegisterUpdate>, BusError> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let mut statement = connection.prepare("SELECT revision,key,value,deleted FROM register_updates WHERE revision>?1 ORDER BY revision").map_err(internal)?;
        let updates = statement
            .query_map([revision], |row| {
                let revision = row.get(0)?;
                let key: String = row.get(1)?;
                let deleted: bool = row.get(3)?;
                Ok(if deleted {
                    RegisterUpdate::Delete { key, revision }
                } else {
                    RegisterUpdate::Put(RegisterEntry {
                        key,
                        value: row.get(2)?,
                        revision,
                    })
                })
            })
            .map_err(internal)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(internal)?;
        Ok(updates)
    }
}

#[async_trait]
impl WorkQueue for LocalBus {
    async fn enqueue(&self, message: BusMessage) -> Result<Cursor, BusError> {
        Stream::publish(self, message).await
    }
    async fn pull(&self, worker: &str, max_deliveries: u32) -> Result<QueuePull, BusError> {
        let request = PullRequest {
            durable: hypermid_contracts::Id::new(worker)
                .map_err(|error| BusError::Invalid(error.to_string()))?,
            filter: ">".into(),
            ack_wait: Duration::from_millis(1),
            max_deliveries,
        };
        match Stream::pull(self, &request).await? {
            None => Ok(QueuePull::Empty),
            Some(delivery) => {
                let item = QueueItem {
                    cursor: delivery.cursor,
                    delivery_count: delivery.delivery_count,
                    message: delivery.message,
                };
                if item.delivery_count > max_deliveries {
                    Ok(QueuePull::MaxDeliveriesExceeded(item))
                } else {
                    Ok(QueuePull::Item(item))
                }
            }
        }
    }
    async fn ack(&self, worker: &str, delivery: Cursor) -> Result<(), BusError> {
        Stream::dispose(self, worker, delivery, DeliveryDisposition::Ack).await
    }
    async fn retry(&self, worker: &str, delivery: Cursor) -> Result<(), BusError> {
        Stream::dispose(
            self,
            worker,
            delivery,
            DeliveryDisposition::Nak {
                delay: Duration::ZERO,
            },
        )
        .await
    }
    async fn dead_letter_then_term(
        &self,
        worker: &str,
        delivery: Cursor,
        dead_letter: BusMessage,
    ) -> Result<Cursor, BusError> {
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| BusError::Internal("local bus lock poisoned".into()))?;
        let tx = connection.transaction().map_err(internal)?;
        let current: Option<u64> = tx
            .query_row(
                "SELECT inflight_sequence FROM consumers WHERE durable=?1",
                [worker],
                |row| row.get(0),
            )
            .optional()
            .map_err(internal)?
            .flatten();
        if current != Some(delivery.sequence) {
            return Err(BusError::Conflict);
        }
        let dead_cursor = Self::publish_tx(&tx, &dead_letter)?;
        tx.execute("UPDATE consumers SET ack_sequence=?2,inflight_sequence=NULL,delivery_count=0,deadline_ms=NULL WHERE durable=?1", params![worker, delivery.sequence]).map_err(internal)?;
        tx.commit().map_err(internal)?;
        Ok(dead_cursor)
    }
}

fn next_revision(tx: &Transaction<'_>) -> Result<u64, BusError> {
    let revision: u64 = tx
        .query_row("SELECT revision FROM register_meta", [], |row| row.get(0))
        .map_err(internal)?;
    let revision = revision
        .checked_add(1)
        .ok_or_else(|| BusError::Internal("register revision exhausted".into()))?;
    tx.execute("UPDATE register_meta SET revision=?1", [revision])
        .map_err(internal)?;
    Ok(revision)
}

fn subject_matches(filter: &str, subject: &str) -> bool {
    if filter == ">" {
        return true;
    }
    let filter: Vec<_> = filter.split('.').collect();
    let subject: Vec<_> = subject.split('.').collect();
    for (index, token) in filter.iter().enumerate() {
        if *token == ">" {
            return index + 1 == filter.len();
        }
        if subject.get(index).is_none() || (*token != "*" && Some(token) != subject.get(index)) {
            return false;
        }
    }
    filter.len() == subject.len()
}

fn validate_key(key: &str) -> Result<(), BusError> {
    if key.is_empty() || key.len() > 256 {
        Err(BusError::Invalid(
            "register key length is outside 1..=256".into(),
        ))
    } else {
        Ok(())
    }
}
fn now_ms() -> Result<u64, BusError> {
    Ok(SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(internal)?
        .as_millis() as u64)
}
fn internal(error: impl std::fmt::Display) -> BusError {
    BusError::Internal(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_contracts::{Digest, Id, Scope, Trace};
    use std::collections::BTreeMap;

    fn message(id: &str, subject: &str) -> BusMessage {
        BusMessage {
            subject: subject.into(),
            id: Id::new(id).unwrap(),
            digest: Digest::sha256(id),
            headers: BTreeMap::new(),
            scope: Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None),
            trace: Trace::new(Id::new("trace").unwrap(), Id::new("request").unwrap()),
        }
    }

    #[tokio::test]
    async fn durable_local_bus_recovers_and_dead_letters_before_term() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("bus.sqlite3");
        let bus = LocalBus::open(&path).unwrap();
        let first = Stream::publish(&bus, message("event-1", "jobs.run"))
            .await
            .unwrap();
        assert_eq!(
            Stream::publish(&bus, message("event-1", "jobs.run"))
                .await
                .unwrap(),
            first
        );
        drop(bus);

        let bus = LocalBus::open(&path).unwrap();
        let item = WorkQueue::pull(&bus, "worker-1", 1).await.unwrap();
        let QueuePull::Item(item) = item else {
            panic!("expected durable queue item")
        };
        WorkQueue::retry(&bus, "worker-1", item.cursor)
            .await
            .unwrap();
        let exhausted = WorkQueue::pull(&bus, "worker-1", 1).await.unwrap();
        let QueuePull::MaxDeliveriesExceeded(item) = exhausted else {
            panic!("expected exhaustion")
        };
        let dead = WorkQueue::dead_letter_then_term(
            &bus,
            "worker-1",
            item.cursor,
            message("dead-1", "dead.jobs"),
        )
        .await
        .unwrap();
        assert!(dead.sequence > item.cursor.sequence);
        assert!(matches!(
            WorkQueue::pull(&bus, "worker-1", 1).await.unwrap(),
            QueuePull::Item(_)
        ));
    }
}
