use crate::{error, EffectState, MemoryResult, MemoryTransaction};
use getrandom::fill;
use rusqlite::{params, OptionalExtension};
use sha2::{Digest as _, Sha256};

#[derive(Clone, Eq, PartialEq)]
pub struct LeaseToken([u8; 32]);

impl LeaseToken {
    pub fn generate() -> MemoryResult<Self> {
        let mut bytes = [0_u8; 32];
        fill(&mut bytes).map_err(|_| {
            error(
                "ENTROPY_UNAVAILABLE",
                "operating-system entropy is unavailable",
                EffectState::NotStarted,
            )
        })?;
        Ok(Self(bytes))
    }

    pub fn from_bytes(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    pub fn digest_hex(&self) -> String {
        hex_lower(&Sha256::digest(self.0))
    }

    pub fn to_hex(&self) -> String {
        hex_lower(&self.0)
    }
}

impl std::fmt::Debug for LeaseToken {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("LeaseToken(<redacted>)")
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LeaseClaim {
    pub owner_scope_digest: String,
    pub lock_family: String,
    pub job_id: String,
    pub holder_id: String,
    pub expires_at_ms: i64,
    token: LeaseToken,
}

impl LeaseClaim {
    pub fn token(&self) -> &LeaseToken {
        &self.token
    }
}

pub fn claim(
    transaction: &MemoryTransaction<'_>,
    owner_scope_digest: &str,
    lock_family: &str,
    job_id: &str,
    holder_id: &str,
    now_ms: i64,
    ttl_ms: i64,
) -> MemoryResult<LeaseClaim> {
    if now_ms < 0 || ttl_ms <= 0 {
        return Err(invalid("lease timestamp and duration must be positive"));
    }
    let expires_at_ms = now_ms
        .checked_add(ttl_ms)
        .ok_or_else(|| invalid("lease expiry overflow"))?;
    let live = transaction
        .raw()
        .query_row(
            "SELECT expires_at_ms FROM maintenance_leases
             WHERE owner_scope_digest=?1 AND lock_family=?2",
            params![owner_scope_digest, lock_family],
            |row| row.get::<_, i64>(0),
        )
        .optional()
        .map_err(sql_error)?;
    if live.is_some_and(|expiry| expiry > now_ms) {
        return Err(contended());
    }
    transaction
        .raw()
        .execute(
            "DELETE FROM maintenance_leases
             WHERE owner_scope_digest=?1 AND lock_family=?2 AND expires_at_ms<=?3",
            params![owner_scope_digest, lock_family, now_ms],
        )
        .map_err(sql_error)?;

    let token = LeaseToken::generate()?;
    transaction
        .raw()
        .execute(
            "INSERT INTO maintenance_leases(
                owner_scope_digest, lock_family, job_id, fencing_token_digest,
                holder_id, acquired_at_ms, heartbeat_at_ms, expires_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?7)",
            params![
                owner_scope_digest,
                lock_family,
                job_id,
                token.digest_hex(),
                holder_id,
                now_ms,
                expires_at_ms,
            ],
        )
        .map_err(sql_error)?;
    Ok(LeaseClaim {
        owner_scope_digest: owner_scope_digest.to_owned(),
        lock_family: lock_family.to_owned(),
        job_id: job_id.to_owned(),
        holder_id: holder_id.to_owned(),
        expires_at_ms,
        token,
    })
}

pub fn heartbeat(
    transaction: &MemoryTransaction<'_>,
    claim: &mut LeaseClaim,
    now_ms: i64,
    ttl_ms: i64,
) -> MemoryResult<()> {
    if ttl_ms <= 0 || now_ms < 0 || now_ms >= claim.expires_at_ms {
        return Err(stale());
    }
    let expires_at_ms = now_ms
        .checked_add(ttl_ms)
        .ok_or_else(|| invalid("lease expiry overflow"))?;
    let changed = transaction
        .raw()
        .execute(
            "UPDATE maintenance_leases SET heartbeat_at_ms=?1, expires_at_ms=?2
             WHERE owner_scope_digest=?3 AND lock_family=?4 AND job_id=?5
               AND holder_id=?6 AND fencing_token_digest=?7 AND expires_at_ms>?1",
            params![
                now_ms,
                expires_at_ms,
                claim.owner_scope_digest,
                claim.lock_family,
                claim.job_id,
                claim.holder_id,
                claim.token.digest_hex(),
            ],
        )
        .map_err(sql_error)?;
    if changed != 1 {
        return Err(stale());
    }
    claim.expires_at_ms = expires_at_ms;
    Ok(())
}

pub fn require_live(
    transaction: &MemoryTransaction<'_>,
    claim: &LeaseClaim,
    now_ms: i64,
) -> MemoryResult<()> {
    let found = transaction
        .raw()
        .query_row(
            "SELECT 1 FROM maintenance_leases
             WHERE owner_scope_digest=?1 AND lock_family=?2 AND job_id=?3
               AND holder_id=?4 AND fencing_token_digest=?5 AND expires_at_ms>?6",
            params![
                claim.owner_scope_digest,
                claim.lock_family,
                claim.job_id,
                claim.holder_id,
                claim.token.digest_hex(),
                now_ms,
            ],
            |_| Ok(()),
        )
        .optional()
        .map_err(sql_error)?;
    found.ok_or_else(stale)
}

pub fn release(
    transaction: &MemoryTransaction<'_>,
    claim: &LeaseClaim,
    now_ms: i64,
) -> MemoryResult<()> {
    require_live(transaction, claim, now_ms)?;
    let changed = transaction
        .raw()
        .execute(
            "DELETE FROM maintenance_leases
             WHERE owner_scope_digest=?1 AND lock_family=?2 AND job_id=?3
               AND holder_id=?4 AND fencing_token_digest=?5",
            params![
                claim.owner_scope_digest,
                claim.lock_family,
                claim.job_id,
                claim.holder_id,
                claim.token.digest_hex(),
            ],
        )
        .map_err(sql_error)?;
    if changed != 1 {
        return Err(stale());
    }
    Ok(())
}

fn invalid(message: &'static str) -> hypermid_contracts::Error {
    error("INVALID_LEASE", message, EffectState::NotStarted)
}

fn contended() -> hypermid_contracts::Error {
    error(
        "LEASE_CONTENDED",
        "scope maintenance lease is held by another live worker",
        EffectState::NotStarted,
    )
}

fn stale() -> hypermid_contracts::Error {
    error(
        "STALE_FENCE",
        "lease is expired, superseded, or belongs to another worker",
        EffectState::NotStarted,
    )
}

fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "STORE_WRITE_FAILED",
        format!("maintenance lease persistence failed: {source}"),
        EffectState::Unknown,
    )
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}
