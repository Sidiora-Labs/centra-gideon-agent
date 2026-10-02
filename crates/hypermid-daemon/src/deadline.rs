use std::time::{Duration, Instant};

use hypermid_transport::AbsoluteDeadline;

#[derive(Clone, Copy, Debug)]
pub struct RequestDeadline {
    absolute: AbsoluteDeadline,
}

impl RequestDeadline {
    pub fn from_wire(wire_deadline_ms: u64, wall_now_ms: u64, monotonic_now: Instant) -> Self {
        Self {
            absolute: AbsoluteDeadline::from_wire(wire_deadline_ms, wall_now_ms, monotonic_now),
        }
    }

    pub fn wire_deadline_ms(self) -> u64 {
        self.absolute.wire_deadline_ms()
    }

    pub fn remaining_at(self, now: Instant) -> Duration {
        self.absolute.remaining_at(now)
    }

    pub fn is_expired_at(self, now: Instant) -> bool {
        self.absolute.is_expired_at(now)
    }
}
