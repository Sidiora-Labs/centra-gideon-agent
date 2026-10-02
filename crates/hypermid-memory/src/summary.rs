use hypermid_contracts::{Digest, Id};
use rusqlite::{params, Transaction};
use serde::{Deserialize, Serialize};

use crate::provenance::{error, sql_error};
use crate::MemoryResult;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SummaryLevel {
    Brief,
    Standard,
    Detailed,
    Exhaustive,
}

impl SummaryLevel {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Brief => "brief",
            Self::Standard => "standard",
            Self::Detailed => "detailed",
            Self::Exhaustive => "exhaustive",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryDetails {
    pub input_set_digest: Digest,
    pub level: SummaryLevel,
    pub decay_half_life_ms: Option<u64>,
}

pub(crate) fn put_summary_details(
    transaction: &Transaction<'_>,
    record_id: &Id,
    details: &SummaryDetails,
    refreshed_at_ms: u64,
) -> MemoryResult<()> {
    if details.decay_half_life_ms == Some(0) {
        return Err(error(
            "INVALID_SUMMARY",
            "summary decay half life must be positive",
        ));
    }
    transaction
        .execute(
            "INSERT INTO summary_details(
                record_id, input_set_digest, summary_level,
                decay_half_life_ms, refreshed_at_ms, stale_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, NULL)
             ON CONFLICT(record_id) DO UPDATE SET
                input_set_digest=excluded.input_set_digest,
                summary_level=excluded.summary_level,
                decay_half_life_ms=excluded.decay_half_life_ms,
                refreshed_at_ms=excluded.refreshed_at_ms,
                stale_at_ms=NULL",
            params![
                record_id.as_str(),
                details.input_set_digest.to_string(),
                details.level.as_str(),
                details.decay_half_life_ms,
                refreshed_at_ms
            ],
        )
        .map_err(sql_error)?;
    Ok(())
}
