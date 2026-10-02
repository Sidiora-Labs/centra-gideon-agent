use hypermid_contracts::{Digest, Error, Id};
use rusqlite::{params, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};

use crate::MemoryResult;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceKind {
    Memory,
    Message,
    File,
    GitCommit,
    External,
}

impl SourceKind {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Memory => "memory",
            Self::Message => "message",
            Self::File => "file",
            Self::GitCommit => "git_commit",
            Self::External => "external",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceSnapshot {
    pub source_id: Id,
    pub owner_scope_digest: Digest,
    pub kind: SourceKind,
    pub source_digest: Digest,
    pub locator: Option<String>,
    pub captured_content: Option<String>,
    pub capture_method: String,
    pub observed_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProvenanceSpan {
    pub source_id: Id,
    pub span_start: Option<u64>,
    pub span_end: Option<u64>,
    pub quoted_digest: Option<Digest>,
}

impl ProvenanceSpan {
    pub fn validate(&self) -> MemoryResult<()> {
        if self.span_start.is_some() != self.span_end.is_some()
            || self
                .span_start
                .zip(self.span_end)
                .is_some_and(|(start, end)| end < start)
        {
            return Err(error("INVALID_SOURCE_SPAN", "source span is invalid"));
        }
        Ok(())
    }
}

pub(crate) fn put_source(
    transaction: &Transaction<'_>,
    source: &SourceSnapshot,
    created_at_ms: u64,
) -> MemoryResult<Id> {
    if source.capture_method.is_empty() || source.capture_method.len() > 128 {
        return Err(error(
            "INVALID_PROVENANCE",
            "capture_method must contain 1..128 bytes",
        ));
    }
    if source
        .locator
        .as_ref()
        .is_some_and(|value| value.len() > 4_096)
    {
        return Err(error("INVALID_PROVENANCE", "source locator is too large"));
    }
    if let Some(content) = &source.captured_content {
        if Digest::sha256(content.as_bytes()) != source.source_digest {
            return Err(error(
                "SOURCE_DIGEST_MISMATCH",
                "captured source bytes do not match source_digest",
            ));
        }
    }

    let duplicate: Option<String> = transaction
        .query_row(
            "SELECT source_id FROM memory_sources
             WHERE owner_scope_digest=?1 AND source_kind=?2 AND source_digest=?3
               AND COALESCE(locator, '')=COALESCE(?4, '')
             ORDER BY source_id LIMIT 1",
            params![
                source.owner_scope_digest.to_string(),
                source.kind.as_str(),
                source.source_digest.to_string(),
                source.locator
            ],
            |row| row.get(0),
        )
        .optional()
        .map_err(sql_error)?;
    if let Some(source_id) = duplicate {
        return Id::new(source_id).map_err(|_| error("CORRUPT_STORE", "invalid source id"));
    }

    transaction
        .execute(
            "INSERT INTO memory_sources(
                source_id, owner_scope_digest, source_kind, source_digest, locator,
                captured_content, capture_method, observed_at_ms, created_at_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                source.source_id.as_str(),
                source.owner_scope_digest.to_string(),
                source.kind.as_str(),
                source.source_digest.to_string(),
                source.locator,
                source.captured_content,
                source.capture_method,
                source.observed_at_ms,
                created_at_ms
            ],
        )
        .map_err(|cause| {
            if cause.to_string().contains("UNIQUE constraint failed") {
                error("SOURCE_ID_CONFLICT", "source id already names other bytes")
            } else {
                sql_error(cause)
            }
        })?;
    Ok(source.source_id.clone())
}

pub(crate) fn attach_spans(
    transaction: &Transaction<'_>,
    record_id: &Id,
    revision: u64,
    spans: &[ProvenanceSpan],
) -> MemoryResult<()> {
    if spans.len() > 128 {
        return Err(error("INVALID_PROVENANCE", "too many provenance spans"));
    }
    for span in spans {
        span.validate()?;
        let (stored_start, stored_end) = match (span.span_start, span.span_end) {
            (Some(start), Some(end)) => (start, end),
            (None, None) => {
                let captured_bytes: Option<u64> = transaction
                    .query_row(
                        "SELECT CASE WHEN captured_content IS NULL THEN NULL
                                     ELSE length(CAST(captured_content AS BLOB)) END
                         FROM memory_sources WHERE source_id=?1",
                        [span.source_id.as_str()],
                        |row| row.get(0),
                    )
                    .optional()
                    .map_err(sql_error)?
                    .flatten();
                (0, captured_bytes.unwrap_or(0))
            }
            _ => unreachable!("validated source span"),
        };
        if let Some(quoted_digest) = span.quoted_digest {
            let recovered =
                recover_source_bytes(transaction, &span.source_id, span.span_start, span.span_end)?;
            if Digest::sha256(recovered.as_bytes()) != quoted_digest {
                return Err(error(
                    "QUOTED_DIGEST_MISMATCH",
                    "source span does not match quoted_digest",
                ));
            }
        }
        transaction
            .execute(
                "INSERT INTO memory_provenance(
                    record_id, revision, source_id, span_start, span_end, quoted_digest
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    record_id.as_str(),
                    revision,
                    span.source_id.as_str(),
                    stored_start,
                    stored_end,
                    span.quoted_digest.map(Digest::to_hex)
                ],
            )
            .map_err(sql_error)?;
    }
    Ok(())
}

pub fn recover_source_bytes(
    transaction: &Transaction<'_>,
    source_id: &Id,
    span_start: Option<u64>,
    span_end: Option<u64>,
) -> MemoryResult<String> {
    if span_start.is_some() != span_end.is_some()
        || span_start
            .zip(span_end)
            .is_some_and(|(start, end)| end < start)
    {
        return Err(error("INVALID_SOURCE_SPAN", "source span is invalid"));
    }
    let (content, expected_digest): (Option<String>, String) = transaction
        .query_row(
            "SELECT captured_content, source_digest FROM memory_sources WHERE source_id=?1",
            [source_id.as_str()],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(sql_error)?
        .ok_or_else(|| error("SOURCE_NOT_FOUND", "provenance source does not exist"))?;
    let content = content.ok_or_else(|| {
        error(
            "SOURCE_CONTENT_UNAVAILABLE",
            "source has only an external reference",
        )
    })?;
    if Digest::sha256(content.as_bytes()).to_string() != expected_digest {
        return Err(error(
            "SOURCE_DIGEST_MISMATCH",
            "captured source failed its durable digest guard",
        ));
    }
    let Some((start, end)) = span_start.zip(span_end) else {
        return Ok(content);
    };
    let bytes = content.as_bytes();
    let selected = bytes
        .get(start as usize..end as usize)
        .ok_or_else(|| error("INVALID_SOURCE_SPAN", "source span is outside content"))?;
    std::str::from_utf8(selected)
        .map(str::to_owned)
        .map_err(|_| error("INVALID_SOURCE_SPAN", "source span splits UTF-8"))
}

pub(crate) fn error(code: &str, message: &str) -> Error {
    Error::new(code, message, false, None, None).expect("static memory error is valid")
}

pub(crate) fn sql_error(cause: rusqlite::Error) -> Error {
    let _ = cause;
    error("MEMORY_STORE_ERROR", "memory store operation failed")
}
