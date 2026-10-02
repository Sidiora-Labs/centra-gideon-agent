use std::{
    collections::HashSet,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::Arc,
};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use hypermid_protocol::{Digest, Id, Principal, PrincipalKind, Scope, PROTOCOL};
use hypermid_transport::{
    load_local_tls, ConnectionRecord, LocalTlsMaterial, PeerEvidence, TransportError,
};
use serde::{Deserialize, Serialize};
use tokio::net::TcpListener;
use tokio::sync::watch;
use tokio::task::JoinSet;
use tokio_rustls::TlsAcceptor;

use crate::server::{now_ms, serve_connection, ConnectionAuthorization, DaemonError, ServerState};

const MAX_REMOTE_AUTHORIZATION_LIFETIME_MS: u64 = 15 * 60 * 1_000;

#[derive(Clone, Debug)]
pub struct RemoteListenerConfig {
    pub listen_address: SocketAddr,
    pub connection_record: PathBuf,
    pub tls_directory: PathBuf,
    pub client_crl: Option<PathBuf>,
    pub authorization_file: PathBuf,
}

impl RemoteListenerConfig {
    pub fn from_options(
        listen_address: Option<SocketAddr>,
        connection_record: Option<PathBuf>,
        tls_directory: Option<PathBuf>,
        client_crl: Option<PathBuf>,
        authorization_file: Option<PathBuf>,
    ) -> Result<Option<Self>, DaemonError> {
        match (
            listen_address,
            connection_record,
            tls_directory,
            authorization_file,
        ) {
            (None, None, _, None) => Ok(None),
            (
                Some(listen_address),
                Some(connection_record),
                Some(tls_directory),
                Some(authorization_file),
            ) => Ok(Some(Self {
                listen_address,
                connection_record,
                tls_directory,
                client_crl,
                authorization_file,
            })),
            _ => Err(configuration(
                "remote listening requires an address, separate connection record, TLS directory, and authorization file",
            )),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RemoteAuthorization {
    pub principal_id: Id,
    pub scope: Scope,
    pub operations: Vec<String>,
    pub expires_at_ms: u64,
}

impl RemoteAuthorization {
    pub fn load(path: &Path, state: &ServerState, now_ms: u64) -> Result<Self, DaemonError> {
        ensure_owner_only_file(path, "remote authorization")?;
        let bytes = std::fs::read(path)?;
        let authorization: Self = serde_json::from_slice(&bytes)
            .map_err(|_| configuration("remote authorization JSON is invalid"))?;
        authorization.validate(state, now_ms)?;
        Ok(authorization)
    }

    fn validate(&self, state: &ServerState, now_ms: u64) -> Result<(), DaemonError> {
        if self.expires_at_ms <= now_ms
            || self.expires_at_ms.saturating_sub(now_ms) > MAX_REMOTE_AUTHORIZATION_LIFETIME_MS
        {
            return Err(configuration(
                "remote authorization must be unexpired and no more than fifteen minutes long",
            ));
        }
        if self.operations.is_empty() || self.operations.len() > 256 {
            return Err(configuration(
                "remote authorization operations are empty or exceed the bound",
            ));
        }
        let available = state.operations().into_iter().collect::<HashSet<_>>();
        let mut unique = HashSet::new();
        for operation in &self.operations {
            if !available.contains(operation) || !unique.insert(operation) {
                return Err(configuration(
                    "remote authorization contains an unavailable or duplicate operation",
                ));
            }
        }
        Ok(())
    }

    fn connection_authorization(&self) -> ConnectionAuthorization {
        ConnectionAuthorization {
            principal: Principal {
                id: self.principal_id.clone(),
                kind: PrincipalKind::Device,
                scopes: self.operations.clone(),
                module_id: None,
                spawn_generation: None,
            },
            scope: self.scope.clone(),
            expires_at_ms: self.expires_at_ms,
        }
    }
}

pub struct RemoteListener {
    config: RemoteListenerConfig,
    state: Arc<ServerState>,
    secret: [u8; 32],
}

impl RemoteListener {
    pub fn new(config: RemoteListenerConfig, state: Arc<ServerState>, secret: [u8; 32]) -> Self {
        Self {
            config,
            state,
            secret,
        }
    }

    pub async fn bind(self) -> Result<BoundRemoteListener, DaemonError> {
        validate_tls_directory(
            &self.config.tls_directory,
            self.config.client_crl.as_deref(),
        )?;
        let authorization =
            RemoteAuthorization::load(&self.config.authorization_file, &self.state, now_ms()?)?;
        let tls = load_local_tls(
            &self.config.tls_directory,
            self.config.client_crl.as_deref(),
        )?;
        let listener = TcpListener::bind(self.config.listen_address).await?;
        let address = listener.local_addr()?;
        store_connection_record(
            &self.config.connection_record,
            address,
            &tls,
            self.secret,
            &authorization.principal_id,
            authorization.expires_at_ms,
        )?;
        Ok(BoundRemoteListener {
            listener,
            acceptor: TlsAcceptor::from(Arc::clone(&tls.server_config)),
            state: self.state,
            secret: self.secret,
            authorization: authorization.connection_authorization(),
        })
    }

    pub async fn run(self) -> Result<(), DaemonError> {
        self.bind().await?.run().await
    }
}

pub struct BoundRemoteListener {
    listener: TcpListener,
    acceptor: TlsAcceptor,
    state: Arc<ServerState>,
    secret: [u8; 32],
    authorization: ConnectionAuthorization,
}

impl BoundRemoteListener {
    pub fn local_addr(&self) -> Result<SocketAddr, DaemonError> {
        self.listener.local_addr().map_err(Into::into)
    }

    pub async fn serve_one(&self) -> Result<(), DaemonError> {
        let (stream, _) = self.listener.accept().await?;
        let stream = self
            .acceptor
            .accept(stream)
            .await
            .map_err(|error| DaemonError::Transport(TransportError::Tls(error.to_string())))?;
        let certificate = stream
            .get_ref()
            .1
            .peer_certificates()
            .and_then(|certificates| certificates.first())
            .ok_or_else(|| {
                DaemonError::Transport(TransportError::Authentication(
                    "remote TLS peer supplied no leaf certificate",
                ))
            })?;
        let peer = PeerEvidence::TlsIdentity {
            certificate_sha256: Digest::sha256(certificate.as_ref()),
        };
        let result = serve_connection(
            stream,
            Arc::clone(&self.state),
            self.secret,
            peer,
            self.authorization.clone(),
        )
        .await;
        match result {
            Err(error) if error.is_disconnect() => Ok(()),
            result => result,
        }
    }

    pub async fn run(self) -> Result<(), DaemonError> {
        let (_shutdown, receiver) = watch::channel(false);
        self.run_until(receiver).await
    }

    pub async fn run_until(self, mut shutdown: watch::Receiver<bool>) -> Result<(), DaemonError> {
        let mut connections = JoinSet::new();
        loop {
            tokio::select! {
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                }
                accepted = self.listener.accept() => {
                    let (stream, _) = accepted?;
                    let acceptor = self.acceptor.clone();
                    let state = Arc::clone(&self.state);
                    let secret = self.secret;
                    let authorization = self.authorization.clone();
                    connections.spawn(async move {
                        let result = async {
                            let stream = acceptor.accept(stream).await.map_err(|error| {
                                DaemonError::Transport(TransportError::Tls(error.to_string()))
                            })?;
                            let certificate = stream
                                .get_ref()
                                .1
                                .peer_certificates()
                                .and_then(|certificates| certificates.first())
                                .ok_or_else(|| {
                                    DaemonError::Transport(TransportError::Authentication(
                                        "remote TLS peer supplied no leaf certificate",
                                    ))
                                })?;
                            let certificate_sha256 = Digest::sha256(certificate.as_ref());
                            serve_connection(
                                stream,
                                state,
                                secret,
                                PeerEvidence::TlsIdentity { certificate_sha256 },
                                authorization,
                            )
                            .await
                        }
                        .await;
                        if let Err(error) = result {
                            if !error.is_disconnect() {
                                eprintln!("hypermid-daemon remote connection: {error}");
                            }
                        }
                    });
                }
                _ = connections.join_next(), if !connections.is_empty() => {}
            }
        }
        connections.abort_all();
        while connections.join_next().await.is_some() {}
        Ok(())
    }
}

fn store_connection_record(
    path: &Path,
    address: SocketAddr,
    tls: &LocalTlsMaterial,
    secret: [u8; 32],
    credential_id: &Id,
    expires_ms: u64,
) -> Result<(), DaemonError> {
    ConnectionRecord {
        endpoint: format!("tcp://{address}"),
        protocol: PROTOCOL.into(),
        credential_id: credential_id.to_string(),
        secret_b64: BASE64.encode(secret),
        server_name: tls.server_name.clone(),
        ca_certificate: tls.ca_certificate.clone(),
        client_certificate: tls.client_certificate.clone(),
        client_private_key: tls.client_private_key.clone(),
        expires_ms,
    }
    .store(path)?;
    Ok(())
}

fn validate_tls_directory(directory: &Path, client_crl: Option<&Path>) -> Result<(), DaemonError> {
    ensure_owner_only_directory(directory)?;
    for name in [
        "ca.pem",
        "ca-key.pem",
        "server.pem",
        "server-key.pem",
        "client.pem",
        "client-key.pem",
        "server-name",
    ] {
        ensure_owner_only_file(&directory.join(name), "remote TLS material")?;
    }
    if let Some(path) = client_crl {
        ensure_owner_only_file(path, "remote client revocation list")?;
    }
    Ok(())
}

#[cfg(unix)]
fn ensure_owner_only_directory(path: &Path) -> Result<(), DaemonError> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_dir()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.permissions().mode() & 0o077 != 0
    {
        return Err(configuration(
            "remote TLS directory must be an owner-only directory",
        ));
    }
    Ok(())
}

#[cfg(not(unix))]
fn ensure_owner_only_directory(path: &Path) -> Result<(), DaemonError> {
    if !std::fs::symlink_metadata(path)?.file_type().is_dir() {
        return Err(configuration("remote TLS directory must be a directory"));
    }
    Ok(())
}

#[cfg(unix)]
fn ensure_owner_only_file(path: &Path, label: &str) -> Result<(), DaemonError> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.permissions().mode() & 0o777 != 0o600
    {
        return Err(configuration(&format!(
            "{label} must be a current-owner mode-0600 regular file"
        )));
    }
    Ok(())
}

#[cfg(not(unix))]
fn ensure_owner_only_file(path: &Path, label: &str) -> Result<(), DaemonError> {
    if !std::fs::symlink_metadata(path)?.file_type().is_file() {
        return Err(configuration(&format!("{label} must be a regular file")));
    }
    Ok(())
}

fn configuration(message: &str) -> DaemonError {
    DaemonError::Configuration(message.into())
}

#[cfg(test)]
mod remote_listener_tests {
    use super::*;
    use hypermid_client::connect_record;
    use hypermid_core::capability::CapabilityOperation;
    use hypermid_protocol::ConnectionClass;
    use serde_json::json;
    use std::collections::BTreeSet;
    use tokio::time::{timeout, Duration};

    use crate::enrollment::LocalOperatorEnrollment;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope() -> Scope {
        Scope::new(id("owner-1"), id("project-1"), Some(id("workspace-1")))
    }

    #[cfg(unix)]
    fn write_authorization(path: &Path, authorization: &RemoteAuthorization) {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;

        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(path)
            .unwrap();
        file.write_all(&serde_json::to_vec(authorization).unwrap())
            .unwrap();
        file.sync_all().unwrap();
    }

    #[cfg(not(unix))]
    fn write_authorization(path: &Path, authorization: &RemoteAuthorization) {
        std::fs::write(path, serde_json::to_vec(authorization).unwrap()).unwrap();
    }

    #[tokio::test]
    async fn remote_listener_runs_real_loopback_mtls_with_exact_short_scope() {
        let root = tempfile::tempdir().unwrap();
        let tls_directory = root.path().join("tls");
        hypermid_transport::provision_local_tls(&tls_directory, "hypermid.local").unwrap();
        let enrollment = LocalOperatorEnrollment {
            credential_id: id("local-credential-1"),
            scope: scope(),
            capability_id: id("local-capability-1"),
            operations: BTreeSet::from([CapabilityOperation::Read]),
            resources: BTreeSet::from([id("memory-service")]),
            expires_at_ms: now_ms().unwrap() + 60_000,
        };
        let state = Arc::new(
            ServerState::new(
                "daemon-remote".into(),
                &root.path().join("state"),
                &enrollment,
                None,
            )
            .unwrap(),
        );
        let authorization_file = root.path().join("remote-authorization.json");
        let authorization = RemoteAuthorization {
            principal_id: id("remote-device-1"),
            scope: scope(),
            operations: vec!["passthrough".into()],
            expires_at_ms: now_ms().unwrap() + 60_000,
        };
        write_authorization(&authorization_file, &authorization);
        let record = root.path().join("remote-connection.json");
        let config = RemoteListenerConfig {
            listen_address: "127.0.0.1:0".parse().unwrap(),
            connection_record: record.clone(),
            tls_directory: tls_directory.clone(),
            client_crl: None,
            authorization_file,
        };
        let secret = [41_u8; 32];
        let bound = RemoteListener::new(config, state, secret)
            .bind()
            .await
            .unwrap();
        assert!(bound.local_addr().unwrap().ip().is_loopback());
        let server = tokio::spawn(async move { bound.serve_one().await });

        let client = connect_record(&record, scope(), ConnectionClass::Device)
            .await
            .unwrap();
        assert_eq!(client.session().principal.id, id("remote-device-1"));
        assert_eq!(client.session().principal.scopes, vec!["passthrough"]);
        assert_eq!(
            client
                .passthrough(json!({"remote": "encrypted"}))
                .await
                .unwrap(),
            json!({"remote": "encrypted"})
        );
        client.close().await.unwrap();
        timeout(Duration::from_secs(5), server)
            .await
            .expect("remote listener did not terminate")
            .unwrap()
            .unwrap();

        assert!(
            RemoteListenerConfig::from_options(None, None, Some(tls_directory), None, None,)
                .unwrap()
                .is_none()
        );
    }
}
