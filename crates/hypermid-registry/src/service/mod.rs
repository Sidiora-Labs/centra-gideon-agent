pub mod linux;
pub mod macos;
pub mod windows;

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::Command;
use thiserror::Error;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ServicePlatform {
    Linux,
    Macos,
    Windows,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ServiceDefinition {
    pub platform: ServicePlatform,
    pub relative_path: PathBuf,
    pub contents: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceAction {
    Register,
    Reload,
    Start,
    Stop,
    Restart,
    Deregister,
}

#[derive(Clone, Debug)]
pub struct ServiceManager {
    pub platform: ServicePlatform,
}

impl ServiceManager {
    pub fn new(platform: ServicePlatform) -> Self {
        Self { platform }
    }

    pub fn run(
        &self,
        action: ServiceAction,
        definition_path: &std::path::Path,
    ) -> Result<(), ManagerError> {
        if action == ServiceAction::Restart {
            self.run(ServiceAction::Stop, definition_path)?;
            return self.run(ServiceAction::Start, definition_path);
        }
        let (program, arguments) = match self.platform {
            ServicePlatform::Linux => linux::command(action, definition_path),
            ServicePlatform::Macos => macos::command(action, definition_path),
            ServicePlatform::Windows => windows::command(action, definition_path),
        };
        run_exact(&program, &arguments)
    }

    pub fn apply_changed_definition(
        &self,
        definition_path: &std::path::Path,
    ) -> Result<(), ManagerError> {
        self.run(ServiceAction::Reload, definition_path)?;
        self.run(ServiceAction::Restart, definition_path)
    }
}

pub fn run_exact(program: &str, arguments: &[String]) -> Result<(), ManagerError> {
    let output = Command::new(program).args(arguments).output()?;
    if output.status.success() {
        return Ok(());
    }
    Err(ManagerError::Refused {
        status: output.status.code(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

#[derive(Debug, Error)]
pub enum ManagerError {
    #[error("service manager refused request ({status:?}): {stderr}")]
    Refused { status: Option<i32>, stderr: String },
    #[error(transparent)]
    Spawn(#[from] std::io::Error),
}
