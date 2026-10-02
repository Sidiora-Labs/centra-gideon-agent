use crate::{filter_is_contained, validate_token, NamingError};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Role {
    Participant,
    DeliveryAuthority,
    FlowEngine,
    SystemIssuer,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Grant {
    pub publish: Vec<String>,
    pub subscribe: Vec<String>,
    pub create_consumers: bool,
}

impl Grant {
    pub fn validate(&self, role: Role, assigned_prefix: &str) -> Result<(), NamingError> {
        validate_subject_prefix(assigned_prefix)?;
        if self
            .publish
            .iter()
            .chain(&self.subscribe)
            .any(|filter| !filter_is_contained(assigned_prefix, filter))
        {
            return Err(NamingError::FilterEscape);
        }
        let create_allowed = matches!(role, Role::DeliveryAuthority | Role::SystemIssuer);
        if self.create_consumers && !create_allowed {
            return Err(NamingError::GrantTooBroad);
        }
        if matches!(role, Role::Participant)
            && self
                .publish
                .iter()
                .any(|filter| filter.contains(".*.") || filter.ends_with(".>"))
        {
            return Err(NamingError::GrantTooBroad);
        }
        Ok(())
    }
}

fn validate_subject_prefix(prefix: &str) -> Result<(), NamingError> {
    for token in prefix.split('.') {
        validate_token(token)?;
    }
    Ok(())
}
