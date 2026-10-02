use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WindowKind {
    Primary,
    Secondary,
    Tertiary,
    PerModel,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FundingBasis {
    Prepaid,
    Granted,
    Subscription,
    Metered,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountProvenance {
    ProviderVerified,
    OAuthSubject,
    LocalConfiguration,
    Imported,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Money {
    pub minor_units: i64,
    pub exponent: u8,
    pub currency: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Regeneration {
    pub amount_basis_points: u32,
    pub interval_seconds: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct UsageWindow {
    pub kind: WindowKind,
    pub model_id: Option<String>,
    pub raw_percent_basis_points: Option<u32>,
    pub effective_percent_basis_points: Option<u32>,
    pub reset_at_ms: Option<u64>,
    pub regeneration: Option<Regeneration>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct UsageBreakdown {
    pub category: String,
    pub used: u64,
    pub limit: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Balance {
    pub basis: FundingBasis,
    pub amount: Money,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AccountIdentity {
    pub label: String,
    pub subject: String,
    pub provenance: AccountProvenance,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderFailure {
    pub code: String,
    pub message: String,
    pub retryable: bool,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ProviderUsage {
    pub provider_id: String,
    #[serde(default)]
    pub windows: Vec<UsageWindow>,
    #[serde(default)]
    pub breakdowns: Vec<UsageBreakdown>,
    #[serde(default)]
    pub balances: Vec<Balance>,
    pub saved_credits: Option<Money>,
    pub account: Option<AccountIdentity>,
    pub observed_at_ms: u64,
    pub stale_after_ms: Option<u64>,
    pub error: Option<ProviderFailure>,
    #[serde(flatten)]
    pub raw: BTreeMap<String, Value>,
}

#[derive(Debug, Error, Eq, PartialEq)]
pub enum UsageError {
    #[error("basis points exceed 100 percent")]
    InvalidPercentage,
    #[error("per-model windows require model_id and other windows forbid it")]
    InvalidModelWindow,
    #[error("currency must be three uppercase ASCII letters")]
    InvalidCurrency,
    #[error("healthy entries must omit error state")]
    HealthyWithError,
}

impl ProviderUsage {
    pub fn validate(&self, healthy: bool) -> Result<(), UsageError> {
        if healthy && self.error.is_some() {
            return Err(UsageError::HealthyWithError);
        }
        for window in &self.windows {
            if window
                .raw_percent_basis_points
                .is_some_and(|value| value > 10_000)
                || window
                    .effective_percent_basis_points
                    .is_some_and(|value| value > 10_000)
                || window
                    .regeneration
                    .as_ref()
                    .is_some_and(|value| value.amount_basis_points > 10_000)
            {
                return Err(UsageError::InvalidPercentage);
            }
            if matches!(window.kind, WindowKind::PerModel) != window.model_id.is_some() {
                return Err(UsageError::InvalidModelWindow);
            }
        }
        for money in self
            .balances
            .iter()
            .map(|balance| &balance.amount)
            .chain(self.saved_credits.iter())
        {
            if money.currency.len() != 3
                || !money.currency.bytes().all(|byte| byte.is_ascii_uppercase())
            {
                return Err(UsageError::InvalidCurrency);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn round_trip_preserves_missing_zero_degradation_and_unknown_fields() {
        let value = serde_json::json!({"provider_id":"centra","windows":[{"kind":"primary","model_id":null,"raw_percent_basis_points":0,"effective_percent_basis_points":null,"reset_at_ms":null,"regeneration":{"amount_basis_points":125,"interval_seconds":60}}],"breakdowns":[{"category":"summary","used":8,"limit":null}],"balances":[{"basis":"granted","amount":{"minor_units":0,"exponent":2,"currency":"USD"}}],"saved_credits":null,"account":{"label":"team","subject":"acct-1","provenance":"provider_verified"},"observed_at_ms":42,"stale_after_ms":60,"error":{"code":"RATE_LIMIT","message":"temporarily unavailable","retryable":true},"future":"kept"});
        let usage: ProviderUsage = serde_json::from_value(value).unwrap();
        usage.validate(false).unwrap();
        assert_eq!(usage.windows[0].raw_percent_basis_points, Some(0));
        assert_eq!(usage.windows[0].effective_percent_basis_points, None);
        assert_eq!(usage.raw.get("future"), Some(&Value::String("kept".into())));
        assert!(usage.validate(true).is_err());
    }
}
