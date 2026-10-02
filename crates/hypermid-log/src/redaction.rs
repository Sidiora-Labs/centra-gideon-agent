use crate::format::LogRecord;
use regex::Regex;
use serde_json::Value;
use std::collections::{BTreeMap, HashSet};

pub struct RedactionPolicy {
    secrets: Vec<String>,
    bearer: Regex,
    assignment: Regex,
}

impl RedactionPolicy {
    pub fn new(secrets: impl IntoIterator<Item = String>) -> Self {
        let mut secrets: Vec<_> = secrets
            .into_iter()
            .filter(|value| !value.is_empty())
            .collect();
        secrets.sort_by_key(|value| std::cmp::Reverse(value.len()));
        Self {
            secrets,
            bearer: Regex::new(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}").unwrap(),
            assignment: Regex::new(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+")
                .unwrap(),
        }
    }

    pub fn text(&self, value: &str) -> String {
        let mut redacted = value.to_owned();
        for secret in &self.secrets {
            redacted = redacted.replace(secret, "[REDACTED]");
        }
        redacted = self
            .bearer
            .replace_all(&redacted, "Bearer [REDACTED]")
            .into_owned();
        self.assignment
            .replace_all(&redacted, "$1=[REDACTED]")
            .into_owned()
    }
}

pub fn redact_record(
    record: &LogRecord,
    fleet: &RedactionPolicy,
    module_redactor: Option<&dyn Fn(&str) -> String>,
) -> LogRecord {
    let sensitive: HashSet<&str> = [
        "authorization",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "api_key",
        "private_key",
        "certificate_key",
    ]
    .into_iter()
    .collect();
    let redact = |value: &str| {
        let value = fleet.text(value);
        let value = module_redactor
            .map(|redactor| redactor(&value))
            .unwrap_or(value);
        guard_controls(&value)
    };
    let fields = record
        .fields
        .iter()
        .filter(|(key, _)| !sensitive.contains(key.to_ascii_lowercase().as_str()))
        .map(|(key, value)| (key.clone(), redact_value(value, &redact)))
        .collect::<BTreeMap<_, _>>();
    LogRecord {
        timestamp: record.timestamp.clone(),
        level: record.level,
        logger: record.logger.clone(),
        bound: record
            .bound
            .iter()
            .filter(|item| !sensitive.contains(item.key.to_ascii_lowercase().as_str()))
            .map(|item| crate::format::BoundField {
                key: item.key.clone(),
                value: redact(&item.value),
            })
            .collect(),
        message: redact(&record.message),
        fields,
    }
}

fn redact_value(value: &Value, redact: &dyn Fn(&str) -> String) -> Value {
    match value {
        Value::String(value) => Value::String(redact(value)),
        Value::Array(values) => Value::Array(
            values
                .iter()
                .map(|value| redact_value(value, redact))
                .collect(),
        ),
        Value::Object(values) => Value::Object(
            values
                .iter()
                .map(|(key, value)| (key.clone(), redact_value(value, redact)))
                .collect(),
        ),
        _ => value.clone(),
    }
}

pub fn guard_controls(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut output = String::with_capacity(value.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == 0x1b && index + 1 < bytes.len() && bytes[index + 1] == b'[' {
            index += 2;
            while index < bytes.len() {
                let byte = bytes[index];
                index += 1;
                if (0x40..=0x7e).contains(&byte) {
                    break;
                }
            }
            continue;
        }
        let character = value[index..].chars().next().unwrap();
        index += character.len_utf8();
        if character.is_control() {
            output.push_str(&format!("\\u{:04x}", character as u32));
        } else {
            output.push(character);
        }
    }
    output
}
