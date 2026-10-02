use std::{fmt, str::FromStr};

use hypermid_contracts::{Cursor, Error, Id, Scope};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum ControlOperation {
    ServerDescribe,
    RegistryList,
    RegistryRescan,
    RouteOpen,
    RoutePoll,
    RouteClose,
    SupervisorList,
    SupervisorRestart,
    SupervisorSwap,
    SupervisorSetEnabled,
    SupervisorHealth,
    SupervisorProbe,
    SupervisorRoutes,
    SupervisorStderr,
    SupervisorTerminals,
    SupervisorSpawnSnapshot,
    SupervisorSpawnSubscribe,
    EventsSubscribe,
    EventsAck,
    OperatorStatus,
    DiagnosticsGet,
    DiagnosticsRerun,
    LogsRead,
    MaintenancePlan,
    MaintenanceApply,
    MaintenanceStatus,
    MaintenanceCancel,
}

impl ControlOperation {
    pub const ALL: [Self; 27] = [
        Self::ServerDescribe,
        Self::RegistryList,
        Self::RegistryRescan,
        Self::RouteOpen,
        Self::RoutePoll,
        Self::RouteClose,
        Self::SupervisorList,
        Self::SupervisorRestart,
        Self::SupervisorSwap,
        Self::SupervisorSetEnabled,
        Self::SupervisorHealth,
        Self::SupervisorProbe,
        Self::SupervisorRoutes,
        Self::SupervisorStderr,
        Self::SupervisorTerminals,
        Self::SupervisorSpawnSnapshot,
        Self::SupervisorSpawnSubscribe,
        Self::EventsSubscribe,
        Self::EventsAck,
        Self::OperatorStatus,
        Self::DiagnosticsGet,
        Self::DiagnosticsRerun,
        Self::LogsRead,
        Self::MaintenancePlan,
        Self::MaintenanceApply,
        Self::MaintenanceStatus,
        Self::MaintenanceCancel,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ServerDescribe => "server.describe",
            Self::RegistryList => "registry.list",
            Self::RegistryRescan => "registry.rescan",
            Self::RouteOpen => "route.open",
            Self::RoutePoll => "route.poll",
            Self::RouteClose => "route.close",
            Self::SupervisorList => "supervisor.list",
            Self::SupervisorRestart => "supervisor.restart",
            Self::SupervisorSwap => "supervisor.swap",
            Self::SupervisorSetEnabled => "supervisor.set_enabled",
            Self::SupervisorHealth => "supervisor.health",
            Self::SupervisorProbe => "supervisor.probe",
            Self::SupervisorRoutes => "supervisor.routes",
            Self::SupervisorStderr => "supervisor.stderr",
            Self::SupervisorTerminals => "supervisor.terminals",
            Self::SupervisorSpawnSnapshot => "supervisor.spawn_snapshot",
            Self::SupervisorSpawnSubscribe => "supervisor.spawn_subscribe",
            Self::EventsSubscribe => "events.subscribe",
            Self::EventsAck => "events.ack",
            Self::OperatorStatus => "operator.status",
            Self::DiagnosticsGet => "diagnostics.get",
            Self::DiagnosticsRerun => "diagnostics.rerun",
            Self::LogsRead => "logs.read",
            Self::MaintenancePlan => "maintenance.plan",
            Self::MaintenanceApply => "maintenance.apply",
            Self::MaintenanceStatus => "maintenance.status",
            Self::MaintenanceCancel => "maintenance.cancel",
        }
    }

    pub const fn required_scope(self) -> &'static str {
        self.as_str()
    }

    pub const fn is_mutation(self) -> bool {
        matches!(
            self,
            Self::RegistryRescan
                | Self::RouteOpen
                | Self::RouteClose
                | Self::SupervisorRestart
                | Self::SupervisorSwap
                | Self::SupervisorSetEnabled
                | Self::SupervisorProbe
                | Self::EventsAck
                | Self::DiagnosticsRerun
                | Self::MaintenanceApply
                | Self::MaintenanceCancel
        )
    }
}

impl fmt::Display for ControlOperation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for ControlOperation {
    type Err = ();

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::ALL
            .into_iter()
            .find(|operation| operation.as_str() == value)
            .ok_or(())
    }
}

impl Serialize for ControlOperation {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for ControlOperation {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        String::deserialize(deserializer)?
            .parse()
            .map_err(|()| serde::de::Error::custom("unknown control operation"))
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ControlRequest {
    pub message_id: Id,
    pub operation: ControlOperation,
    pub scope: Scope,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GenerationStamp {
    pub daemon_instance_id: Id,
    pub registry_generation: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationOutcome {
    Applied,
    Refused,
    AlreadyApplied,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ControlReply {
    pub generation: GenerationStamp,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outcome: Option<MutationOutcome>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payload: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Error>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SubscriptionGap {
    pub supplied: Cursor,
    pub recovery_cursor: Cursor,
    pub reason: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn operation_names_round_trip_and_mutations_are_explicit() {
        for operation in ControlOperation::ALL {
            assert_eq!(operation.as_str().parse(), Ok(operation));
            assert_eq!(
                serde_json::from_str::<ControlOperation>(
                    &serde_json::to_string(&operation).unwrap()
                )
                .unwrap(),
                operation
            );
        }
        assert!(ControlOperation::RegistryRescan.is_mutation());
        assert!(!ControlOperation::RegistryList.is_mutation());
    }
}
