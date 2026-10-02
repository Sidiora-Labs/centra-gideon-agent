use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::{OsStr, OsString},
    os::unix::{ffi::OsStrExt, process::CommandExt},
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use sha2::{Digest as _, Sha256};
use tokio::{
    io::{AsyncRead, AsyncWrite},
    process::{Child, ChildStderr, ChildStdin, ChildStdout, Command},
    time::{timeout, Instant},
};

const BWRAP_PATH: &str = "/usr/bin/bwrap";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FilesystemAccess {
    ReadOnly,
    ReadWrite,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FilesystemGrant {
    pub host_path: PathBuf,
    pub access: FilesystemAccess,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NetworkGrant {
    Denied,
    Inherited,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ResourceCeilings {
    pub address_space_bytes: u64,
    pub cpu_seconds: u64,
    pub file_bytes: u64,
    pub open_files: u64,
    pub processes: u64,
}

impl Default for ResourceCeilings {
    fn default() -> Self {
        Self {
            address_space_bytes: 512 * 1024 * 1024,
            cpu_seconds: 60,
            file_bytes: 16 * 1024 * 1024,
            open_files: 64,
            processes: 32,
        }
    }
}

#[derive(Clone, Debug)]
pub struct SandboxLaunch {
    pub executable: PathBuf,
    pub executable_sha256: String,
    pub arguments: Vec<OsString>,
    pub environment: BTreeMap<OsString, OsString>,
    pub working_directory: PathBuf,
    pub filesystem: Vec<FilesystemGrant>,
    pub network: NetworkGrant,
    pub ceilings: ResourceCeilings,
    pub deadline: Instant,
}

#[derive(Debug, thiserror::Error)]
pub enum SandboxError {
    #[error("sandbox policy is invalid: {0}")]
    InvalidPolicy(&'static str),
    #[error("sandbox path is unavailable")]
    PathUnavailable,
    #[error("sandbox path escapes an authorized filesystem grant")]
    PathEscape,
    #[error("executable digest does not match the declared digest")]
    ExecutableDigestMismatch,
    #[error("sandbox launch deadline has expired")]
    DeadlineExpired,
    #[error("bubblewrap sandbox is unavailable")]
    BackendUnavailable,
    #[error("sandbox I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("sandbox child has no piped {0}")]
    MissingPipe(&'static str),
    #[error("sandbox child termination timed out")]
    TerminationTimedOut,
}

struct ValidatedLaunch {
    executable: PathBuf,
    working_directory: PathBuf,
    filesystem: Vec<FilesystemGrant>,
}

pub struct SandboxedChild {
    child: Child,
    deadline: Instant,
}

impl SandboxedChild {
    pub fn id(&self) -> Option<u32> {
        self.child.id()
    }

    pub fn deadline(&self) -> Instant {
        self.deadline
    }

    pub fn take_stdin(&mut self) -> Result<ChildStdin, SandboxError> {
        self.child
            .stdin
            .take()
            .ok_or(SandboxError::MissingPipe("stdin"))
    }

    pub fn take_stdout(&mut self) -> Result<ChildStdout, SandboxError> {
        self.child
            .stdout
            .take()
            .ok_or(SandboxError::MissingPipe("stdout"))
    }

    pub fn take_stderr(&mut self) -> Result<ChildStderr, SandboxError> {
        self.child
            .stderr
            .take()
            .ok_or(SandboxError::MissingPipe("stderr"))
    }

    pub async fn wait(&mut self) -> Result<std::process::ExitStatus, SandboxError> {
        let remaining = self.deadline.saturating_duration_since(Instant::now());
        timeout(remaining, self.child.wait())
            .await
            .map_err(|_| SandboxError::DeadlineExpired)?
            .map_err(Into::into)
    }

    pub async fn terminate_and_reap(
        &mut self,
        graceful: Duration,
    ) -> Result<std::process::ExitStatus, SandboxError> {
        if let Some(status) = self.child.try_wait()? {
            return Ok(status);
        }
        let root = self.child.id().ok_or(SandboxError::TerminationTimedOut)?;
        let mut tracked = process_tree(root);
        signal_identities(&tracked, libc::SIGTERM);
        let waited = timeout(graceful, self.child.wait()).await;
        tracked.extend(process_tree(root));
        signal_identities(&tracked, libc::SIGKILL);
        let status = match waited {
            Ok(status) => status?,
            Err(_) => timeout(Duration::from_secs(5), self.child.wait())
                .await
                .map_err(|_| SandboxError::TerminationTimedOut)??,
        };
        let deadline = Instant::now() + Duration::from_secs(5);
        while tracked.iter().any(ProcessIdentity::is_live) {
            signal_identities(&tracked, libc::SIGKILL);
            if Instant::now() >= deadline {
                return Err(SandboxError::TerminationTimedOut);
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        Ok(status)
    }
}

pub async fn spawn(launch: SandboxLaunch) -> Result<SandboxedChild, SandboxError> {
    if launch.deadline <= Instant::now() {
        return Err(SandboxError::DeadlineExpired);
    }
    let validated = validate(&launch).await?;
    if !Path::new(BWRAP_PATH).is_file() {
        return Err(SandboxError::BackendUnavailable);
    }
    let mut command = Command::new(BWRAP_PATH);
    command
        .kill_on_drop(true)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env_clear()
        .arg("--die-with-parent")
        .arg("--unshare-user")
        .arg("--unshare-ipc")
        .arg("--unshare-uts")
        .arg("--unshare-cgroup")
        .arg("--dir")
        .arg("/proc")
        .arg("--dev")
        .arg("/dev")
        .arg("--tmpfs")
        .arg("/tmp")
        .arg("--ro-bind")
        .arg("/usr")
        .arg("/usr");
    for system_path in ["/lib", "/lib64"] {
        if Path::new(system_path).exists() {
            command.arg("--ro-bind").arg(system_path).arg(system_path);
        }
    }
    if launch.network == NetworkGrant::Denied {
        command.arg("--unshare-net");
    }
    for (index, grant) in validated.filesystem.iter().enumerate() {
        let target = PathBuf::from(format!("/run/hypermid/grant-{index}"));
        command.arg("--dir").arg(&target);
        match grant.access {
            FilesystemAccess::ReadOnly => command.arg("--ro-bind"),
            FilesystemAccess::ReadWrite => command.arg("--bind"),
        };
        command.arg(&grant.host_path).arg(&target);
    }
    command
        .arg("--ro-bind")
        .arg(&validated.executable)
        .arg("/run/hypermid/executable");
    let working_target = mapped_path(&validated.working_directory, &validated.filesystem)?;
    command.arg("--chdir").arg(working_target);
    for (name, value) in &launch.environment {
        validate_environment_name(name)?;
        command.arg("--setenv").arg(name).arg(value);
    }
    command.arg("--").arg("/run/hypermid/executable");
    command.args(&launch.arguments);

    let ceilings = launch.ceilings;
    unsafe {
        command.as_std_mut().pre_exec(move || {
            set_limit(libc::RLIMIT_AS, ceilings.address_space_bytes)?;
            set_limit(libc::RLIMIT_CPU, ceilings.cpu_seconds)?;
            set_limit(libc::RLIMIT_FSIZE, ceilings.file_bytes)?;
            set_limit(libc::RLIMIT_NOFILE, ceilings.open_files)?;
            set_limit(libc::RLIMIT_NPROC, ceilings.processes)?;
            if libc::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let child = command.spawn()?;
    Ok(SandboxedChild {
        child,
        deadline: launch.deadline,
    })
}

async fn validate(launch: &SandboxLaunch) -> Result<ValidatedLaunch, SandboxError> {
    validate_digest(&launch.executable_sha256)?;
    validate_ceilings(launch.ceilings)?;
    if launch.environment.len() > 128
        || launch.arguments.len() > 256
        || launch.filesystem.len() > 64
    {
        return Err(SandboxError::InvalidPolicy(
            "launch collections exceed their limits",
        ));
    }
    let executable = tokio::fs::canonicalize(&launch.executable)
        .await
        .map_err(|_| SandboxError::PathUnavailable)?;
    if !tokio::fs::metadata(&executable).await?.is_file() {
        return Err(SandboxError::InvalidPolicy(
            "executable is not a regular file",
        ));
    }
    let actual = digest_file(&executable).await?;
    if actual != launch.executable_sha256 {
        return Err(SandboxError::ExecutableDigestMismatch);
    }
    let working_directory = tokio::fs::canonicalize(&launch.working_directory)
        .await
        .map_err(|_| SandboxError::PathUnavailable)?;
    if !tokio::fs::metadata(&working_directory).await?.is_dir() {
        return Err(SandboxError::InvalidPolicy(
            "working directory is not a directory",
        ));
    }
    let mut filesystem = Vec::with_capacity(launch.filesystem.len());
    for grant in &launch.filesystem {
        let host_path = tokio::fs::canonicalize(&grant.host_path)
            .await
            .map_err(|_| SandboxError::PathUnavailable)?;
        if host_path == Path::new("/") || host_path == Path::new("/root") {
            return Err(SandboxError::InvalidPolicy(
                "broad filesystem grants are forbidden",
            ));
        }
        filesystem.push(FilesystemGrant {
            host_path,
            access: grant.access,
        });
    }
    mapped_path(&working_directory, &filesystem)?;
    Ok(ValidatedLaunch {
        executable,
        working_directory,
        filesystem,
    })
}

fn mapped_path(path: &Path, grants: &[FilesystemGrant]) -> Result<PathBuf, SandboxError> {
    grants
        .iter()
        .enumerate()
        .filter_map(|(index, grant)| {
            path.strip_prefix(&grant.host_path).ok().map(|relative| {
                PathBuf::from(format!("/run/hypermid/grant-{index}")).join(relative)
            })
        })
        .min_by_key(|candidate| candidate.as_os_str().len())
        .ok_or(SandboxError::PathEscape)
}

async fn digest_file(path: &Path) -> Result<String, SandboxError> {
    use tokio::io::AsyncReadExt;
    let mut file = tokio::fs::File::open(path).await?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).await?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn validate_digest(digest: &str) -> Result<(), SandboxError> {
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(SandboxError::InvalidPolicy("executable digest is invalid"));
    }
    Ok(())
}

fn validate_ceilings(ceilings: ResourceCeilings) -> Result<(), SandboxError> {
    if ceilings.address_space_bytes < 16 * 1024 * 1024
        || ceilings.cpu_seconds == 0
        || ceilings.file_bytes == 0
        || ceilings.open_files < 8
        || ceilings.processes == 0
    {
        return Err(SandboxError::InvalidPolicy("resource ceiling is invalid"));
    }
    Ok(())
}

fn validate_environment_name(name: &OsStr) -> Result<(), SandboxError> {
    let bytes = name.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 128
        || bytes[0].is_ascii_digit()
        || !bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || *byte == b'_')
    {
        return Err(SandboxError::InvalidPolicy("environment name is invalid"));
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct ProcessIdentity {
    pid: u32,
    start_time: u64,
}

impl ProcessIdentity {
    fn load(pid: u32) -> Option<(Self, u32)> {
        let value = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
        let (_, fields) = value.rsplit_once(") ")?;
        let fields = fields.split_ascii_whitespace().collect::<Vec<_>>();
        let parent = fields.get(1)?.parse().ok()?;
        let start_time = fields.get(19)?.parse().ok()?;
        Some((Self { pid, start_time }, parent))
    }

    fn is_live(&self) -> bool {
        Self::load(self.pid).is_some_and(|(current, _)| current.start_time == self.start_time)
    }
}

fn process_tree(root: u32) -> BTreeSet<ProcessIdentity> {
    let mut processes = Vec::new();
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return BTreeSet::new();
    };
    for entry in entries.flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|value| value.parse().ok())
        else {
            continue;
        };
        if let Some((identity, parent)) = ProcessIdentity::load(pid) {
            processes.push((identity, parent));
        }
    }
    let mut selected = BTreeSet::new();
    let mut parents = BTreeSet::from([root]);
    loop {
        let mut changed = false;
        for (identity, parent) in &processes {
            if parents.contains(parent) && selected.insert(*identity) {
                parents.insert(identity.pid);
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
    if let Some((identity, _)) = ProcessIdentity::load(root) {
        selected.insert(identity);
    }
    selected
}

fn signal_identities(processes: &BTreeSet<ProcessIdentity>, signal: libc::c_int) {
    for process in processes.iter().rev() {
        if process.is_live() {
            unsafe {
                libc::kill(process.pid as libc::pid_t, signal);
            }
        }
    }
}

fn set_limit(resource: libc::__rlimit_resource_t, value: u64) -> std::io::Result<()> {
    let limit = libc::rlimit {
        rlim_cur: value,
        rlim_max: value,
    };
    if unsafe { libc::setrlimit(resource, &limit) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

pub trait SandboxInput: AsyncWrite + Unpin + Send {}
impl<T: AsyncWrite + Unpin + Send> SandboxInput for T {}
pub trait SandboxOutput: AsyncRead + Unpin + Send {}
impl<T: AsyncRead + Unpin + Send> SandboxOutput for T {}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::BTreeMap, os::unix::fs::PermissionsExt};
    use tempfile::tempdir;
    use tokio::io::AsyncReadExt;

    #[tokio::test]
    async fn sandbox_enforces_filesystem_environment_and_executable_identity() {
        let root = tempdir().unwrap();
        let allowed = root.path().join("allowed");
        let forbidden = root.path().join("forbidden.txt");
        std::fs::create_dir(&allowed).unwrap();
        std::fs::write(allowed.join("allowed.txt"), b"allowed").unwrap();
        std::fs::write(&forbidden, b"secret").unwrap();
        std::fs::set_permissions(&allowed, std::fs::Permissions::from_mode(0o755)).unwrap();
        let executable = std::fs::canonicalize("/usr/bin/sh").unwrap();
        let digest = digest_file(&executable).await.unwrap();
        let script = format!(
            "test \"$HYPERMID_ALLOWED\" = present && test -z \"$HOME\" && test -r allowed.txt && test ! -e '{}' && printf contained",
            forbidden.display()
        );
        let mut child = spawn(SandboxLaunch {
            executable,
            executable_sha256: digest,
            arguments: vec![OsString::from("-c"), OsString::from(script)],
            environment: BTreeMap::from([(
                OsString::from("HYPERMID_ALLOWED"),
                OsString::from("present"),
            )]),
            working_directory: allowed.clone(),
            filesystem: vec![FilesystemGrant {
                host_path: allowed,
                access: FilesystemAccess::ReadOnly,
            }],
            network: NetworkGrant::Denied,
            ceilings: ResourceCeilings::default(),
            deadline: Instant::now() + Duration::from_secs(10),
        })
        .await
        .unwrap();
        let mut stdout = child.take_stdout().unwrap();
        let mut output = Vec::new();
        stdout.read_to_end(&mut output).await.unwrap();
        let status = child.wait().await.unwrap();
        assert!(status.success());
        assert_eq!(output, b"contained");

        let error = match spawn(SandboxLaunch {
            executable: PathBuf::from("/usr/bin/sh"),
            executable_sha256: "00".repeat(32),
            arguments: Vec::new(),
            environment: BTreeMap::new(),
            working_directory: root.path().to_path_buf(),
            filesystem: vec![FilesystemGrant {
                host_path: root.path().to_path_buf(),
                access: FilesystemAccess::ReadOnly,
            }],
            network: NetworkGrant::Denied,
            ceilings: ResourceCeilings::default(),
            deadline: Instant::now() + Duration::from_secs(10),
        })
        .await
        {
            Ok(_) => panic!("digest mismatch was accepted"),
            Err(error) => error,
        };
        assert!(matches!(error, SandboxError::ExecutableDigestMismatch));
    }
}
