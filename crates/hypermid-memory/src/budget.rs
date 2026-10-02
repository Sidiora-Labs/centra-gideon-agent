use crate::{error, EffectState, MemoryResult, MemoryTransaction};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BudgetAmount {
    pub items: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub requests: u64,
    pub cost_units: u64,
    pub retries: u64,
    pub wall_ms: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModelBudget {
    pub max_items: u64,
    pub max_input_tokens: u64,
    pub max_output_tokens: u64,
    pub max_requests: u64,
    pub max_cost_units: u64,
    pub max_retries: u64,
    pub max_wall_ms: u64,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ActualUsage {
    pub items: Option<u64>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub requests: Option<u64>,
    pub cost_units: Option<u64>,
    pub retries: Option<u64>,
    pub wall_ms: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BudgetReservation {
    pub reservation_id: String,
    pub job_id: String,
    pub reserved: BudgetAmount,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct UsageSummary {
    pub charged: BudgetAmount,
    pub unknown_mask: u8,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BudgetState {
    charged: BudgetAmount,
    #[serde(default)]
    reservations: BTreeMap<String, BudgetAmount>,
    #[serde(default)]
    settlements: BTreeMap<String, Settlement>,
    #[serde(default)]
    unknown_mask: u8,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Settlement {
    actual: ActualUsage,
    charged: BudgetAmount,
    unknown_mask: u8,
}

pub fn reserve(
    transaction: &MemoryTransaction<'_>,
    job_id: &str,
    reservation_id: &str,
    amount: BudgetAmount,
) -> MemoryResult<BudgetReservation> {
    let (limit, mut state) = load(transaction.raw(), job_id)?;
    if let Some(existing) = state.reservations.get(reservation_id) {
        if existing == &amount {
            return Ok(BudgetReservation {
                reservation_id: reservation_id.to_owned(),
                job_id: job_id.to_owned(),
                reserved: amount,
            });
        }
        return Err(conflict("reservation id was reused with different limits"));
    }
    if state.settlements.contains_key(reservation_id) {
        return Err(conflict("reservation was already settled"));
    }
    let pending = state
        .reservations
        .values()
        .copied()
        .try_fold(BudgetAmount::default(), checked_add)?;
    require_within(
        limit,
        checked_add(checked_add(state.charged, pending)?, amount)?,
    )?;
    state.reservations.insert(reservation_id.to_owned(), amount);
    save(transaction, job_id, &state)?;
    Ok(BudgetReservation {
        reservation_id: reservation_id.to_owned(),
        job_id: job_id.to_owned(),
        reserved: amount,
    })
}

pub fn settle(
    transaction: &MemoryTransaction<'_>,
    reservation: &BudgetReservation,
    actual: ActualUsage,
) -> MemoryResult<UsageSummary> {
    let (limit, mut state) = load(transaction.raw(), &reservation.job_id)?;
    let reserved = state
        .reservations
        .remove(&reservation.reservation_id)
        .ok_or_else(|| conflict("reservation is missing or already settled"))?;
    if reserved != reservation.reserved {
        return Err(conflict("reservation contents changed before settlement"));
    }
    let (charged, unknown_mask) = actual.charge_against(reserved)?;
    let next = checked_add(state.charged, charged)?;
    require_within(limit, next)?;
    state.charged = next;
    state.unknown_mask |= unknown_mask;
    state.settlements.insert(
        reservation.reservation_id.clone(),
        Settlement {
            actual,
            charged,
            unknown_mask,
        },
    );
    save(transaction, &reservation.job_id, &state)?;
    Ok(UsageSummary {
        charged: state.charged,
        unknown_mask: state.unknown_mask,
    })
}

pub fn release(
    transaction: &MemoryTransaction<'_>,
    reservation: &BudgetReservation,
) -> MemoryResult<()> {
    let (_, mut state) = load(transaction.raw(), &reservation.job_id)?;
    let removed = state.reservations.remove(&reservation.reservation_id);
    if removed.as_ref() != Some(&reservation.reserved) {
        return Err(conflict("reservation is missing or changed"));
    }
    save(transaction, &reservation.job_id, &state)
}

pub fn usage(connection: &Connection, job_id: &str) -> MemoryResult<UsageSummary> {
    let (_, state) = load(connection, job_id)?;
    Ok(UsageSummary {
        charged: state.charged,
        unknown_mask: state.unknown_mask,
    })
}

impl ActualUsage {
    fn charge_against(self, reserved: BudgetAmount) -> MemoryResult<(BudgetAmount, u8)> {
        let mut unknown_mask = 0_u8;
        Ok((
            BudgetAmount {
                items: known_or_reserved(self.items, reserved.items, &mut unknown_mask, 0)?,
                input_tokens: known_or_reserved(
                    self.input_tokens,
                    reserved.input_tokens,
                    &mut unknown_mask,
                    1,
                )?,
                output_tokens: known_or_reserved(
                    self.output_tokens,
                    reserved.output_tokens,
                    &mut unknown_mask,
                    2,
                )?,
                requests: known_or_reserved(
                    self.requests,
                    reserved.requests,
                    &mut unknown_mask,
                    3,
                )?,
                cost_units: known_or_reserved(
                    self.cost_units,
                    reserved.cost_units,
                    &mut unknown_mask,
                    4,
                )?,
                retries: known_or_reserved(self.retries, reserved.retries, &mut unknown_mask, 5)?,
                wall_ms: known_or_reserved(self.wall_ms, reserved.wall_ms, &mut unknown_mask, 6)?,
            },
            unknown_mask,
        ))
    }
}

fn load(connection: &Connection, job_id: &str) -> MemoryResult<(ModelBudget, BudgetState)> {
    let (budget_json, usage_json): (String, String) = connection
        .query_row(
            "SELECT budget_json, usage_json FROM maintenance_jobs WHERE job_id=?1",
            [job_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .map_err(sql_error)?;
    let budget = serde_json::from_str(&budget_json)
        .map_err(|_| corrupt("maintenance budget is malformed"))?;
    let state = if usage_json.trim() == "{}" {
        BudgetState::default()
    } else {
        serde_json::from_str(&usage_json)
            .map_err(|_| corrupt("maintenance usage ledger is malformed"))?
    };
    Ok((budget, state))
}

fn save(
    transaction: &MemoryTransaction<'_>,
    job_id: &str,
    state: &BudgetState,
) -> MemoryResult<()> {
    let encoded = serde_json::to_string(state)
        .map_err(|_| corrupt("maintenance usage ledger could not be encoded"))?;
    let changed = transaction
        .raw()
        .execute(
            "UPDATE maintenance_jobs SET usage_json=?1 WHERE job_id=?2",
            params![encoded, job_id],
        )
        .map_err(sql_error)?;
    if changed != 1 {
        return Err(conflict("maintenance job disappeared during budget update"));
    }
    Ok(())
}

fn known_or_reserved(
    actual: Option<u64>,
    reserved: u64,
    mask: &mut u8,
    bit: u8,
) -> MemoryResult<u64> {
    match actual {
        Some(actual) if actual <= reserved => Ok(actual),
        Some(_) => Err(exceeded()),
        None => {
            *mask |= 1 << bit;
            Ok(reserved)
        }
    }
}

fn checked_add(left: BudgetAmount, right: BudgetAmount) -> MemoryResult<BudgetAmount> {
    Ok(BudgetAmount {
        items: left.items.checked_add(right.items).ok_or_else(overflow)?,
        input_tokens: left
            .input_tokens
            .checked_add(right.input_tokens)
            .ok_or_else(overflow)?,
        output_tokens: left
            .output_tokens
            .checked_add(right.output_tokens)
            .ok_or_else(overflow)?,
        requests: left
            .requests
            .checked_add(right.requests)
            .ok_or_else(overflow)?,
        cost_units: left
            .cost_units
            .checked_add(right.cost_units)
            .ok_or_else(overflow)?,
        retries: left
            .retries
            .checked_add(right.retries)
            .ok_or_else(overflow)?,
        wall_ms: left
            .wall_ms
            .checked_add(right.wall_ms)
            .ok_or_else(overflow)?,
    })
}

fn require_within(limit: ModelBudget, amount: BudgetAmount) -> MemoryResult<()> {
    if amount.items > limit.max_items
        || amount.input_tokens > limit.max_input_tokens
        || amount.output_tokens > limit.max_output_tokens
        || amount.requests > limit.max_requests
        || amount.cost_units > limit.max_cost_units
        || amount.retries > limit.max_retries
        || amount.wall_ms > limit.max_wall_ms
    {
        return Err(exceeded());
    }
    Ok(())
}

fn exceeded() -> hypermid_contracts::Error {
    error(
        "MODEL_BUDGET_EXCEEDED",
        "maintenance model budget is exhausted",
        EffectState::NotStarted,
    )
}

fn overflow() -> hypermid_contracts::Error {
    error(
        "MODEL_BUDGET_OVERFLOW",
        "maintenance model budget value overflowed",
        EffectState::NotStarted,
    )
}

fn conflict(message: &'static str) -> hypermid_contracts::Error {
    error(
        "BUDGET_RESERVATION_CONFLICT",
        message,
        EffectState::NotStarted,
    )
}

fn corrupt(message: &'static str) -> hypermid_contracts::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}

fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "STORE_WRITE_FAILED",
        format!("maintenance budget persistence failed: {source}"),
        EffectState::Unknown,
    )
}
