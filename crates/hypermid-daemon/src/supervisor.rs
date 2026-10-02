use std::{
    collections::{BTreeMap, VecDeque},
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
};

use hypermid_contracts::Digest;
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::{
    child_journal::{ChildEvent, ChildJournal, ChildJournalError},
    containment::{ContainmentError, ContainmentStatus, ProcessContainment, ProcessIdentity},
    manifest::{OverlapPolicy, RestartMode, RestartPolicy},
    registry::{ModuleRegistrationState, Registry, RegistryError},
    router::{RouteError, Router},
};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SupervisorState {
    Disabled,
    Starting,
    Warming,
    Ready,
    Draining,
    Stopping,
    Backoff,
    Failed,
    Retired,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HealthState {
    Unknown,
    Healthy,
    Degraded,
    Failing,
}

#[derive(Clone, Debug)]
pub struct SupervisedModuleSpec {
    pub module_id: String,
    pub executable: PathBuf,
    pub arguments: Vec<String>,
    pub environment: BTreeMap<String, String>,
    pub artifact_digest: Digest,
    pub restart: RestartPolicy,
    pub overlap: OverlapPolicy,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ModuleSnapshot {
    pub module_id: String,
    pub state: SupervisorState,
    pub spawn_generation: u64,
    pub pid: Option<u32>,
    pub artifact_digest: Digest,
    pub ready: bool,
    pub restart_count: u32,
    pub next_retry_ms: Option<u64>,
    pub active_routes: usize,
    pub health: HealthState,
    pub last_terminal_reason: Option<String>,
    pub containment: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TerminalEvidence {
    pub module_id: String,
    pub spawn_generation: u64,
    pub exit_code: Option<i32>,
    pub reason: String,
    pub observed_ms: u64,
}

#[derive(Clone, Debug)]
pub struct SupervisorConfig {
    pub stderr_max_lines: usize,
    pub stderr_max_bytes: usize,
    pub terminal_capacity: usize,
    pub stop_grace_ms: u64,
    pub cgroup_root: Option<PathBuf>,
}

impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            stderr_max_lines: 256,
            stderr_max_bytes: 64 * 1024,
            terminal_capacity: 256,
            stop_grace_ms: 2_000,
            cgroup_root: None,
        }
    }
}

pub(crate) struct ManagedProcess {
    child: Child,
    identity: ProcessIdentity,
    containment: ContainmentStatus,
    stderr: Arc<Mutex<StderrRing>>,
    stop_deadline_ms: Option<u64>,
}

pub struct WarmCandidate {
    module_id: String,
    generation: u64,
    spec: SupervisedModuleSpec,
    process: Option<ManagedProcess>,
}

impl WarmCandidate {
    pub fn module_id(&self) -> &str {
        &self.module_id
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn is_running(&mut self) -> Result<bool, std::io::Error> {
        let Some(process) = self.process.as_mut() else {
            return Ok(false);
        };
        Ok(process.child.try_wait()?.is_none())
    }
}

struct ModuleSlot {
    spec: SupervisedModuleSpec,
    state: SupervisorState,
    spawn_generation: u64,
    process: Option<ManagedProcess>,
    restart_times: VecDeque<u64>,
    restart_count: u32,
    next_retry_ms: Option<u64>,
    drain_deadline_ms: Option<u64>,
    health: HealthState,
    last_terminal_reason: Option<String>,
    retained_stderr: Vec<String>,
    enabled: bool,
}

struct SupervisorInner {
    modules: BTreeMap<String, ModuleSlot>,
    terminals: VecDeque<TerminalEvidence>,
}

#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<Mutex<SupervisorInner>>,
    journal: ChildJournal,
    containment: ProcessContainment,
    router: Option<Router>,
    registry: Option<Registry>,
    config: SupervisorConfig,
}

#[derive(Debug, Error)]
pub enum SupervisorError {
    #[error("supervisor lock is poisoned")]
    Poisoned,
    #[error("unknown module {0}")]
    UnknownModule(String),
    #[error("module {0} is already running")]
    AlreadyRunning(String),
    #[error("module {0} is not warming")]
    NotWarming(String),
    #[error("module process spawn failed: {0}")]
    Spawn(#[from] std::io::Error),
    #[error(transparent)]
    Containment(#[from] ContainmentError),
    #[error(transparent)]
    Journal(#[from] ChildJournalError),
    #[error(transparent)]
    Registry(#[from] RegistryError),
    #[error(transparent)]
    Route(#[from] RouteError),
}

impl Supervisor {
    pub fn new(journal: ChildJournal, config: SupervisorConfig) -> Self {
        Self::with_fabric(journal, config, None, None)
    }

    pub fn with_fabric(
        journal: ChildJournal,
        config: SupervisorConfig,
        registry: Option<Registry>,
        router: Option<Router>,
    ) -> Self {
        Self {
            inner: Arc::new(Mutex::new(SupervisorInner {
                modules: BTreeMap::new(),
                terminals: VecDeque::with_capacity(config.terminal_capacity),
            })),
            journal,
            containment: ProcessContainment::new(config.cgroup_root.clone()),
            router,
            registry,
            config,
        }
    }

    pub fn configure(&self, spec: SupervisedModuleSpec) -> Result<(), SupervisorError> {
        let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        let module_id = spec.module_id.clone();
        if let Some(existing) = inner.modules.get_mut(&module_id) {
            if existing.process.is_some() {
                return Err(SupervisorError::AlreadyRunning(module_id));
            }
            existing.spec = spec;
            existing.state = SupervisorState::Disabled;
            existing.enabled = false;
            existing.next_retry_ms = None;
            return Ok(());
        }
        inner.modules.insert(
            module_id,
            ModuleSlot {
                spec,
                state: SupervisorState::Disabled,
                spawn_generation: 0,
                process: None,
                restart_times: VecDeque::new(),
                restart_count: 0,
                next_retry_ms: None,
                drain_deadline_ms: None,
                health: HealthState::Unknown,
                last_terminal_reason: None,
                retained_stderr: Vec::new(),
                enabled: false,
            },
        );
        Ok(())
    }

    pub fn set_enabled(
        &self,
        module_id: &str,
        enabled: bool,
        now_ms: u64,
    ) -> Result<(), SupervisorError> {
        {
            let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            let slot = inner
                .modules
                .get_mut(module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
            slot.enabled = enabled;
            if !enabled && slot.process.is_none() {
                slot.state = SupervisorState::Disabled;
            }
        }
        if enabled {
            self.spawn(module_id, now_ms)?;
        } else {
            self.begin_drain(module_id, now_ms)?;
        }
        Ok(())
    }

    pub fn spawn(&self, module_id: &str, now_ms: u64) -> Result<u64, SupervisorError> {
        let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        let slot = inner
            .modules
            .get_mut(module_id)
            .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
        if slot.process.is_some() {
            return Err(SupervisorError::AlreadyRunning(module_id.into()));
        }
        slot.enabled = true;
        slot.state = SupervisorState::Starting;
        slot.spawn_generation = slot.spawn_generation.saturating_add(1).max(1);
        let generation = slot.spawn_generation;
        let mut command = Command::new(&slot.spec.executable);
        command
            .args(&slot.spec.arguments)
            .env_clear()
            .envs(&slot.spec.environment)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        let mut child = command.spawn()?;
        let identity = match self
            .containment
            .capture(child.id(), slot.spec.artifact_digest)
        {
            Ok(identity) => identity,
            Err(error) => {
                let _ = child.kill();
                slot.state = SupervisorState::Failed;
                return Err(error.into());
            }
        };
        let containment = self.containment.attach(module_id, generation, &identity);
        let stderr = Arc::new(Mutex::new(StderrRing::new(
            self.config.stderr_max_lines,
            self.config.stderr_max_bytes,
        )));
        if let Some(pipe) = child.stderr.take() {
            collect_stderr(pipe, Arc::clone(&stderr));
        }
        self.journal.append(ChildEvent::Spawned {
            module_id: module_id.into(),
            spawn_generation: generation,
            identity: identity.clone(),
            observed_ms: now_ms,
        })?;
        slot.process = Some(ManagedProcess {
            child,
            identity,
            containment,
            stderr,
            stop_deadline_ms: None,
        });
        slot.state = SupervisorState::Warming;
        slot.health = HealthState::Unknown;
        slot.next_retry_ms = None;
        Ok(generation)
    }

    pub fn warm_candidate(
        &self,
        spec: SupervisedModuleSpec,
        now_ms: u64,
    ) -> Result<WarmCandidate, SupervisorError> {
        let generation = {
            let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            inner
                .modules
                .get(&spec.module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(spec.module_id.clone()))?
                .spawn_generation
                .saturating_add(1)
                .max(1)
        };
        let mut command = Command::new(&spec.executable);
        command
            .args(&spec.arguments)
            .env_clear()
            .envs(&spec.environment)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        let mut child = command.spawn()?;
        let identity = match self.containment.capture(child.id(), spec.artifact_digest) {
            Ok(identity) => identity,
            Err(error) => {
                let _ = child.kill();
                return Err(error.into());
            }
        };
        let containment = self
            .containment
            .attach(&spec.module_id, generation, &identity);
        let stderr = Arc::new(Mutex::new(StderrRing::new(
            self.config.stderr_max_lines,
            self.config.stderr_max_bytes,
        )));
        if let Some(pipe) = child.stderr.take() {
            collect_stderr(pipe, Arc::clone(&stderr));
        }
        self.journal.append(ChildEvent::Spawned {
            module_id: spec.module_id.clone(),
            spawn_generation: generation,
            identity: identity.clone(),
            observed_ms: now_ms,
        })?;
        Ok(WarmCandidate {
            module_id: spec.module_id.clone(),
            generation,
            spec,
            process: Some(ManagedProcess {
                child,
                identity,
                containment,
                stderr,
                stop_deadline_ms: None,
            }),
        })
    }

    pub fn discard_candidate(
        &self,
        mut candidate: WarmCandidate,
        now_ms: u64,
        reason: &str,
    ) -> Result<(), SupervisorError> {
        if let Some(mut process) = candidate.process.take() {
            if self.containment.identity_matches(&process.identity)? {
                process.child.kill()?;
                let _ = process.child.wait();
            }
            self.journal.append(ChildEvent::Terminal {
                module_id: candidate.module_id,
                spawn_generation: candidate.generation,
                exit_code: None,
                reason: reason.into(),
                observed_ms: now_ms,
            })?;
        }
        Ok(())
    }

    pub fn promote_candidate(
        &self,
        mut candidate: WarmCandidate,
        now_ms: u64,
    ) -> Result<(), SupervisorError> {
        let module_id = candidate.module_id.clone();
        let new_process = candidate
            .process
            .take()
            .ok_or_else(|| SupervisorError::NotWarming(module_id.clone()))?;
        let (old_generation, mut incumbent) = {
            let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            let slot = inner
                .modules
                .get_mut(&module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(module_id.clone()))?;
            if slot.spec.overlap != OverlapPolicy::Safe
                || candidate.generation <= slot.spawn_generation
            {
                return Err(SupervisorError::NotWarming(module_id));
            }
            let old_generation = slot.spawn_generation;
            let incumbent = slot.process.take();
            slot.spec = candidate.spec;
            slot.spawn_generation = candidate.generation;
            slot.process = Some(new_process);
            slot.state = SupervisorState::Ready;
            slot.health = HealthState::Healthy;
            slot.next_retry_ms = None;
            (old_generation, incumbent)
        };
        if let Some(registry) = &self.registry {
            let _ = registry.set_state(
                &module_id,
                old_generation,
                ModuleRegistrationState::Draining,
            );
        }
        if let Some(router) = &self.router {
            router.close_module_routes(&module_id, old_generation)?;
        }
        if let Some(process) = incumbent.as_mut() {
            if self.containment.identity_matches(&process.identity)? {
                process.child.kill()?;
                let _ = process.child.wait();
            }
            self.journal.append(ChildEvent::Retired {
                module_id,
                spawn_generation: old_generation,
                observed_ms: now_ms,
            })?;
        }
        Ok(())
    }

    pub fn mark_ready(
        &self,
        module_id: &str,
        spawn_generation: u64,
    ) -> Result<(), SupervisorError> {
        let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        let slot = inner
            .modules
            .get_mut(module_id)
            .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
        if slot.state != SupervisorState::Warming || slot.spawn_generation != spawn_generation {
            return Err(SupervisorError::NotWarming(module_id.into()));
        }
        slot.state = SupervisorState::Ready;
        slot.health = HealthState::Healthy;
        Ok(())
    }

    pub fn probe(&self, module_id: &str, now_ms: u64) -> Result<HealthState, SupervisorError> {
        self.poll(module_id, now_ms)?;
        let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        Ok(inner
            .modules
            .get(module_id)
            .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?
            .health)
    }

    pub fn poll(&self, module_id: &str, now_ms: u64) -> Result<bool, SupervisorError> {
        let terminal = {
            let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            let slot = inner
                .modules
                .get_mut(module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
            let Some(process) = slot.process.as_mut() else {
                return Ok(false);
            };
            match process.child.try_wait()? {
                None => return Ok(false),
                Some(status) => Some((status.code(), status.success(), slot.spawn_generation)),
            }
        };
        let Some((exit_code, success, generation)) = terminal else {
            return Ok(false);
        };
        self.record_terminal(module_id, generation, exit_code, success, now_ms)?;
        Ok(true)
    }

    pub fn begin_drain(&self, module_id: &str, now_ms: u64) -> Result<(), SupervisorError> {
        let (generation, deadline) = {
            let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            let slot = inner
                .modules
                .get_mut(module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
            if slot.process.is_none() {
                slot.state = if slot.enabled {
                    SupervisorState::Failed
                } else {
                    SupervisorState::Disabled
                };
                return Ok(());
            }
            slot.state = SupervisorState::Draining;
            let deadline = now_ms.saturating_add(slot.spec.restart.drain_timeout_ms);
            slot.drain_deadline_ms = Some(deadline);
            (slot.spawn_generation, deadline)
        };
        if let Some(registry) = &self.registry {
            registry.set_state(module_id, generation, ModuleRegistrationState::Draining)?;
        }
        if self.active_routes(module_id, generation)? == 0 || deadline <= now_ms {
            self.begin_stop(module_id, now_ms)?;
        }
        Ok(())
    }

    pub fn restart(&self, module_id: &str, now_ms: u64) -> Result<(), SupervisorError> {
        {
            let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            let slot = inner
                .modules
                .get_mut(module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
            slot.enabled = true;
        }
        self.begin_drain(module_id, now_ms)
    }

    pub fn tick(&self, now_ms: u64) -> Result<(), SupervisorError> {
        let modules = {
            let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            inner.modules.keys().cloned().collect::<Vec<_>>()
        };
        for module_id in modules {
            self.poll(&module_id, now_ms)?;
            let action = {
                let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
                let slot = &inner.modules[&module_id];
                match slot.state {
                    SupervisorState::Backoff
                        if slot
                            .next_retry_ms
                            .is_some_and(|deadline| now_ms >= deadline) =>
                    {
                        1
                    }
                    SupervisorState::Draining
                        if slot
                            .drain_deadline_ms
                            .is_some_and(|deadline| now_ms >= deadline)
                            || self.active_routes(&module_id, slot.spawn_generation)? == 0 =>
                    {
                        2
                    }
                    SupervisorState::Stopping
                        if slot
                            .process
                            .as_ref()
                            .and_then(|process| process.stop_deadline_ms)
                            .is_some_and(|deadline| now_ms >= deadline) =>
                    {
                        3
                    }
                    _ => 0,
                }
            };
            match action {
                1 => {
                    self.spawn(&module_id, now_ms)?;
                }
                2 => {
                    self.begin_stop(&module_id, now_ms)?;
                }
                3 => {
                    self.force_stop(&module_id)?;
                }
                _ => {}
            }
        }
        Ok(())
    }

    pub fn snapshot(&self) -> Result<Vec<ModuleSnapshot>, SupervisorError> {
        let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        let route_counts = self
            .router
            .as_ref()
            .map(Router::snapshot)
            .transpose()?
            .map(|snapshot| snapshot.routes)
            .unwrap_or_default();
        Ok(inner
            .modules
            .values()
            .map(|slot| ModuleSnapshot {
                module_id: slot.spec.module_id.clone(),
                state: slot.state,
                spawn_generation: slot.spawn_generation,
                pid: slot.process.as_ref().map(|process| process.identity.pid),
                artifact_digest: slot.spec.artifact_digest,
                ready: slot.state == SupervisorState::Ready,
                restart_count: slot.restart_count,
                next_retry_ms: slot.next_retry_ms,
                active_routes: route_counts
                    .iter()
                    .filter(|route| {
                        route.module_id == slot.spec.module_id
                            && route.spawn_generation == slot.spawn_generation
                    })
                    .count(),
                health: slot.health,
                last_terminal_reason: slot.last_terminal_reason.clone(),
                containment: slot
                    .process
                    .as_ref()
                    .map(|process| match &process.containment {
                        ContainmentStatus::Contained(path) => format!("cgroup:{}", path.display()),
                        ContainmentStatus::IdentityOnly { reason } => {
                            format!("identity_only:{reason}")
                        }
                    }),
            })
            .collect())
    }

    pub fn stderr(&self, module_id: &str) -> Result<Vec<String>, SupervisorError> {
        let (ring, retained) = {
            let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
            let slot = inner
                .modules
                .get(module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
            (
                slot.process
                    .as_ref()
                    .map(|process| Arc::clone(&process.stderr)),
                slot.retained_stderr.clone(),
            )
        };
        let Some(ring) = ring else {
            return Ok(retained);
        };
        let values = ring
            .lock()
            .map_err(|_| SupervisorError::Poisoned)?
            .lines
            .iter()
            .cloned()
            .collect();
        Ok(values)
    }

    pub fn terminals(&self) -> Result<Vec<TerminalEvidence>, SupervisorError> {
        let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        Ok(inner.terminals.iter().cloned().collect())
    }

    pub fn spawn_snapshot(
        &self,
        module_id: &str,
    ) -> Result<Option<ProcessIdentity>, SupervisorError> {
        let inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        Ok(inner
            .modules
            .get(module_id)
            .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?
            .process
            .as_ref()
            .map(|process| process.identity.clone()))
    }

    pub fn recover_orphans(&self, now_ms: u64) -> Result<Vec<ProcessIdentity>, SupervisorError> {
        let snapshot = self.journal.load()?;
        let mut terminated = Vec::new();
        for ((module_id, generation), identity) in snapshot.live {
            if self.containment.terminate_matching(&identity, true)? {
                terminated.push(identity);
            }
            self.journal.append(ChildEvent::Retired {
                module_id,
                spawn_generation: generation,
                observed_ms: now_ms,
            })?;
        }
        Ok(terminated)
    }

    fn begin_stop(&self, module_id: &str, now_ms: u64) -> Result<(), SupervisorError> {
        let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        let slot = inner
            .modules
            .get_mut(module_id)
            .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
        let Some(process) = slot.process.as_mut() else {
            return Ok(());
        };
        if self.containment.identity_matches(&process.identity)? {
            self.containment
                .terminate_matching(&process.identity, false)?;
        }
        process.stop_deadline_ms = Some(now_ms.saturating_add(self.config.stop_grace_ms));
        slot.state = SupervisorState::Stopping;
        Ok(())
    }

    fn force_stop(&self, module_id: &str) -> Result<(), SupervisorError> {
        let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        let slot = inner
            .modules
            .get_mut(module_id)
            .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
        if let Some(process) = slot.process.as_mut() {
            if self.containment.identity_matches(&process.identity)? {
                process.child.kill()?;
            }
        }
        Ok(())
    }

    fn record_terminal(
        &self,
        module_id: &str,
        generation: u64,
        exit_code: Option<i32>,
        success: bool,
        now_ms: u64,
    ) -> Result<(), SupervisorError> {
        let reason = if success { "exited" } else { "crashed" }.to_owned();
        self.journal.append(ChildEvent::Terminal {
            module_id: module_id.into(),
            spawn_generation: generation,
            exit_code,
            reason: reason.clone(),
            observed_ms: now_ms,
        })?;
        let evidence = TerminalEvidence {
            module_id: module_id.into(),
            spawn_generation: generation,
            exit_code,
            reason,
            observed_ms: now_ms,
        };
        let mut inner = self.inner.lock().map_err(|_| SupervisorError::Poisoned)?;
        {
            let slot = inner
                .modules
                .get_mut(module_id)
                .ok_or_else(|| SupervisorError::UnknownModule(module_id.into()))?;
            let was_stopping = slot.state == SupervisorState::Stopping;
            if let Some(process) = slot.process.take() {
                slot.retained_stderr = process
                    .stderr
                    .lock()
                    .map(|ring| ring.lines.iter().cloned().collect())
                    .unwrap_or_default();
            }
            slot.last_terminal_reason = Some(evidence.reason.clone());
            slot.health = HealthState::Failing;
            if !slot.enabled || was_stopping {
                slot.state = if slot.enabled {
                    SupervisorState::Backoff
                } else {
                    SupervisorState::Disabled
                };
                slot.next_retry_ms = slot.enabled.then_some(now_ms);
            } else {
                let should_restart = match slot.spec.restart.mode {
                    RestartMode::Never => false,
                    RestartMode::OnFailure => !success,
                    RestartMode::Always => true,
                };
                while slot.restart_times.front().is_some_and(|observed| {
                    now_ms.saturating_sub(*observed) > slot.spec.restart.window_ms
                }) {
                    slot.restart_times.pop_front();
                }
                if should_restart
                    && slot.restart_times.len() < slot.spec.restart.max_restarts as usize
                {
                    slot.restart_times.push_back(now_ms);
                    slot.restart_count = slot.restart_count.saturating_add(1);
                    let delay = restart_backoff(&slot.spec, slot.restart_count, generation);
                    slot.next_retry_ms = Some(now_ms.saturating_add(delay));
                    slot.state = SupervisorState::Backoff;
                } else {
                    slot.next_retry_ms = None;
                    slot.state = SupervisorState::Failed;
                }
            }
        }
        if inner.terminals.len() == self.config.terminal_capacity {
            inner.terminals.pop_front();
        }
        inner.terminals.push_back(evidence);
        Ok(())
    }

    fn active_routes(&self, module_id: &str, generation: u64) -> Result<usize, SupervisorError> {
        Ok(self
            .router
            .as_ref()
            .map(Router::snapshot)
            .transpose()?
            .map(|snapshot| {
                snapshot
                    .routes
                    .iter()
                    .filter(|route| {
                        route.module_id == module_id && route.spawn_generation == generation
                    })
                    .count()
            })
            .unwrap_or(0))
    }
}

fn restart_backoff(spec: &SupervisedModuleSpec, restart_count: u32, generation: u64) -> u64 {
    let exponent = restart_count.saturating_sub(1).min(62);
    let base = spec
        .restart
        .base_backoff_ms
        .saturating_mul(1_u64 << exponent)
        .min(spec.restart.max_backoff_ms);
    let digest = Digest::sha256(format!("{}:{generation}", spec.module_id));
    let jitter_ceiling = (base / 4).max(1);
    let jitter = u64::from(digest.as_bytes()[0]) % jitter_ceiling;
    base.saturating_add(jitter).min(spec.restart.max_backoff_ms)
}

struct StderrRing {
    max_lines: usize,
    max_bytes: usize,
    bytes: usize,
    lines: VecDeque<String>,
}

impl StderrRing {
    fn new(max_lines: usize, max_bytes: usize) -> Self {
        Self {
            max_lines: max_lines.max(1),
            max_bytes: max_bytes.max(1),
            bytes: 0,
            lines: VecDeque::new(),
        }
    }

    fn push(&mut self, mut line: String) {
        if line.len() > self.max_bytes {
            line.truncate(self.max_bytes);
            line.push_str(" [truncated]");
        }
        self.bytes = self.bytes.saturating_add(line.len());
        self.lines.push_back(line);
        let mut truncated = false;
        while self.lines.len() > self.max_lines || self.bytes > self.max_bytes {
            if let Some(removed) = self.lines.pop_front() {
                self.bytes = self.bytes.saturating_sub(removed.len());
                truncated = true;
            }
        }
        if truncated && self.lines.front().map(String::as_str) != Some("[earlier stderr truncated]")
        {
            self.lines.push_front("[earlier stderr truncated]".into());
        }
    }
}

fn collect_stderr(pipe: impl std::io::Read + Send + 'static, ring: Arc<Mutex<StderrRing>>) {
    std::thread::spawn(move || {
        for line in BufReader::new(pipe).lines() {
            let Ok(line) = line else { break };
            let Ok(mut ring) = ring.lock() else { break };
            ring.push(line);
        }
    });
}
