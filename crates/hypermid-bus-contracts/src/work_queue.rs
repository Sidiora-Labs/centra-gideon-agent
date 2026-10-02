use crate::{BusError, BusMessage};
use async_trait::async_trait;
use hypermid_contracts::Cursor;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueueItem {
    pub cursor: Cursor,
    pub delivery_count: u32,
    pub message: BusMessage,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum QueuePull {
    Item(QueueItem),
    Empty,
    MaxDeliveriesExceeded(QueueItem),
}

#[async_trait]
pub trait WorkQueue: Send + Sync {
    async fn enqueue(&self, message: BusMessage) -> Result<Cursor, BusError>;
    async fn pull(&self, worker: &str, max_deliveries: u32) -> Result<QueuePull, BusError>;
    async fn ack(&self, worker: &str, cursor: Cursor) -> Result<(), BusError>;
    async fn retry(&self, worker: &str, cursor: Cursor) -> Result<(), BusError>;
    async fn dead_letter_then_term(
        &self,
        worker: &str,
        cursor: Cursor,
        dead_letter: BusMessage,
    ) -> Result<Cursor, BusError>;
}
