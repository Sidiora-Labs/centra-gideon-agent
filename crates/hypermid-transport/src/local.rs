use std::path::{Path, PathBuf};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use serde::{Deserialize, Serialize};

use crate::TransportError;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConnectionRecord {
    pub endpoint: String,
    pub protocol: String,
    pub credential_id: String,
    pub secret_b64: String,
    pub server_name: String,
    pub ca_certificate: PathBuf,
    pub client_certificate: PathBuf,
    pub client_private_key: PathBuf,
    pub expires_ms: u64,
}

impl ConnectionRecord {
    pub fn secret_bytes(&self) -> Result<Vec<u8>, TransportError> {
        let decoded = BASE64
            .decode(&self.secret_b64)
            .map_err(|_| TransportError::Authentication("connection secret is invalid"))?;
        if decoded.len() != 32 {
            return Err(TransportError::Authentication(
                "connection secret must be 32 bytes",
            ));
        }
        Ok(decoded)
    }

    pub fn load(path: &Path) -> Result<Self, TransportError> {
        ensure_owner_only(path)?;
        let bytes = std::fs::read(path)?;
        serde_json::from_slice(&bytes)
            .map_err(|_| TransportError::Authentication("connection record is invalid"))
    }

    pub fn store(&self, path: &Path) -> Result<(), TransportError> {
        let parent = path.parent().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "connection record needs a parent",
            )
        })?;
        std::fs::create_dir_all(parent)?;
        let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
        let bytes = serde_json::to_vec(self)
            .map_err(|_| TransportError::Authentication("connection record is invalid"))?;
        write_owner_only(&temporary, &bytes)?;
        std::fs::rename(&temporary, path)?;
        Ok(())
    }
}

pub enum LocalEndpoint {
    #[cfg(unix)]
    Unix(tokio::net::UnixListener),
    Loopback(tokio::net::TcpListener),
}

impl LocalEndpoint {
    #[cfg(unix)]
    pub async fn bind_unix(path: &Path) -> Result<Self, TransportError> {
        use std::os::unix::fs::PermissionsExt;

        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
            std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700))?;
        }
        let listener = tokio::net::UnixListener::bind(path)?;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
        Ok(Self::Unix(listener))
    }

    pub async fn bind_loopback() -> Result<Self, TransportError> {
        Ok(Self::Loopback(
            tokio::net::TcpListener::bind("127.0.0.1:0").await?,
        ))
    }
}

#[cfg(unix)]
fn ensure_owner_only(path: &Path) -> Result<(), TransportError> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() || metadata.permissions().mode() & 0o077 != 0 {
        return Err(TransportError::Authentication(
            "connection record is not an owner-only regular file",
        ));
    }
    if metadata.uid() != unsafe { libc::geteuid() } {
        return Err(TransportError::Authentication(
            "connection record has a different owner",
        ));
    }
    Ok(())
}

#[cfg(not(unix))]
fn ensure_owner_only(path: &Path) -> Result<(), TransportError> {
    if !std::fs::symlink_metadata(path)?.file_type().is_file() {
        return Err(TransportError::Authentication(
            "connection record is not a regular file",
        ));
    }
    Ok(())
}

#[cfg(unix)]
fn write_owner_only(path: &Path, bytes: &[u8]) -> Result<(), TransportError> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let mut file = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn write_owner_only(path: &Path, bytes: &[u8]) -> Result<(), TransportError> {
    std::fs::write(path, bytes)?;
    Ok(())
}
