use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

use hypermid_contracts::Digest;
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProcessIdentity {
    pub pid: u32,
    pub start_identity: String,
    pub executable_digest: Digest,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ContainmentStatus {
    Contained(PathBuf),
    IdentityOnly { reason: String },
}

#[derive(Clone, Debug, Default)]
pub struct ProcessContainment {
    cgroup_root: Option<PathBuf>,
}

#[derive(Debug, Error)]
pub enum ContainmentError {
    #[error("process {0} is no longer live")]
    ProcessGone(u32),
    #[error("process identity changed for pid {0}")]
    IdentityChanged(u32),
    #[error("process identity could not be read: {0}")]
    IdentityIo(#[from] std::io::Error),
    #[error("process termination was refused: {0}")]
    Termination(String),
}

impl ProcessContainment {
    pub fn new(cgroup_root: Option<PathBuf>) -> Self {
        Self { cgroup_root }
    }

    pub fn capture(
        &self,
        pid: u32,
        executable_digest: Digest,
    ) -> Result<ProcessIdentity, ContainmentError> {
        Ok(ProcessIdentity {
            pid,
            start_identity: process_start_identity(pid)?,
            executable_digest,
        })
    }

    pub fn attach(
        &self,
        module_id: &str,
        spawn_generation: u64,
        identity: &ProcessIdentity,
    ) -> ContainmentStatus {
        #[cfg(target_os = "linux")]
        {
            let Some(root) = &self.cgroup_root else {
                return ContainmentStatus::IdentityOnly {
                    reason: "delegated cgroup root is unavailable".into(),
                };
            };
            let directory = root.join(format!("{}-{}", safe_name(module_id), spawn_generation));
            let result = fs::create_dir_all(&directory)
                .and_then(|_| fs::write(directory.join("cgroup.procs"), identity.pid.to_string()));
            return match result {
                Ok(()) => ContainmentStatus::Contained(directory),
                Err(error) => ContainmentStatus::IdentityOnly {
                    reason: error.to_string(),
                },
            };
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (module_id, spawn_generation, identity);
            ContainmentStatus::IdentityOnly {
                reason: "native subtree containment is unavailable on this platform".into(),
            }
        }
    }

    pub fn identity_matches(&self, expected: &ProcessIdentity) -> Result<bool, ContainmentError> {
        match process_start_identity(expected.pid) {
            Ok(actual) => Ok(actual == expected.start_identity),
            Err(ContainmentError::ProcessGone(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    pub fn terminate_matching(
        &self,
        expected: &ProcessIdentity,
        force: bool,
    ) -> Result<bool, ContainmentError> {
        if !self.identity_matches(expected)? {
            return Ok(false);
        }
        #[cfg(unix)]
        {
            let signal = if force { "-KILL" } else { "-TERM" };
            let status = Command::new("kill")
                .arg(signal)
                .arg(expected.pid.to_string())
                .status()?;
            if !status.success() {
                return Err(ContainmentError::Termination(format!(
                    "kill exited with {status}"
                )));
            }
            Ok(true)
        }
        #[cfg(not(unix))]
        {
            let _ = force;
            Err(ContainmentError::Termination(
                "recovered process termination is unsupported on this platform".into(),
            ))
        }
    }
}

#[cfg(target_os = "linux")]
fn process_start_identity(pid: u32) -> Result<String, ContainmentError> {
    let path = PathBuf::from(format!("/proc/{pid}/stat"));
    let value = fs::read_to_string(path).map_err(|error| {
        if error.kind() == std::io::ErrorKind::NotFound {
            ContainmentError::ProcessGone(pid)
        } else {
            ContainmentError::IdentityIo(error)
        }
    })?;
    let end = value
        .rfind(')')
        .ok_or_else(|| ContainmentError::Termination("invalid proc stat".into()))?;
    let fields: Vec<_> = value[end + 1..].split_whitespace().collect();
    let start_time = fields
        .get(19)
        .ok_or_else(|| ContainmentError::Termination("proc stat has no start time".into()))?;
    Ok((*start_time).to_owned())
}

#[cfg(all(unix, not(target_os = "linux")))]
fn process_start_identity(pid: u32) -> Result<String, ContainmentError> {
    let output = Command::new("ps")
        .args(["-o", "lstart=", "-p", &pid.to_string()])
        .output()?;
    if !output.status.success() || output.stdout.is_empty() {
        return Err(ContainmentError::ProcessGone(pid));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

#[cfg(not(unix))]
fn process_start_identity(pid: u32) -> Result<String, ContainmentError> {
    Ok(format!("windows-process-{pid}"))
}

fn safe_name(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .collect()
}

pub fn path_is_owned_child(path: &Path, root: &Path) -> bool {
    path.starts_with(root)
}
