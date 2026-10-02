use async_trait::async_trait;
use hypermid_bus_contracts::{
    BusError, BusMessage, DeliveryDisposition, PullRequest, QueueItem, QueuePull, Stream, WorkQueue,
};
use hypermid_contracts::{Cursor, Id};
use std::time::Duration;

use crate::NatsStream;

#[derive(Clone)]
pub struct NatsWorkQueue {
    stream: NatsStream,
    filter: String,
    ack_wait: Duration,
}

impl NatsWorkQueue {
    pub fn bind(
        stream: NatsStream,
        filter: impl Into<String>,
        ack_wait: Duration,
    ) -> Result<Self, BusError> {
        let filter = filter.into();
        if filter.is_empty() || ack_wait.is_zero() {
            return Err(BusError::Invalid("invalid work queue binding".into()));
        }
        Ok(Self {
            stream,
            filter,
            ack_wait,
        })
    }
}

#[async_trait]
impl WorkQueue for NatsWorkQueue {
    async fn enqueue(&self, message: BusMessage) -> Result<Cursor, BusError> {
        self.stream.publish(message).await
    }
    async fn pull(&self, worker: &str, max_deliveries: u32) -> Result<QueuePull, BusError> {
        let request = PullRequest {
            durable: Id::new(worker).map_err(|error| BusError::Invalid(error.to_string()))?,
            filter: self.filter.clone(),
            ack_wait: self.ack_wait,
            max_deliveries,
        };
        match self.stream.pull(&request).await? {
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
        self.stream
            .dispose(worker, delivery, DeliveryDisposition::Ack)
            .await
    }
    async fn retry(&self, worker: &str, delivery: Cursor) -> Result<(), BusError> {
        self.stream
            .dispose(
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
        let dead_cursor = self.stream.publish(dead_letter).await?;
        self.stream
            .dispose(worker, delivery, DeliveryDisposition::Term)
            .await?;
        Ok(dead_cursor)
    }
}
