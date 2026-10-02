use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use rcgen::{
    BasicConstraints, CertificateParams, DistinguishedName, DnType, ExtendedKeyUsagePurpose, IsCa,
    KeyPair,
};
use rustls::server::WebPkiClientVerifier;
use rustls::{ClientConfig, RootCertStore, ServerConfig};
use rustls_pki_types::{
    CertificateDer, CertificateRevocationListDer, PrivateKeyDer, PrivatePkcs8KeyDer,
};

use crate::TransportError;

#[derive(Clone, Debug)]
pub struct TlsServerPolicy {
    pub required_scopes: Vec<String>,
    pub enabled: bool,
}

impl TlsServerPolicy {
    pub fn authorize<'a>(
        &self,
        granted_scopes: impl IntoIterator<Item = &'a str>,
    ) -> Result<(), TransportError> {
        if !self.enabled {
            return Err(TransportError::Authentication(
                "external listener is disabled",
            ));
        }
        let granted: std::collections::HashSet<&str> = granted_scopes.into_iter().collect();
        if !self
            .required_scopes
            .iter()
            .all(|scope| granted.contains(scope.as_str()))
        {
            return Err(TransportError::Authentication(
                "principal lacks an external listener scope",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct TlsClientPolicy {
    pub server_name: String,
}

#[derive(Debug)]
pub struct LocalTlsMaterial {
    pub ca_certificate: PathBuf,
    pub ca_private_key: PathBuf,
    pub server_certificate: PathBuf,
    pub server_private_key: PathBuf,
    pub client_certificate: PathBuf,
    pub client_private_key: PathBuf,
    pub server_name: String,
    pub server_config: Arc<ServerConfig>,
    pub client_config: Arc<ClientConfig>,
}

pub fn provision_local_tls(
    directory: &Path,
    server_name: &str,
) -> Result<LocalTlsMaterial, TransportError> {
    if server_name.is_empty() || server_name.len() > 253 {
        return Err(TransportError::Tls("invalid local TLS server name".into()));
    }
    create_private_directory(directory)?;

    let mut ca_params = CertificateParams::new(Vec::<String>::new())
        .map_err(|error| TransportError::Tls(error.to_string()))?;
    ca_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    ca_params.distinguished_name = distinguished_name("Hypermid Local CA");
    let ca_key = KeyPair::generate().map_err(|error| TransportError::Tls(error.to_string()))?;
    let ca = ca_params
        .self_signed(&ca_key)
        .map_err(|error| TransportError::Tls(error.to_string()))?;

    let mut server_params = CertificateParams::new(vec![server_name.to_owned()])
        .map_err(|error| TransportError::Tls(error.to_string()))?;
    server_params.distinguished_name = distinguished_name("Hypermid Local Daemon");
    server_params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    let server_key = KeyPair::generate().map_err(|error| TransportError::Tls(error.to_string()))?;
    let server = server_params
        .signed_by(&server_key, &ca, &ca_key)
        .map_err(|error| TransportError::Tls(error.to_string()))?;

    let mut client_params = CertificateParams::new(Vec::<String>::new())
        .map_err(|error| TransportError::Tls(error.to_string()))?;
    client_params.distinguished_name = distinguished_name("Hypermid Local Client");
    client_params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ClientAuth];
    let client_key = KeyPair::generate().map_err(|error| TransportError::Tls(error.to_string()))?;
    let client = client_params
        .signed_by(&client_key, &ca, &ca_key)
        .map_err(|error| TransportError::Tls(error.to_string()))?;

    let ca_certificate = directory.join("ca.pem");
    let ca_private_key = directory.join("ca-key.pem");
    let server_certificate = directory.join("server.pem");
    let server_private_key = directory.join("server-key.pem");
    let client_certificate = directory.join("client.pem");
    let client_private_key = directory.join("client-key.pem");
    let server_name_path = directory.join("server-name");
    write_private(&ca_certificate, ca.pem().as_bytes())?;
    write_private(&ca_private_key, ca_key.serialize_pem().as_bytes())?;
    write_private(&server_certificate, server.pem().as_bytes())?;
    write_private(&server_private_key, server_key.serialize_pem().as_bytes())?;
    write_private(&client_certificate, client.pem().as_bytes())?;
    write_private(&client_private_key, client_key.serialize_pem().as_bytes())?;
    write_private(&server_name_path, server_name.as_bytes())?;

    let mut roots = RootCertStore::empty();
    roots
        .add(ca.der().clone())
        .map_err(|error| TransportError::Tls(error.to_string()))?;
    let server_config = server_config(
        roots.clone(),
        vec![server.der().clone()],
        PrivatePkcs8KeyDer::from(server_key.serialize_der()).into(),
    )?;
    let client_config = client_config(
        roots,
        vec![client.der().clone()],
        PrivatePkcs8KeyDer::from(client_key.serialize_der()).into(),
    )?;
    Ok(LocalTlsMaterial {
        ca_certificate,
        ca_private_key,
        server_certificate,
        server_private_key,
        client_certificate,
        client_private_key,
        server_name: server_name.to_owned(),
        server_config: Arc::new(server_config),
        client_config: Arc::new(client_config),
    })
}

pub fn load_local_tls(
    directory: &Path,
    client_crl: Option<&Path>,
) -> Result<LocalTlsMaterial, TransportError> {
    let ca_certificate = directory.join("ca.pem");
    let ca_private_key = directory.join("ca-key.pem");
    let server_certificate = directory.join("server.pem");
    let server_private_key = directory.join("server-key.pem");
    let client_certificate = directory.join("client.pem");
    let client_private_key = directory.join("client-key.pem");
    let server_name = std::fs::read_to_string(directory.join("server-name"))?;
    if server_name.is_empty()
        || server_name.len() > 253
        || server_name.contains(char::is_whitespace)
    {
        return Err(TransportError::Tls(
            "stored local TLS server name is invalid".into(),
        ));
    }

    let ca = load_certificates(&ca_certificate)?;
    if ca.len() != 1 {
        return Err(TransportError::Tls(
            "local TLS CA file must contain exactly one certificate".into(),
        ));
    }
    let mut roots = RootCertStore::empty();
    roots
        .add(ca[0].clone())
        .map_err(|error| TransportError::Tls(error.to_string()))?;
    let crls = match client_crl {
        Some(path) => load_crls(path)?,
        None => Vec::new(),
    };
    let server_config = server_config_with_crls(
        roots.clone(),
        load_certificates(&server_certificate)?,
        load_private_key(&server_private_key)?,
        crls,
    )?;
    let client_config = client_config(
        roots,
        load_certificates(&client_certificate)?,
        load_private_key(&client_private_key)?,
    )?;
    Ok(LocalTlsMaterial {
        ca_certificate,
        ca_private_key,
        server_certificate,
        server_private_key,
        client_certificate,
        client_private_key,
        server_name,
        server_config: Arc::new(server_config),
        client_config: Arc::new(client_config),
    })
}

fn load_certificates(path: &Path) -> Result<Vec<CertificateDer<'static>>, TransportError> {
    let file = std::fs::File::open(path)?;
    rustls_pemfile::certs(&mut std::io::BufReader::new(file))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| TransportError::Tls(error.to_string()))
}

fn load_private_key(path: &Path) -> Result<PrivateKeyDer<'static>, TransportError> {
    let file = std::fs::File::open(path)?;
    rustls_pemfile::private_key(&mut std::io::BufReader::new(file))
        .map_err(|error| TransportError::Tls(error.to_string()))?
        .ok_or_else(|| TransportError::Tls("TLS private key file contains no key".into()))
}

fn load_crls(path: &Path) -> Result<Vec<CertificateRevocationListDer<'static>>, TransportError> {
    let file = std::fs::File::open(path)?;
    let crls = rustls_pemfile::crls(&mut std::io::BufReader::new(file))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| TransportError::Tls(error.to_string()))?;
    if crls.is_empty() {
        return Err(TransportError::Tls(
            "client CRL file contains no CRL".into(),
        ));
    }
    Ok(crls)
}

fn distinguished_name(common_name: &str) -> DistinguishedName {
    let mut name = DistinguishedName::new();
    name.push(DnType::CommonName, common_name);
    name
}

#[cfg(unix)]
fn create_private_directory(path: &Path) -> Result<(), TransportError> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::create_dir_all(path)?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    Ok(())
}

#[cfg(not(unix))]
fn create_private_directory(path: &Path) -> Result<(), TransportError> {
    std::fs::create_dir_all(path)?;
    Ok(())
}

#[cfg(unix)]
fn write_private(path: &Path, bytes: &[u8]) -> Result<(), TransportError> {
    use std::{io::Write, os::unix::fs::OpenOptionsExt};
    let mut file = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn write_private(path: &Path, bytes: &[u8]) -> Result<(), TransportError> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

pub fn server_config(
    roots: RootCertStore,
    certificate_chain: Vec<CertificateDer<'static>>,
    private_key: PrivateKeyDer<'static>,
) -> Result<ServerConfig, TransportError> {
    server_config_with_crls(roots, certificate_chain, private_key, Vec::new())
}

pub fn server_config_with_crls(
    roots: RootCertStore,
    certificate_chain: Vec<CertificateDer<'static>>,
    private_key: PrivateKeyDer<'static>,
    crls: Vec<CertificateRevocationListDer<'static>>,
) -> Result<ServerConfig, TransportError> {
    let mut verifier = WebPkiClientVerifier::builder(Arc::new(roots));
    if !crls.is_empty() {
        verifier = verifier.with_crls(crls);
    }
    let verifier = verifier
        .build()
        .map_err(|error| TransportError::Tls(error.to_string()))?;
    ServerConfig::builder_with_protocol_versions(&[&rustls::version::TLS13])
        .with_client_cert_verifier(verifier)
        .with_single_cert(certificate_chain, private_key)
        .map_err(|error| TransportError::Tls(error.to_string()))
}

pub fn client_config(
    roots: RootCertStore,
    certificate_chain: Vec<CertificateDer<'static>>,
    private_key: PrivateKeyDer<'static>,
) -> Result<ClientConfig, TransportError> {
    ClientConfig::builder_with_protocol_versions(&[&rustls::version::TLS13])
        .with_root_certificates(roots)
        .with_client_auth_cert(certificate_chain, private_key)
        .map_err(|error| TransportError::Tls(error.to_string()))
}
