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


pub const CAPTURE_SCHEMA: &str = "
CREATE TABLE memory_capture_origins(
 source_id TEXT PRIMARY KEY REFERENCES memory_sources(source_id) ON DELETE CASCADE,
 owner_scope_digest TEXT NOT NULL, history_scope_digest TEXT NOT NULL,
 history_session_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
 source_digest TEXT NOT NULL, original_actor_json TEXT NOT NULL,
 effective_actor_json TEXT NOT NULL, ingress_event_id TEXT NOT NULL,
 ingress_own_digest TEXT NOT NULL, capture_kind TEXT NOT NULL CHECK(capture_kind='owner_words'),
 capture_digest TEXT NOT NULL, captured_at_ms INTEGER NOT NULL
);
CREATE TABLE memory_chat_retractions(
 owner_scope_digest TEXT NOT NULL, history_scope_digest TEXT NOT NULL,
 history_session_id TEXT NOT NULL, retracted_at_ms INTEGER NOT NULL,
 PRIMARY KEY(owner_scope_digest,history_scope_digest,history_session_id)
);";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureActor { pub kind: String, pub name: String, pub tenant: String }

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OwnerWordCapture {
 pub history_session_id: Id, pub source_event_id: Id, pub source_digest: Digest,
 pub original_actor: CaptureActor, pub effective_actor: CaptureActor,
 pub ingress_event_id: String, pub ingress_own_digest: Digest,
 pub own_text: String, pub capture_kind: String,
}

impl OwnerWordCapture {
 pub fn validate(&self) -> MemoryResult<()> {
  if self.capture_kind != "owner_words" || self.original_actor.kind != "owner"
   || self.effective_actor.kind != "owner" || self.ingress_event_id.is_empty()
   || self.ingress_event_id.len()>4096 || self.own_text.is_empty()
   || self.own_text.len()>262144 || Digest::sha256(self.own_text.as_bytes()) != self.ingress_own_digest {
    return Err(error("CAPTURE_ORIGIN_DENIED","owner-word capture evidence is invalid"));
  }
  Ok(())
 }
}

pub(crate) fn attach_capture(transaction: &Transaction<'_>, draft: &crate::RecordDraft,
 sources: &[SourceSnapshot], capture: &OwnerWordCapture, actor_scope: &crate::Scope, now_ms:u64) -> MemoryResult<()> {
 capture.validate()?;
 let history_digest=crate::scope_digest(actor_scope).to_string();
 let target_digest=crate::scope_digest(&draft.scope).to_string();
 let retired:bool=transaction.query_row("SELECT EXISTS(SELECT 1 FROM memory_chat_retractions WHERE owner_scope_digest=?1 AND history_scope_digest=?2 AND history_session_id=?3)",params![target_digest,history_digest,capture.history_session_id.as_str()],|r|r.get(0)).map_err(sql_error)?;
 if retired { return Err(error("CHAT_SOURCE_RETRACTED","chat source has been retired")); }
 if sources.len()!=1 || sources[0].kind!=SourceKind::Message || sources[0].source_digest!=capture.source_digest
  || draft.provenance.len()!=1 || draft.provenance[0].source_id!=sources[0].source_id {
   return Err(error("CAPTURE_SOURCE_MISMATCH","capture must reference its exact native message snapshot"));
 }
 let existing:bool=transaction.query_row("SELECT EXISTS(SELECT 1 FROM memory_capture_origins WHERE source_id=?1)",[sources[0].source_id.as_str()],|r|r.get(0)).map_err(sql_error)?;
 if existing {
  let prior=validated_capture(transaction,&sources[0].source_id)?.ok_or_else(damaged_capture)?;
  if prior.owner_scope_digest.to_string()!=target_digest || prior.history_scope_digest.to_string()!=history_digest || prior.capture!=*capture {
   return Err(error("CAPTURE_SOURCE_CONFLICT","source attribution is immutable"));
  }
  return Ok(());
 }
 let digest=bound_capture_digest(capture,&target_digest,&history_digest,now_ms)?;
 transaction.execute("INSERT INTO memory_capture_origins VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)",params![sources[0].source_id.as_str(),target_digest,history_digest,capture.history_session_id.as_str(),capture.source_event_id.as_str(),capture.source_digest.to_string(),serde_json::to_string(&capture.original_actor).map_err(|_|damaged_capture())?,serde_json::to_string(&capture.effective_actor).map_err(|_|damaged_capture())?,capture.ingress_event_id,capture.ingress_own_digest.to_string(),capture.capture_kind,digest,now_ms]).map_err(sql_error)?;
 Ok(())
}


#[derive(Clone,Debug)]
pub(crate) struct ValidatedCapture {
 pub capture:OwnerWordCapture,
 pub owner_scope_digest:Digest,
 pub history_scope_digest:Digest,
 pub capture_digest:Digest,
 pub retired:bool,
}

fn damaged_capture()->Error {
 error("CAPTURE_PROOF_DAMAGED","persisted capture evidence is unavailable or inconsistent")
}

fn bound_capture_digest(capture:&OwnerWordCapture,owner:&str,history:&str,at:u64)->MemoryResult<String> {
 let mut encoded=b"hypermid.memory.capture-origin.v2\0".to_vec();
 encoded.extend(serde_json::to_vec(&(owner,history,at,capture)).map_err(|_|damaged_capture())?);
 Ok(Digest::sha256(&encoded).to_string())
}

struct StoredCapture {
 owner:String,history:String,session:String,event:String,digest:String,
 original:String,effective:String,ingress:String,own_digest:String,kind:String,proof:String,at:u64,
}

pub(crate) fn validated_capture(tx:&Transaction<'_>,source_id:&Id)->MemoryResult<Option<ValidatedCapture>> {
 let raw=tx.query_row("SELECT owner_scope_digest,history_scope_digest,history_session_id,source_event_id,source_digest,original_actor_json,effective_actor_json,ingress_event_id,ingress_own_digest,capture_kind,capture_digest,captured_at_ms FROM memory_capture_origins WHERE source_id=?1",[source_id.as_str()],|r|Ok(StoredCapture{owner:r.get(0)?,history:r.get(1)?,session:r.get(2)?,event:r.get(3)?,digest:r.get(4)?,original:r.get(5)?,effective:r.get(6)?,ingress:r.get(7)?,own_digest:r.get(8)?,kind:r.get(9)?,proof:r.get(10)?,at:r.get(11)?})).optional().map_err(sql_error)?;
 let Some(raw)=raw else{return Ok(None)};
 let snapshot:Option<(String,String,String,Option<String>,String)>=tx.query_row("SELECT owner_scope_digest,source_kind,source_digest,captured_content,capture_method FROM memory_sources WHERE source_id=?1",[source_id.as_str()],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?))).optional().map_err(sql_error)?;
 let (source_owner,source_kind,source_digest,content,method)=snapshot.ok_or_else(damaged_capture)?;
 let content=content.ok_or_else(damaged_capture)?;
 if source_owner!=raw.owner || source_kind!="message" || source_digest!=raw.digest || method!="accepted_owner_words"
  || Digest::sha256(content.as_bytes()).to_string()!=raw.digest {return Err(damaged_capture());}
 let row:serde_json::Value=serde_json::from_str(&content).map_err(|_|damaged_capture())?;
 let own_text=row["content"].as_str().ok_or_else(damaged_capture)?.to_owned();
 let capture=OwnerWordCapture{history_session_id:Id::new(raw.session.clone()).map_err(|_|damaged_capture())?,
  source_event_id:Id::new(raw.event.clone()).map_err(|_|damaged_capture())?,source_digest:raw.digest.parse().map_err(|_|damaged_capture())?,
  original_actor:serde_json::from_str(&raw.original).map_err(|_|damaged_capture())?,effective_actor:serde_json::from_str(&raw.effective).map_err(|_|damaged_capture())?,
  ingress_event_id:raw.ingress.clone(),ingress_own_digest:raw.own_digest.parse().map_err(|_|damaged_capture())?,own_text,capture_kind:raw.kind.clone()};
 capture.validate().map_err(|_|damaged_capture())?;
 let ingress=&row["meta"]["ingress"];
 let thread=ingress["source_thread"].as_str().ok_or_else(damaged_capture)?;
 let mut candidates=vec![thread.to_owned()];
 let mut residual=thread;
 while let Some(rest)=residual.strip_prefix("dashboard_"){residual=rest;}
 if !residual.is_empty() && residual!=thread {candidates.push(format!("dashboard_{residual}"));}
 let canonical=candidates.into_iter().find(|key|format!("session:{}",Digest::sha256(key.as_bytes()))==capture.history_session_id.as_str()).ok_or_else(damaged_capture)?;
 let native_event=if let Some(event)=row["source_event_id"].as_str().filter(|v|!v.is_empty()){event.to_owned()} else {
  let mut identity=canonical.into_bytes();identity.push(0);identity.extend(content.as_bytes());
  format!("legacy:{}",Digest::sha256(identity))
 };
 if row["role"]!="user" || native_event!=capture.source_event_id.as_str()
  || ingress["source_event_id"].as_str()!=Some(&capture.ingress_event_id)
  || ingress["source_digest"].as_str()!=Some(raw.own_digest.as_str())
  || ingress["principal"]!=serde_json::to_value(&capture.original_actor).map_err(|_|damaged_capture())?
  || ingress["origin_proof"].as_str().is_none_or(|p|p.is_empty()) {return Err(damaged_capture());}
 let owner:Digest=raw.owner.parse().map_err(|_|damaged_capture())?;
 let history:Digest=raw.history.parse().map_err(|_|damaged_capture())?;
 for digest in [&raw.owner,&raw.history] {
  let scope:Option<String>=tx.query_row("SELECT scope_json FROM memory_scopes WHERE scope_digest=?1",[digest],|r|r.get(0)).optional().map_err(sql_error)?;
  let scope:crate::Scope=serde_json::from_str(&scope.ok_or_else(damaged_capture)?).map_err(|_|damaged_capture())?;
  if crate::scope_digest(&scope).to_string()!=*digest {return Err(damaged_capture());}
 }
 let bound=bound_capture_digest(&capture,&raw.owner,&raw.history,raw.at)?;
 if raw.proof!=bound {
  let legacy=Digest::sha256(serde_json::to_vec(&capture).map_err(|_|damaged_capture())?).to_string();
  if raw.proof!=legacy {return Err(damaged_capture());}
  let original_binding:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM memory_provenance p JOIN memory_revisions v ON v.record_id=p.record_id AND v.revision=p.revision JOIN memory_records r ON r.record_id=p.record_id WHERE p.source_id=?1 AND v.author_scope_digest=?2 AND v.authored_at_ms=?3 AND r.owner_scope_digest=?4)",params![source_id.as_str(),raw.history,raw.at,raw.owner],|r|r.get(0)).map_err(sql_error)?;
  if !original_binding {return Err(damaged_capture());}
 }
 let retired:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM memory_chat_retractions WHERE owner_scope_digest=?1 AND history_scope_digest=?2 AND history_session_id=?3)",params![raw.owner,raw.history,raw.session],|r|r.get(0)).map_err(sql_error)?;
 Ok(Some(ValidatedCapture{capture,owner_scope_digest:owner,history_scope_digest:history,capture_digest:raw.proof.parse().map_err(|_|damaged_capture())?,retired}))
}

pub(crate) fn require_live_source(tx:&Transaction<'_>,source_id:&Id)->MemoryResult<()> {
 if validated_capture(tx,source_id)?.is_some_and(|capture|capture.retired) {
  return Err(error("CHAT_SOURCE_RETRACTED","chat source has been retired"));
 }
 Ok(())
}

pub(crate) fn require_live_revision(tx:&Transaction<'_>,record_id:&Id,digest:Digest)->MemoryResult<()> {
 let mut pending=vec![(record_id.clone(),digest)];
 let mut seen=std::collections::BTreeSet::new();
 while let Some((id,digest))=pending.pop(){
  if !seen.insert((id.to_string(),digest.to_string())){continue;}
  let revision:Option<u64>=tx.query_row("SELECT revision FROM memory_revisions WHERE record_id=?1 AND revision_digest=?2",params![id.as_str(),digest.to_string()],|r|r.get(0)).optional().map_err(sql_error)?;
  let revision=revision.ok_or_else(damaged_capture)?;
  let mut st=tx.prepare("SELECT source_id FROM memory_provenance WHERE record_id=?1 AND revision=?2").map_err(sql_error)?;
  let sources=st.query_map(params![id.as_str(),revision],|r|r.get::<_,String>(0)).map_err(sql_error)?.collect::<Result<Vec<_>,_>>().map_err(sql_error)?;
  for source in sources {require_live_source(tx,&Id::new(source).map_err(|_|damaged_capture())?)?;}
  let mut st=tx.prepare("SELECT parent_record_id,parent_revision_digest FROM memory_lineage WHERE child_record_id=?1 AND child_revision=?2 AND relation IN ('derived_from','merged_from','split_from','imported_from')").map_err(sql_error)?;
  let parents=st.query_map(params![id.as_str(),revision],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?))).map_err(sql_error)?.collect::<Result<Vec<_>,_>>().map_err(sql_error)?;
  for (id,digest) in parents {pending.push((Id::new(id).map_err(|_|damaged_capture())?,digest.parse().map_err(|_|damaged_capture())?));}
 }
 Ok(())
}
