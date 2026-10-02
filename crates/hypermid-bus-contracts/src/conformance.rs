use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Property {
    PublishOrdering,
    LifetimeReplay,
    DeliveryCounts,
    TerminalDisposition,
    ProgressExtension,
    RegisterSnapshots,
    QueueExhaustion,
    DeadLetterOrdering,
    ErrorMapping,
    ProcessPersistence,
    CredentialRotation,
    DeviceRekey,
    ServerGrantDenial,
    Revocation,
    LeafTopology,
    SystemEvents,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Classification {
    Applicable,
    Inapplicable { reason: &'static str },
}

pub trait ConformanceProfile {
    fn classifications(&self) -> BTreeMap<Property, Classification>;
}

pub fn deterministic_profile() -> BTreeMap<Property, Classification> {
    use Classification::{Applicable, Inapplicable};
    use Property::*;
    let mut result = BTreeMap::new();
    for property in [
        PublishOrdering,
        LifetimeReplay,
        DeliveryCounts,
        TerminalDisposition,
        ProgressExtension,
        RegisterSnapshots,
        QueueExhaustion,
        DeadLetterOrdering,
        ErrorMapping,
    ] {
        result.insert(property, Applicable);
    }
    for property in [
        ProcessPersistence,
        CredentialRotation,
        DeviceRekey,
        ServerGrantDenial,
        Revocation,
        LeafTopology,
        SystemEvents,
    ] {
        result.insert(
            property,
            Inapplicable {
                reason: "requires a provisioned NATS server boundary",
            },
        );
    }
    result
}
