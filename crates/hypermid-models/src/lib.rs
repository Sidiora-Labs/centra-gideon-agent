use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PriceRate {
    pub nanodollars_per_million_tokens: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ModelRecord {
    pub id: String,
    #[serde(default)]
    pub modalities: Vec<String>,
    #[serde(default)]
    pub context_tokens: Option<u64>,
    #[serde(default)]
    pub input_price: Option<PriceRate>,
    #[serde(default)]
    pub output_price: Option<PriceRate>,
    #[serde(flatten)]
    pub raw: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ProviderRecord {
    pub id: String,
    #[serde(default)]
    pub models: Vec<ModelRecord>,
    #[serde(flatten)]
    pub raw: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModelCatalog {
    pub providers: Vec<ProviderRecord>,
}

#[derive(Debug, Error, Eq, PartialEq)]
pub enum CatalogError {
    #[error("catalog must be an object")]
    NotObject,
    #[error("catalog providers must be an array")]
    ProvidersNotArray,
    #[error("provider or model identifier is missing")]
    MissingIdentifier,
    #[error("price must be a non-negative decimal string or number")]
    InvalidPrice,
    #[error("nonzero price is below nanodollar precision")]
    PriceUnderflow,
    #[error("price exceeds supported range")]
    PriceOverflow,
}

impl ModelCatalog {
    pub fn parse(value: Value) -> Result<Self, CatalogError> {
        let root = value.as_object().ok_or(CatalogError::NotObject)?;
        let providers = root
            .get("providers")
            .and_then(Value::as_array)
            .ok_or(CatalogError::ProvidersNotArray)?;
        let providers = providers
            .iter()
            .map(parse_provider)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self { providers })
    }
}

fn parse_provider(value: &Value) -> Result<ProviderRecord, CatalogError> {
    let mut object = value.as_object().cloned().ok_or(CatalogError::NotObject)?;
    let id = take_id(&mut object)?;
    let models = match object.remove("models") {
        None => Vec::new(),
        Some(Value::Array(values)) => values
            .iter()
            .map(parse_model)
            .collect::<Result<Vec<_>, _>>()?,
        Some(_) => return Err(CatalogError::ProvidersNotArray),
    };
    Ok(ProviderRecord {
        id,
        models,
        raw: object.into_iter().collect(),
    })
}

fn parse_model(value: &Value) -> Result<ModelRecord, CatalogError> {
    let mut object = value.as_object().cloned().ok_or(CatalogError::NotObject)?;
    let id = take_id(&mut object)?;
    let modalities = match object.remove("modalities") {
        None => Vec::new(),
        Some(Value::Array(values)) => values
            .into_iter()
            .filter_map(|value| value.as_str().map(str::to_owned))
            .collect(),
        Some(_) => return Err(CatalogError::NotObject),
    };
    let context_tokens = object
        .remove("context_tokens")
        .map(|value| value.as_u64().ok_or(CatalogError::NotObject))
        .transpose()?;
    let input_price = take_price(&mut object, "input_price")?;
    let output_price = take_price(&mut object, "output_price")?;
    Ok(ModelRecord {
        id,
        modalities,
        context_tokens,
        input_price,
        output_price,
        raw: object.into_iter().collect(),
    })
}

fn take_id(object: &mut Map<String, Value>) -> Result<String, CatalogError> {
    object
        .remove("id")
        .and_then(|value| value.as_str().map(str::to_owned))
        .filter(|value| !value.is_empty())
        .ok_or(CatalogError::MissingIdentifier)
}

fn take_price(
    object: &mut Map<String, Value>,
    key: &str,
) -> Result<Option<PriceRate>, CatalogError> {
    let Some(value) = object.remove(key) else {
        return Ok(None);
    };
    let text = match value {
        Value::String(value) => value,
        Value::Number(value) => value.to_string(),
        _ => return Err(CatalogError::InvalidPrice),
    };
    Ok(Some(PriceRate {
        nanodollars_per_million_tokens: decimal_to_nanodollars(&text)?,
    }))
}

pub fn decimal_to_nanodollars(value: &str) -> Result<u64, CatalogError> {
    let value = value.trim();
    if value.is_empty() || value.starts_with('-') {
        return Err(CatalogError::InvalidPrice);
    }
    let (mantissa, exponent) = match value.find(['e', 'E']) {
        Some(index) => (
            &value[..index],
            value[index + 1..]
                .parse::<i32>()
                .map_err(|_| CatalogError::InvalidPrice)?,
        ),
        None => (value, 0),
    };
    let mut split = mantissa.split('.');
    let whole = split.next().unwrap_or("");
    let fraction = split.next().unwrap_or("");
    if split.next().is_some()
        || whole.is_empty()
        || !whole.bytes().all(|byte| byte.is_ascii_digit())
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(CatalogError::InvalidPrice);
    }
    let digits = format!("{whole}{fraction}");
    let numerator = digits
        .parse::<u128>()
        .map_err(|_| CatalogError::PriceOverflow)?;
    let scale = fraction.len() as i32 - exponent - 9;
    let rounded = if scale <= 0 {
        numerator
            .checked_mul(pow10((-scale) as u32)?)
            .ok_or(CatalogError::PriceOverflow)?
    } else {
        let divisor = pow10(scale as u32)?;
        let quotient = numerator / divisor;
        let remainder = numerator % divisor;
        match remainder
            .checked_mul(2)
            .ok_or(CatalogError::PriceOverflow)?
            .cmp(&divisor)
        {
            std::cmp::Ordering::Greater => quotient + 1,
            std::cmp::Ordering::Equal if quotient % 2 == 1 => quotient + 1,
            _ => quotient,
        }
    };
    if numerator != 0 && rounded == 0 {
        return Err(CatalogError::PriceUnderflow);
    }
    u64::try_from(rounded).map_err(|_| CatalogError::PriceOverflow)
}

fn pow10(power: u32) -> Result<u128, CatalogError> {
    10_u128
        .checked_pow(power)
        .ok_or(CatalogError::PriceOverflow)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parses_exact_prices_and_preserves_unknowns() {
        let catalog = ModelCatalog::parse(serde_json::json!({"providers":[{"id":"centra","region":"local","models":[{"id":"alpha","input_price":"0.0000000025","output_price":0,"future":{"x":1}}]}]})).unwrap();
        let model = &catalog.providers[0].models[0];
        assert_eq!(
            model
                .input_price
                .as_ref()
                .unwrap()
                .nanodollars_per_million_tokens,
            2
        );
        assert_eq!(
            model
                .output_price
                .as_ref()
                .unwrap()
                .nanodollars_per_million_tokens,
            0
        );
        assert!(model.raw.contains_key("future"));
        assert!(catalog.providers[0].raw.contains_key("region"));
        assert_eq!(
            decimal_to_nanodollars("0.0000000005").unwrap_err(),
            CatalogError::PriceUnderflow
        );
    }
}
