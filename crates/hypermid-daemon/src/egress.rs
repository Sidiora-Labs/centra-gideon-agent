use std::collections::BTreeSet;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::time::{SystemTime, UNIX_EPOCH};

use hypermid_contracts::{Id, Scope};
use hypermid_transport::AuthenticatedSession;
use thiserror::Error;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

pub const EGRESS_DENIED: &str = "HYPERMID_EGRESS_DENIED";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum AddressClass {
    Public,
    Private,
    Loopback,
    LinkLocal,
    Multicast,
    Metadata,
    Unspecified,
    Reserved,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Destination {
    pub scheme: String,
    pub hostname: String,
    pub port: u16,
}

impl Destination {
    pub fn normalized(
        scheme: impl AsRef<str>,
        hostname: impl AsRef<str>,
        port: u16,
    ) -> Result<Self, EgressError> {
        let scheme = normalize_scheme(scheme.as_ref())?;
        let hostname = normalize_hostname(hostname.as_ref())?;
        if port == 0 {
            return Err(EgressError::denied("destination.invalid"));
        }
        Ok(Self {
            scheme,
            hostname,
            port,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProxyPolicy {
    DirectOnly,
    Required {
        scheme: String,
        hostname: String,
        port: u16,
        address_classes: BTreeSet<AddressClass>,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SelectedProxy {
    pub scheme: String,
    pub hostname: String,
    pub port: u16,
    pub resolved_addresses: Vec<IpAddr>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EgressGrant {
    pub grant_id: String,
    pub principal_id: String,
    pub operation: String,
    pub scheme: String,
    pub hostname: String,
    pub ports: BTreeSet<u16>,
    pub address_classes: BTreeSet<AddressClass>,
    pub proxy_policy: ProxyPolicy,
    pub redirect_limit: u8,
    pub byte_limit: u64,
    pub expires_at_ms: u64,
}

impl EgressGrant {
    pub fn validate(&self) -> Result<(), EgressError> {
        if self.grant_id.is_empty()
            || self.principal_id.is_empty()
            || self.operation.is_empty()
            || self.ports.is_empty()
            || self.byte_limit == 0
            || normalize_scheme(&self.scheme)? != self.scheme
            || normalize_hostname(&self.hostname)? != self.hostname
            || self.ports.contains(&0)
        {
            return Err(EgressError::denied("grant.invalid"));
        }
        if let ProxyPolicy::Required {
            scheme,
            hostname,
            port,
            address_classes,
        } = &self.proxy_policy
        {
            if *port == 0
                || address_classes.is_empty()
                || normalize_scheme(scheme)? != *scheme
                || normalize_hostname(hostname)? != *hostname
            {
                return Err(EgressError::denied("grant.proxy_invalid"));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct ConnectionAttempt<'a> {
    pub principal_id: &'a str,
    pub operation: &'a str,
    pub destination: &'a Destination,
    pub resolved_addresses: &'a [IpAddr],
    pub selected_proxy: Option<&'a SelectedProxy>,
    pub redirect_hops: u8,
    pub now_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EgressAuthorization {
    pub grant_id: String,
    pub max_response_bytes: u64,
    pub redirects_remaining: u8,
}

#[derive(Clone, Debug, Default)]
pub struct EgressPolicy;

impl EgressPolicy {
    pub fn authorize_attempt(
        &self,
        grant: Option<&EgressGrant>,
        attempt: ConnectionAttempt<'_>,
    ) -> Result<EgressAuthorization, EgressError> {
        let grant = grant.ok_or_else(|| EgressError::denied("grant.missing"))?;
        grant.validate()?;
        if grant.principal_id != attempt.principal_id || grant.operation != attempt.operation {
            return Err(EgressError::denied("grant.scope"));
        }
        if attempt.now_ms >= grant.expires_at_ms {
            return Err(EgressError::denied("grant.expired"));
        }
        if grant.scheme != attempt.destination.scheme
            || grant.hostname != attempt.destination.hostname
            || !grant.ports.contains(&attempt.destination.port)
        {
            return Err(EgressError::denied("destination.not_granted"));
        }
        if attempt.redirect_hops > grant.redirect_limit {
            return Err(EgressError::denied("redirect.limit"));
        }
        validate_resolved(
            &attempt.destination.hostname,
            attempt.resolved_addresses,
            &grant.address_classes,
        )?;
        validate_proxy(&grant.proxy_policy, attempt.selected_proxy)?;
        Ok(EgressAuthorization {
            grant_id: grant.grant_id.clone(),
            max_response_bytes: grant.byte_limit,
            redirects_remaining: grant.redirect_limit - attempt.redirect_hops,
        })
    }

    pub fn enforce_response_limit(
        &self,
        authorization: &EgressAuthorization,
        received_bytes: u64,
    ) -> Result<(), EgressError> {
        if received_bytes > authorization.max_response_bytes {
            return Err(EgressError::denied("response.byte_limit"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct EgressRequestContext {
    session: AuthenticatedSession,
    capability_id: Id,
    operation: String,
}

impl EgressRequestContext {
    pub fn from_session(
        session: &AuthenticatedSession,
        capability_id: Id,
        operation: impl Into<String>,
    ) -> Self {
        Self {
            session: session.clone(),
            capability_id,
            operation: operation.into(),
        }
    }

    pub fn session(&self) -> &AuthenticatedSession {
        &self.session
    }

    pub fn scope(&self) -> &Scope {
        &self.session.bound_scope
    }

    pub fn principal_id(&self) -> &Id {
        &self.session.accepted.principal.id
    }

    pub fn capability_id(&self) -> &Id {
        &self.capability_id
    }

    pub fn operation(&self) -> &str {
        &self.operation
    }
}

pub trait EgressGrantAuthority: Send + Sync {
    fn authorize(
        &self,
        context: &EgressRequestContext,
        attempt: ConnectionAttempt<'_>,
    ) -> Result<EgressAuthorization, EgressAuthorityError>;
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
#[error("egress authority denied the request ({rule})")]
pub struct EgressAuthorityError {
    pub code: &'static str,
    pub rule: &'static str,
}

impl EgressAuthorityError {
    pub fn policy(error: EgressError) -> Self {
        Self {
            code: error.code,
            rule: error.rule,
        }
    }

    pub fn authorization_denied() -> Self {
        Self {
            code: EGRESS_DENIED,
            rule: "capability.denied",
        }
    }

    pub fn unavailable() -> Self {
        Self {
            code: EGRESS_DENIED,
            rule: "authority.unavailable",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GuardedHttpResponse {
    pub status: u16,
    pub body: Vec<u8>,
}

pub struct GuardedHttpClient<'a, A: EgressGrantAuthority> {
    authority: &'a A,
    context: EgressRequestContext,
}

impl<'a, A: EgressGrantAuthority> GuardedHttpClient<'a, A> {
    pub fn new(authority: &'a A, context: EgressRequestContext) -> Self {
        Self { authority, context }
    }

    pub async fn get(&self, url: &str) -> Result<GuardedHttpResponse, GuardedHttpError> {
        let mut target = HttpTarget::parse(url)?;
        let mut redirect_hops = 0_u8;
        loop {
            if target.destination.scheme != "http" {
                return Err(GuardedHttpError::denied("transport.scheme"));
            }
            let resolved_addresses = resolve(&target.destination).await?;
            let response = request_once(
                self.authority,
                &self.context,
                &target,
                &resolved_addresses,
                redirect_hops,
            )
            .await?;
            let Some(location) = response.redirect_location.as_deref() else {
                return Ok(GuardedHttpResponse {
                    status: response.status,
                    body: response.body,
                });
            };
            redirect_hops = redirect_hops
                .checked_add(1)
                .ok_or_else(|| GuardedHttpError::denied("redirect.limit"))?;
            target = target.redirect(location)?;
        }
    }
}

#[derive(Debug, Error)]
pub enum GuardedHttpError {
    #[error(transparent)]
    Authority(#[from] EgressAuthorityError),
    #[error("egress request denied ({rule})")]
    Denied {
        code: &'static str,
        rule: &'static str,
    },
    #[error("egress transport failed")]
    Io(#[from] std::io::Error),
}

impl GuardedHttpError {
    fn denied(rule: &'static str) -> Self {
        Self::Denied {
            code: EGRESS_DENIED,
            rule,
        }
    }

    pub fn rule(&self) -> Option<&'static str> {
        match self {
            Self::Authority(error) => Some(error.rule),
            Self::Denied { rule, .. } => Some(rule),
            Self::Io(_) => None,
        }
    }
}

struct HttpTarget {
    destination: Destination,
    path_and_query: String,
}

impl HttpTarget {
    fn parse(url: &str) -> Result<Self, GuardedHttpError> {
        if url.contains(['\r', '\n', '#']) {
            return Err(GuardedHttpError::denied("destination.invalid"));
        }
        let (scheme, rest) = url
            .split_once("://")
            .ok_or_else(|| GuardedHttpError::denied("destination.invalid"))?;
        let (authority, path) = match rest.split_once('/') {
            Some((authority, path)) => (authority, format!("/{path}")),
            None => (rest, "/".to_owned()),
        };
        if authority.is_empty() || authority.contains('@') {
            return Err(GuardedHttpError::denied("destination.invalid"));
        }
        let (hostname, port) = match authority.rsplit_once(':') {
            Some((hostname, port)) if !hostname.contains(':') => (
                hostname,
                port.parse::<u16>()
                    .map_err(|_| GuardedHttpError::denied("destination.invalid"))?,
            ),
            None => (
                authority,
                if scheme.eq_ignore_ascii_case("http") {
                    80
                } else {
                    443
                },
            ),
            _ => return Err(GuardedHttpError::denied("destination.invalid")),
        };
        let destination = Destination::normalized(scheme, hostname, port)
            .map_err(|error| GuardedHttpError::Authority(EgressAuthorityError::policy(error)))?;
        Ok(Self {
            destination,
            path_and_query: path,
        })
    }

    fn redirect(&self, location: &str) -> Result<Self, GuardedHttpError> {
        if location.starts_with('/') {
            if location.contains(['\r', '\n', '#']) {
                return Err(GuardedHttpError::denied("redirect.invalid"));
            }
            return Ok(Self {
                destination: self.destination.clone(),
                path_and_query: location.to_owned(),
            });
        }
        Self::parse(location).map_err(|_| GuardedHttpError::denied("redirect.invalid"))
    }
}

async fn resolve(destination: &Destination) -> Result<Vec<IpAddr>, GuardedHttpError> {
    let mut addresses = tokio::net::lookup_host((destination.hostname.as_str(), destination.port))
        .await?
        .map(|address| address.ip())
        .collect::<Vec<_>>();
    addresses.sort();
    addresses.dedup();
    if addresses.is_empty() {
        return Err(GuardedHttpError::denied("dns.empty"));
    }
    Ok(addresses)
}

struct RawHttpResponse {
    status: u16,
    redirect_location: Option<String>,
    body: Vec<u8>,
}

async fn request_once<A: EgressGrantAuthority>(
    authority: &A,
    context: &EgressRequestContext,
    target: &HttpTarget,
    addresses: &[IpAddr],
    redirect_hops: u8,
) -> Result<RawHttpResponse, GuardedHttpError> {
    let mut last_error = None;
    let mut authorized_stream = None;
    for address in addresses {
        let checked = authority.authorize(
            context,
            ConnectionAttempt {
                principal_id: context.principal_id().as_str(),
                operation: context.operation(),
                destination: &target.destination,
                resolved_addresses: addresses,
                selected_proxy: None,
                redirect_hops,
                now_ms: current_time_ms()?,
            },
        )?;
        match TcpStream::connect(SocketAddr::new(*address, target.destination.port)).await {
            Ok(connection) => {
                authorized_stream = Some((connection, checked));
                break;
            }
            Err(error) => last_error = Some(error),
        }
    }
    let (mut stream, authorization) = match authorized_stream {
        Some(connection) => connection,
        None => {
            return Err(last_error
                .map(GuardedHttpError::Io)
                .unwrap_or_else(|| GuardedHttpError::denied("dns.empty")))
        }
    };
    let request = format!(
        "GET {} HTTP/1.1\r\nHost: {}:{}\r\nConnection: close\r\n\r\n",
        target.path_and_query, target.destination.hostname, target.destination.port
    );
    stream.write_all(request.as_bytes()).await?;
    let mut received = Vec::new();
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        let count = stream.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        received.extend_from_slice(&buffer[..count]);
        if received.len() as u64 > authorization.max_response_bytes {
            return Err(GuardedHttpError::denied("response.byte_limit"));
        }
    }
    parse_http_response(received)
}

fn parse_http_response(bytes: Vec<u8>) -> Result<RawHttpResponse, GuardedHttpError> {
    let header_end = bytes
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| GuardedHttpError::denied("response.invalid"))?;
    let header = std::str::from_utf8(&bytes[..header_end])
        .map_err(|_| GuardedHttpError::denied("response.invalid"))?;
    let mut lines = header.split("\r\n");
    let status = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| GuardedHttpError::denied("response.invalid"))?;
    let mut redirect_location = None;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            return Err(GuardedHttpError::denied("response.invalid"));
        };
        if name.eq_ignore_ascii_case("location") {
            redirect_location = Some(value.trim().to_owned());
        }
    }
    if !matches!(status, 301 | 302 | 303 | 307 | 308) {
        redirect_location = None;
    } else if redirect_location.as_deref().is_none_or(str::is_empty) {
        return Err(GuardedHttpError::denied("redirect.invalid"));
    }
    Ok(RawHttpResponse {
        status,
        redirect_location,
        body: bytes[header_end + 4..].to_vec(),
    })
}

fn current_time_ms() -> Result<u64, GuardedHttpError> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| GuardedHttpError::denied("clock.invalid"))?;
    u64::try_from(elapsed.as_millis()).map_err(|_| GuardedHttpError::denied("clock.invalid"))
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
#[error("egress request denied ({rule})")]
pub struct EgressError {
    pub code: &'static str,
    pub rule: &'static str,
}

impl EgressError {
    fn denied(rule: &'static str) -> Self {
        Self {
            code: EGRESS_DENIED,
            rule,
        }
    }
}

pub fn normalize_hostname(hostname: &str) -> Result<String, EgressError> {
    let hostname = hostname.trim().trim_end_matches('.').to_ascii_lowercase();
    if hostname.is_empty() || hostname.len() > 253 || hostname.contains(['/', '\\', '@', ':']) {
        return Err(EgressError::denied("destination.invalid"));
    }
    if let Ok(address) = hostname.parse::<IpAddr>() {
        return Ok(address.to_string());
    }
    if !hostname.split('.').all(|label| {
        !label.is_empty()
            && label.len() <= 63
            && !label.starts_with('-')
            && !label.ends_with('-')
            && label
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    }) {
        return Err(EgressError::denied("destination.invalid"));
    }
    Ok(hostname)
}

fn normalize_scheme(scheme: &str) -> Result<String, EgressError> {
    let scheme = scheme.trim().to_ascii_lowercase();
    let mut bytes = scheme.bytes();
    if !matches!(bytes.next(), Some(first) if first.is_ascii_lowercase())
        || !bytes.all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"+-.".contains(&byte)
        })
    {
        return Err(EgressError::denied("destination.invalid"));
    }
    Ok(scheme)
}

fn validate_proxy(
    policy: &ProxyPolicy,
    selected: Option<&SelectedProxy>,
) -> Result<(), EgressError> {
    match (policy, selected) {
        (ProxyPolicy::DirectOnly, None) => Ok(()),
        (ProxyPolicy::DirectOnly, Some(_)) => Err(EgressError::denied("proxy.not_granted")),
        (ProxyPolicy::Required { .. }, None) => Err(EgressError::denied("proxy.required")),
        (
            ProxyPolicy::Required {
                scheme,
                hostname,
                port,
                address_classes,
            },
            Some(selected),
        ) => {
            if normalize_scheme(&selected.scheme)? != *scheme
                || normalize_hostname(&selected.hostname)? != *hostname
                || selected.port != *port
            {
                return Err(EgressError::denied("proxy.bypass"));
            }
            validate_resolved(hostname, &selected.resolved_addresses, address_classes)
        }
    }
}

fn validate_resolved(
    hostname: &str,
    addresses: &[IpAddr],
    allowed: &BTreeSet<AddressClass>,
) -> Result<(), EgressError> {
    if addresses.is_empty() {
        return Err(EgressError::denied("dns.empty"));
    }
    if metadata_hostname(hostname) && !allowed.contains(&AddressClass::Metadata) {
        return Err(EgressError::denied("address.metadata"));
    }
    for address in addresses {
        let class = address_class(*address);
        if matches!(class, AddressClass::Unspecified | AddressClass::Reserved) {
            return Err(EgressError::denied("address.class"));
        }
        if !allowed.contains(&class) {
            return Err(EgressError::denied(match class {
                AddressClass::Metadata => "address.metadata",
                _ => "address.class",
            }));
        }
    }
    Ok(())
}

fn metadata_hostname(hostname: &str) -> bool {
    matches!(
        hostname,
        "metadata" | "metadata.google.internal" | "instance-data.ec2.internal"
    )
}

pub fn address_class(address: IpAddr) -> AddressClass {
    if let IpAddr::V6(address) = address {
        if let Some(mapped) = address.to_ipv4_mapped() {
            return address_class(IpAddr::V4(mapped));
        }
    }
    if is_metadata(address) {
        return AddressClass::Metadata;
    }
    match address {
        IpAddr::V4(address) if address.is_unspecified() => AddressClass::Unspecified,
        IpAddr::V6(address) if address.is_unspecified() => AddressClass::Unspecified,
        IpAddr::V4(address) if address.is_loopback() => AddressClass::Loopback,
        IpAddr::V6(address) if address.is_loopback() => AddressClass::Loopback,
        IpAddr::V4(address) if address.is_link_local() => AddressClass::LinkLocal,
        IpAddr::V6(address) if address.is_unicast_link_local() => AddressClass::LinkLocal,
        IpAddr::V4(address) if address.is_multicast() => AddressClass::Multicast,
        IpAddr::V6(address) if address.is_multicast() => AddressClass::Multicast,
        IpAddr::V4(address) if address.is_private() => AddressClass::Private,
        IpAddr::V6(address) if address.is_unique_local() => AddressClass::Private,
        IpAddr::V4(address) if !is_public_v4(address) => AddressClass::Reserved,
        IpAddr::V6(address) if !is_public_v6(address) => AddressClass::Reserved,
        _ => AddressClass::Public,
    }
}

fn is_metadata(address: IpAddr) -> bool {
    matches!(
        address,
        IpAddr::V4(address)
            if address == Ipv4Addr::new(169, 254, 169, 254)
                || address == Ipv4Addr::new(100, 100, 100, 200)
                || address == Ipv4Addr::new(192, 0, 0, 192)
    ) || address
        == "fd00:ec2::254"
            .parse::<IpAddr>()
            .expect("static IPv6 address")
}

fn is_public_v4(address: Ipv4Addr) -> bool {
    let [a, b, c, _] = address.octets();
    !(address.is_private()
        || address.is_loopback()
        || address.is_link_local()
        || address.is_multicast()
        || address.is_unspecified()
        || address.is_broadcast()
        || address.is_documentation()
        || a == 0
        || (a == 100 && (64..=127).contains(&b))
        || (a == 192 && b == 0 && c == 0)
        || (a == 198 && (b == 18 || b == 19))
        || a >= 240)
}

fn is_public_v6(address: Ipv6Addr) -> bool {
    !(address.is_loopback()
        || address.is_unspecified()
        || address.is_multicast()
        || address.is_unique_local()
        || address.is_unicast_link_local()
        || address.segments()[0] == 0x2001 && address.segments()[1] == 0x0db8)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn grant() -> EgressGrant {
        EgressGrant {
            grant_id: "network-1".into(),
            principal_id: "principal-1".into(),
            operation: "model.invoke".into(),
            scheme: "https".into(),
            hostname: "api.example.com".into(),
            ports: BTreeSet::from([443]),
            address_classes: BTreeSet::from([AddressClass::Public]),
            proxy_policy: ProxyPolicy::DirectOnly,
            redirect_limit: 1,
            byte_limit: 1024,
            expires_at_ms: 200,
        }
    }

    fn destination() -> Destination {
        Destination::normalized("HTTPS", "API.EXAMPLE.COM.", 443).unwrap()
    }

    #[test]
    fn egress_default_deny_and_mixed_dns_fail_closed() {
        let policy = EgressPolicy;
        let destination = destination();
        let public: IpAddr = "93.184.216.34".parse().unwrap();
        let private: IpAddr = "10.0.0.1".parse().unwrap();
        let authorize = |grant: Option<&EgressGrant>, addresses: &[IpAddr]| {
            policy.authorize_attempt(
                grant,
                ConnectionAttempt {
                    principal_id: "principal-1",
                    operation: "model.invoke",
                    destination: &destination,
                    resolved_addresses: addresses,
                    selected_proxy: None,
                    redirect_hops: 0,
                    now_ms: 100,
                },
            )
        };
        assert_eq!(
            authorize(None, &[public]).unwrap_err().rule,
            "grant.missing"
        );
        assert_eq!(
            authorize(Some(&grant()), &[public, private])
                .unwrap_err()
                .rule,
            "address.class"
        );
    }

    #[test]
    fn egress_rechecks_redirect_metadata_proxy_expiry_and_bytes() {
        let policy = EgressPolicy;
        let base = grant();
        let destination = destination();
        let public: IpAddr = "93.184.216.34".parse().unwrap();
        let metadata: IpAddr = "127.0.0.1".parse().unwrap();
        let authorize = |grant: &EgressGrant, addresses: &[IpAddr], hops, now_ms| {
            policy.authorize_attempt(
                Some(grant),
                ConnectionAttempt {
                    principal_id: "principal-1",
                    operation: "model.invoke",
                    destination: &destination,
                    resolved_addresses: addresses,
                    selected_proxy: None,
                    redirect_hops: hops,
                    now_ms,
                },
            )
        };
        assert_eq!(
            authorize(&base, &[metadata], 0, 100).unwrap_err().rule,
            "address.metadata"
        );
        assert_eq!(
            authorize(&base, &[public], 2, 100).unwrap_err().rule,
            "redirect.limit"
        );
        assert_eq!(
            authorize(&base, &[public], 0, 200).unwrap_err().rule,
            "grant.expired"
        );
        let authorization = authorize(&base, &[public], 1, 100).unwrap();
        assert_eq!(authorization.redirects_remaining, 0);
        assert_eq!(
            policy
                .enforce_response_limit(&authorization, 1025)
                .unwrap_err()
                .rule,
            "response.byte_limit"
        );
        let mut proxied = base.clone();
        proxied.proxy_policy = ProxyPolicy::Required {
            scheme: "https".into(),
            hostname: "proxy.example.com".into(),
            port: 8443,
            address_classes: BTreeSet::from([AddressClass::Public]),
        };
        assert_eq!(
            authorize(&proxied, &[public], 0, 100).unwrap_err().rule,
            "proxy.required"
        );
    }
}
