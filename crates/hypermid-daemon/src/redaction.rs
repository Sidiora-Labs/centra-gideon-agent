use serde_json::{Map, Value};

const REDACTED: &str = "[redacted]";

#[derive(Clone, Debug, Default)]
pub struct Redactor {
    registered: Vec<Vec<u8>>,
}

impl Redactor {
    pub fn new(values: impl IntoIterator<Item = Vec<u8>>) -> Self {
        let mut registered: Vec<Vec<u8>> = values
            .into_iter()
            .filter(|value| value.len() >= 4)
            .collect();
        registered.sort_by_key(|value| std::cmp::Reverse(value.len()));
        registered.dedup();
        Self { registered }
    }

    pub fn redact_json(&self, value: &Value) -> Value {
        match value {
            Value::Object(fields) => Value::Object(
                fields
                    .iter()
                    .map(|(key, value)| {
                        let value = if sensitive_field(key) {
                            Value::String(REDACTED.into())
                        } else {
                            self.redact_json(value)
                        };
                        (key.clone(), value)
                    })
                    .collect::<Map<_, _>>(),
            ),
            Value::Array(values) => {
                Value::Array(values.iter().map(|value| self.redact_json(value)).collect())
            }
            Value::String(value) => Value::String(self.redact_text(value)),
            other => other.clone(),
        }
    }

    pub fn redact_text(&self, value: &str) -> String {
        String::from_utf8_lossy(&self.redact_bytes(value.as_bytes())).into_owned()
    }

    pub fn redact_bytes(&self, value: &[u8]) -> Vec<u8> {
        let mut output = value.to_vec();
        for secret in &self.registered {
            output = replace_all(&output, secret, REDACTED.as_bytes());
        }
        scrub_common_credentials(&mut output)
    }
}

fn sensitive_field(key: &str) -> bool {
    let normalized = key
        .bytes()
        .filter(|byte| byte.is_ascii_alphanumeric())
        .map(|byte| byte.to_ascii_lowercase())
        .collect::<Vec<_>>();
    matches!(
        normalized.as_slice(),
        b"authorization"
            | b"cookie"
            | b"setcookie"
            | b"password"
            | b"passwd"
            | b"secret"
            | b"token"
            | b"apikey"
            | b"credential"
            | b"clientsecret"
            | b"privatekey"
            | b"accesstoken"
            | b"refreshtoken"
            | b"certificatekey"
    ) || normalized.ends_with(b"secret")
        || normalized.ends_with(b"token")
}

fn replace_all(input: &[u8], needle: &[u8], replacement: &[u8]) -> Vec<u8> {
    if needle.is_empty() {
        return input.to_vec();
    }
    let mut result = Vec::with_capacity(input.len());
    let mut cursor = 0;
    while cursor < input.len() {
        if input[cursor..].starts_with(needle) {
            result.extend_from_slice(replacement);
            cursor += needle.len();
        } else {
            result.push(input[cursor]);
            cursor += 1;
        }
    }
    result
}

fn scrub_common_credentials(input: &mut Vec<u8>) -> Vec<u8> {
    let mut output = String::from_utf8_lossy(input).into_owned();
    for marker in [
        "bearer ",
        "api_key=",
        "api-key=",
        "apikey=",
        "access_token=",
        "refresh_token=",
        "client_secret=",
        "password=",
    ] {
        output = scrub_marker(&output, marker);
    }
    scrub_url_userinfo(&output).into_bytes()
}

fn scrub_marker(input: &str, marker: &str) -> String {
    let lower = input.to_ascii_lowercase();
    let mut result = String::with_capacity(input.len());
    let mut cursor = 0;
    while let Some(relative) = lower[cursor..].find(marker) {
        let start = cursor + relative;
        let value_start = start + marker.len();
        result.push_str(&input[cursor..value_start]);
        result.push_str(REDACTED);
        let value_end = input[value_start..]
            .find(|character: char| {
                character.is_whitespace() || matches!(character, '&' | ',' | ';' | '"' | '\'')
            })
            .map(|relative| value_start + relative)
            .unwrap_or(input.len());
        cursor = value_end;
    }
    result.push_str(&input[cursor..]);
    result
}

fn scrub_url_userinfo(input: &str) -> String {
    let mut output = input.to_owned();
    let mut search_from = 0;
    while let Some(scheme_relative) = output[search_from..].find("://") {
        let authority_start = search_from + scheme_relative + 3;
        let authority_end = output[authority_start..]
            .find(|character: char| matches!(character, '/' | '?' | '#' | ' ' | '\n' | '\r'))
            .map(|relative| authority_start + relative)
            .unwrap_or(output.len());
        let Some(at_relative) = output[authority_start..authority_end].rfind('@') else {
            search_from = authority_end;
            continue;
        };
        let at = authority_start + at_relative;
        output.replace_range(authority_start..at, REDACTED);
        search_from = authority_start + REDACTED.len() + 1;
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn redaction_scrubs_structured_registered_and_common_credentials() {
        let canary = "unique-canary-credential";
        let redactor = Redactor::new([canary.as_bytes().to_vec()]);
        let value = json!({
            "authorization": format!("Bearer {canary}"),
            "nested": {"message": format!("sent {canary} api_key=other-secret")},
            "url": "https://operator:password@example.com/path"
        });
        let redacted = redactor.redact_json(&value).to_string();
        assert!(!redacted.contains(canary));
        assert!(!redacted.contains("other-secret"));
        assert!(!redacted.contains("operator:password"));
        assert!(redacted.contains(REDACTED));
    }
}
