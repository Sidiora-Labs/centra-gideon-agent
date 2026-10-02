use crate::{BusError, BusMessage, PullRequest};
use async_trait::async_trait;
use hypermid_contracts::Cursor;
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Delivery {
    pub cursor: Cursor,
    pub delivery_count: u32,
    pub message: BusMessage,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeliveryDisposition {
    Ack,
    Nak { delay: Duration },
    Term,
    InProgress { extension: Duration },
}

#[async_trait]
pub trait Stream: Send + Sync {
    async fn publish(&self, message: BusMessage) -> Result<Cursor, BusError>;
    async fn pull(&self, request: &PullRequest) -> Result<Option<Delivery>, BusError>;
    async fn dispose(
        &self,
        durable: &str,
        cursor: Cursor,
        disposition: DeliveryDisposition,
    ) -> Result<(), BusError>;
}
