use crate::protocol::{
    bind_authenticated_request, BoundMemoryRequest, MemoryEventBatch, MemoryRequest, MemoryResponse,
};
use crate::{error, Cursor, EffectState, MemoryResult, Scope};
use hypermid_protocol::{parse_json, Envelope, Principal};
use serde_json::to_vec;
use std::collections::HashSet;

pub trait MemoryEndpoint {
    fn acknowledged(
        &mut self,
        request: &BoundMemoryRequest,
    ) -> MemoryResult<Option<MemoryResponse>>;

    fn execute(&mut self, request: BoundMemoryRequest) -> MemoryResult<MemoryResponse>;

    fn resume(
        &mut self,
        scope: &Scope,
        after: Cursor,
        maximum_events: usize,
    ) -> MemoryResult<MemoryEventBatch>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BusLimits {
    pub max_inflight: usize,
    pub max_request_bytes: usize,
}

impl BusLimits {
    pub fn validate(self) -> MemoryResult<Self> {
        if self.max_inflight == 0
            || self.max_request_bytes == 0
            || self.max_request_bytes > hypermid_protocol::MAX_FRAME_BYTES
        {
            return Err(transport_error(
                "MEMORY_BUS_LIMIT_INVALID",
                "the memory bus limits are invalid",
            ));
        }
        Ok(self)
    }
}

pub struct AuthenticatedMemoryBus<E> {
    endpoint: E,
    principal: Principal,
    authenticated_scope: Scope,
    limits: BusLimits,
    inflight: usize,
    cancelled: HashSet<String>,
    connected: bool,
}

impl<E: MemoryEndpoint> AuthenticatedMemoryBus<E> {
    pub fn new(
        endpoint: E,
        principal: Principal,
        authenticated_scope: Scope,
        limits: BusLimits,
    ) -> MemoryResult<Self> {
        Ok(Self {
            endpoint,
            principal,
            authenticated_scope,
            limits: limits.validate()?,
            inflight: 0,
            cancelled: HashSet::new(),
            connected: true,
        })
    }

    pub fn cancel(&mut self, message_id: &str) -> bool {
        self.cancelled.insert(message_id.to_owned())
    }

    pub fn disconnect(&mut self) {
        self.cancelled.clear();
        self.inflight = 0;
        self.connected = false;
    }

    pub fn reconnect(&mut self) {
        self.connected = true;
    }

    pub fn dispatch(&mut self, envelope: Envelope, now_ms: u64) -> MemoryResult<MemoryResponse> {
        if !self.connected {
            return Err(transport_error(
                "MEMORY_BUS_DISCONNECTED",
                "the memory bus is disconnected",
            ));
        }
        if self.cancelled.remove(envelope.message_id.as_str()) {
            return Err(transport_error(
                "MEMORY_REQUEST_CANCELLED",
                "the memory request was cancelled before dispatch",
            ));
        }
        if self.inflight >= self.limits.max_inflight {
            return Err(transport_error(
                "MEMORY_BACKPRESSURE",
                "the memory bus has no request credit",
            ));
        }
        let bytes = to_vec(&envelope).map_err(|_| {
            transport_error(
                "MEMORY_ENVELOPE_INVALID",
                "the memory envelope cannot be encoded",
            )
        })?;
        if bytes.len() > self.limits.max_request_bytes {
            return Err(transport_error(
                "MEMORY_BACKPRESSURE",
                "the memory request exceeds its byte credit",
            ));
        }
        let decoded: Envelope = parse_json(&bytes).map_err(|_| {
            transport_error(
                "MEMORY_ENVELOPE_INVALID",
                "the memory envelope failed shared protocol validation",
            )
        })?;
        self.inflight += 1;
        let bound = bind_authenticated_request(
            &self.principal,
            &self.authenticated_scope,
            &decoded,
            now_ms,
        );
        let result = bound.and_then(|request| execute_once(&mut self.endpoint, request));
        self.inflight -= 1;
        result
    }

    pub fn resume(
        &mut self,
        after: Cursor,
        maximum_events: usize,
    ) -> MemoryResult<MemoryEventBatch> {
        if !self.connected {
            return Err(transport_error(
                "MEMORY_BUS_DISCONNECTED",
                "the memory bus is disconnected",
            ));
        }
        if maximum_events == 0 || maximum_events > self.limits.max_inflight {
            return Err(transport_error(
                "MEMORY_BACKPRESSURE",
                "the requested event credit exceeds the memory bus limit",
            ));
        }
        let batch = self
            .endpoint
            .resume(&self.authenticated_scope, after, maximum_events)?;
        batch.validate(maximum_events)?;
        Ok(batch)
    }

    pub fn into_endpoint(self) -> E {
        self.endpoint
    }
}

pub fn dispatch_in_process<E: MemoryEndpoint>(
    endpoint: &mut E,
    principal: &Principal,
    authenticated_scope: &Scope,
    request: MemoryRequest,
    now_ms: u64,
) -> MemoryResult<MemoryResponse> {
    request.validate()?;
    let envelope = crate::protocol::request_envelope(request)?;
    let bound = bind_authenticated_request(principal, authenticated_scope, &envelope, now_ms)?;
    execute_once(endpoint, bound)
}

fn execute_once<E: MemoryEndpoint>(
    endpoint: &mut E,
    request: BoundMemoryRequest,
) -> MemoryResult<MemoryResponse> {
    if request.request.operation.mutates() {
        if let Some(mut response) = endpoint.acknowledged(&request)? {
            response.replayed = true;
            return Ok(response);
        }
    }
    endpoint.execute(request)
}

fn transport_error(code: &'static str, message: &'static str) -> hypermid_contracts::Error {
    error(code, message, EffectState::NotStarted)
}
