use std::sync::Arc;

use hypermid_contracts::Id;
use hypermid_transport::{RequestLifecycle, TerminalKind};

use crate::deadline::RequestDeadline;

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct RouteKey {
    pub route_id: String,
    pub route_epoch: u64,
}

#[derive(Debug)]
pub struct CorrelatedRequest {
    pub route: RouteKey,
    pub deadline: RequestDeadline,
    pub lifecycle: Arc<RequestLifecycle>,
    pub admitted: bool,
}

impl CorrelatedRequest {
    pub fn new(message_id: Id, route: RouteKey, deadline: RequestDeadline, admitted: bool) -> Self {
        Self {
            route,
            deadline,
            lifecycle: Arc::new(RequestLifecycle::new(message_id)),
            admitted,
        }
    }

    pub fn try_terminal(&self, terminal: TerminalKind) -> bool {
        self.lifecycle.try_terminal(terminal)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TerminalRecord {
    pub message_id: Id,
    pub route: RouteKey,
    pub terminal: TerminalKind,
}
