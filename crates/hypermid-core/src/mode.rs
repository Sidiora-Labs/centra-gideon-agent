use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextMode {
    Off,
    PassThrough,
    Shadow,
    Primary,
}

impl Default for ContextMode {
    fn default() -> Self {
        Self::Off
    }
}

impl ContextMode {
    pub const fn capabilities(self) -> ModeCapabilities {
        match self {
            Self::Off => ModeCapabilities {
                hypermid_active: false,
                observes_health: false,
                computes_projection: false,
                allows_model_calls: false,
                allows_durable_writes: false,
                allows_publication: false,
                allows_serving_cursor_advance: false,
                requires_writer_lease: false,
                preserves_host_request_bytes: true,
            },
            Self::PassThrough => ModeCapabilities {
                hypermid_active: true,
                observes_health: true,
                computes_projection: false,
                allows_model_calls: false,
                allows_durable_writes: false,
                allows_publication: false,
                allows_serving_cursor_advance: false,
                requires_writer_lease: false,
                preserves_host_request_bytes: true,
            },
            Self::Shadow => ModeCapabilities {
                hypermid_active: true,
                observes_health: true,
                computes_projection: true,
                allows_model_calls: false,
                allows_durable_writes: false,
                allows_publication: false,
                allows_serving_cursor_advance: false,
                requires_writer_lease: false,
                preserves_host_request_bytes: true,
            },
            Self::Primary => ModeCapabilities {
                hypermid_active: true,
                observes_health: true,
                computes_projection: true,
                allows_model_calls: true,
                allows_durable_writes: true,
                allows_publication: true,
                allows_serving_cursor_advance: true,
                requires_writer_lease: true,
                preserves_host_request_bytes: false,
            },
        }
    }
}

pub type Mode = ContextMode;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ModeCapabilities {
    pub hypermid_active: bool,
    pub observes_health: bool,
    pub computes_projection: bool,
    pub allows_model_calls: bool,
    pub allows_durable_writes: bool,
    pub allows_publication: bool,
    pub allows_serving_cursor_advance: bool,
    pub requires_writer_lease: bool,
    pub preserves_host_request_bytes: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OverflowPolicy {
    ReclaimThenRefuse,
    RefuseImmediately,
}

impl Default for OverflowPolicy {
    fn default() -> Self {
        Self::ReclaimThenRefuse
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RefusalPolicy {
    Refuse,
    CompatibleLastKnownGood,
    HostPassthrough,
}

impl Default for RefusalPolicy {
    fn default() -> Self {
        Self::Refuse
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct FeatureFlags {
    pub background_summaries: bool,
    pub reduction_tools: bool,
    pub automatic_reclaim: bool,
    pub nudges: bool,
    pub subagent_contributions: bool,
    pub synthetic_hook_blocks: bool,
}

impl FeatureFlags {
    pub const fn disabled() -> Self {
        Self {
            background_summaries: false,
            reduction_tools: false,
            automatic_reclaim: false,
            nudges: false,
            subagent_contributions: false,
            synthetic_hook_blocks: false,
        }
    }

    pub const fn effective_for(self, mode: ContextMode) -> Self {
        if matches!(mode, ContextMode::Primary) {
            self
        } else {
            Self::disabled()
        }
    }
}
