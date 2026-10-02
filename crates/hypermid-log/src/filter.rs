use serde::{Deserialize, Serialize};
use std::str::FromStr;
use thiserror::Error;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Level {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
    Off,
}

impl FromStr for Level {
    type Err = FilterError;
    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "trace" => Ok(Self::Trace),
            "debug" => Ok(Self::Debug),
            "info" => Ok(Self::Info),
            "warn" | "warning" => Ok(Self::Warn),
            "error" => Ok(Self::Error),
            "off" => Ok(Self::Off),
            _ => Err(FilterError::InvalidLevel),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Rule {
    prefix: String,
    level: Level,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FilterSet {
    rules: Vec<Rule>,
    malformed: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum FilterError {
    #[error("log filter entry is malformed")]
    Malformed,
    #[error("log level is invalid")]
    InvalidLevel,
}

impl FilterSet {
    pub fn parse(value: Option<&str>) -> Self {
        let mut rules = Vec::new();
        let mut malformed = false;
        for part in value
            .unwrap_or("")
            .split(',')
            .map(str::trim)
            .filter(|part| !part.is_empty())
        {
            let Some((prefix, level)) = part.rsplit_once('=') else {
                malformed = true;
                continue;
            };
            if !valid_logger(prefix) {
                malformed = true;
                continue;
            }
            match level.parse() {
                Ok(level) => rules.push(Rule {
                    prefix: prefix.to_owned(),
                    level,
                }),
                Err(_) => malformed = true,
            }
        }
        rules.sort_by(|left, right| right.prefix.len().cmp(&left.prefix.len()));
        Self { rules, malformed }
    }

    pub fn malformed(&self) -> bool {
        self.malformed
    }

    pub fn level_for(&self, logger: &str) -> Level {
        self.rules
            .iter()
            .find(|rule| prefix_matches(&rule.prefix, logger))
            .map(|rule| rule.level)
            .unwrap_or(Level::Info)
    }

    pub fn enabled(&self, logger: &str, level: Level) -> bool {
        level >= self.level_for(logger) && self.level_for(logger) != Level::Off
    }
}

fn prefix_matches(prefix: &str, logger: &str) -> bool {
    prefix == "*"
        || logger == prefix
        || logger
            .strip_prefix(prefix)
            .is_some_and(|tail| tail.starts_with('.'))
}

pub(crate) fn valid_logger(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 160
        && value.split('.').all(|part| {
            !part.is_empty()
                && part
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn most_specific_prefix_wins_and_bad_config_falls_back_safely() {
        let filters = FilterSet::parse(Some("hypermid=warn,hypermid.cache=debug,bad"));
        assert_eq!(filters.level_for("hypermid.cache.store"), Level::Debug);
        assert_eq!(filters.level_for("hypermid.push"), Level::Warn);
        assert_eq!(filters.level_for("unrelated"), Level::Info);
        assert!(filters.malformed());
    }
}
