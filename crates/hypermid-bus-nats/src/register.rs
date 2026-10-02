use crate::connection::{map_connection, NatsConnection};
use async_trait::async_trait;
use futures::{StreamExt, TryStreamExt};
use hypermid_bus_contracts::{BusError, Register, RegisterEntry, RegisterSnapshot, RegisterUpdate};

#[derive(Clone)]
pub struct NatsRegister {
    store: async_nats::jetstream::kv::Store,
}

impl NatsRegister {
    pub async fn bind(connection: &NatsConnection, bucket: &str) -> Result<Self, BusError> {
        let store = connection
            .context
            .get_key_value(bucket)
            .await
            .map_err(map_connection)?;
        Ok(Self { store })
    }
}

#[async_trait]
impl Register for NatsRegister {
    async fn put(
        &self,
        key: &str,
        value: Vec<u8>,
        expected_revision: Option<u64>,
    ) -> Result<RegisterEntry, BusError> {
        let revision = match expected_revision {
            Some(revision) => self
                .store
                .update(key, value.clone().into(), revision)
                .await
                .map_err(map_connection)?,
            None => self
                .store
                .create(key, value.clone().into())
                .await
                .map_err(map_connection)?,
        };
        Ok(RegisterEntry {
            key: key.into(),
            value,
            revision,
        })
    }
    async fn get(&self, key: &str) -> Result<Option<RegisterEntry>, BusError> {
        Ok(self
            .store
            .entry(key)
            .await
            .map_err(map_connection)?
            .map(|entry| RegisterEntry {
                key: entry.key,
                value: entry.value.to_vec(),
                revision: entry.revision,
            }))
    }
    async fn delete(&self, key: &str, expected_revision: u64) -> Result<u64, BusError> {
        self.store
            .delete_expect_revision(key, Some(expected_revision))
            .await
            .map_err(map_connection)?;
        let entry = self
            .store
            .entry(key)
            .await
            .map_err(map_connection)?
            .ok_or(BusError::Missing)?;
        Ok(entry.revision)
    }
    async fn snapshot(&self) -> Result<RegisterSnapshot, BusError> {
        let keys = self
            .store
            .keys()
            .await
            .map_err(map_connection)?
            .map_err(map_connection)
            .try_collect::<Vec<_>>()
            .await
            .map_err(map_connection)?;
        let mut entries = Vec::with_capacity(keys.len());
        let mut revision = 0;
        for key in keys {
            if let Some(entry) = self.get(&key).await? {
                revision = revision.max(entry.revision);
                entries.push(entry);
            }
        }
        entries.sort_by(|left, right| left.key.cmp(&right.key));
        Ok(RegisterSnapshot { revision, entries })
    }
    async fn watch_after(&self, revision: u64) -> Result<Vec<RegisterUpdate>, BusError> {
        let mut watch = self
            .store
            .watch_all_from_revision(revision.saturating_add(1))
            .await
            .map_err(map_connection)?;
        let mut updates = Vec::new();
        while let Some(entry) = watch.next().await {
            let entry = entry.map_err(map_connection)?;
            let update = match entry.operation {
                async_nats::jetstream::kv::Operation::Put => RegisterUpdate::Put(RegisterEntry {
                    key: entry.key,
                    value: entry.value.to_vec(),
                    revision: entry.revision,
                }),
                _ => RegisterUpdate::Delete {
                    key: entry.key,
                    revision: entry.revision,
                },
            };
            let done = entry.seen_current;
            updates.push(update);
            if done {
                break;
            }
        }
        Ok(updates)
    }
}
