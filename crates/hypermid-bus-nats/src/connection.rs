use hypermid_bus_contracts::BusError;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[derive(Clone, Debug)]
pub enum NatsAuth {
    Anonymous,
    UserPassword { username: String, password: String },
    CredentialsFile(PathBuf),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ConnectionEventKind {
    Connected,
    Disconnected,
    AuthorizationViolation,
    ServerError(String),
    Closed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionEvent {
    pub kind: ConnectionEventKind,
    pub operation: Option<String>,
}

#[derive(Clone)]
pub struct NatsConnection {
    pub(crate) client: async_nats::Client,
    pub(crate) context: async_nats::jetstream::Context,
    public_id: String,
    events: Arc<Mutex<Vec<ConnectionEvent>>>,
    active_operation: Arc<Mutex<Option<String>>>,
}

impl NatsConnection {
    pub async fn connect(url: &str, credential_public_id: &str) -> Result<Self, BusError> {
        Self::connect_with_auth(url, credential_public_id, NatsAuth::Anonymous).await
    }

    pub async fn connect_with_auth(
        url: &str,
        credential_public_id: &str,
        auth: NatsAuth,
    ) -> Result<Self, BusError> {
        validate_public_id(credential_public_id)?;
        let prefix = format!("_INBOX.HM.{credential_public_id}");
        let events = Arc::new(Mutex::new(Vec::new()));
        let active_operation = Arc::new(Mutex::new(None));
        let callback_events = Arc::clone(&events);
        let callback_operation = Arc::clone(&active_operation);
        let mut options = async_nats::ConnectOptions::new()
            .name(format!("hypermid-{credential_public_id}"))
            .custom_inbox_prefix(prefix)
            .event_callback(move |event| {
                let events = Arc::clone(&callback_events);
                let operation = callback_operation
                    .lock()
                    .ok()
                    .and_then(|guard| guard.clone());
                async move {
                    let kind = match event {
                        async_nats::Event::Connected => Some(ConnectionEventKind::Connected),
                        async_nats::Event::Disconnected => Some(ConnectionEventKind::Disconnected),
                        async_nats::Event::Closed => Some(ConnectionEventKind::Closed),
                        async_nats::Event::ServerError(
                            async_nats::ServerError::AuthorizationViolation,
                        ) => Some(ConnectionEventKind::AuthorizationViolation),
                        async_nats::Event::ServerError(error) => {
                            Some(ConnectionEventKind::ServerError(error.to_string()))
                        }
                        _ => None,
                    };
                    if let Some(kind) = kind {
                        if let Ok(mut events) = events.lock() {
                            events.push(ConnectionEvent { kind, operation });
                        }
                    }
                }
            });
        options = match auth {
            NatsAuth::Anonymous => options,
            NatsAuth::UserPassword { username, password } => {
                options.user_and_password(username, password)
            }
            NatsAuth::CredentialsFile(path) => options
                .credentials_file(path)
                .await
                .map_err(map_connection)?,
        };
        let client = options.connect(url).await.map_err(map_connection)?;
        let context = async_nats::jetstream::new(client.clone());
        Ok(Self {
            client,
            context,
            public_id: credential_public_id.into(),
            events,
            active_operation,
        })
    }

    pub fn credential_public_id(&self) -> &str {
        &self.public_id
    }

    pub async fn flush(&self) -> Result<(), BusError> {
        self.client.flush().await.map_err(map_connection)
    }

    pub fn begin_operation(&self, operation: &str) -> Result<(), BusError> {
        validate_public_id(operation)?;
        let mut active = self
            .active_operation
            .lock()
            .map_err(|_| BusError::Internal("NATS sentinel lock poisoned".into()))?;
        if active.is_some() {
            return Err(BusError::Conflict);
        }
        *active = Some(operation.into());
        Ok(())
    }

    pub fn end_operation(&self, operation: &str) -> Result<(), BusError> {
        let mut active = self
            .active_operation
            .lock()
            .map_err(|_| BusError::Internal("NATS sentinel lock poisoned".into()))?;
        if active.as_deref() != Some(operation) {
            return Err(BusError::Conflict);
        }
        *active = None;
        Ok(())
    }

    pub fn take_events(&self) -> Result<Vec<ConnectionEvent>, BusError> {
        let mut events = self
            .events
            .lock()
            .map_err(|_| BusError::Internal("NATS event lock poisoned".into()))?;
        Ok(std::mem::take(&mut *events))
    }
}

fn validate_public_id(value: &str) -> Result<(), BusError> {
    if value.is_empty()
        || value.len() > 96
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        Err(BusError::Invalid(
            "credential public Id is not a safe inbox token".into(),
        ))
    } else {
        Ok(())
    }
}

pub(crate) fn map_connection(error: impl std::fmt::Display) -> BusError {
    let message = error.to_string();
    let normalized = message.to_ascii_lowercase();
    if normalized.contains("permission") || normalized.contains("authorization") {
        BusError::Denied
    } else if normalized.contains("timed out") || normalized.contains("timeout") {
        BusError::Timeout
    } else if normalized.contains("not found") || normalized.contains("404") {
        BusError::Missing
    } else if normalized.contains("disconnected") {
        BusError::Disconnected
    } else {
        BusError::Unavailable
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::NatsStream;
    use async_nats::jetstream;
    use futures::StreamExt;
    use hypermid_bus_contracts::{BusMessage, PullRequest, Stream};
    use hypermid_contracts::{Digest, Id, Scope, Trace};
    use std::collections::BTreeMap;
    use std::process::Command;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    fn env(name: &str) -> String {
        std::env::var(name)
            .unwrap_or_else(|_| panic!("{name} must be set for real NATS conformance"))
    }
    fn auth(user: &str, password: &str) -> NatsAuth {
        NatsAuth::UserPassword {
            username: user.into(),
            password: password.into(),
        }
    }
    fn message(id: &str, subject: &str) -> BusMessage {
        BusMessage {
            subject: subject.into(),
            id: Id::new(id).unwrap(),
            digest: Digest::sha256(id),
            headers: BTreeMap::new(),
            scope: Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None),
            trace: Trace::new(Id::new("trace").unwrap(), Id::new("request").unwrap()),
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn authenticated_server_leaf_revocation_and_system_matrix() {
        let main_url = env("HYPERMID_MATRIX_MAIN_URL");
        let new_user = env("HYPERMID_MATRIX_NEW_USER");
        let new_password = env("HYPERMID_MATRIX_NEW_PASSWORD");
        let old_user = env("HYPERMID_MATRIX_OLD_USER");
        let old_password = env("HYPERMID_MATRIX_OLD_PASSWORD");
        let leaf_url = env("HYPERMID_MATRIX_LEAF_URL");
        let leaf_user = env("HYPERMID_MATRIX_LEAF_USER");
        let leaf_password = env("HYPERMID_MATRIX_LEAF_PASSWORD");
        let system_user = env("HYPERMID_MATRIX_SYSTEM_USER");
        let system_password = env("HYPERMID_MATRIX_SYSTEM_PASSWORD");
        let nats_container = env("HYPERMID_TEST_NATS_CONTAINER");

        assert!(NatsConnection::connect_with_auth(
            &main_url,
            "revoked-old",
            auth(&old_user, &old_password)
        )
        .await
        .is_err());
        let main = NatsConnection::connect_with_auth(
            &main_url,
            "active-new",
            auth(&new_user, &new_password),
        )
        .await
        .unwrap();
        let leaf = NatsConnection::connect_with_auth(
            &leaf_url,
            "device-rekey",
            auth(&leaf_user, &leaf_password),
        )
        .await
        .unwrap();
        let system = NatsConnection::connect_with_auth(
            &main_url,
            "system-observer",
            auth(&system_user, &system_password),
        )
        .await
        .unwrap();

        main.begin_operation("denied-publish").unwrap();
        let _ = main
            .client
            .publish("forbidden.escape", "denied".into())
            .await;
        let _ = main.flush().await;
        tokio::time::sleep(Duration::from_millis(150)).await;
        main.end_operation("denied-publish").unwrap();
        assert!(main
            .take_events()
            .unwrap()
            .iter()
            .any(|event| event.operation.as_deref() == Some("denied-publish")
                && matches!(
                    event.kind,
                    ConnectionEventKind::ServerError(_)
                        | ConnectionEventKind::AuthorizationViolation
                )));

        let suffix = format!(
            "{}_{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let leaf_subject = format!("hm.matrix.{suffix}.leaf");
        let mut leaf_delivery = main.client.subscribe(leaf_subject.clone()).await.unwrap();
        main.flush().await.unwrap();
        let mut received = None;
        for _ in 0..10 {
            leaf.client
                .publish(leaf_subject.clone(), "through-leaf".into())
                .await
                .unwrap();
            leaf.flush().await.unwrap();
            if let Ok(message) =
                tokio::time::timeout(Duration::from_millis(500), leaf_delivery.next()).await
            {
                received = message;
                break;
            }
        }
        let received = received.expect("leaf-routed message");
        assert_eq!(&received.payload[..], b"through-leaf");

        let mut system_events = system.client.subscribe("$SYS.>").await.unwrap();
        system
            .client
            .publish("$SYS.REQ.SERVER.PING", "".into())
            .await
            .unwrap();
        system.flush().await.unwrap();
        let observed = tokio::time::timeout(Duration::from_secs(3), system_events.next())
            .await
            .unwrap()
            .expect("system event");
        assert!(observed.subject.starts_with("$SYS."));

        let stream_name = format!("HM_MATRIX_{suffix}").to_uppercase();
        let event_subject = format!("hm.matrix.{suffix}.events");
        let durable = format!("matrix_{suffix}");
        let provisioned = main
            .context
            .create_stream(jetstream::stream::Config {
                name: stream_name.clone(),
                subjects: vec![event_subject.clone()],
                storage: jetstream::stream::StorageType::File,
                ..Default::default()
            })
            .await
            .unwrap();
        provisioned
            .create_consumer(jetstream::consumer::pull::Config {
                durable_name: Some(durable.clone()),
                filter_subject: event_subject.clone(),
                ack_wait: Duration::from_secs(2),
                max_deliver: 2,
                max_ack_pending: 1,
                ..Default::default()
            })
            .await
            .unwrap();
        let bus = NatsStream::bind(main.clone(), &stream_name).await.unwrap();
        let expected = bus
            .publish(message("matrix-event", &event_subject))
            .await
            .unwrap();

        let restart = Command::new("docker")
            .args(["restart", nats_container.as_str()])
            .status()
            .expect("docker restart invocation");
        assert!(restart.success());
        let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
        loop {
            if main.flush().await.is_ok() {
                break;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "NATS did not recover after server restart"
            );
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
        let events = main.take_events().unwrap();
        assert!(events
            .iter()
            .any(|event| event.kind == ConnectionEventKind::Disconnected));
        assert!(events
            .iter()
            .any(|event| event.kind == ConnectionEventKind::Connected));
        let recovered = NatsConnection::connect_with_auth(
            &main_url,
            "active-new-recovered",
            auth(&new_user, &new_password),
        )
        .await
        .unwrap();
        let resumed = NatsStream::bind(recovered.clone(), &stream_name)
            .await
            .unwrap();
        let request = PullRequest {
            durable: Id::new(&durable).unwrap(),
            filter: event_subject,
            ack_wait: Duration::from_secs(2),
            max_deliveries: 2,
        };
        let delivery = resumed
            .pull(&request)
            .await
            .unwrap()
            .expect("retained delivery after server restart");
        assert_eq!(delivery.cursor, expected);
        resumed
            .dispose(
                &durable,
                delivery.cursor,
                hypermid_bus_contracts::DeliveryDisposition::Ack,
            )
            .await
            .unwrap();
        recovered.context.delete_stream(&stream_name).await.unwrap();
    }
}
