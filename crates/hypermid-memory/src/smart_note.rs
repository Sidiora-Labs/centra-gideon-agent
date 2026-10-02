use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use thiserror::Error;

use crate::Digest;

pub const MAX_SMART_NOTE_CLAUSES: usize = 32;
pub const MAX_SMART_NOTE_ARRAY_VALUES: usize = 64;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PredicateOperator {
    All,
    Any,
    None,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Comparison {
    Eq,
    Neq,
    In,
    Contains,
    Gte,
    Lte,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
pub enum PredicateField {
    #[serde(rename = "event.kind")]
    EventKind,
    #[serde(rename = "event.label")]
    EventLabel,
    #[serde(rename = "record.kind")]
    RecordKind,
    #[serde(rename = "record.category")]
    RecordCategory,
    #[serde(rename = "record.status")]
    RecordStatus,
    #[serde(rename = "time.hour")]
    TimeHour,
    #[serde(rename = "time.weekday")]
    TimeWeekday,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(untagged)]
pub enum PredicateValue {
    String(String),
    Number(f64),
    Boolean(bool),
    Array(Vec<PredicateScalar>),
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(untagged)]
pub enum PredicateScalar {
    String(String),
    Number(f64),
    Boolean(bool),
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PredicateClause {
    pub field: PredicateField,
    pub comparison: Comparison,
    pub value: PredicateValue,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SmartPredicate {
    pub operator: PredicateOperator,
    pub clauses: Vec<PredicateClause>,
}

#[derive(Clone, Debug, Default)]
pub struct PredicateContext {
    values: BTreeMap<PredicateField, PredicateScalar>,
}

impl PredicateContext {
    pub fn insert(&mut self, field: PredicateField, value: PredicateScalar) {
        self.values.insert(field, value);
    }

    pub fn from_json(value: &Value) -> Result<Self, PredicateError> {
        let mut context = Self::default();
        for (field, path) in [
            (PredicateField::EventKind, ["event", "kind"]),
            (PredicateField::EventLabel, ["event", "label"]),
            (PredicateField::RecordKind, ["record", "kind"]),
            (PredicateField::RecordCategory, ["record", "category"]),
            (PredicateField::RecordStatus, ["record", "status"]),
            (PredicateField::TimeHour, ["time", "hour"]),
            (PredicateField::TimeWeekday, ["time", "weekday"]),
        ] {
            if let Some(raw) = value.get(path[0]).and_then(|item| item.get(path[1])) {
                context.insert(field, scalar_from_json(raw)?);
            }
        }
        Ok(context)
    }
}

#[derive(Debug, Error, Eq, PartialEq)]
pub enum PredicateError {
    #[error("smart-note predicate must contain between 1 and 32 clauses")]
    InvalidClauseCount,
    #[error("smart-note predicate value is not a supported scalar")]
    InvalidValue,
    #[error("comparison is incompatible with the predicate value")]
    InvalidComparison,
}

impl SmartPredicate {
    pub fn validate(&self) -> Result<(), PredicateError> {
        if self.clauses.is_empty() || self.clauses.len() > MAX_SMART_NOTE_CLAUSES {
            return Err(PredicateError::InvalidClauseCount);
        }
        for clause in &self.clauses {
            validate_clause(clause)?;
        }
        Ok(())
    }

    pub fn digest(&self) -> Result<Digest, PredicateError> {
        self.validate()?;
        let bytes = serde_json::to_vec(self).map_err(|_| PredicateError::InvalidValue)?;
        Ok(Digest::sha256(&bytes))
    }

    pub fn evaluate(&self, context: &PredicateContext) -> Result<bool, PredicateError> {
        self.validate()?;
        let mut matches = Vec::with_capacity(self.clauses.len());
        for clause in &self.clauses {
            matches.push(evaluate_clause(clause, context)?);
        }
        Ok(match self.operator {
            PredicateOperator::All => matches.into_iter().all(|value| value),
            PredicateOperator::Any => matches.into_iter().any(|value| value),
            PredicateOperator::None => matches.into_iter().all(|value| !value),
        })
    }
}

fn validate_clause(clause: &PredicateClause) -> Result<(), PredicateError> {
    match (&clause.comparison, &clause.value) {
        (Comparison::Eq | Comparison::Neq, value) if valid_scalar_value(value) => Ok(()),
        (Comparison::In, PredicateValue::Array(values))
            if !values.is_empty()
                && values.len() <= MAX_SMART_NOTE_ARRAY_VALUES
                && values.iter().all(valid_scalar) =>
        {
            Ok(())
        }
        (Comparison::Contains, PredicateValue::String(value)) if valid_string(value) => Ok(()),
        (Comparison::Gte | Comparison::Lte, PredicateValue::Number(value)) if value.is_finite() => {
            Ok(())
        }
        _ => Err(PredicateError::InvalidComparison),
    }
}

fn valid_scalar_value(value: &PredicateValue) -> bool {
    match value {
        PredicateValue::String(value) => valid_string(value),
        PredicateValue::Number(value) => value.is_finite(),
        PredicateValue::Boolean(_) => true,
        PredicateValue::Array(_) => false,
    }
}

fn valid_scalar(value: &PredicateScalar) -> bool {
    match value {
        PredicateScalar::String(value) => valid_string(value),
        PredicateScalar::Number(value) => value.is_finite(),
        PredicateScalar::Boolean(_) => true,
    }
}

fn valid_string(value: &str) -> bool {
    value.len() <= 1_024
}

fn evaluate_clause(
    clause: &PredicateClause,
    context: &PredicateContext,
) -> Result<bool, PredicateError> {
    let Some(actual) = context.values.get(&clause.field) else {
        return Ok(false);
    };
    match clause.comparison {
        Comparison::Eq => Ok(scalar_eq(actual, &clause.value)),
        Comparison::Neq => Ok(!scalar_eq(actual, &clause.value)),
        Comparison::In => match &clause.value {
            PredicateValue::Array(values) => Ok(values.iter().any(|value| value == actual)),
            _ => Err(PredicateError::InvalidComparison),
        },
        Comparison::Contains => match (actual, &clause.value) {
            (PredicateScalar::String(actual), PredicateValue::String(expected)) => {
                Ok(actual.contains(expected))
            }
            _ => Err(PredicateError::InvalidComparison),
        },
        Comparison::Gte => compare_numbers(actual, &clause.value, |a, b| a >= b),
        Comparison::Lte => compare_numbers(actual, &clause.value, |a, b| a <= b),
    }
}

fn scalar_eq(actual: &PredicateScalar, expected: &PredicateValue) -> bool {
    match (actual, expected) {
        (PredicateScalar::String(a), PredicateValue::String(b)) => a == b,
        (PredicateScalar::Number(a), PredicateValue::Number(b)) => a == b,
        (PredicateScalar::Boolean(a), PredicateValue::Boolean(b)) => a == b,
        _ => false,
    }
}

fn compare_numbers(
    actual: &PredicateScalar,
    expected: &PredicateValue,
    compare: impl FnOnce(f64, f64) -> bool,
) -> Result<bool, PredicateError> {
    match (actual, expected) {
        (PredicateScalar::Number(actual), PredicateValue::Number(expected))
            if actual.is_finite() && expected.is_finite() =>
        {
            Ok(compare(*actual, *expected))
        }
        _ => Err(PredicateError::InvalidComparison),
    }
}

fn scalar_from_json(value: &Value) -> Result<PredicateScalar, PredicateError> {
    match value {
        Value::String(value) if value.len() <= 1_024 => Ok(PredicateScalar::String(value.clone())),
        Value::Number(value) => value
            .as_f64()
            .filter(|value| value.is_finite())
            .map(PredicateScalar::Number)
            .ok_or(PredicateError::InvalidValue),
        Value::Bool(value) => Ok(PredicateScalar::Boolean(*value)),
        _ => Err(PredicateError::InvalidValue),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evaluates_only_allowlisted_bounded_data() {
        let predicate: SmartPredicate = serde_json::from_value(serde_json::json!({
            "operator": "all",
            "clauses": [
                {"field": "record.kind", "comparison": "eq", "value": "fact"},
                {"field": "time.hour", "comparison": "gte", "value": 9}
            ]
        }))
        .unwrap();
        let context = PredicateContext::from_json(&serde_json::json!({
            "record": {"kind": "fact", "content": "ignored"},
            "time": {"hour": 12},
            "path": "/etc/passwd"
        }))
        .unwrap();
        assert!(predicate.evaluate(&context).unwrap());
        assert!(serde_json::from_value::<SmartPredicate>(serde_json::json!({
            "operator": "all",
            "clauses": [{"field": "file.path", "comparison": "eq", "value": "/tmp"}]
        }))
        .is_err());
    }
}
