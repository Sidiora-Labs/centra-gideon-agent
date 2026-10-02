use std::path::Path;

use hypermid_contracts::{Digest, Id};
use serde::{Deserialize, Serialize};

use crate::journal::{Journal, JournalError};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
enum BridgeEntry {
    OutboxIntent {
        message_id: Id,
        subject: String,
        digest: Digest,
    },
    OutboxReceipt {
        message_id: Id,
        broker_sequence: u64,
    },
    InboxReceipt {
        message_id: Id,
        digest: Digest,
    },
}

pub trait BrokerPublisher {
    type Error;

    fn publish(&mut self, message_id: &Id, subject: &str, body: &[u8]) -> Result<u64, Self::Error>;
}

pub struct DurableBridge {
    journal: Journal<BridgeEntry>,
}

impl DurableBridge {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, JournalError> {
        Ok(Self {
            journal: Journal::open(path)?,
        })
    }

    pub fn publish<P: BrokerPublisher>(
        &mut self,
        publisher: &mut P,
        message_id: Id,
        subject: String,
        body: &[u8],
    ) -> Result<u64, BridgeError<P::Error>> {
        let digest = Digest::sha256(body);
        self.journal.append(BridgeEntry::OutboxIntent {
            message_id: message_id.clone(),
            subject: subject.clone(),
            digest,
        })?;
        let broker_sequence = publisher
            .publish(&message_id, &subject, body)
            .map_err(BridgeError::Broker)?;
        self.journal.append(BridgeEntry::OutboxReceipt {
            message_id,
            broker_sequence,
        })?;
        Ok(broker_sequence)
    }

    pub fn record_inbox_before_delivery(
        &mut self,
        message_id: Id,
        body: &[u8],
    ) -> Result<(), JournalError> {
        self.journal.append(BridgeEntry::InboxReceipt {
            message_id,
            digest: Digest::sha256(body),
        })?;
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum BridgeError<E> {
    #[error(transparent)]
    Journal(#[from] JournalError),
    #[error("broker publish failed")]
    Broker(E),
}
