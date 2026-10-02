use hypermid_contracts::Id;
use rusqlite::{params, OptionalExtension, Transaction};

pub const BUDGET_SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS hypermid_budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    job_class TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    region TEXT NOT NULL,
    background INTEGER NOT NULL CHECK(background IN (0, 1)),
    estimated_input_tokens INTEGER NOT NULL,
    estimated_output_tokens INTEGER NOT NULL,
    reserved_cost_nanodollars INTEGER NOT NULL,
    actual_input_tokens INTEGER,
    actual_output_tokens INTEGER,
    actual_cost_nanodollars INTEGER,
    provider_request_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('reserved', 'settled', 'unknown', 'released')),
    reserved_at_ms INTEGER NOT NULL,
    reconciled_at_ms INTEGER
);
CREATE INDEX IF NOT EXISTS hypermid_budget_owner_time
ON hypermid_budget_reservations(owner_id, project_id, reserved_at_ms);
CREATE INDEX IF NOT EXISTS hypermid_budget_job
ON hypermid_budget_reservations(owner_id, project_id, job_id);
"#;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BudgetLimits {
    pub max_call_nanodollars: u64,
    pub max_concurrent: u64,
    pub max_hourly_nanodollars: u64,
    pub max_daily_nanodollars: u64,
    pub max_job_nanodollars: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProviderPolicy {
    pub provider_id: String,
    pub model_id: String,
    pub region: String,
    pub enabled: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OwnerBudgetPolicy {
    pub owner_id: Id,
    pub project_id: Id,
    pub revision: u64,
    pub limits: BudgetLimits,
    pub providers: Vec<ProviderPolicy>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AdmissionRequest {
    pub reservation_id: Id,
    pub owner_id: Id,
    pub project_id: Id,
    pub job_id: Id,
    pub job_class: String,
    pub provider_id: String,
    pub model_id: String,
    pub region: String,
    pub background: bool,
    pub estimated_input_tokens: u64,
    pub estimated_output_tokens: u64,
    pub estimated_cost_nanodollars: u64,
    pub now_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReservationStatus {
    Reserved,
    Settled,
    Unknown,
    Released,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Reservation {
    pub reservation_id: Id,
    pub status: ReservationStatus,
    pub reserved_cost_nanodollars: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ActualUsage {
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cost_nanodollars: Option<u64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum BudgetError {
    #[error("model use denied by owner policy: scope")]
    ScopeDenied,
    #[error("model use denied by owner policy: provider")]
    ProviderDenied,
    #[error("model use denied by owner policy: per_call")]
    PerCallExceeded,
    #[error("model use denied by owner policy: concurrency")]
    ConcurrencyExceeded,
    #[error("model use denied by owner policy: hourly")]
    HourlyExceeded,
    #[error("model use denied by owner policy: daily")]
    DailyExceeded,
    #[error("model use denied by owner policy: job_total")]
    JobExceeded,
    #[error("budget reservation is invalid")]
    InvalidReservation,
    #[error("budget storage failed")]
    Storage,
}

pub fn install_schema(transaction: &Transaction<'_>) -> Result<(), BudgetError> {
    transaction
        .execute_batch(BUDGET_SCHEMA_SQL)
        .map_err(|_| BudgetError::Storage)
}

pub fn reserve(
    transaction: &Transaction<'_>,
    policy: &OwnerBudgetPolicy,
    request: &AdmissionRequest,
) -> Result<Reservation, BudgetError> {
    if request.owner_id != policy.owner_id
        || request.project_id != policy.project_id
        || policy.revision == 0
    {
        return Err(BudgetError::ScopeDenied);
    }
    let provider_allowed = policy.providers.iter().any(|provider| {
        provider.enabled
            && provider.provider_id == request.provider_id
            && provider.model_id == request.model_id
            && provider.region == request.region
    });
    if !provider_allowed {
        return Err(BudgetError::ProviderDenied);
    }
    if request.estimated_cost_nanodollars > policy.limits.max_call_nanodollars {
        return Err(BudgetError::PerCallExceeded);
    }
    let now = sql_u64(request.now_ms)?;
    let hour_start = now.saturating_sub(3_600_000);
    let day_start = now.saturating_sub(86_400_000);
    let concurrent = count_active(transaction, request)?;
    if concurrent >= policy.limits.max_concurrent {
        return Err(BudgetError::ConcurrencyExceeded);
    }
    let hourly = sum_charged(transaction, request, Some(hour_start), None)?;
    checked_limit(
        hourly,
        request.estimated_cost_nanodollars,
        policy.limits.max_hourly_nanodollars,
        BudgetError::HourlyExceeded,
    )?;
    let daily = sum_charged(transaction, request, Some(day_start), None)?;
    checked_limit(
        daily,
        request.estimated_cost_nanodollars,
        policy.limits.max_daily_nanodollars,
        BudgetError::DailyExceeded,
    )?;
    let job = sum_charged(transaction, request, None, Some(&request.job_id))?;
    checked_limit(
        job,
        request.estimated_cost_nanodollars,
        policy.limits.max_job_nanodollars,
        BudgetError::JobExceeded,
    )?;

    let inserted = transaction
        .execute(
            "INSERT INTO hypermid_budget_reservations(
                 reservation_id, owner_id, project_id, job_id, job_class,
                 provider_id, model_id, region, background,
                 estimated_input_tokens, estimated_output_tokens,
                 reserved_cost_nanodollars, status, reserved_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, 'reserved', ?13)",
            params![
                request.reservation_id.as_str(),
                request.owner_id.as_str(),
                request.project_id.as_str(),
                request.job_id.as_str(),
                request.job_class,
                request.provider_id,
                request.model_id,
                request.region,
                request.background,
                sql_u64(request.estimated_input_tokens)?,
                sql_u64(request.estimated_output_tokens)?,
                sql_u64(request.estimated_cost_nanodollars)?,
                now,
            ],
        )
        .map_err(|_| BudgetError::InvalidReservation)?;
    if inserted != 1 {
        return Err(BudgetError::InvalidReservation);
    }
    Ok(Reservation {
        reservation_id: request.reservation_id.clone(),
        status: ReservationStatus::Reserved,
        reserved_cost_nanodollars: request.estimated_cost_nanodollars,
    })
}

pub fn reconcile(
    transaction: &Transaction<'_>,
    reservation_id: &Id,
    actual: Option<ActualUsage>,
    provider_request_id: Option<&str>,
    charge_unknown: bool,
    now_ms: u64,
) -> Result<Reservation, BudgetError> {
    let reserved: Option<u64> = transaction
        .query_row(
            "SELECT reserved_cost_nanodollars FROM hypermid_budget_reservations
             WHERE reservation_id=?1 AND status='reserved'",
            params![reservation_id.as_str()],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| BudgetError::Storage)?;
    let reserved = reserved.ok_or(BudgetError::InvalidReservation)?;
    let status = if charge_unknown {
        ReservationStatus::Unknown
    } else {
        ReservationStatus::Settled
    };
    let actual_cost = if charge_unknown {
        None
    } else {
        actual.and_then(|usage| usage.cost_nanodollars)
    };
    transaction
        .execute(
            "UPDATE hypermid_budget_reservations SET
                 actual_input_tokens=?2, actual_output_tokens=?3,
                 actual_cost_nanodollars=?4, provider_request_id=?5,
                 status=?6, reconciled_at_ms=?7
             WHERE reservation_id=?1 AND status='reserved'",
            params![
                reservation_id.as_str(),
                actual
                    .and_then(|usage| usage.input_tokens)
                    .map(sql_u64)
                    .transpose()?,
                actual
                    .and_then(|usage| usage.output_tokens)
                    .map(sql_u64)
                    .transpose()?,
                actual_cost.map(sql_u64).transpose()?,
                provider_request_id,
                status_name(status),
                sql_u64(now_ms)?,
            ],
        )
        .map_err(|_| BudgetError::Storage)?;
    Ok(Reservation {
        reservation_id: reservation_id.clone(),
        status,
        reserved_cost_nanodollars: reserved,
    })
}

pub fn release(
    transaction: &Transaction<'_>,
    reservation_id: &Id,
    now_ms: u64,
) -> Result<Reservation, BudgetError> {
    let reserved: Option<u64> = transaction
        .query_row(
            "SELECT reserved_cost_nanodollars FROM hypermid_budget_reservations
             WHERE reservation_id=?1 AND status='reserved'",
            params![reservation_id.as_str()],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| BudgetError::Storage)?;
    let reserved = reserved.ok_or(BudgetError::InvalidReservation)?;
    transaction
        .execute(
            "UPDATE hypermid_budget_reservations
             SET status='released', reconciled_at_ms=?2
             WHERE reservation_id=?1 AND status='reserved'",
            params![reservation_id.as_str(), sql_u64(now_ms)?],
        )
        .map_err(|_| BudgetError::Storage)?;
    Ok(Reservation {
        reservation_id: reservation_id.clone(),
        status: ReservationStatus::Released,
        reserved_cost_nanodollars: reserved,
    })
}

fn count_active(
    transaction: &Transaction<'_>,
    request: &AdmissionRequest,
) -> Result<u64, BudgetError> {
    transaction
        .query_row(
            "SELECT COUNT(*) FROM hypermid_budget_reservations
             WHERE owner_id=?1 AND project_id=?2 AND status IN ('reserved', 'unknown')",
            params![request.owner_id.as_str(), request.project_id.as_str()],
            |row| row.get(0),
        )
        .map_err(|_| BudgetError::Storage)
}

fn sum_charged(
    transaction: &Transaction<'_>,
    request: &AdmissionRequest,
    since_ms: Option<i64>,
    job_id: Option<&Id>,
) -> Result<u64, BudgetError> {
    let value: u64 = transaction
        .query_row(
            "SELECT COALESCE(SUM(CASE
                 WHEN status='settled' THEN COALESCE(actual_cost_nanodollars, reserved_cost_nanodollars)
                 ELSE reserved_cost_nanodollars END), 0)
             FROM hypermid_budget_reservations
             WHERE owner_id=?1 AND project_id=?2 AND status!='released'
               AND (?3 IS NULL OR reserved_at_ms>=?3)
               AND (?4 IS NULL OR job_id=?4)",
            params![
                request.owner_id.as_str(),
                request.project_id.as_str(),
                since_ms,
                job_id.map(Id::as_str),
            ],
            |row| row.get(0),
        )
        .map_err(|_| BudgetError::Storage)?;
    Ok(value)
}

fn checked_limit(
    used: u64,
    requested: u64,
    limit: u64,
    error: BudgetError,
) -> Result<(), BudgetError> {
    if used
        .checked_add(requested)
        .map_or(true, |total| total > limit)
    {
        return Err(error);
    }
    Ok(())
}

fn sql_u64(value: u64) -> Result<i64, BudgetError> {
    i64::try_from(value).map_err(|_| BudgetError::InvalidReservation)
}

fn status_name(status: ReservationStatus) -> &'static str {
    match status {
        ReservationStatus::Reserved => "reserved",
        ReservationStatus::Settled => "settled",
        ReservationStatus::Unknown => "unknown",
        ReservationStatus::Released => "released",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn policy(concurrency: u64) -> OwnerBudgetPolicy {
        OwnerBudgetPolicy {
            owner_id: id("owner-1"),
            project_id: id("project-1"),
            revision: 1,
            limits: BudgetLimits {
                max_call_nanodollars: 100,
                max_concurrent: concurrency,
                max_hourly_nanodollars: 150,
                max_daily_nanodollars: 200,
                max_job_nanodollars: 120,
            },
            providers: vec![ProviderPolicy {
                provider_id: "centra".into(),
                model_id: "model-a".into(),
                region: "eu".into(),
                enabled: true,
            }],
        }
    }

    fn request(id_value: &str, cost: u64) -> AdmissionRequest {
        AdmissionRequest {
            reservation_id: id(id_value),
            owner_id: id("owner-1"),
            project_id: id("project-1"),
            job_id: id("job-1"),
            job_class: "summary".into(),
            provider_id: "centra".into(),
            model_id: "model-a".into(),
            region: "eu".into(),
            background: true,
            estimated_input_tokens: 10,
            estimated_output_tokens: 10,
            estimated_cost_nanodollars: cost,
            now_ms: 100_000_000,
        }
    }

    fn connection() -> Connection {
        let connection = Connection::open_in_memory().unwrap();
        let tx = connection.unchecked_transaction().unwrap();
        install_schema(&tx).unwrap();
        tx.commit().unwrap();
        connection
    }

    #[test]
    fn budget_unknown_charge_retains_concurrency_and_cost() {
        let mut connection = connection();
        let tx = connection.transaction().unwrap();
        reserve(&tx, &policy(1), &request("reservation-1", 60)).unwrap();
        tx.commit().unwrap();
        let tx = connection.transaction().unwrap();
        reconcile(&tx, &id("reservation-1"), None, None, true, 100_000_010).unwrap();
        tx.commit().unwrap();
        let tx = connection.transaction().unwrap();
        assert_eq!(
            reserve(&tx, &policy(1), &request("reservation-2", 10)),
            Err(BudgetError::ConcurrencyExceeded)
        );
    }

    #[test]
    fn budget_exact_provider_and_all_cost_limits_are_enforced() {
        let mut connection = connection();
        let mut denied_provider = request("reservation-provider", 1);
        denied_provider.region = "us".into();
        let tx = connection.transaction().unwrap();
        assert_eq!(
            reserve(&tx, &policy(3), &denied_provider),
            Err(BudgetError::ProviderDenied)
        );
        reserve(&tx, &policy(3), &request("reservation-1", 80)).unwrap();
        assert_eq!(
            reserve(&tx, &policy(3), &request("reservation-2", 50)),
            Err(BudgetError::JobExceeded)
        );
    }
}
