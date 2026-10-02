use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};

use hypermid_protocol::Id;

const OPEN: u8 = 0;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum TerminalKind {
    Response = 1,
    Error = 2,
    Cancelled = 3,
    TimedOut = 4,
    Disconnected = 5,
}

impl TerminalKind {
    fn from_byte(value: u8) -> Option<Self> {
        match value {
            1 => Some(Self::Response),
            2 => Some(Self::Error),
            3 => Some(Self::Cancelled),
            4 => Some(Self::TimedOut),
            5 => Some(Self::Disconnected),
            _ => None,
        }
    }
}

#[derive(Debug)]
pub struct RequestLifecycle {
    message_id: Id,
    cancellation_requested: AtomicBool,
    terminal: AtomicU8,
}

impl RequestLifecycle {
    pub fn new(message_id: Id) -> Self {
        Self {
            message_id,
            cancellation_requested: AtomicBool::new(false),
            terminal: AtomicU8::new(OPEN),
        }
    }

    pub fn message_id(&self) -> &Id {
        &self.message_id
    }

    pub fn cancel(&self) -> bool {
        !self.cancellation_requested.swap(true, Ordering::AcqRel)
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancellation_requested.load(Ordering::Acquire)
    }

    pub fn permits_non_terminal_output(&self) -> bool {
        !self.is_cancelled() && self.terminal.load(Ordering::Acquire) == OPEN
    }

    pub fn try_terminal(&self, outcome: TerminalKind) -> bool {
        self.terminal
            .compare_exchange(OPEN, outcome as u8, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    }

    pub fn terminal(&self) -> Option<TerminalKind> {
        TerminalKind::from_byte(self.terminal.load(Ordering::Acquire))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancellation_is_idempotent_and_terminal_is_unique() {
        let request = RequestLifecycle::new(Id::new("request-1").unwrap());
        assert!(request.cancel());
        assert!(!request.cancel());
        assert!(!request.permits_non_terminal_output());
        assert!(request.try_terminal(TerminalKind::Cancelled));
        assert!(!request.try_terminal(TerminalKind::Response));
        assert_eq!(request.terminal(), Some(TerminalKind::Cancelled));
    }
}
