use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
    time::Instant,
};

use hypermid_contracts::Id;
use hypermid_protocol::{Envelope, MessageKind};
use hypermid_transport::{ControlQueue, RequestLifecycle, TerminalKind};
use thiserror::Error;

use crate::{
    cancellation::{CorrelatedRequest, RouteKey, TerminalRecord},
    deadline::RequestDeadline,
    flow::{Admission, FlowError, QueuedRequest, RouteFlow, RouteFlowConfig},
};

#[derive(Clone, Debug)]
pub struct ConnectionFlow {
    inner: Arc<Mutex<ConnectionState>>,
}

#[derive(Debug)]
struct ConnectionState {
    routes: HashMap<RouteKey, RouteFlow>,
    requests: HashMap<Id, CorrelatedRequest>,
    terminals: VecDeque<TerminalRecord>,
    terminal_capacity: usize,
    control: ControlQueue<Envelope>,
}

#[derive(Clone, Debug)]
pub struct RequestAdmission {
    pub disposition: Admission,
    pub lifecycle: Arc<RequestLifecycle>,
    pub deadline: RequestDeadline,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TerminalDecision {
    Won(TerminalRecord),
    AlreadySettled(TerminalKind),
    Unknown,
}

#[derive(Debug, Error)]
pub enum ConnectionFlowError {
    #[error("connection flow lock is poisoned")]
    Poisoned,
    #[error("route is not registered on this connection")]
    UnknownRoute,
    #[error("request envelope is incomplete or has the wrong kind")]
    InvalidRequest,
    #[error("request id is already correlated")]
    DuplicateRequest,
    #[error("request deadline has expired")]
    DeadlineExpired,
    #[error(transparent)]
    Flow(#[from] FlowError),
    #[error("control queue is full")]
    ControlQueueFull,
}

impl ConnectionFlow {
    pub fn new(
        control_capacity: usize,
        terminal_capacity: usize,
    ) -> Result<Self, ConnectionFlowError> {
        if terminal_capacity == 0 {
            return Err(ConnectionFlowError::ControlQueueFull);
        }
        Ok(Self {
            inner: Arc::new(Mutex::new(ConnectionState {
                routes: HashMap::new(),
                requests: HashMap::new(),
                terminals: VecDeque::with_capacity(terminal_capacity),
                terminal_capacity,
                control: ControlQueue::new(control_capacity)
                    .map_err(|_| ConnectionFlowError::ControlQueueFull)?,
            })),
        })
    }

    pub fn register_route(
        &self,
        route: RouteKey,
        config: RouteFlowConfig,
    ) -> Result<(), ConnectionFlowError> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        state.routes.insert(route, RouteFlow::new(config)?);
        Ok(())
    }

    pub fn admit_request(
        &self,
        envelope: &Envelope,
        frame_bytes: u64,
        wall_now_ms: u64,
        monotonic_now: Instant,
    ) -> Result<RequestAdmission, ConnectionFlowError> {
        if envelope.kind != MessageKind::Request {
            return Err(ConnectionFlowError::InvalidRequest);
        }
        let route = RouteKey {
            route_id: envelope
                .route_id
                .as_ref()
                .map(Id::as_str)
                .ok_or(ConnectionFlowError::InvalidRequest)?
                .to_owned(),
            route_epoch: envelope
                .route_epoch
                .ok_or(ConnectionFlowError::InvalidRequest)?,
        };
        let wire_deadline = envelope
            .deadline_ms
            .ok_or(ConnectionFlowError::InvalidRequest)?;
        let deadline = RequestDeadline::from_wire(wire_deadline, wall_now_ms, monotonic_now);
        if deadline.is_expired_at(monotonic_now) {
            return Err(ConnectionFlowError::DeadlineExpired);
        }
        let mut state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        if state.requests.contains_key(&envelope.message_id) {
            return Err(ConnectionFlowError::DuplicateRequest);
        }
        let flow = state
            .routes
            .get_mut(&route)
            .ok_or(ConnectionFlowError::UnknownRoute)?;
        let disposition = flow.admit(QueuedRequest {
            message_id: envelope.message_id.clone(),
            frame_bytes,
        })?;
        let request = CorrelatedRequest::new(
            envelope.message_id.clone(),
            route,
            deadline,
            disposition == Admission::Admitted,
        );
        let lifecycle = Arc::clone(&request.lifecycle);
        state.requests.insert(envelope.message_id.clone(), request);
        Ok(RequestAdmission {
            disposition,
            lifecycle,
            deadline,
        })
    }

    pub fn enqueue_control(&self, envelope: Envelope) -> Result<(), ConnectionFlowError> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        state
            .control
            .push(envelope)
            .map_err(|_| ConnectionFlowError::ControlQueueFull)
    }

    pub fn pop_control(&self) -> Result<Option<Envelope>, ConnectionFlowError> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        Ok(state.control.pop())
    }

    pub fn cancel(&self, message_id: &Id) -> Result<TerminalDecision, ConnectionFlowError> {
        let lifecycle = {
            let state = self
                .inner
                .lock()
                .map_err(|_| ConnectionFlowError::Poisoned)?;
            state
                .requests
                .get(message_id)
                .map(|request| Arc::clone(&request.lifecycle))
        };
        let Some(lifecycle) = lifecycle else {
            return self.existing_terminal(message_id);
        };
        lifecycle.cancel();
        self.settle(message_id, TerminalKind::Cancelled)
    }

    pub fn settle(
        &self,
        message_id: &Id,
        terminal: TerminalKind,
    ) -> Result<TerminalDecision, ConnectionFlowError> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        let Some(request) = state.requests.get(message_id) else {
            return Ok(state
                .terminals
                .iter()
                .find(|record| &record.message_id == message_id)
                .map_or(TerminalDecision::Unknown, |record| {
                    TerminalDecision::AlreadySettled(record.terminal)
                }));
        };
        if !request.try_terminal(terminal) {
            return Ok(TerminalDecision::AlreadySettled(
                request
                    .lifecycle
                    .terminal()
                    .expect("terminal race has a winner"),
            ));
        }
        let request = state.requests.remove(message_id).expect("request exists");
        let promoted = {
            let flow = state
                .routes
                .get_mut(&request.route)
                .ok_or(ConnectionFlowError::UnknownRoute)?;
            if request.admitted {
                flow.finish(message_id)?;
                flow.take_ready().map(|ready| ready.message_id)
            } else {
                flow.remove_queued(message_id);
                None
            }
        };
        if let Some(promoted) = promoted {
            if let Some(correlated) = state.requests.get_mut(&promoted) {
                correlated.admitted = true;
            }
        }
        let record = TerminalRecord {
            message_id: message_id.clone(),
            route: request.route,
            terminal,
        };
        if state.terminals.len() == state.terminal_capacity {
            state.terminals.pop_front();
        }
        state.terminals.push_back(record.clone());
        Ok(TerminalDecision::Won(record))
    }

    pub fn expire_at(&self, now: Instant) -> Result<Vec<TerminalRecord>, ConnectionFlowError> {
        let ids = {
            let state = self
                .inner
                .lock()
                .map_err(|_| ConnectionFlowError::Poisoned)?;
            state
                .requests
                .iter()
                .filter(|(_, request)| request.deadline.is_expired_at(now))
                .map(|(id, _)| id.clone())
                .collect::<Vec<_>>()
        };
        let mut expired = Vec::new();
        for id in ids {
            if let TerminalDecision::Won(record) = self.settle(&id, TerminalKind::TimedOut)? {
                expired.push(record);
            }
        }
        Ok(expired)
    }

    pub fn disconnect(&self) -> Result<Vec<TerminalRecord>, ConnectionFlowError> {
        let ids = {
            let state = self
                .inner
                .lock()
                .map_err(|_| ConnectionFlowError::Poisoned)?;
            state.requests.keys().cloned().collect::<Vec<_>>()
        };
        let mut disconnected = Vec::new();
        for id in ids {
            if let TerminalDecision::Won(record) = self.settle(&id, TerminalKind::Disconnected)? {
                disconnected.push(record);
            }
        }
        Ok(disconnected)
    }

    pub fn route_available(&self, route: &RouteKey) -> Result<(u32, u64), ConnectionFlowError> {
        let state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        state
            .routes
            .get(route)
            .map(RouteFlow::available)
            .ok_or(ConnectionFlowError::UnknownRoute)
    }

    pub fn route_counts(&self, route: &RouteKey) -> Result<(usize, usize), ConnectionFlowError> {
        let state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        state
            .routes
            .get(route)
            .map(|flow| (flow.active_requests(), flow.queued_requests()))
            .ok_or(ConnectionFlowError::UnknownRoute)
    }

    pub fn grant_bytes(
        &self,
        route: &RouteKey,
        bytes: u64,
    ) -> Result<Vec<Id>, ConnectionFlowError> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        let ready = {
            let flow = state
                .routes
                .get_mut(route)
                .ok_or(ConnectionFlowError::UnknownRoute)?;
            flow.grant_bytes(bytes)?;
            let mut ready = Vec::new();
            while let Some(request) = flow.take_ready() {
                ready.push(request.message_id);
            }
            ready
        };
        for message_id in &ready {
            if let Some(correlated) = state.requests.get_mut(message_id) {
                correlated.admitted = true;
            }
        }
        Ok(ready)
    }

    fn existing_terminal(&self, message_id: &Id) -> Result<TerminalDecision, ConnectionFlowError> {
        let state = self
            .inner
            .lock()
            .map_err(|_| ConnectionFlowError::Poisoned)?;
        Ok(state
            .terminals
            .iter()
            .find(|record| &record.message_id == message_id)
            .map_or(TerminalDecision::Unknown, |record| {
                TerminalDecision::AlreadySettled(record.terminal)
            }))
    }
}
