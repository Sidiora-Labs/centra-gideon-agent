use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace};
use hypermid_role_harness::{RoleDescriptor, RoleMajor, Stability};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

pub const TOOL_ROLE_V1: &str = "hypermid.tool/v1";
pub const TOOL_ROLE_OPERATIONS: [&str; 6] = [
    "describe",
    "catalog",
    "call",
    "withdraw",
    "late_results",
    "ack_late_result",
];

pub fn descriptor(implementation_version: impl Into<String>) -> RoleDescriptor {
    RoleDescriptor::new(
        implementation_version,
        vec![
            RoleMajor::new(TOOL_ROLE_V1, TOOL_ROLE_OPERATIONS, Stability::Stable)
                .expect("static tool role"),
        ],
    )
    .expect("static tool descriptor")
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogRequest {
    pub preset: String,
    #[serde(default)]
    pub parameters: BTreeMap<String, Value>,
    pub composition: Value,
    pub system_text: SystemTextRequest,
    pub digest_only: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SystemTextRequest {
    Omit,
    Include,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ResultOperation {
    Prepend,
    Append,
    Replace,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ToolCatalogEntry {
    pub name: String,
    pub schema_digest: Digest,
    pub semantics: u64,
    pub capabilities: BTreeSet<String>,
    pub result_ops: BTreeSet<ResultOperation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub input_schema: Value,
}

impl ToolCatalogEntry {
    pub fn new(
        name: impl Into<String>,
        semantics: u64,
        capabilities: BTreeSet<String>,
        result_ops: BTreeSet<ResultOperation>,
        description: Option<String>,
        input_schema: Value,
    ) -> Result<Self, ToolViolation> {
        let name = name.into();
        if name.is_empty() || name.len() > 160 || semantics == 0 || !input_schema.is_object() {
            return Err(ToolViolation::InvalidCatalogEntry);
        }
        reject_host_only_required(&input_schema)?;
        let schema_digest = structural_schema_digest(&input_schema)?;
        Ok(Self {
            name,
            schema_digest,
            semantics,
            capabilities,
            result_ops,
            description,
            input_schema,
        })
    }

    pub fn schema_pin(&self) -> SchemaPin {
        SchemaPin {
            tool_name: self.name.clone(),
            schema_digest: self.schema_digest,
            semantics: self.semantics,
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogResponse {
    pub generation: u64,
    pub catalog_digest: Digest,
    pub composition_digest: Digest,
    pub tools: Vec<ToolCatalogEntry>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub system_text: Option<String>,
    pub session_capabilities: BTreeSet<String>,
}

impl CatalogResponse {
    pub fn build(
        generation: u64,
        request: &CatalogRequest,
        tools: Vec<ToolCatalogEntry>,
        system_text: Option<String>,
        session_capabilities: BTreeSet<String>,
    ) -> Result<Self, ToolViolation> {
        if generation == 0
            || (request.system_text == SystemTextRequest::Omit && system_text.is_some())
        {
            return Err(ToolViolation::InvalidCatalog);
        }
        let composition_digest = canonical_digest(&request.composition)?;
        let bound = serde_json::json!({
            "preset": request.preset,
            "parameters": request.parameters,
            "composition": request.composition,
            "system_text_requested": request.system_text,
            "tools": tools,
            "system_text": system_text,
            "session_capabilities": session_capabilities,
        });
        let catalog_digest = canonical_digest(&bound)?;
        Ok(Self {
            generation,
            catalog_digest,
            composition_digest,
            tools,
            system_text,
            session_capabilities,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SchemaPin {
    pub tool_name: String,
    pub schema_digest: Digest,
    pub semantics: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ToolCall {
    pub scope: Scope,
    pub carrier_id: Id,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub call_key: Option<String>,
    pub schema_pin: SchemaPin,
    pub arguments: Value,
    pub trace: Trace,
}

impl ToolCall {
    pub fn validate_against(&self, entry: &ToolCatalogEntry) -> Result<(), ToolViolation> {
        if self.schema_pin != entry.schema_pin() {
            return Err(ToolViolation::StaleSchemaPin);
        }
        if self.call_key.as_ref().is_some_and(|key| {
            key.is_empty() || key.len() > 256 || key.chars().any(char::is_control)
        }) {
            return Err(ToolViolation::InvalidCallKey);
        }
        if !self.arguments.is_object() {
            return Err(ToolViolation::InvalidArguments);
        }
        reject_model_host_only(&self.arguments, &entry.input_schema)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectProgress {
    Prepared,
    Authorized,
    DispatchStarted,
    Settled,
}

impl EffectProgress {
    pub fn restart_action(self) -> RestartAction {
        match self {
            Self::DispatchStarted => RestartAction::Reconcile,
            Self::Settled => RestartAction::ReturnRecorded,
            _ => RestartAction::DoNotDispatch,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RestartAction {
    DoNotDispatch,
    Reconcile,
    ReturnRecorded,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WithdrawalOutcome {
    Withdrawn,
    AlreadyStarted,
    Completed,
    Refused,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WithdrawalRequest {
    pub scope: Scope,
    pub requester_id: Id,
    pub carrier_id: Id,
    pub call_key: String,
}

impl WithdrawalRequest {
    pub fn authorize(&self, stamped_scope: &Scope, owner_id: &Id) -> Result<(), ToolViolation> {
        if &self.scope != stamped_scope
            || (&self.requester_id != owner_id && self.requester_id != self.carrier_id)
        {
            return Err(ToolViolation::WithdrawalDenied);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LateResult {
    pub kind: String,
    pub scope: Scope,
    pub custodian_id: Id,
    pub call_key: String,
    pub event_id: Id,
    pub cursor: Cursor,
    pub settled_at_ms: u64,
    pub reduced: bool,
    #[serde(default)]
    pub result: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outcome: Option<String>,
}

pub fn structural_schema_digest(schema: &Value) -> Result<Digest, ToolViolation> {
    if !schema.is_object() {
        return Err(ToolViolation::InvalidSchema);
    }
    let mut structural = schema.clone();
    strip_descriptive_keywords(&mut structural);
    canonical_digest(&structural)
}

pub fn canonical_digest(value: &Value) -> Result<Digest, ToolViolation> {
    let bytes = serde_json::to_vec(value).map_err(|_| ToolViolation::InvalidJson)?;
    Ok(Digest::sha256(bytes))
}

fn strip_descriptive_keywords(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for key in ["description", "$comment", "examples", "title"] {
                map.remove(key);
            }
            for child in map.values_mut() {
                strip_descriptive_keywords(child);
            }
        }
        Value::Array(items) => items.iter_mut().for_each(strip_descriptive_keywords),
        _ => {}
    }
}

fn reject_host_only_required(schema: &Value) -> Result<(), ToolViolation> {
    let Some(properties) = schema.get("properties").and_then(Value::as_object) else {
        return Ok(());
    };
    let required = schema
        .get("required")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for (name, property) in properties {
        if property.get("x-hypermid-host-only") == Some(&Value::Bool(true))
            && required.iter().any(|item| item.as_str() == Some(name))
        {
            return Err(ToolViolation::RequiredHostOnlyParameter);
        }
    }
    Ok(())
}

fn reject_model_host_only(arguments: &Value, schema: &Value) -> Result<(), ToolViolation> {
    let Some(args) = arguments.as_object() else {
        return Err(ToolViolation::InvalidArguments);
    };
    let properties = schema.get("properties").and_then(Value::as_object);
    if args.keys().any(|name| {
        properties
            .and_then(|p| p.get(name))
            .and_then(|p| p.get("x-hypermid-host-only"))
            == Some(&Value::Bool(true))
    }) {
        return Err(ToolViolation::ModelSuppliedHostOnlyParameter);
    }
    Ok(())
}

#[derive(Debug, thiserror::Error)]
pub enum ToolViolation {
    #[error("invalid tool catalog entry")]
    InvalidCatalogEntry,
    #[error("invalid tool catalog")]
    InvalidCatalog,
    #[error("input schema must be an object")]
    InvalidSchema,
    #[error("host-only parameters must be optional")]
    RequiredHostOnlyParameter,
    #[error("model supplied a host-only parameter")]
    ModelSuppliedHostOnlyParameter,
    #[error("schema pin is stale or incompatible")]
    StaleSchemaPin,
    #[error("call key is invalid")]
    InvalidCallKey,
    #[error("tool arguments must be an object")]
    InvalidArguments,
    #[error("withdrawal requester lacks stamped authority")]
    WithdrawalDenied,
    #[error("value cannot be represented as canonical JSON")]
    InvalidJson,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn structural_digest_ignores_descriptions_but_pins_shape() {
        let a = serde_json::json!({"type":"object","description":"first","properties":{"q":{"type":"string","description":"a"}}});
        let b = serde_json::json!({"type":"object","description":"second","properties":{"q":{"type":"string","description":"b"}}});
        assert_eq!(
            structural_schema_digest(&a).unwrap(),
            structural_schema_digest(&b).unwrap()
        );
        let c = serde_json::json!({"type":"object","properties":{"q":{"type":"number"}}});
        assert_ne!(
            structural_schema_digest(&a).unwrap(),
            structural_schema_digest(&c).unwrap()
        );
    }
}
