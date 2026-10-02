use hypermid_contracts::{Digest, MAX_SAFE_INTEGER};
use hypermid_core::mode::{ContextMode, FeatureFlags, OverflowPolicy, RefusalPolicy};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextConfig {
    pub mode: ContextMode,
    pub overflow_policy: OverflowPolicy,
    pub refusal_policy: RefusalPolicy,
    pub features: FeatureFlags,
}

impl Default for ContextConfig {
    fn default() -> Self {
        Self {
            mode: ContextMode::Off,
            overflow_policy: OverflowPolicy::ReclaimThenRefuse,
            refusal_policy: RefusalPolicy::Refuse,
            features: FeatureFlags::disabled(),
        }
    }
}

impl ContextConfig {
    pub fn canonical_bytes(self) -> Result<Vec<u8>, ConfigError> {
        Ok(serde_json::to_vec(&CanonicalConfig::from(self))?)
    }

    pub fn digest(self) -> Result<Digest, ConfigError> {
        Ok(Digest::sha256(self.canonical_bytes()?))
    }

    pub const fn effective_features(self) -> FeatureFlags {
        self.features.effective_for(self.mode)
    }
}

#[derive(Serialize)]
struct CanonicalConfig {
    features: CanonicalFeatures,
    mode: ContextMode,
    overflow_policy: OverflowPolicy,
    refusal_policy: RefusalPolicy,
}

impl From<ContextConfig> for CanonicalConfig {
    fn from(value: ContextConfig) -> Self {
        Self {
            features: CanonicalFeatures::from(value.features),
            mode: value.mode,
            overflow_policy: value.overflow_policy,
            refusal_policy: value.refusal_policy,
        }
    }
}

#[derive(Serialize)]
struct CanonicalFeatures {
    automatic_reclaim: bool,
    background_summaries: bool,
    nudges: bool,
    reduction_tools: bool,
    subagent_contributions: bool,
    synthetic_hook_blocks: bool,
}

impl From<FeatureFlags> for CanonicalFeatures {
    fn from(value: FeatureFlags) -> Self {
        Self {
            automatic_reclaim: value.automatic_reclaim,
            background_summaries: value.background_summaries,
            nudges: value.nudges,
            reduction_tools: value.reduction_tools,
            subagent_contributions: value.subagent_contributions,
            synthetic_hook_blocks: value.synthetic_hook_blocks,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfigSnapshot {
    pub config: ContextConfig,
    pub policy_revision: u64,
    pub config_digest: Digest,
}

impl ConfigSnapshot {
    pub fn initial(config: ContextConfig) -> Result<Self, ConfigError> {
        Ok(Self {
            config,
            policy_revision: 1,
            config_digest: config.digest()?,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PendingConfiguration {
    pub expected_policy_revision: u64,
    pub previous_config_digest: Digest,
    pub next_config: ContextConfig,
    pub next_config_digest: Digest,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfigTransition {
    pub previous: ConfigSnapshot,
    pub next: ConfigSnapshot,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StageOutcome {
    Unchanged(ConfigSnapshot),
    Pending(PendingConfiguration),
}

#[derive(Clone, Debug)]
pub struct TurnBoundaryConfig {
    active: ConfigSnapshot,
    pending: Option<PendingConfiguration>,
}

impl TurnBoundaryConfig {
    pub fn new(initial: ContextConfig) -> Result<Self, ConfigError> {
        Ok(Self {
            active: ConfigSnapshot::initial(initial)?,
            pending: None,
        })
    }

    pub fn active(&self) -> &ConfigSnapshot {
        &self.active
    }

    pub fn pending(&self) -> Option<&PendingConfiguration> {
        self.pending.as_ref()
    }

    pub fn stage(
        &mut self,
        expected_config_digest: Digest,
        next_config: ContextConfig,
    ) -> Result<StageOutcome, ConfigError> {
        if expected_config_digest != self.active.config_digest {
            return Err(ConfigError::StaleConfiguration);
        }
        let next_config_digest = next_config.digest()?;
        if let Some(pending) = &self.pending {
            if pending.next_config_digest == next_config_digest {
                return Ok(StageOutcome::Pending(pending.clone()));
            }
            return Err(ConfigError::ChangeAlreadyPending);
        }
        if next_config_digest == self.active.config_digest {
            return Ok(StageOutcome::Unchanged(self.active.clone()));
        }
        let pending = PendingConfiguration {
            expected_policy_revision: self.active.policy_revision,
            previous_config_digest: self.active.config_digest,
            next_config,
            next_config_digest,
        };
        self.pending = Some(pending.clone());
        Ok(StageOutcome::Pending(pending))
    }

    pub fn apply_at_turn_boundary(&mut self) -> Result<Option<ConfigTransition>, ConfigError> {
        let Some(pending) = self.pending.as_ref() else {
            return Ok(None);
        };
        if pending.expected_policy_revision != self.active.policy_revision
            || pending.previous_config_digest != self.active.config_digest
        {
            return Err(ConfigError::StaleConfiguration);
        }
        let next_revision = self
            .active
            .policy_revision
            .checked_add(1)
            .filter(|revision| *revision <= MAX_SAFE_INTEGER)
            .ok_or(ConfigError::PolicyRevisionExhausted)?;
        let pending = self.pending.take().expect("pending checked above");
        let previous = self.active.clone();
        let next = ConfigSnapshot {
            config: pending.next_config,
            policy_revision: next_revision,
            config_digest: pending.next_config_digest,
        };
        self.active = next.clone();
        Ok(Some(ConfigTransition { previous, next }))
    }
}

impl Default for TurnBoundaryConfig {
    fn default() -> Self {
        Self::new(ContextConfig::default()).expect("default configuration is serializable")
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("configuration does not match the active digest")]
    StaleConfiguration,
    #[error("a different configuration change is already pending")]
    ChangeAlreadyPending,
    #[error("policy revision exhausted the interoperable integer range")]
    PolicyRevisionExhausted,
    #[error("configuration cannot be serialized: {0}")]
    Serialization(#[from] serde_json::Error),
}
