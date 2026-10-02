pub mod cancellation;
pub mod deadline;
pub mod federation;
pub mod flow;
pub mod framing;
pub mod handshake;
pub mod local;
pub mod relay;
pub mod tls;

pub use cancellation::{RequestLifecycle, TerminalKind};
pub use deadline::AbsoluteDeadline;
pub use flow::{ControlQueue, FlowError, RouteCredits};
pub use framing::{read_json_frame, write_json_frame, FrameCodec, SessionSequence};
pub use handshake::{AuthenticatedSession, PeerEvidence, ServerHandshake, ServerHandshakeConfig};
pub use local::{ConnectionRecord, LocalEndpoint};
pub use tls::{
    client_config, load_local_tls, provision_local_tls, server_config, server_config_with_crls,
    LocalTlsMaterial, TlsClientPolicy, TlsServerPolicy,
};

#[derive(Debug, thiserror::Error)]
pub enum TransportError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Protocol(#[from] hypermid_protocol::ProtocolError),
    #[error("TLS configuration error: {0}")]
    Tls(String),
    #[error("authentication failed: {0}")]
    Authentication(&'static str),
    #[error("authentication challenge expired")]
    ChallengeExpired,
    #[error("frame sequence {received} is invalid; expected {expected}")]
    InvalidSequence { expected: u64, received: u64 },
}
