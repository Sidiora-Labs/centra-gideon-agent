use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fmt;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProtectedReservations {
    pub provider_output: u64,
    pub live_tail: u64,
    pub active_tool_pairs: u64,
    pub latest_user_request: u64,
    pub unresolved_approvals: u64,
    pub protected_window: u64,
}

impl ProtectedReservations {
    pub fn total(&self) -> Result<u64, BudgetError> {
        [
            self.provider_output,
            self.live_tail,
            self.active_tool_pairs,
            self.latest_user_request,
            self.unresolved_approvals,
            self.protected_window,
        ]
        .into_iter()
        .try_fold(0_u64, |total, value| {
            total.checked_add(value).ok_or(BudgetError::Overflow)
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryBudget {
    pub context_window: u64,
    pub reservations: ProtectedReservations,
}

impl HistoryBudget {
    pub fn available_history(&self) -> Result<u64, BudgetError> {
        self.context_window
            .checked_sub(self.reservations.total()?)
            .ok_or(BudgetError::ReservationsExceedWindow)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BudgetError {
    Overflow,
    ReservationsExceedWindow,
}
impl fmt::Display for BudgetError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{self:?}")
    }
}
impl Error for BudgetError {}
