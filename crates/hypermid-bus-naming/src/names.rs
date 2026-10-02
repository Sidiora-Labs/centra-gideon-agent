use crate::{validate_token, NamingError};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StreamFamily {
    RoomPosts,
    WakeSignals,
    PeerDeliveries,
    EffectIntents,
    DeadLetterEffects,
    ModuleEvents,
}

impl StreamFamily {
    fn token(self) -> &'static str {
        match self {
            Self::RoomPosts => "room-posts",
            Self::WakeSignals => "wake-signals",
            Self::PeerDeliveries => "peer-deliveries",
            Self::EffectIntents => "effect-intents",
            Self::DeadLetterEffects => "dead-letter-effects",
            Self::ModuleEvents => "module-events",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DurableNames {
    pub agent: String,
    pub module: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Topology {
    root: String,
    pub census_bucket: String,
}

impl Topology {
    pub fn new(owner: &str, project: &str, workspace: Option<&str>) -> Result<Self, NamingError> {
        validate_token(owner)?;
        validate_token(project)?;
        if let Some(workspace) = workspace {
            validate_token(workspace)?;
        }
        let mut root = format!("hm.{owner}.{project}");
        if let Some(workspace) = workspace {
            root.push('.');
            root.push_str(workspace);
        }
        Ok(Self {
            census_bucket: format!("HM_CENSUS_{}", root[3..].replace('.', "_")),
            root,
        })
    }

    pub fn stream_subject(&self, family: StreamFamily) -> String {
        format!("{}.{}.>", self.root, family.token())
    }

    pub fn stream_name(&self, family: StreamFamily) -> String {
        format!(
            "HM_{}_{}",
            self.root[3..].replace('.', "_").to_uppercase(),
            family.token().replace('-', "_").to_uppercase()
        )
    }

    pub fn subject(
        &self,
        family: StreamFamily,
        principal: &str,
        event: &str,
    ) -> Result<String, NamingError> {
        validate_token(principal)?;
        validate_token(event)?;
        Ok(format!(
            "{}.{}.{}.{}",
            self.root,
            family.token(),
            principal,
            event
        ))
    }

    pub fn durables(&self, agent: &str, module: &str) -> Result<DurableNames, NamingError> {
        validate_token(agent)?;
        validate_token(module)?;
        let scope = self.root[3..].replace('.', "_");
        Ok(DurableNames {
            agent: format!("hm_{scope}_agent_{agent}"),
            module: format!("hm_{scope}_module_{module}"),
        })
    }

    pub fn validate_non_overlapping(&self) -> Result<(), NamingError> {
        let subjects: Vec<_> = [
            StreamFamily::RoomPosts,
            StreamFamily::WakeSignals,
            StreamFamily::PeerDeliveries,
            StreamFamily::EffectIntents,
            StreamFamily::DeadLetterEffects,
            StreamFamily::ModuleEvents,
        ]
        .into_iter()
        .map(|family| self.stream_subject(family))
        .collect();
        for (index, left) in subjects.iter().enumerate() {
            for right in &subjects[index + 1..] {
                let left = left.trim_end_matches('>');
                let right = right.trim_end_matches('>');
                if left.starts_with(right) || right.starts_with(left) {
                    return Err(NamingError::StreamOverlap);
                }
            }
        }
        Ok(())
    }
}
