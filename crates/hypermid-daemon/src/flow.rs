use std::collections::{HashSet, VecDeque};

use hypermid_contracts::Id;
use hypermid_transport::{FlowError as CreditError, RouteCredits};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RouteFlowConfig {
    pub request_credits: u32,
    pub byte_credits: u64,
    pub max_queued_requests: usize,
    pub max_queued_bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueuedRequest {
    pub message_id: Id,
    pub frame_bytes: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Admission {
    Admitted,
    Queued,
}

#[derive(Debug)]
pub struct RouteFlow {
    credits: RouteCredits,
    max_frame_bytes: u64,
    max_queued_requests: usize,
    max_queued_bytes: u64,
    queued_bytes: u64,
    queued: VecDeque<QueuedRequest>,
    active: HashSet<Id>,
}

#[derive(Debug, Error)]
pub enum FlowError {
    #[error(transparent)]
    Credit(#[from] CreditError),
    #[error("route queue is full")]
    QueueFull,
    #[error("request frame exceeds the route byte limit")]
    FrameTooLarge,
    #[error("request id is already active on this route")]
    DuplicateRequest,
    #[error("terminal request was not active on this route")]
    UnknownRequest,
}

impl RouteFlow {
    pub fn new(config: RouteFlowConfig) -> Result<Self, FlowError> {
        if config.max_queued_requests == 0 || config.max_queued_bytes == 0 {
            return Err(FlowError::QueueFull);
        }
        Ok(Self {
            credits: RouteCredits::new(config.request_credits, config.byte_credits)?,
            max_frame_bytes: config.byte_credits,
            max_queued_requests: config.max_queued_requests,
            max_queued_bytes: config.max_queued_bytes,
            queued_bytes: 0,
            queued: VecDeque::with_capacity(config.max_queued_requests),
            active: HashSet::new(),
        })
    }

    pub fn admit(&mut self, request: QueuedRequest) -> Result<Admission, FlowError> {
        if request.frame_bytes > self.max_frame_bytes {
            return Err(FlowError::FrameTooLarge);
        }
        if self.active.contains(&request.message_id)
            || self
                .queued
                .iter()
                .any(|item| item.message_id == request.message_id)
        {
            return Err(FlowError::DuplicateRequest);
        }
        match self.credits.admit(request.frame_bytes) {
            Ok(()) => {
                self.active.insert(request.message_id);
                Ok(Admission::Admitted)
            }
            Err(CreditError::RequestCreditExhausted | CreditError::ByteCreditExhausted) => {
                let next_bytes = self
                    .queued_bytes
                    .checked_add(request.frame_bytes)
                    .ok_or(FlowError::QueueFull)?;
                if self.queued.len() >= self.max_queued_requests
                    || next_bytes > self.max_queued_bytes
                {
                    return Err(FlowError::QueueFull);
                }
                self.queued_bytes = next_bytes;
                self.queued.push_back(request);
                Ok(Admission::Queued)
            }
            Err(error) => Err(error.into()),
        }
    }

    pub fn remove_queued(&mut self, message_id: &Id) -> bool {
        let Some(index) = self
            .queued
            .iter()
            .position(|item| &item.message_id == message_id)
        else {
            return false;
        };
        let request = self.queued.remove(index).expect("queued index exists");
        self.queued_bytes -= request.frame_bytes;
        true
    }

    pub fn finish(&mut self, message_id: &Id) -> Result<(), FlowError> {
        if !self.active.remove(message_id) {
            return Err(FlowError::UnknownRequest);
        }
        self.credits.finish_request()?;
        Ok(())
    }

    pub fn grant_bytes(&mut self, bytes: u64) -> Result<(), FlowError> {
        self.credits.grant_bytes(bytes)?;
        Ok(())
    }

    pub fn take_ready(&mut self) -> Option<QueuedRequest> {
        let request = self.queued.front()?.clone();
        if self.credits.admit(request.frame_bytes).is_err() {
            return None;
        }
        let request = self.queued.pop_front().expect("front request exists");
        self.queued_bytes -= request.frame_bytes;
        self.active.insert(request.message_id.clone());
        Some(request)
    }

    pub fn available(&self) -> (u32, u64) {
        self.credits.available()
    }

    pub fn active_requests(&self) -> usize {
        self.active.len()
    }

    pub fn queued_requests(&self) -> usize {
        self.queued.len()
    }
}
