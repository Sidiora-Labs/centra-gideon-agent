pub mod frame;
pub mod handshake;

pub use frame::{
    chunk_event, parse_json, Envelope, EventChunk, EventReassembler, MessageKind, ProtocolError,
    MAX_CHUNK_BYTES, MAX_FRAME_BYTES, PROTOCOL,
};
pub use handshake::{
    authentication_proof, verify_authentication_proof, AuthenticationMethod, ClientAuthentication,
    ClientHello, CompatibilityRefusal, ConnectionClass, ConnectionLimits, Principal, PrincipalKind,
    ServerChallenge, SessionAccepted,
};

pub use hypermid_contracts::{Cursor, Digest, Error, Id, Scope, Trace};
