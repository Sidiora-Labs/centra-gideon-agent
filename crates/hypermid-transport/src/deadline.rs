use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug)]
pub struct AbsoluteDeadline {
    wire_deadline_ms: u64,
    monotonic_deadline: Instant,
}

impl AbsoluteDeadline {
    pub fn from_wire(wire_deadline_ms: u64, wall_now_ms: u64, monotonic_now: Instant) -> Self {
        let remaining_ms = wire_deadline_ms.saturating_sub(wall_now_ms);
        Self {
            wire_deadline_ms,
            monotonic_deadline: monotonic_now + Duration::from_millis(remaining_ms),
        }
    }

    pub fn wire_deadline_ms(self) -> u64 {
        self.wire_deadline_ms
    }

    pub fn remaining_at(self, now: Instant) -> Duration {
        self.monotonic_deadline.saturating_duration_since(now)
    }

    pub fn is_expired_at(self, now: Instant) -> bool {
        now >= self.monotonic_deadline
    }
}
