use std::{
    collections::{HashMap, VecDeque},
    fs::File,
    io::BufReader,
    path::Path,
    pin::Pin,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    time::{SystemTime, UNIX_EPOCH},
};

use hypermid_contracts::{Cursor, Digest, EffectState, Error as WireError, Id, Scope, Trace};
use hypermid_protocol::{
    authentication_proof, ClientAuthentication, ClientHello, ConnectionClass, EventChunk,
    Principal, ServerChallenge, SessionAccepted, PROTOCOL,
};
use hypermid_transport::ConnectionRecord;
use hypermid_transport::{read_json_frame, write_json_frame, TransportError};
use rustls::RootCertStore;
use rustls_pki_types::{PrivateKeyDer, ServerName};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::{
    io::{split, AsyncRead, AsyncWrite, ReadHalf, WriteHalf},
    net::{TcpStream, UnixStream},
    sync::{broadcast, oneshot, Mutex},
    task::JoinHandle,
    time::{timeout, Duration},
};
use tokio_rustls::{client::TlsStream, TlsConnector};

const CONTROL_ROUTE: &str = "control";
const CONTROL_EPOCH: u64 = 1;
const DEFAULT_DEADLINE_MS: u64 = 30_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EffectKind {
    Query,
    Mutation,
}

#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Transport(#[from] TransportError),
    #[error("invalid Hypermid message: {0}")]
    Protocol(String),
    #[error("Hypermid request timed out")]
    TimedOut,
    #[error("Hypermid connection ended before a response")]
    ConnectionEnded,
    #[error("mutation outcome is unknown after dispatch")]
    OutcomeUnknown,
    #[error("remote error: {0}")]
    Remote(#[from] WireError),
}

#[derive(Debug)]
pub enum EndpointStream {
    Unix(UnixStream),
    Loopback(TcpStream),
}

impl AsyncRead for EndpointStream {
    fn poll_read(
        self: Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
        buffer: &mut tokio::io::ReadBuf<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        match self.get_mut() {
            Self::Unix(stream) => Pin::new(stream).poll_read(context, buffer),
            Self::Loopback(stream) => Pin::new(stream).poll_read(context, buffer),
        }
    }
}

impl AsyncWrite for EndpointStream {
    fn poll_write(
        self: Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
        bytes: &[u8],
    ) -> std::task::Poll<std::io::Result<usize>> {
        match self.get_mut() {
            Self::Unix(stream) => Pin::new(stream).poll_write(context, bytes),
            Self::Loopback(stream) => Pin::new(stream).poll_write(context, bytes),
        }
    }

    fn poll_flush(
        self: Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        match self.get_mut() {
            Self::Unix(stream) => Pin::new(stream).poll_flush(context),
            Self::Loopback(stream) => Pin::new(stream).poll_flush(context),
        }
    }

    fn poll_shutdown(
        self: Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        match self.get_mut() {
            Self::Unix(stream) => Pin::new(stream).poll_shutdown(context),
            Self::Loopback(stream) => Pin::new(stream).poll_shutdown(context),
        }
    }
}

pub type LocalTlsStream = TlsStream<EndpointStream>;

pub async fn connect_record(
    record_path: impl AsRef<Path>,
    scope: Scope,
    connection_class: ConnectionClass,
) -> Result<HypermidClient<LocalTlsStream>, ClientError> {
    let record = ConnectionRecord::load(record_path.as_ref())?;
    if record.protocol != PROTOCOL {
        return Err(ClientError::Protocol(format!(
            "unsupported connection-record protocol {:?}",
            record.protocol
        )));
    }
    if record.expires_ms <= now_ms() {
        return Err(ClientError::Protocol(
            "connection record has expired".into(),
        ));
    }
    let secret = record.secret_bytes()?;
    let stream = open_tls(&record).await?;
    HypermidClient::connect(stream, &secret, scope, connection_class).await
}

pub async fn open_tls(record: &ConnectionRecord) -> Result<LocalTlsStream, ClientError> {
    if record.protocol != PROTOCOL {
        return Err(ClientError::Protocol(
            "unsupported connection protocol".into(),
        ));
    }
    let mut roots = RootCertStore::empty();
    for certificate in load_certificates(&record.ca_certificate)? {
        roots
            .add(certificate)
            .map_err(|error| ClientError::Protocol(format!("invalid CA certificate: {error}")))?;
    }
    let certificates = load_certificates(&record.client_certificate)?;
    let private_key = load_private_key(&record.client_private_key)?;
    let config = rustls::ClientConfig::builder_with_protocol_versions(&[&rustls::version::TLS13])
        .with_root_certificates(roots)
        .with_client_auth_cert(certificates, private_key)
        .map_err(|error| ClientError::Protocol(format!("invalid client certificate: {error}")))?;
    let endpoint = if let Some(address) = record.endpoint.strip_prefix("tcp://") {
        let stream = TcpStream::connect(address).await?;
        if !stream.peer_addr()?.ip().is_loopback() {
            return Err(ClientError::Protocol(
                "local connection record contains a non-loopback TCP endpoint".into(),
            ));
        }
        EndpointStream::Loopback(stream)
    } else {
        let path = Path::new(&record.endpoint);
        if !path.is_absolute() {
            return Err(ClientError::Protocol(
                "Unix connection-record endpoint must be absolute".into(),
            ));
        }
        EndpointStream::Unix(UnixStream::connect(path).await?)
    };
    let server_name = ServerName::try_from(record.server_name.clone())
        .map_err(|_| ClientError::Protocol("invalid TLS server name".into()))?;
    TlsConnector::from(std::sync::Arc::new(config))
        .connect(server_name, endpoint)
        .await
        .map_err(|error| ClientError::Protocol(format!("mutual TLS failed: {error}")))
}

fn load_certificates(
    path: &Path,
) -> Result<Vec<rustls_pki_types::CertificateDer<'static>>, ClientError> {
    let file = File::open(path)?;
    rustls_pemfile::certs(&mut BufReader::new(file))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| ClientError::Protocol(format!("invalid certificate file: {error}")))
}

fn load_private_key(path: &Path) -> Result<PrivateKeyDer<'static>, ClientError> {
    let file = File::open(path)?;
    rustls_pemfile::private_key(&mut BufReader::new(file))
        .map_err(|error| ClientError::Protocol(format!("invalid private key: {error}")))?
        .ok_or_else(|| ClientError::Protocol("private key file is empty".into()))
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum WireKind {
    Request,
    Response,
    Event,
    Credit,
    Cancel,
    Ping,
    Pong,
    Close,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct WireEnvelope {
    protocol: String,
    kind: WireKind,
    sequence: u64,
    message_id: Id,
    #[serde(skip_serializing_if = "Option::is_none")]
    reply_to: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    route_id: Option<Id>,
    #[serde(skip_serializing_if = "Option::is_none")]
    route_epoch: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    operation: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope: Option<Scope>,
    #[serde(skip_serializing_if = "Option::is_none")]
    trace: Option<Trace>,
    #[serde(skip_serializing_if = "Option::is_none")]
    deadline_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    payload: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<WireError>,
}

impl WireEnvelope {
    fn validate(&self) -> Result<(), ClientError> {
        if self.protocol != PROTOCOL {
            return Err(ClientError::Protocol(format!(
                "unsupported protocol {:?}",
                self.protocol
            )));
        }
        match self.kind {
            WireKind::Request
                if self.route_id.is_none()
                    || self.route_epoch.is_none()
                    || self.operation.is_none()
                    || self.deadline_ms.is_none() =>
            {
                Err(ClientError::Protocol(
                    "request fields are incomplete".into(),
                ))
            }
            WireKind::Response | WireKind::Cancel if self.reply_to.is_none() => Err(
                ClientError::Protocol("response or cancel has no correlation id".into()),
            ),
            _ => Ok(()),
        }
    }
}

#[derive(Clone, Debug)]
pub struct ClientEvent {
    pub message_id: Id,
    pub operation: Option<String>,
    pub payload: Option<Value>,
    pub error: Option<WireError>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct EffectStatus {
    pub effect_id: Id,
    pub state: EffectState,
    pub result_digest: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ScopedEvent {
    pub event_id: Id,
    pub topic: String,
    pub producer: Principal,
    pub scope: Scope,
    pub at_ms: u64,
    pub schema_name: String,
    pub schema_version: u64,
    pub payload_digest: Digest,
    pub trace: Option<Trace>,
    pub cursor: Cursor,
    pub payload: Value,
    #[serde(default = "first_delivery")]
    pub delivery_count: u32,
}

fn first_delivery() -> u32 {
    1
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SubscriptionSnapshot {
    pub subscription_id: Id,
    pub cursor: Cursor,
    #[serde(default)]
    pub replay: Vec<ScopedEvent>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AcknowledgeReceipt {
    pub acknowledged: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResumePoint {
    pub consumer_id: Id,
    pub scope: Scope,
    pub topic_filter: String,
    pub cursor: Cursor,
}

pub trait ResumeStore: Send + Sync {
    fn load(&self) -> Result<Option<ResumePoint>, ClientError>;
    fn save(&self, point: &ResumePoint) -> Result<(), ClientError>;
}

pub struct DurableSubscription<'a, S>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    client: &'a HypermidClient<S>,
    point: ResumePoint,
    snapshot: SubscriptionSnapshot,
    replay: VecDeque<ScopedEvent>,
    events: broadcast::Receiver<ClientEvent>,
    last_seen: Cursor,
    resume_store: Option<&'a dyn ResumeStore>,
}

impl<'a, S> DurableSubscription<'a, S>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    pub fn snapshot(&self) -> &SubscriptionSnapshot {
        &self.snapshot
    }

    pub fn resume_point(&self) -> &ResumePoint {
        &self.point
    }

    pub async fn next_event(&mut self) -> Result<ScopedEvent, ClientError> {
        if let Some(event) = self.replay.pop_front() {
            self.validate_event(&event)?;
            return Ok(event);
        }
        loop {
            let delivered = self.events.recv().await.map_err(|error| {
                ClientError::Protocol(format!("durable subscription event stream failed: {error}"))
            })?;
            if let Some(error) = delivered.error {
                return Err(ClientError::Remote(error));
            }
            if delivered.operation.as_deref() != Some("events.deliver") {
                continue;
            }
            let value = delivered
                .payload
                .ok_or_else(|| ClientError::Protocol("event delivery has no payload".into()))?;
            let envelope: DeliveryEnvelope = serde_json::from_value(value)
                .map_err(|error| ClientError::Protocol(error.to_string()))?;
            if envelope.subscription_id != self.point.consumer_id {
                continue;
            }
            validate_scoped_event(&envelope.event)?;
            self.validate_event(&envelope.event)?;
            return Ok(envelope.event);
        }
    }

    pub async fn acknowledge(
        &mut self,
        event: &ScopedEvent,
    ) -> Result<AcknowledgeReceipt, ClientError> {
        if event.scope != self.point.scope {
            return Err(ClientError::Protocol(
                "cannot acknowledge an event from another scope".into(),
            ));
        }
        let value = self
            .client
            .request(
                "events.ack",
                json!({
                    "consumer_id": self.point.consumer_id,
                    "event_id": event.event_id,
                }),
                EffectKind::Mutation,
                None,
            )
            .await?;
        let receipt: AcknowledgeReceipt = serde_json::from_value(value)
            .map_err(|error| ClientError::Protocol(error.to_string()))?;
        if !receipt.acknowledged {
            return Err(ClientError::Protocol(
                "daemon did not acknowledge the durable event".into(),
            ));
        }
        self.point.cursor = event.cursor;
        if let Some(store) = self.resume_store {
            store.save(&self.point)?;
        }
        Ok(receipt)
    }

    pub async fn close(self) -> Result<(), ClientError> {
        let value = self
            .client
            .request(
                "events.unsubscribe",
                json!({"consumer_id": self.point.consumer_id}),
                EffectKind::Mutation,
                None,
            )
            .await?;
        if value.get("unsubscribed").and_then(Value::as_bool) != Some(true) {
            return Err(ClientError::Protocol(
                "daemon did not close the durable subscription".into(),
            ));
        }
        Ok(())
    }

    fn validate_event(&mut self, event: &ScopedEvent) -> Result<(), ClientError> {
        if event.scope != self.point.scope {
            return Err(ClientError::Protocol(
                "daemon delivered an event outside the subscription scope".into(),
            ));
        }
        if event.cursor.epoch != self.last_seen.epoch
            || event.cursor.sequence <= self.last_seen.sequence
        {
            return Err(ClientError::Protocol(
                "daemon delivered a replayed, reordered, or foreign-epoch event".into(),
            ));
        }
        self.last_seen = event.cursor;
        Ok(())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DeliveryEnvelope {
    subscription_id: Id,
    event: ScopedEvent,
}

struct Pending {
    effect: EffectKind,
    sender: oneshot::Sender<Result<Value, ClientError>>,
}

struct Inner<W> {
    writer: Mutex<W>,
    pending: Mutex<HashMap<Id, Pending>>,
    events: broadcast::Sender<ClientEvent>,
    outbound_sequence: AtomicU64,
    next_id: AtomicU64,
    closed: AtomicBool,
    scope: Scope,
}

pub struct HypermidClient<S>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    inner: Arc<Inner<WriteHalf<S>>>,
    reader: JoinHandle<()>,
    session: SessionAccepted,
}

impl<S> HypermidClient<S>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    pub async fn connect(
        mut stream: S,
        credential: &[u8],
        scope: Scope,
        connection_class: ConnectionClass,
    ) -> Result<Self, ClientError> {
        if credential.len() != 32 {
            return Err(ClientError::Protocol(
                "connection credential must be exactly 32 bytes".into(),
            ));
        }
        let mut nonce = [0_u8; 32];
        getrandom::fill(&mut nonce)
            .map_err(|error| ClientError::Protocol(format!("random source failed: {error}")))?;
        let hello = ClientHello {
            kind: "hello".into(),
            protocols: vec![PROTOCOL.into()],
            client_nonce: hex(&nonce),
            connection_class,
            scope: scope.clone(),
        };
        write_json_frame(&mut stream, &canonical_value(&hello)?).await?;
        let challenge_value: Value = read_json_frame(&mut stream).await?;
        let challenge: ServerChallenge = serde_json::from_value(challenge_value)
            .map_err(|error| ClientError::Protocol(error.to_string()))?;
        if challenge.kind != "challenge" {
            return Err(ClientError::Protocol(
                "daemon did not return an authentication challenge".into(),
            ));
        }
        let proof = authentication_proof(credential, &hello, &challenge)
            .map_err(|error| ClientError::Protocol(error.to_string()))?;
        let authentication = ClientAuthentication {
            kind: "authenticate".into(),
            proof,
            token: None,
            module_id: None,
            spawn_generation: None,
        };
        write_json_frame(&mut stream, &canonical_value(&authentication)?).await?;
        let accepted_value: Value = read_json_frame(&mut stream).await?;
        let session: SessionAccepted = serde_json::from_value(accepted_value)
            .map_err(|error| ClientError::Protocol(error.to_string()))?;
        if session.kind != "accepted" || session.protocol != PROTOCOL {
            return Err(ClientError::Protocol(
                "daemon did not accept the authenticated session".into(),
            ));
        }

        let (reader, writer) = split(stream);
        let (events, _) = broadcast::channel(256);
        let inner = Arc::new(Inner {
            writer: Mutex::new(writer),
            pending: Mutex::new(HashMap::new()),
            events,
            outbound_sequence: AtomicU64::new(1),
            next_id: AtomicU64::new(0),
            closed: AtomicBool::new(false),
            scope,
        });
        let reader_inner = Arc::clone(&inner);
        let reader = tokio::spawn(async move { read_loop(reader, reader_inner).await });
        Ok(Self {
            inner,
            reader,
            session,
        })
    }

    pub fn session(&self) -> &SessionAccepted {
        &self.session
    }

    pub fn subscribe(&self) -> broadcast::Receiver<ClientEvent> {
        self.inner.events.subscribe()
    }

    pub async fn request(
        &self,
        operation: impl Into<String>,
        payload: Value,
        effect: EffectKind,
        deadline_ms: Option<u64>,
    ) -> Result<Value, ClientError> {
        if self.inner.closed.load(Ordering::Acquire) {
            return Err(ClientError::ConnectionEnded);
        }
        let message_id = self.new_id("request")?;
        let trace = Trace::new(self.new_id("trace")?, message_id.clone());
        let deadline_ms = deadline_ms.unwrap_or_else(|| now_ms() + DEFAULT_DEADLINE_MS);
        let envelope = WireEnvelope {
            protocol: PROTOCOL.into(),
            kind: WireKind::Request,
            sequence: self.inner.outbound_sequence.fetch_add(1, Ordering::AcqRel),
            message_id: message_id.clone(),
            reply_to: None,
            route_id: Some(Id::new(CONTROL_ROUTE).expect("control route id is valid")),
            route_epoch: Some(CONTROL_EPOCH),
            operation: Some(operation.into()),
            scope: Some(self.inner.scope.clone()),
            trace: Some(trace),
            deadline_ms: Some(deadline_ms),
            payload: Some(payload),
            error: None,
        };
        envelope.validate()?;
        let (sender, receiver) = oneshot::channel();
        self.inner
            .pending
            .lock()
            .await
            .insert(message_id.clone(), Pending { effect, sender });
        let write_result = {
            let mut writer = self.inner.writer.lock().await;
            write_json_frame(&mut *writer, &canonical_value(&envelope)?).await
        };
        if let Err(error) = write_result {
            self.inner.pending.lock().await.remove(&message_id);
            return match effect {
                EffectKind::Mutation => Err(ClientError::OutcomeUnknown),
                EffectKind::Query => Err(ClientError::Transport(error)),
            };
        }
        let wait_ms = deadline_ms.saturating_sub(now_ms()).max(1);
        match timeout(Duration::from_millis(wait_ms), receiver).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => match effect {
                EffectKind::Mutation => Err(ClientError::OutcomeUnknown),
                EffectKind::Query => Err(ClientError::ConnectionEnded),
            },
            Err(_) => {
                self.inner.pending.lock().await.remove(&message_id);
                let _ = self.cancel(message_id).await;
                match effect {
                    EffectKind::Mutation => Err(ClientError::OutcomeUnknown),
                    EffectKind::Query => Err(ClientError::TimedOut),
                }
            }
        }
    }

    pub async fn cancel(&self, target: Id) -> Result<(), ClientError> {
        let envelope = WireEnvelope {
            protocol: PROTOCOL.into(),
            kind: WireKind::Cancel,
            sequence: self.inner.outbound_sequence.fetch_add(1, Ordering::AcqRel),
            message_id: self.new_id("cancel")?,
            reply_to: Some(target),
            route_id: None,
            route_epoch: None,
            operation: None,
            scope: None,
            trace: None,
            deadline_ms: None,
            payload: None,
            error: None,
        };
        let mut writer = self.inner.writer.lock().await;
        write_json_frame(&mut *writer, &canonical_value(&envelope)?).await?;
        Ok(())
    }

    pub async fn describe(&self) -> Result<Value, ClientError> {
        self.request("server.describe", json!({}), EffectKind::Query, None)
            .await
    }

    pub async fn passthrough(&self, payload: Value) -> Result<Value, ClientError> {
        let response = self
            .request("passthrough", payload, EffectKind::Query, None)
            .await?;
        if response.get("mode").and_then(Value::as_str) != Some("passthrough") {
            return Err(ClientError::Protocol(
                "daemon returned an invalid passthrough response".into(),
            ));
        }
        Ok(response.get("payload").cloned().unwrap_or(Value::Null))
    }

    pub async fn effect_status(&self, effect_id: &Id) -> Result<EffectStatus, ClientError> {
        let value = self
            .request(
                "effects.status",
                json!({"effect_id": effect_id}),
                EffectKind::Query,
                None,
            )
            .await?;
        serde_json::from_value(value).map_err(|error| ClientError::Protocol(error.to_string()))
    }

    pub async fn subscribe_events<'a>(
        &'a self,
        consumer_id: Id,
        scope: Scope,
        topic_filter: impl Into<String>,
        after: Option<Cursor>,
        resume_store: Option<&'a dyn ResumeStore>,
    ) -> Result<DurableSubscription<'a, S>, ClientError> {
        if scope != self.inner.scope {
            return Err(ClientError::Protocol(
                "subscription scope does not match the authenticated session".into(),
            ));
        }
        let topic_filter = topic_filter.into();
        validate_topic_filter(&topic_filter)?;
        let stored = match resume_store {
            Some(store) => store.load()?,
            None => None,
        };
        if let Some(point) = &stored {
            validate_topic_filter(&point.topic_filter)?;
            if point.consumer_id != consumer_id
                || point.scope != scope
                || point.topic_filter != topic_filter
            {
                return Err(ClientError::Protocol(
                    "stored subscription identity does not match this request".into(),
                ));
            }
            if after.is_some_and(|cursor| cursor != point.cursor) {
                return Err(ClientError::Protocol(
                    "explicit cursor conflicts with durable resume state".into(),
                ));
            }
        }
        let effective_after = stored.as_ref().map(|point| point.cursor).or(after);
        let events = self.subscribe();
        let payload = json!({
            "consumer_id": consumer_id,
            "scope": scope,
            "topic_filter": topic_filter,
            "after": effective_after,
        });
        let value = self
            .request("events.subscribe", payload, EffectKind::Query, None)
            .await?;
        let snapshot: SubscriptionSnapshot = serde_json::from_value(value)
            .map_err(|error| ClientError::Protocol(error.to_string()))?;
        if snapshot.subscription_id != consumer_id {
            return Err(ClientError::Protocol(
                "daemon changed the durable consumer identity".into(),
            ));
        }
        for event in &snapshot.replay {
            validate_scoped_event(event)?;
        }
        let initial_cursor = match effective_after {
            Some(cursor) => cursor,
            None => Cursor::new(snapshot.cursor.epoch, 0)
                .map_err(|error| ClientError::Protocol(error.to_string()))?,
        };
        Ok(DurableSubscription {
            client: self,
            point: stored.unwrap_or(ResumePoint {
                consumer_id,
                scope,
                topic_filter,
                cursor: initial_cursor,
            }),
            replay: snapshot.replay.iter().cloned().collect(),
            snapshot,
            events,
            last_seen: initial_cursor,
            resume_store,
        })
    }

    pub async fn resume_events<'a>(
        &'a self,
        consumer_id: Id,
        scope: Scope,
        topic_filter: impl Into<String>,
        after: Cursor,
        resume_store: Option<&'a dyn ResumeStore>,
    ) -> Result<DurableSubscription<'a, S>, ClientError> {
        self.subscribe_events(consumer_id, scope, topic_filter, Some(after), resume_store)
            .await
    }

    pub async fn close(self) -> Result<(), ClientError> {
        self.inner.closed.store(true, Ordering::Release);
        let envelope = WireEnvelope {
            protocol: PROTOCOL.into(),
            kind: WireKind::Close,
            sequence: self.inner.outbound_sequence.fetch_add(1, Ordering::AcqRel),
            message_id: self.new_id("close")?,
            reply_to: None,
            route_id: None,
            route_epoch: None,
            operation: None,
            scope: None,
            trace: None,
            deadline_ms: None,
            payload: None,
            error: None,
        };
        let result = {
            let mut writer = self.inner.writer.lock().await;
            write_json_frame(&mut *writer, &canonical_value(&envelope)?).await
        };
        self.reader.abort();
        result.map_err(Into::into)
    }

    fn new_id(&self, prefix: &str) -> Result<Id, ClientError> {
        let value = self.inner.next_id.fetch_add(1, Ordering::AcqRel);
        Id::new(format!("{prefix}-{:016x}-{value:016x}", now_ms()))
            .map_err(|error| ClientError::Protocol(error.to_string()))
    }
}

async fn read_loop<S>(mut reader: ReadHalf<S>, inner: Arc<Inner<WriteHalf<S>>>)
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let mut expected_sequence = 1_u64;
    loop {
        let value: Value = match read_json_frame(&mut reader).await {
            Ok(value) => value,
            Err(_) => break,
        };
        let envelope: WireEnvelope = match serde_json::from_value(value) {
            Ok(envelope) => envelope,
            Err(_) => break,
        };
        if envelope.validate().is_err() || envelope.sequence != expected_sequence {
            break;
        }
        expected_sequence = match expected_sequence.checked_add(1) {
            Some(next) => next,
            None => break,
        };
        match envelope.kind {
            WireKind::Response => {
                if let Some(reply_to) = envelope.reply_to {
                    if let Some(pending) = inner.pending.lock().await.remove(&reply_to) {
                        let result = match envelope.error {
                            Some(error) => Err(ClientError::Remote(error)),
                            None => Ok(envelope.payload.unwrap_or(Value::Null)),
                        };
                        let _ = pending.sender.send(result);
                    }
                }
            }
            WireKind::Event => {
                let _ = inner.events.send(ClientEvent {
                    message_id: envelope.message_id,
                    operation: envelope.operation,
                    payload: envelope.payload,
                    error: envelope.error,
                });
            }
            WireKind::Ping => {}
            WireKind::Close => break,
            _ => break,
        }
    }
    inner.closed.store(true, Ordering::Release);
    for (_, pending) in std::mem::take(&mut *inner.pending.lock().await) {
        let result = match pending.effect {
            EffectKind::Mutation => Err(ClientError::OutcomeUnknown),
            EffectKind::Query => Err(ClientError::ConnectionEnded),
        };
        let _ = pending.sender.send(result);
    }
}

fn canonical_value<T: Serialize>(value: &T) -> Result<Value, ClientError> {
    serde_json::to_value(value).map_err(|error| ClientError::Protocol(error.to_string()))
}

fn validate_scoped_event(event: &ScopedEvent) -> Result<(), ClientError> {
    if event.delivery_count == 0 {
        return Err(ClientError::Protocol(
            "event delivery count must be at least one".into(),
        ));
    }
    if event.topic.is_empty() || event.topic.len() > 256 {
        return Err(ClientError::Protocol("event topic is invalid".into()));
    }
    if event.schema_name.is_empty() || event.schema_name.len() > 160 {
        return Err(ClientError::Protocol("event schema name is invalid".into()));
    }
    let payload = serde_json::to_vec(&event.payload)
        .map_err(|error| ClientError::Protocol(error.to_string()))?;
    if Digest::sha256(payload) != event.payload_digest {
        return Err(ClientError::Protocol(
            "event payload digest does not match its payload".into(),
        ));
    }
    Ok(())
}

fn validate_topic_filter(filter: &str) -> Result<(), ClientError> {
    let topic = filter.strip_suffix(".*").unwrap_or(filter);
    if topic.is_empty()
        || topic.len() > 256
        || !topic.bytes().enumerate().all(|(index, byte)| {
            if index == 0 {
                byte.is_ascii_lowercase()
            } else {
                byte.is_ascii_lowercase()
                    || byte.is_ascii_digit()
                    || matches!(byte, b'.' | b'_' | b'-')
            }
        })
    {
        return Err(ClientError::Protocol(
            "subscription topic filter is invalid".into(),
        ));
    }
    Ok(())
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    result
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

pub fn decode_event_chunk(value: Value) -> Result<EventChunk, ClientError> {
    serde_json::from_value(value).map_err(|error| ClientError::Protocol(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_protocol::MAX_FRAME_BYTES;
    use tokio::io::{duplex, AsyncWriteExt};

    #[tokio::test]
    async fn client_contract_vector_is_additive_and_rejects_oversized_header() {
        let vectors: Value = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../checks/hypermid/vectors/client_v1.json"
        )))
        .unwrap();
        let request = vectors["frames"]["request"]["value"].clone();
        let envelope: WireEnvelope = serde_json::from_value(request).unwrap();
        envelope.validate().unwrap();
        assert_eq!(envelope.sequence, 1);

        let additive: WireEnvelope =
            serde_json::from_value(vectors["additive_response"].clone()).unwrap();
        additive.validate().unwrap();
        assert_eq!(additive.sequence, 4);

        let (mut peer, mut client) = duplex(4);
        let writer = tokio::spawn(async move {
            peer.write_all(&((MAX_FRAME_BYTES as u32) + 1).to_be_bytes())
                .await
                .unwrap();
        });
        let error = read_json_frame::<_, Value>(&mut client).await.unwrap_err();
        assert!(matches!(
            error,
            TransportError::Protocol(hypermid_protocol::ProtocolError::FrameTooLarge { .. })
        ));
        writer.await.unwrap();
    }
}
