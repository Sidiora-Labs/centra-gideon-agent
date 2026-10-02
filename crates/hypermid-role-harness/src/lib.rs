use async_trait::async_trait;
use hypermid_contracts::{Error, Id, Scope, Trace};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::BTreeSet,
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

pub const ROLE_PROTOCOL: &str = "hypermid.v1";

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Stability {
    Alpha,
    Beta,
    Stable,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RoleMajor {
    pub version: String,
    pub ops: BTreeSet<String>,
    pub stability: Stability,
}

impl RoleMajor {
    pub fn new(
        version: impl Into<String>,
        ops: impl IntoIterator<Item = impl Into<String>>,
        stability: Stability,
    ) -> Result<Self, HarnessError> {
        let version = version.into();
        if !valid_role_version(&version) {
            return Err(HarnessError::InvalidDescriptor("invalid role version"));
        }
        let ops: BTreeSet<String> = ops.into_iter().map(Into::into).collect();
        if ops.is_empty() || ops.iter().any(|op| op.is_empty() || op.len() > 64) {
            return Err(HarnessError::InvalidDescriptor("invalid operation set"));
        }
        Ok(Self {
            version,
            ops,
            stability,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RoleDescriptor {
    pub implementation_version: String,
    pub majors: Vec<RoleMajor>,
}

impl RoleDescriptor {
    pub fn new(
        implementation_version: impl Into<String>,
        majors: Vec<RoleMajor>,
    ) -> Result<Self, HarnessError> {
        let implementation_version = implementation_version.into();
        if implementation_version.is_empty()
            || implementation_version.len() > 128
            || majors.is_empty()
        {
            return Err(HarnessError::InvalidDescriptor("empty role descriptor"));
        }
        let mut versions = BTreeSet::new();
        if !majors.iter().all(|major| versions.insert(&major.version)) {
            return Err(HarnessError::InvalidDescriptor("duplicate role major"));
        }
        Ok(Self {
            implementation_version,
            majors,
        })
    }

    pub fn require(&self, version: &str, operation: &str) -> Result<(), HarnessError> {
        let major = self
            .majors
            .iter()
            .find(|major| major.version == version)
            .ok_or(HarnessError::UnsupportedVersion)?;
        if !major.ops.contains(operation) {
            return Err(HarnessError::UnsupportedOperation);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RequestEnvelope {
    pub protocol: String,
    pub role_version: String,
    pub method: String,
    pub params: Value,
    pub trace: Trace,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<Scope>,
}

impl RequestEnvelope {
    pub fn new(
        role_version: impl Into<String>,
        method: impl Into<String>,
        params: Value,
        trace: Trace,
        scope: Option<Scope>,
    ) -> Result<Self, HarnessError> {
        let role_version = role_version.into();
        let method = method.into();
        if !valid_role_version(&role_version) || method.is_empty() || method.len() > 64 {
            return Err(HarnessError::InvalidEnvelope);
        }
        Ok(Self {
            protocol: ROLE_PROTOCOL.to_owned(),
            role_version,
            method,
            params,
            trace,
            scope,
        })
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResponseEnvelope {
    pub request_id: Id,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Error>,
}

impl ResponseEnvelope {
    pub fn success(request_id: Id, result: Value) -> Self {
        Self {
            request_id,
            result: Some(result),
            error: None,
        }
    }

    pub fn failure(request_id: Id, error: Error) -> Self {
        Self {
            request_id,
            result: None,
            error: Some(error),
        }
    }

    pub fn validate(&self) -> Result<(), HarnessError> {
        if self.result.is_some() == self.error.is_some() {
            return Err(HarnessError::InvalidEnvelope);
        }
        Ok(())
    }
}

#[async_trait]
pub trait ProviderRoute: Send + Sync {
    async fn request(&self, request: RequestEnvelope) -> Result<ResponseEnvelope, HarnessError>;
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RoleMessage {
    pub message_id: Id,
    pub ordinal: u64,
    pub role: MessageRole,
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub original: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool: Option<ToolAttribution>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ToolAttribution {
    pub provider_id: Id,
    pub call_key: String,
    pub schema_pin: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum KillMechanism {
    Terminate,
    Kill,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DurablePoint {
    pub name: String,
    pub allowed_kills: BTreeSet<KillMechanism>,
}

impl DurablePoint {
    pub fn new(
        name: impl Into<String>,
        allowed_kills: impl IntoIterator<Item = KillMechanism>,
    ) -> Result<Self, HarnessError> {
        let name = name.into();
        let allowed_kills = allowed_kills.into_iter().collect::<BTreeSet<_>>();
        if !valid_point_name(&name) || allowed_kills.is_empty() {
            return Err(HarnessError::InvalidDurablePoint);
        }
        Ok(Self {
            name,
            allowed_kills,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct KillRecord {
    pub point: String,
    pub mechanism: KillMechanism,
    pub state_root: PathBuf,
    pub pid: u32,
}

#[derive(Debug, Default)]
pub struct CrashDriver {
    ledger: Vec<KillRecord>,
    used_roots: BTreeSet<PathBuf>,
    used_points: BTreeSet<String>,
}

impl CrashDriver {
    pub fn cut(
        &mut self,
        process: &mut LiveProcessHarness,
        point: &DurablePoint,
        mechanism: KillMechanism,
    ) -> Result<(), HarnessError> {
        if !point.allowed_kills.contains(&mechanism) {
            return Err(HarnessError::KillNotAllowed);
        }
        if self.used_points.contains(&point.name) || self.used_roots.contains(&process.state_root) {
            return Err(HarnessError::CrashCutReused);
        }
        let child = process
            .child
            .as_mut()
            .ok_or(HarnessError::ProcessNotRunning)?;
        let pid = child.id();
        child.kill()?;
        let status = child.wait()?;
        if status.success() {
            return Err(HarnessError::ProcessNotKilled);
        }
        self.used_points.insert(point.name.clone());
        self.used_roots.insert(process.state_root.clone());
        self.ledger.push(KillRecord {
            point: point.name.clone(),
            mechanism,
            state_root: process.state_root.clone(),
            pid,
        });
        process.child = None;
        process.stdin = None;
        process.stdout = None;
        Ok(())
    }

    pub fn ledger(&self) -> &[KillRecord] {
        &self.ledger
    }
    pub fn exercised_real_process(&self) -> bool {
        !self.ledger.is_empty()
    }
}

#[derive(Debug)]
pub struct LiveProcessHarness {
    program: PathBuf,
    args: Vec<String>,
    state_root: PathBuf,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<ChildStdout>>,
}

impl LiveProcessHarness {
    pub fn new(
        program: impl Into<PathBuf>,
        args: Vec<String>,
        state_root: impl Into<PathBuf>,
    ) -> Result<Self, HarnessError> {
        let state_root = state_root.into();
        if state_root.exists() || !state_root.is_absolute() {
            return Err(HarnessError::StateRootNotFresh);
        }
        fs::create_dir_all(state_root.join("points"))?;
        Ok(Self {
            program: program.into(),
            args,
            state_root,
            child: None,
            stdin: None,
            stdout: None,
        })
    }

    pub fn state_root(&self) -> &Path {
        &self.state_root
    }

    pub fn start(&mut self) -> Result<u32, HarnessError> {
        if self.child.is_some() {
            return Err(HarnessError::ProcessAlreadyRunning);
        }
        let mut child = Command::new(&self.program)
            .args(&self.args)
            .env("HYPERMID_ROLE_STATE_ROOT", &self.state_root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        let pid = child.id();
        self.stdin = child.stdin.take();
        self.stdout = child.stdout.take().map(BufReader::new);
        self.child = Some(child);
        Ok(pid)
    }

    pub fn wait_for_point(
        &self,
        point: &DurablePoint,
        timeout: Duration,
    ) -> Result<(), HarnessError> {
        let marker = self.state_root.join("points").join(&point.name);
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if marker.is_file() {
                return Ok(());
            }
            if self.child.as_ref().is_none_or(|child| child.id() == 0) {
                return Err(HarnessError::ProcessNotRunning);
            }
            thread::sleep(Duration::from_millis(10));
        }
        Err(HarnessError::DurablePointTimeout)
    }

    pub fn restart(&mut self) -> Result<u32, HarnessError> {
        self.start()
    }

    pub fn request(&mut self, request: &RequestEnvelope) -> Result<ResponseEnvelope, HarnessError> {
        let stdin = self.stdin.as_mut().ok_or(HarnessError::ProcessNotRunning)?;
        serde_json::to_writer(&mut *stdin, request)?;
        stdin.write_all(b"\n")?;
        stdin.flush()?;
        let stdout = self
            .stdout
            .as_mut()
            .ok_or(HarnessError::ProcessNotRunning)?;
        let mut line = String::new();
        if stdout.read_line(&mut line)? == 0 {
            return Err(HarnessError::RouteClosed);
        }
        if line.len() > 8 * 1024 * 1024 {
            return Err(HarnessError::RouteResponseTooLarge);
        }
        let response: ResponseEnvelope = serde_json::from_str(&line)?;
        response.validate()?;
        if response.request_id != request.trace.request_id {
            return Err(HarnessError::MismatchedRequestId);
        }
        Ok(response)
    }
}

impl Drop for LiveProcessHarness {
    fn drop(&mut self) {
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[derive(Debug)]
pub struct ProcessRoute {
    inner: Mutex<LiveProcessHarness>,
}

impl ProcessRoute {
    pub fn new(mut harness: LiveProcessHarness) -> Result<Self, HarnessError> {
        harness.start()?;
        Ok(Self {
            inner: Mutex::new(harness),
        })
    }

    pub fn with_harness<T>(
        &self,
        operation: impl FnOnce(&mut LiveProcessHarness) -> Result<T, HarnessError>,
    ) -> Result<T, HarnessError> {
        let mut harness = self.inner.lock().map_err(|_| HarnessError::RoutePoisoned)?;
        operation(&mut harness)
    }
}

#[async_trait]
impl ProviderRoute for ProcessRoute {
    async fn request(&self, request: RequestEnvelope) -> Result<ResponseEnvelope, HarnessError> {
        self.with_harness(|harness| harness.request(&request))
    }
}

fn valid_role_version(value: &str) -> bool {
    let Some((name, major)) = value.rsplit_once("/v") else {
        return false;
    };
    name.starts_with("hypermid.")
        && name[9..]
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b".-".contains(&b))
        && !major.is_empty()
        && major.bytes().all(|b| b.is_ascii_digit())
        && !major.starts_with('0')
}

fn valid_point_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

#[derive(Debug, thiserror::Error)]
pub enum HarnessError {
    #[error("invalid role descriptor: {0}")]
    InvalidDescriptor(&'static str),
    #[error("unsupported role version")]
    UnsupportedVersion,
    #[error("unsupported role operation")]
    UnsupportedOperation,
    #[error("invalid role envelope")]
    InvalidEnvelope,
    #[error("invalid durable point")]
    InvalidDurablePoint,
    #[error("kill mechanism is not allowed at the durable point")]
    KillNotAllowed,
    #[error("crash cut reused a point or state root")]
    CrashCutReused,
    #[error("state root must be a fresh absolute path")]
    StateRootNotFresh,
    #[error("role process is already running")]
    ProcessAlreadyRunning,
    #[error("role process is not running")]
    ProcessNotRunning,
    #[error("role process exited cleanly instead of being killed")]
    ProcessNotKilled,
    #[error("durable point was not reached before the deadline")]
    DurablePointTimeout,
    #[error("role process closed its response route")]
    RouteClosed,
    #[error("role process response exceeded eight MiB")]
    RouteResponseTooLarge,
    #[error("role process returned a different request Id")]
    MismatchedRequestId,
    #[error("role process route lock is poisoned")]
    RoutePoisoned,
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crash_driver_requires_a_real_process_and_unique_root() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("role-state");
        let mut harness = LiveProcessHarness::new("/bin/sh", vec!["-c".into(), "mkdir -p \"$HYPERMID_ROLE_STATE_ROOT/points\"; touch \"$HYPERMID_ROLE_STATE_ROOT/points/prepared\"; exec sleep 30".into()], &root).unwrap();
        harness.start().unwrap();
        let point = DurablePoint::new("prepared", [KillMechanism::Kill]).unwrap();
        harness
            .wait_for_point(&point, Duration::from_secs(2))
            .unwrap();
        let mut driver = CrashDriver::default();
        driver
            .cut(&mut harness, &point, KillMechanism::Kill)
            .unwrap();
        assert!(driver.exercised_real_process());
        assert_eq!(driver.ledger()[0].state_root, root);
    }
}
