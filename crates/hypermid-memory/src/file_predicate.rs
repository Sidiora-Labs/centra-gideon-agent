use sha2::{Digest as _, Sha256};
use std::{
    fs, io,
    path::{Path, PathBuf},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DecisionReason {
    InsideRoot,
    OutsideRoot,
    SymlinkEscape,
    Ignored,
    Binary,
    TooLarge,
    Generated,
    Vendor,
    SecretPolicy,
    ChangedDuringRead,
}

#[derive(Clone, Debug)]
pub struct FilePolicy {
    pub max_bytes: u64,
    pub ignored_names: Vec<String>,
    pub denied_names: Vec<String>,
    pub generated_suffixes: Vec<String>,
    pub vendor_directories: Vec<String>,
    pub secret_names: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct FileDecision {
    pub requested_path: PathBuf,
    pub canonical_path: PathBuf,
    pub authorized_root: PathBuf,
    pub allowed: bool,
    pub reason: DecisionReason,
    pub content_digest: Option<String>,
    pub content: Option<String>,
}

pub fn inspect(path: &Path, root: &Path, policy: &FilePolicy) -> io::Result<FileDecision> {
    let canonical_root = root.canonicalize()?;
    let lexical = absolute(path)?;
    let canonical = path.canonicalize()?;
    let decision = |allowed, reason, digest, content| FileDecision {
        requested_path: path.to_path_buf(),
        canonical_path: canonical.clone(),
        authorized_root: canonical_root.clone(),
        allowed,
        reason,
        content_digest: digest,
        content,
    };
    if !lexical.starts_with(&canonical_root) {
        return Ok(decision(false, DecisionReason::OutsideRoot, None, None));
    }
    if !canonical.starts_with(&canonical_root) {
        return Ok(decision(false, DecisionReason::SymlinkEscape, None, None));
    }
    let name = canonical
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    if policy.denied_names.iter().any(|value| value == name) {
        return Ok(decision(false, DecisionReason::OutsideRoot, None, None));
    }
    if policy.ignored_names.iter().any(|value| value == name) {
        return Ok(decision(false, DecisionReason::Ignored, None, None));
    }
    if policy.secret_names.iter().any(|value| value == name) {
        return Ok(decision(false, DecisionReason::SecretPolicy, None, None));
    }
    if policy
        .generated_suffixes
        .iter()
        .any(|suffix| name.ends_with(suffix))
    {
        return Ok(decision(false, DecisionReason::Generated, None, None));
    }
    if canonical
        .strip_prefix(&canonical_root)
        .unwrap()
        .components()
        .any(|component| {
            policy
                .vendor_directories
                .iter()
                .any(|vendor| component.as_os_str() == vendor.as_str())
        })
    {
        return Ok(decision(false, DecisionReason::Vendor, None, None));
    }
    if canonical.metadata()?.len() > policy.max_bytes {
        return Ok(decision(false, DecisionReason::TooLarge, None, None));
    }
    let bytes = fs::read(&canonical)?;
    let digest = hex_digest(&bytes);
    if bytes.iter().take(8192).any(|byte| *byte == 0) {
        return Ok(decision(false, DecisionReason::Binary, Some(digest), None));
    }
    let Ok(content) = String::from_utf8(bytes) else {
        return Ok(decision(false, DecisionReason::Binary, Some(digest), None));
    };
    Ok(decision(
        true,
        DecisionReason::InsideRoot,
        Some(digest),
        Some(content),
    ))
}

pub fn revalidate(decision: &FileDecision) -> io::Result<DecisionReason> {
    let canonical = decision.requested_path.canonicalize()?;
    if canonical != decision.canonical_path || !canonical.starts_with(&decision.authorized_root) {
        return Ok(DecisionReason::SymlinkEscape);
    }
    let current = hex_digest(&fs::read(canonical)?);
    if decision.content_digest.as_deref() != Some(current.as_str()) {
        return Ok(DecisionReason::ChangedDuringRead);
    }
    Ok(decision.reason)
}

fn absolute(path: &Path) -> io::Result<PathBuf> {
    if path.is_absolute() {
        Ok(path.to_path_buf())
    } else {
        Ok(std::env::current_dir()?.join(path))
    }
}

fn hex_digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
