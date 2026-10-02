use std::collections::HashMap;
use std::fmt;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use thiserror::Error;

use crate::redaction::Redactor;

pub const SECRET_DENIED: &str = "HYPERMID_SECRET_DENIED";

#[derive(Clone, Eq, Hash, PartialEq)]
pub struct SecretHandle(String);

impl SecretHandle {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for SecretHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("SecretHandle")
            .field(&"[opaque]")
            .finish()
    }
}

#[derive(Clone)]
struct StoredSecret {
    principal_id: String,
    operation: String,
    value: Vec<u8>,
}

impl fmt::Debug for StoredSecret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("StoredSecret")
            .field("principal_id", &self.principal_id)
            .field("operation", &self.operation)
            .field("value", &"[redacted]")
            .finish()
    }
}

#[derive(Default)]
pub struct SecretVault {
    secrets: HashMap<SecretHandle, StoredSecret>,
}

impl fmt::Debug for SecretVault {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SecretVault")
            .field("registered", &self.secrets.len())
            .finish()
    }
}

impl SecretVault {
    pub fn register(
        &mut self,
        principal_id: impl Into<String>,
        operation: impl Into<String>,
        value: impl Into<Vec<u8>>,
    ) -> Result<SecretHandle, SecretError> {
        let principal_id = principal_id.into();
        let operation = operation.into();
        let value = value.into();
        if principal_id.is_empty()
            || operation.is_empty()
            || value.len() < 4
            || value.len() > 64 * 1024
        {
            return Err(SecretError::denied("secret.invalid"));
        }
        let mut random = [0_u8; 24];
        getrandom::fill(&mut random).map_err(|_| SecretError::denied("secret.handle"))?;
        let handle = SecretHandle(format!("secret_{}", URL_SAFE_NO_PAD.encode(random)));
        self.secrets.insert(
            handle.clone(),
            StoredSecret {
                principal_id,
                operation,
                value,
            },
        );
        Ok(handle)
    }

    pub fn with_resolved_secret<T>(
        &self,
        handle: &SecretHandle,
        principal_id: &str,
        operation: &str,
        dispatch: impl FnOnce(&[u8]) -> T,
    ) -> Result<T, SecretError> {
        let secret = self
            .secrets
            .get(handle)
            .ok_or_else(|| SecretError::denied("secret.unknown"))?;
        if secret.principal_id != principal_id || secret.operation != operation {
            return Err(SecretError::denied("secret.scope"));
        }
        Ok(dispatch(&secret.value))
    }

    pub fn revoke(&mut self, handle: &SecretHandle) -> bool {
        self.secrets.remove(handle).is_some()
    }

    pub fn redactor(&self) -> Redactor {
        Redactor::new(self.secrets.values().map(|secret| secret.value.clone()))
    }
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
#[error("secret use denied ({rule})")]
pub struct SecretError {
    pub code: &'static str,
    pub rule: &'static str,
}

impl SecretError {
    fn denied(rule: &'static str) -> Self {
        Self {
            code: SECRET_DENIED,
            rule,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn egress_secret_resolution_is_scoped_and_debug_is_opaque() {
        let mut vault = SecretVault::default();
        let handle = vault
            .register("principal-1", "model.invoke", b"canary-credential".to_vec())
            .unwrap();
        assert!(!format!("{vault:?}{handle:?}").contains("canary-credential"));
        assert_eq!(
            vault
                .with_resolved_secret(&handle, "principal-2", "model.invoke", |_| ())
                .unwrap_err()
                .rule,
            "secret.scope"
        );
        let observed = vault
            .with_resolved_secret(&handle, "principal-1", "model.invoke", |value| value.len())
            .unwrap();
        assert_eq!(observed, 17);
    }
}
