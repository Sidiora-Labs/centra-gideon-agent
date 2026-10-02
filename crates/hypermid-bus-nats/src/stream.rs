use crate::connection::{map_connection, NatsConnection};
use async_nats::jetstream::{self, consumer::PullConsumer, AckKind};
use async_trait::async_trait;
use futures::StreamExt;
use hypermid_bus_contracts::{
    cursor, BusError, BusMessage, Delivery, DeliveryDisposition, PullRequest, Stream,
};
use hypermid_contracts::Cursor;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;

#[derive(Clone)]
pub struct NatsStream {
    connection: NatsConnection,
    stream_name: String,
    pending: Arc<Mutex<HashMap<(String, u64), jetstream::Message>>>,
}

impl NatsStream {
    pub async fn bind(connection: NatsConnection, stream_name: &str) -> Result<Self, BusError> {
        if stream_name.is_empty() {
            return Err(BusError::Invalid("stream name is empty".into()));
        }
        connection
            .context
            .get_stream(stream_name)
            .await
            .map_err(map_connection)?;
        Ok(Self {
            connection,
            stream_name: stream_name.into(),
            pending: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    pub fn stream_name(&self) -> &str {
        &self.stream_name
    }
}

#[async_trait]
impl Stream for NatsStream {
    async fn publish(&self, message: BusMessage) -> Result<Cursor, BusError> {
        message.validate()?;
        let operation = message.id.to_string();
        self.connection.begin_operation(&operation)?;
        let result = async {
            let payload = serde_json::to_vec(&message)
                .map_err(|error| BusError::Internal(error.to_string()))?;
            let publish = jetstream::context::Publish::build()
                .payload(payload.into())
                .message_id(message.id.as_str())
                .header("X-Hypermid-Digest", message.digest.to_string());
            let ack = self
                .connection
                .context
                .send_publish(message.subject.clone(), publish)
                .await
                .map_err(map_connection)?
                .await
                .map_err(map_connection)?;
            if ack.stream != self.stream_name {
                return Err(BusError::Conflict);
            }
            if ack.duplicate {
                let stream = self
                    .connection
                    .context
                    .get_stream(&self.stream_name)
                    .await
                    .map_err(map_connection)?;
                let stored = stream
                    .get_raw_message(ack.sequence)
                    .await
                    .map_err(map_connection)?;
                let digest = stored
                    .headers
                    .get("X-Hypermid-Digest")
                    .map(|value| value.as_str());
                if digest != Some(message.digest.to_string().as_str()) {
                    return Err(BusError::Conflict);
                }
            }
            self.connection.flush().await?;
            cursor(ack.sequence)
        }
        .await;
        self.connection.end_operation(&operation)?;
        result
    }

    async fn pull(&self, request: &PullRequest) -> Result<Option<Delivery>, BusError> {
        request.validate()?;
        if self
            .pending
            .lock()
            .await
            .keys()
            .any(|(durable, _)| durable == request.durable.as_str())
        {
            return Ok(None);
        }
        let stream = self
            .connection
            .context
            .get_stream(&self.stream_name)
            .await
            .map_err(map_connection)?;
        let consumer: PullConsumer = stream
            .get_consumer(request.durable.as_str())
            .await
            .map_err(map_connection)?;
        let config = &consumer.cached_info().config;
        if config.durable_name.as_deref() != Some(request.durable.as_str())
            || config.filter_subject != request.filter
            || config.max_ack_pending != 1
            || config.max_deliver != i64::from(request.max_deliveries)
        {
            return Err(BusError::Conflict);
        }
        let mut messages = consumer
            .fetch()
            .max_messages(1)
            .expires(Duration::from_millis(250))
            .messages()
            .await
            .map_err(map_connection)?;
        let Some(message) = messages.next().await else {
            return Ok(None);
        };
        let message = message.map_err(map_connection)?;
        let info = message.info().map_err(map_connection)?;
        let sequence = info.stream_sequence;
        let delivery_count = u32::try_from(info.delivered)
            .map_err(|_| BusError::Internal("delivery count overflow".into()))?;
        let body: BusMessage = serde_json::from_slice(&message.payload)
            .map_err(|error| BusError::Internal(error.to_string()))?;
        self.pending
            .lock()
            .await
            .insert((request.durable.to_string(), sequence), message);
        Ok(Some(Delivery {
            cursor: cursor(sequence)?,
            delivery_count,
            message: body,
        }))
    }

    async fn dispose(
        &self,
        durable: &str,
        delivery: Cursor,
        disposition: DeliveryDisposition,
    ) -> Result<(), BusError> {
        let Some(message) = self
            .pending
            .lock()
            .await
            .remove(&(durable.into(), delivery.sequence))
        else {
            return Err(BusError::Conflict);
        };
        match disposition {
            DeliveryDisposition::Ack => message.double_ack().await.map_err(map_connection),
            DeliveryDisposition::Nak { delay } => message
                .ack_with(AckKind::Nak(Some(delay)))
                .await
                .map_err(map_connection),
            DeliveryDisposition::Term => message
                .ack_with(AckKind::Term)
                .await
                .map_err(map_connection),
            DeliveryDisposition::InProgress { .. } => message
                .ack_with(AckKind::Progress)
                .await
                .map_err(map_connection),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use async_nats::jetstream::consumer;
    use hypermid_contracts::{Digest, Id, Scope, Trace};
    use std::collections::BTreeMap;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn message(id: &str, digest_input: &str, subject: &str) -> BusMessage {
        BusMessage {
            subject: subject.into(),
            id: Id::new(id).unwrap(),
            digest: Digest::sha256(digest_input),
            headers: BTreeMap::new(),
            scope: Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None),
            trace: Trace::new(Id::new("trace").unwrap(), Id::new("request").unwrap()),
        }
    }

    #[tokio::test]
    async fn provisioned_nats_stream_persists_and_resumes() {
        let url = std::env::var("HYPERMID_TEST_NATS_URL")
            .expect("HYPERMID_TEST_NATS_URL must name a real local NATS service");
        let suffix = format!(
            "{}_{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let stream_name = format!("HM_TEST_{suffix}");
        let subject = format!("hm.test.{suffix}.events");
        let durable = format!("worker_{suffix}");
        let connection = NatsConnection::connect(&url, "test-credential-a")
            .await
            .unwrap();
        let provisioned = connection
            .context
            .create_stream(jetstream::stream::Config {
                name: stream_name.clone(),
                subjects: vec![subject.clone()],
                storage: jetstream::stream::StorageType::File,
                duplicate_window: Duration::from_secs(120),
                ..Default::default()
            })
            .await
            .unwrap();
        provisioned
            .create_consumer(consumer::pull::Config {
                durable_name: Some(durable.clone()),
                filter_subject: subject.clone(),
                ack_wait: Duration::from_millis(200),
                max_deliver: 2,
                max_ack_pending: 1,
                ..Default::default()
            })
            .await
            .unwrap();

        let bus = NatsStream::bind(connection.clone(), &stream_name)
            .await
            .unwrap();
        let first = bus
            .publish(message("event-1", "payload", &subject))
            .await
            .unwrap();
        assert_eq!(
            bus.publish(message("event-1", "payload", &subject))
                .await
                .unwrap(),
            first
        );
        assert!(matches!(
            bus.publish(message("event-1", "different", &subject)).await,
            Err(BusError::Conflict)
        ));
        connection.flush().await.unwrap();
        drop(bus);
        drop(connection);

        let rotated = NatsConnection::connect(&url, "test-credential-b")
            .await
            .unwrap();
        let bus = NatsStream::bind(rotated.clone(), &stream_name)
            .await
            .unwrap();
        let request = PullRequest {
            durable: Id::new(&durable).unwrap(),
            filter: subject.clone(),
            ack_wait: Duration::from_millis(200),
            max_deliveries: 2,
        };
        let delivery = bus
            .pull(&request)
            .await
            .unwrap()
            .expect("retained message after reconnect");
        assert_eq!(delivery.cursor, first);
        bus.dispose(&durable, delivery.cursor, DeliveryDisposition::Ack)
            .await
            .unwrap();
        rotated.context.delete_stream(&stream_name).await.unwrap();
    }
}
