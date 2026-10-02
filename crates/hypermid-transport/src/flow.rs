use std::collections::VecDeque;

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum FlowError {
    #[error("route request credit is exhausted")]
    RequestCreditExhausted,
    #[error("route byte credit is exhausted")]
    ByteCreditExhausted,
    #[error("control queue is full")]
    ControlQueueFull,
    #[error("credit exceeds the configured route limit")]
    CreditOverflow,
}

#[derive(Clone, Debug)]
pub struct RouteCredits {
    max_requests: u32,
    max_bytes: u64,
    requests: u32,
    bytes: u64,
}

impl RouteCredits {
    pub fn new(requests: u32, bytes: u64) -> Result<Self, FlowError> {
        if requests == 0 || bytes == 0 {
            return Err(FlowError::CreditOverflow);
        }
        Ok(Self {
            max_requests: requests,
            max_bytes: bytes,
            requests,
            bytes,
        })
    }

    pub fn admit(&mut self, frame_bytes: u64) -> Result<(), FlowError> {
        if self.requests == 0 {
            return Err(FlowError::RequestCreditExhausted);
        }
        if frame_bytes > self.bytes {
            return Err(FlowError::ByteCreditExhausted);
        }
        self.requests -= 1;
        self.bytes -= frame_bytes;
        Ok(())
    }

    pub fn finish_request(&mut self) -> Result<(), FlowError> {
        self.requests = self
            .requests
            .checked_add(1)
            .filter(|value| *value <= self.max_requests)
            .ok_or(FlowError::CreditOverflow)?;
        Ok(())
    }

    pub fn grant_bytes(&mut self, bytes: u64) -> Result<(), FlowError> {
        self.bytes = self
            .bytes
            .checked_add(bytes)
            .filter(|value| *value <= self.max_bytes)
            .ok_or(FlowError::CreditOverflow)?;
        Ok(())
    }

    pub fn available(&self) -> (u32, u64) {
        (self.requests, self.bytes)
    }
}

#[derive(Clone, Debug)]
pub struct ControlQueue<T> {
    capacity: usize,
    queue: VecDeque<T>,
}

impl<T> ControlQueue<T> {
    pub fn new(capacity: usize) -> Result<Self, FlowError> {
        if capacity == 0 {
            return Err(FlowError::ControlQueueFull);
        }
        Ok(Self {
            capacity,
            queue: VecDeque::with_capacity(capacity),
        })
    }

    pub fn push(&mut self, value: T) -> Result<(), FlowError> {
        if self.queue.len() == self.capacity {
            return Err(FlowError::ControlQueueFull);
        }
        self.queue.push_back(value);
        Ok(())
    }

    pub fn pop(&mut self) -> Option<T> {
        self.queue.pop_front()
    }

    pub fn len(&self) -> usize {
        self.queue.len()
    }

    pub fn is_empty(&self) -> bool {
        self.queue.is_empty()
    }
}
