use crate::NamingError;
use std::time::Duration;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetentionPolicy {
    Limits,
    WorkQueue,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Limits {
    pub max_messages: u64,
    pub max_bytes: u64,
    pub max_age: Duration,
    pub max_message_bytes: u64,
    pub retention: RetentionPolicy,
}

impl Limits {
    pub fn validate(&self, backing_store_retention: Duration) -> Result<(), NamingError> {
        if self.max_messages == 0
            || self.max_bytes == 0
            || self.max_message_bytes == 0
            || self.max_message_bytes > self.max_bytes
            || self.max_age.is_zero()
        {
            return Err(NamingError::InvalidLimits);
        }
        if backing_store_retention < self.max_age {
            return Err(NamingError::RetentionMismatch);
        }
        Ok(())
    }
}
