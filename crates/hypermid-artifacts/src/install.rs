use crate::manifest::{
    sha256_hex, ArtifactCapability, FileKind, Platform, SignedArtifactManifest, TrustStore,
};
use flate2::read::GzDecoder;
use sha2::{Digest as _, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use tar::EntryType;
use tempfile::Builder;
use thiserror::Error;
use unicode_normalization::UnicodeNormalization;

#[derive(Clone, Copy, Debug)]
pub struct InstallLimits {
    pub max_archive_bytes: u64,
    pub max_unpacked_bytes: u64,
    pub max_entries: usize,
}

impl Default for InstallLimits {
    fn default() -> Self {
        Self {
            max_archive_bytes: 256 * 1024 * 1024,
            max_unpacked_bytes: 1024 * 1024 * 1024,
            max_entries: 16_384,
        }
    }
}

#[derive(Debug)]
pub struct VerifiedArtifact {
    pub manifest: crate::manifest::ArtifactManifest,
    pub staging_path: PathBuf,
}

pub fn stage_verified(
    archive_bytes: &[u8],
    signed: &SignedArtifactManifest,
    trust: &TrustStore,
    expected_platform: &Platform,
    approved_capabilities: &BTreeSet<ArtifactCapability>,
    staging_root: &Path,
    limits: InstallLimits,
) -> Result<VerifiedArtifact, InstallError> {
    trust.verify(signed)?;
    if archive_bytes.len() as u64 > limits.max_archive_bytes {
        return Err(InstallError::ArchiveLimit);
    }
    if sha256_hex(archive_bytes) != signed.manifest.archive_sha256 {
        return Err(InstallError::DigestMismatch("archive".to_owned()));
    }
    if &signed.manifest.platform != expected_platform {
        return Err(InstallError::PlatformMismatch);
    }
    let expansion: Vec<_> = signed
        .manifest
        .capabilities
        .difference(approved_capabilities)
        .cloned()
        .collect();
    if !expansion.is_empty() {
        return Err(InstallError::CapabilityApprovalRequired(expansion));
    }
    fs::create_dir_all(staging_root)?;
    let stage = Builder::new()
        .prefix("artifact-")
        .tempdir_in(staging_root)?;
    extract_and_verify(archive_bytes, signed, stage.path(), limits)?;
    sync_tree(stage.path())?;
    let staging_path = stage.keep();
    Ok(VerifiedArtifact {
        manifest: signed.manifest.clone(),
        staging_path,
    })
}

fn extract_and_verify(
    archive_bytes: &[u8],
    signed: &SignedArtifactManifest,
    stage: &Path,
    limits: InstallLimits,
) -> Result<(), InstallError> {
    let mut declared = BTreeMap::new();
    let mut normalized = BTreeSet::new();
    for file in &signed.manifest.files {
        let path = safe_relative(&file.path)?;
        if !normalized.insert(normalized_path(&path)) {
            return Err(InstallError::PathCollision(file.path.clone()));
        }
        if declared.insert(path, file).is_some() {
            return Err(InstallError::PathCollision(file.path.clone()));
        }
    }
    let entrypoint = safe_relative(&signed.manifest.entrypoint)?;
    if !declared.contains_key(&entrypoint) {
        return Err(InstallError::MissingEntrypoint);
    }

    let decoder = GzDecoder::new(archive_bytes);
    let mut archive = tar::Archive::new(decoder);
    let mut seen = BTreeSet::new();
    let mut total = 0_u64;
    for (index, item) in archive.entries()?.enumerate() {
        if index >= limits.max_entries {
            return Err(InstallError::EntryLimit);
        }
        let mut entry = item?;
        let path = safe_relative(&entry.path()?.to_string_lossy())?;
        let key = normalized_path(&path);
        if !seen.insert(key) {
            return Err(InstallError::PathCollision(path.display().to_string()));
        }
        let declared_file = declared
            .get(&path)
            .ok_or_else(|| InstallError::UndeclaredPath(path.display().to_string()))?;
        let target = stage.join(&path);
        match entry.header().entry_type() {
            EntryType::Regular => {
                if declared_file.kind != FileKind::Regular || declared_file.link_target.is_some() {
                    return Err(InstallError::FileKind(path.display().to_string()));
                }
                let size = entry.size();
                total = total.checked_add(size).ok_or(InstallError::UnpackedLimit)?;
                if total > limits.max_unpacked_bytes || size != declared_file.size {
                    return Err(InstallError::UnpackedLimit);
                }
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)?;
                }
                let mut output = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&target)?;
                let mut hasher = Sha256::new();
                let mut remaining = size;
                let mut buffer = [0_u8; 16 * 1024];
                while remaining > 0 {
                    let read_limit = buffer.len().min(remaining as usize);
                    let count = entry.read(&mut buffer[..read_limit])?;
                    if count == 0 {
                        return Err(InstallError::Truncated(path.display().to_string()));
                    }
                    output.write_all(&buffer[..count])?;
                    hasher.update(&buffer[..count]);
                    remaining -= count as u64;
                }
                output.sync_all()?;
                if hex::encode(hasher.finalize()) != declared_file.sha256 {
                    return Err(InstallError::DigestMismatch(path.display().to_string()));
                }
                set_executable(&target, declared_file.executable)?;
            }
            EntryType::Symlink => {
                if declared_file.kind != FileKind::Symlink || declared_file.executable {
                    return Err(InstallError::FileKind(path.display().to_string()));
                }
                let link = entry
                    .link_name()?
                    .ok_or_else(|| InstallError::UnsafeLink(path.display().to_string()))?;
                let link_text = link.to_string_lossy().to_string();
                if declared_file.link_target.as_deref() != Some(link_text.as_str())
                    || sha256_hex(link_text.as_bytes()) != declared_file.sha256
                    || link_text.len() as u64 != declared_file.size
                {
                    return Err(InstallError::DigestMismatch(path.display().to_string()));
                }
                validate_link(&path, &link)?;
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)?;
                }
                create_symlink(&link, &target)?;
            }
            _ => return Err(InstallError::UnsupportedEntry(path.display().to_string())),
        }
    }
    if seen.len() != declared.len() {
        return Err(InstallError::MissingDeclaredFile);
    }
    Ok(())
}

fn safe_relative(value: &str) -> Result<PathBuf, InstallError> {
    let path = Path::new(value);
    if value.is_empty() || path.is_absolute() {
        return Err(InstallError::UnsafePath(value.to_owned()));
    }
    let mut clean = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(value) if value != "." && !value.is_empty() => clean.push(value),
            _ => return Err(InstallError::UnsafePath(value.to_owned())),
        }
    }
    Ok(clean)
}

fn normalized_path(path: &Path) -> String {
    path.to_string_lossy()
        .replace('\\', "/")
        .nfc()
        .flat_map(char::to_lowercase)
        .collect()
}

fn validate_link(path: &Path, link: &Path) -> Result<(), InstallError> {
    if link.is_absolute() {
        return Err(InstallError::UnsafeLink(path.display().to_string()));
    }
    let mut depth = path.parent().map(|p| p.components().count()).unwrap_or(0);
    for component in link.components() {
        match component {
            Component::Normal(_) => depth += 1,
            Component::ParentDir if depth > 0 => depth -= 1,
            Component::CurDir => {}
            _ => return Err(InstallError::UnsafeLink(path.display().to_string())),
        }
    }
    Ok(())
}

#[cfg(unix)]
fn create_symlink(link: &Path, target: &Path) -> io::Result<()> {
    std::os::unix::fs::symlink(link, target)
}

#[cfg(windows)]
fn create_symlink(link: &Path, target: &Path) -> io::Result<()> {
    std::os::windows::fs::symlink_file(link, target)
}

#[cfg(unix)]
fn set_executable(path: &Path, executable: bool) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(
        path,
        fs::Permissions::from_mode(if executable { 0o700 } else { 0o600 }),
    )
}

#[cfg(windows)]
fn set_executable(_path: &Path, _executable: bool) -> io::Result<()> {
    Ok(())
}

fn sync_tree(root: &Path) -> io::Result<()> {
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        if path.is_dir() {
            sync_tree(&path)?;
        } else if path.is_file() {
            File::open(path)?.sync_all()?;
        }
    }
    File::open(root)?.sync_all()
}

#[derive(Debug, Error)]
pub enum InstallError {
    #[error("archive exceeds the compressed size limit")]
    ArchiveLimit,
    #[error("new artifact capabilities require fresh approval: {0:?}")]
    CapabilityApprovalRequired(Vec<ArtifactCapability>),
    #[error("artifact digest mismatch for {0}")]
    DigestMismatch(String),
    #[error("archive contains too many entries")]
    EntryLimit,
    #[error("archive entry kind does not match manifest for {0}")]
    FileKind(String),
    #[error("artifact manifest entrypoint is absent")]
    MissingEntrypoint,
    #[error("archive omitted a declared file")]
    MissingDeclaredFile,
    #[error("normalized archive path collides: {0}")]
    PathCollision(String),
    #[error("artifact platform is incompatible")]
    PlatformMismatch,
    #[error("archive entry is truncated: {0}")]
    Truncated(String),
    #[error("archive contains undeclared path {0}")]
    UndeclaredPath(String),
    #[error("archive exceeds the unpacked size limit")]
    UnpackedLimit,
    #[error("archive contains unsupported entry {0}")]
    UnsupportedEntry(String),
    #[error("archive link escapes staging: {0}")]
    UnsafeLink(String),
    #[error("archive path is unsafe: {0}")]
    UnsafePath(String),
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error(transparent)]
    Verification(#[from] crate::manifest::VerificationError),
}
