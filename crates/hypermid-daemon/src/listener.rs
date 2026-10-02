use std::{collections::BTreeSet, path::PathBuf, sync::Arc};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use hypermid_contracts::{Id, Scope};
use hypermid_core::capability::CapabilityOperation;
use hypermid_protocol::PROTOCOL;
use hypermid_transport::{
    load_local_tls, provision_local_tls, ConnectionRecord, LocalEndpoint, LocalTlsMaterial,
    TransportError,
};
use tokio::{sync::watch, task::JoinSet};
use tokio_rustls::TlsAcceptor;

use crate::{
    enrollment::LocalOperatorEnrollment,
    remote_listener::{RemoteListener, RemoteListenerConfig},
    server::{serve_connection, ConnectionAuthorization, DaemonError, ServerState},
};

#[derive(Clone, Debug)]
pub struct DaemonConfig {
    pub socket: PathBuf,
    pub connection_record: PathBuf,
    pub tls_directory: Option<PathBuf>,
    pub client_crl: Option<PathBuf>,
    pub mcp_config: Option<PathBuf>,
    pub local_owner_id: Id,
    pub local_credential_id: Id,
    pub local_project_id: Id,
    pub local_workspace_id: Option<Id>,
    pub local_capability_id: Id,
    pub local_capability_operations: BTreeSet<CapabilityOperation>,
    pub local_capability_resources: BTreeSet<Id>,
    pub local_capability_expires_ms: u64,
    pub remote: Option<RemoteListenerConfig>,
}

pub struct Daemon {
    config: DaemonConfig,
    state: Arc<ServerState>,
    secret: [u8; 32],
    credential_id: Id,
    authorization: ConnectionAuthorization,
}

impl Daemon {
    pub fn new(config: DaemonConfig) -> Result<Self, DaemonError> {
        if config
            .remote
            .as_ref()
            .is_some_and(|remote| remote.connection_record == config.connection_record)
        {
            return Err(DaemonError::Configuration(
                "remote connection record must differ from local connection record".into(),
            ));
        }
        if config.local_capability_operations.is_empty()
            || config.local_capability_resources.is_empty()
            || config.local_capability_expires_ms <= crate::server::now_ms()?
        {
            return Err(DaemonError::Configuration(
                "local operator enrollment must be nonempty and unexpired".into(),
            ));
        }
        let mut secret = [0_u8; 32];
        getrandom::fill(&mut secret).map_err(|error| DaemonError::Random(error.to_string()))?;
        let record_parent = config
            .connection_record
            .parent()
            .ok_or_else(|| DaemonError::Configuration("connection record needs a parent".into()))?;
        let state_root = record_parent.join("state");
        std::fs::create_dir_all(&state_root)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&state_root, std::fs::Permissions::from_mode(0o700))?;
        }
        let credential_id = config.local_credential_id.clone();
        let enrollment = LocalOperatorEnrollment {
            credential_id: credential_id.clone(),
            scope: Scope::new(
                config.local_owner_id.clone(),
                config.local_project_id.clone(),
                config.local_workspace_id.clone(),
            ),
            capability_id: config.local_capability_id.clone(),
            operations: config.local_capability_operations.clone(),
            resources: config.local_capability_resources.clone(),
            expires_at_ms: config.local_capability_expires_ms,
        };
        let state = Arc::new(ServerState::new(
            random_id("daemon")?,
            &state_root,
            &enrollment,
            config.mcp_config.as_deref(),
        )?);
        let binding = enrollment.binding();
        let authorization = ConnectionAuthorization {
            principal: hypermid_protocol::Principal {
                id: binding.principal_id,
                kind: hypermid_protocol::PrincipalKind::LocalUser,
                scopes: state.operations(),
                module_id: None,
                spawn_generation: None,
            },
            scope: binding.authorized_scope,
            expires_at_ms: enrollment.expires_at_ms,
        };
        state
            .control()
            .diagnostics()
            .register_secret(BASE64.encode(secret));
        Ok(Self {
            config,
            state,
            secret,
            credential_id,
            authorization,
        })
    }

    pub fn state(&self) -> &Arc<ServerState> {
        &self.state
    }

    #[cfg(unix)]
    pub async fn run(self) -> Result<(), DaemonError> {
        use std::os::unix::fs::MetadataExt;

        remove_stale_socket(&self.config.socket)?;
        let endpoint = LocalEndpoint::bind_unix(&self.config.socket).await?;
        let tls = self.provision_tls()?;
        let acceptor = TlsAcceptor::from(Arc::clone(&tls.server_config));
        let record = self.connection_record(&tls)?;
        let remote = match self.config.remote.clone() {
            Some(config) => Some(
                RemoteListener::new(config, Arc::clone(&self.state), self.secret)
                    .bind()
                    .await?,
            ),
            None => None,
        };
        record.store(&self.config.connection_record)?;
        let owner_uid = std::fs::metadata(&self.config.connection_record)?.uid();

        let LocalEndpoint::Unix(listener) = endpoint else {
            return Err(DaemonError::Configuration(
                "Unix daemon did not bind a Unix endpoint".into(),
            ));
        };

        let retirement_state = Arc::clone(&self.state);
        let (shutdown_tx, shutdown) = watch::channel(false);
        let mut local_shutdown = shutdown.clone();
        let local = async move {
            let mut connections = JoinSet::new();
            loop {
                tokio::select! {
                    changed = local_shutdown.changed() => {
                        if changed.is_err() || *local_shutdown.borrow() {
                            break;
                        }
                    }
                    accepted = listener.accept() => {
                        let (stream, _) = accepted?;
                        let credential = stream.peer_cred()?;
                        if credential.uid() != owner_uid {
                            continue;
                        }
                        let state = Arc::clone(&self.state);
                        let secret = self.secret;
                        let authorization = self.authorization.clone();
                        let acceptor = acceptor.clone();
                        connections.spawn(async move {
                            let result = async {
                                let stream = acceptor.accept(stream).await.map_err(|error| {
                                    DaemonError::Transport(TransportError::Tls(error.to_string()))
                                })?;
                                serve_connection(
                                    stream,
                                    state,
                                    secret,
                                    hypermid_transport::PeerEvidence::UnixOwner {
                                        uid: credential.uid(),
                                    },
                                    authorization,
                                )
                                .await
                            }
                            .await;
                            if let Err(error) = result {
                                if !error.is_disconnect() {
                                    eprintln!("hypermid-daemon connection: {error}");
                                }
                            }
                        });
                    }
                    _ = connections.join_next(), if !connections.is_empty() => {}
                }
            }
            connections.abort_all();
            while connections.join_next().await.is_some() {}
            Ok::<(), DaemonError>(())
        };
        let listeners = async move {
            if let Some(remote) = remote {
                tokio::try_join!(local, remote.run_until(shutdown))?;
                Ok(())
            } else {
                local.await
            }
        };
        tokio::pin!(listeners);
        let result = tokio::select! {
            result = &mut listeners => result,
            signal = shutdown_signal() => {
                signal?;
                let _ = shutdown_tx.send(true);
                listeners.await
            }
        };
        let retirement = retire_state(&retirement_state).await;
        result?;
        retirement
    }

    #[cfg(not(unix))]
    pub async fn run(self) -> Result<(), DaemonError> {
        let endpoint = LocalEndpoint::bind_loopback().await?;
        let LocalEndpoint::Loopback(listener) = endpoint;
        let address = listener.local_addr()?;
        let tls = self.provision_tls()?;
        let acceptor = TlsAcceptor::from(Arc::clone(&tls.server_config));
        let mut record = self.connection_record(&tls)?;
        record.endpoint = format!("tcp://{address}");
        record.store(&self.config.connection_record)?;
        let retirement_state = Arc::clone(&self.state);
        let mut connections = JoinSet::new();
        loop {
            tokio::select! {
                result = tokio::signal::ctrl_c() => {
                    result?;
                    break;
                }
                accepted = listener.accept() => {
                    let (stream, address) = accepted?;
                    if !address.ip().is_loopback() {
                        continue;
                    }
                    let state = Arc::clone(&self.state);
                    let secret = self.secret;
                    let authorization = self.authorization.clone();
                    let acceptor = acceptor.clone();
                    connections.spawn(async move {
                        if let Ok(stream) = acceptor.accept(stream).await {
                            let _ = serve_connection(
                                stream,
                                state,
                                secret,
                                hypermid_transport::PeerEvidence::WindowsOwner {
                                    sid: "connection-record-owner".into(),
                                },
                                authorization,
                            )
                            .await;
                        }
                    });
                }
                _ = connections.join_next(), if !connections.is_empty() => {}
            }
        }
        connections.abort_all();
        while connections.join_next().await.is_some() {}
        retire_state(&retirement_state).await
    }

    fn provision_tls(&self) -> Result<LocalTlsMaterial, DaemonError> {
        if let Some(directory) = &self.config.tls_directory {
            return load_local_tls(directory, self.config.client_crl.as_deref())
                .map_err(Into::into);
        }
        if self.config.client_crl.is_some() {
            return Err(DaemonError::Configuration(
                "client CRL requires a preprovisioned TLS directory".into(),
            ));
        }
        let parent =
            self.config.connection_record.parent().ok_or_else(|| {
                DaemonError::Configuration("connection record needs a parent".into())
            })?;
        let directory = parent.join(format!("tls-{}", self.state.daemon_instance_id()));
        provision_local_tls(&directory, "hypermid.local").map_err(Into::into)
    }

    fn connection_record(&self, tls: &LocalTlsMaterial) -> Result<ConnectionRecord, DaemonError> {
        Ok(ConnectionRecord {
            endpoint: self.config.socket.to_string_lossy().into_owned(),
            protocol: PROTOCOL.into(),
            credential_id: self.credential_id.to_string(),
            secret_b64: BASE64.encode(self.secret),
            server_name: tls.server_name.clone(),
            ca_certificate: tls.ca_certificate.clone(),
            client_certificate: tls.client_certificate.clone(),
            client_private_key: tls.client_private_key.clone(),
            expires_ms: self.authorization.expires_at_ms,
        })
    }
}

#[cfg(unix)]
async fn shutdown_signal() -> Result<(), DaemonError> {
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    tokio::select! {
        result = tokio::signal::ctrl_c() => result.map_err(DaemonError::from),
        _ = terminate.recv() => Ok(()),
    }
}

async fn retire_state(state: &ServerState) -> Result<(), DaemonError> {
    if let Some(mcp) = state.mcp_control() {
        mcp.shutdown(crate::server::now_ms()?)
            .await
            .map_err(|error| DaemonError::Mcp(error.to_string()))?;
    }
    Ok(())
}

fn random_id(prefix: &str) -> Result<String, DaemonError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|error| DaemonError::Random(error.to_string()))?;
    let mut value = String::with_capacity(prefix.len() + 33);
    value.push_str(prefix);
    value.push('-');
    for byte in bytes {
        use std::fmt::Write as _;
        write!(value, "{byte:02x}").expect("writing to String cannot fail");
    }
    Ok(value)
}

#[cfg(unix)]
fn remove_stale_socket(path: &std::path::Path) -> Result<(), DaemonError> {
    use std::os::unix::fs::FileTypeExt;

    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => {
            std::fs::remove_file(path)?;
            Ok(())
        }
        Ok(_) => Err(DaemonError::Configuration(format!(
            "socket path {} exists and is not a socket",
            path.display()
        ))),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}
