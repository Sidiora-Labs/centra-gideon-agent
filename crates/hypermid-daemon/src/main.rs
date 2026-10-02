use std::{
    collections::BTreeSet,
    net::SocketAddr,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

use hypermid_contracts::Id;
use hypermid_contracts::Scope;
use hypermid_core::capability::CapabilityOperation;
use hypermid_daemon::enrollment::{
    install_explicit_operator_grant, revoke_operator_grant, ExplicitOperatorGrant,
};
use hypermid_daemon::{Daemon, DaemonConfig, RemoteListenerConfig};
use serde_json::json;

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("hypermid-daemon: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args_os().skip(1).peekable();
    if arguments.peek().and_then(|argument| argument.to_str()) == Some("grant") {
        arguments.next();
        return run_grant_command(&mut arguments);
    }
    let mut socket = None;
    let mut connection_record = None;
    let mut tls_directory = None;
    let mut client_crl = None;
    let mut mcp_config = None;
    let mut local_owner_id = None;
    let mut local_credential_id = None;
    let mut local_project_id = None;
    let mut local_workspace_id = None;
    let mut local_capability_id = None;
    let mut local_capability_operations = BTreeSet::new();
    let mut local_capability_resources = BTreeSet::new();
    let mut local_capability_expires_ms = None;
    let mut remote_listen = None;
    let mut remote_connection_record = None;
    let mut remote_authorization = None;
    while let Some(argument) = arguments.next() {
        match argument.to_str() {
            Some("--socket") => socket = arguments.next().map(PathBuf::from),
            Some("--connection-record") => connection_record = arguments.next().map(PathBuf::from),
            Some("--tls-directory") => tls_directory = arguments.next().map(PathBuf::from),
            Some("--client-crl") => client_crl = arguments.next().map(PathBuf::from),
            Some("--mcp-config") => mcp_config = arguments.next().map(PathBuf::from),
            Some("--local-owner-id") => {
                local_owner_id = Some(next_id(&mut arguments, "--local-owner-id")?)
            }
            Some("--local-credential-id") => {
                local_credential_id = Some(next_id(&mut arguments, "--local-credential-id")?)
            }
            Some("--local-project-id") => {
                local_project_id = Some(next_id(&mut arguments, "--local-project-id")?)
            }
            Some("--local-workspace-id") => {
                local_workspace_id = Some(next_id(&mut arguments, "--local-workspace-id")?)
            }
            Some("--local-capability-id") => {
                local_capability_id = Some(next_id(&mut arguments, "--local-capability-id")?)
            }
            Some("--local-capability-operation") => {
                let value = next_utf8(&mut arguments, "--local-capability-operation")?;
                let operation = parse_capability_operation(&value)
                    .ok_or_else(|| format!("unsupported --local-capability-operation {value:?}"))?;
                if !local_capability_operations.insert(operation) {
                    return Err(format!("duplicate --local-capability-operation {value:?}").into());
                }
            }
            Some("--local-capability-resource") => {
                let resource = next_id(&mut arguments, "--local-capability-resource")?;
                if !local_capability_resources.insert(resource.clone()) {
                    return Err(format!(
                        "duplicate --local-capability-resource {:?}",
                        resource.as_str()
                    )
                    .into());
                }
            }
            Some("--local-capability-expires-ms") => {
                let value = next_utf8(&mut arguments, "--local-capability-expires-ms")?;
                local_capability_expires_ms =
                    Some(value.parse::<u64>().map_err(|_| {
                        "--local-capability-expires-ms must be an unsigned integer"
                    })?);
            }
            Some("--remote-listen") => {
                let value = next_utf8(&mut arguments, "--remote-listen")?;
                remote_listen = Some(
                    value
                        .parse::<SocketAddr>()
                        .map_err(|_| "--remote-listen must be HOST:PORT")?,
                );
            }
            Some("--remote-connection-record") => {
                remote_connection_record = arguments.next().map(PathBuf::from)
            }
            Some("--remote-authorization") => {
                remote_authorization = arguments.next().map(PathBuf::from)
            }
            Some("--help") | Some("-h") => {
                println!(
                    "Usage: hypermid-daemon --socket PATH --connection-record PATH \
                     --local-credential-id ID --local-owner-id ID --local-project-id ID [--local-workspace-id ID] \
                     --local-capability-id ID --local-capability-operation OP... \
                     --local-capability-resource ID... --local-capability-expires-ms UNIX_MS \
                     [--tls-directory PATH] [--client-crl PATH] \
                     [--mcp-config PATH] \
                     [--remote-listen HOST:PORT --remote-connection-record PATH \
                      --remote-authorization PATH]"
                );
                return Ok(());
            }
            Some(value) => return Err(format!("unknown argument {value:?}").into()),
            None => return Err("arguments must be valid UTF-8".into()),
        }
    }

    let socket = socket.ok_or("--socket is required")?;
    let connection_record = connection_record.ok_or("--connection-record is required")?;
    let local_credential_id = local_credential_id.ok_or("--local-credential-id is required")?;
    let local_owner_id = local_owner_id.ok_or("--local-owner-id is required")?;
    let local_project_id = local_project_id.ok_or("--local-project-id is required")?;
    let local_capability_id = local_capability_id.ok_or("--local-capability-id is required")?;
    let local_capability_expires_ms =
        local_capability_expires_ms.ok_or("--local-capability-expires-ms is required")?;
    if local_capability_operations.is_empty() {
        return Err("at least one --local-capability-operation is required".into());
    }
    if local_capability_resources.is_empty() {
        return Err("at least one --local-capability-resource is required".into());
    }
    if local_capability_expires_ms <= now_ms()? {
        return Err("--local-capability-expires-ms must be in the future".into());
    }
    if client_crl.is_some() && tls_directory.is_none() {
        return Err("--client-crl requires --tls-directory".into());
    }
    if remote_connection_record
        .as_ref()
        .is_some_and(|remote| paths_conflict(&connection_record, remote))
    {
        return Err("--remote-connection-record must differ from --connection-record".into());
    }
    let remote = RemoteListenerConfig::from_options(
        remote_listen,
        remote_connection_record,
        tls_directory.clone(),
        client_crl.clone(),
        remote_authorization,
    )?;
    Daemon::new(DaemonConfig {
        socket,
        connection_record,
        tls_directory,
        client_crl,
        mcp_config,
        local_credential_id,
        local_owner_id,
        local_project_id,
        local_workspace_id,
        local_capability_id,
        local_capability_operations,
        local_capability_resources,
        local_capability_expires_ms,
        remote,
    })?
    .run()
    .await?;
    Ok(())
}

fn run_grant_command(
    arguments: &mut impl Iterator<Item = std::ffi::OsString>,
) -> Result<(), Box<dyn std::error::Error>> {
    match next_utf8(arguments, "grant action")?.as_str() {
        "install" => run_grant_install(arguments),
        "revoke" => run_grant_revoke(arguments),
        action => {
            Err(format!("unknown grant action {action:?}; expected install or revoke").into())
        }
    }
}

fn run_grant_install(
    arguments: &mut impl Iterator<Item = std::ffi::OsString>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut database = None;
    let mut operator_owner_id = None;
    let mut grantee_principal_id = None;
    let mut claimed_owner_id = None;
    let mut claimed_project_id = None;
    let mut claimed_workspace_id = None;
    let mut target_owner_id = None;
    let mut target_project_id = None;
    let mut target_workspace_id = None;
    let mut capability_id = None;
    let mut operations = BTreeSet::new();
    let mut resources = BTreeSet::new();
    let mut expires_ms = None;
    while let Some(argument) = arguments.next() {
        match argument.to_str() {
            Some("--database") => database = arguments.next().map(PathBuf::from),
            Some("--operator-owner-id") => {
                operator_owner_id = Some(next_id(arguments, "--operator-owner-id")?)
            }
            Some("--grantee-principal-id") => {
                grantee_principal_id = Some(next_id(arguments, "--grantee-principal-id")?)
            }
            Some("--claimed-owner-id") => {
                claimed_owner_id = Some(next_id(arguments, "--claimed-owner-id")?)
            }
            Some("--claimed-project-id") => {
                claimed_project_id = Some(next_id(arguments, "--claimed-project-id")?)
            }
            Some("--claimed-workspace-id") => {
                claimed_workspace_id = Some(next_id(arguments, "--claimed-workspace-id")?)
            }
            Some("--target-owner-id") => {
                target_owner_id = Some(next_id(arguments, "--target-owner-id")?)
            }
            Some("--target-project-id") => {
                target_project_id = Some(next_id(arguments, "--target-project-id")?)
            }
            Some("--target-workspace-id") => {
                target_workspace_id = Some(next_id(arguments, "--target-workspace-id")?)
            }
            Some("--capability-id") => capability_id = Some(next_id(arguments, "--capability-id")?),
            Some("--operation") => {
                let value = next_utf8(arguments, "--operation")?;
                let operation = parse_capability_operation(&value)
                    .ok_or_else(|| format!("unsupported --operation {value:?}"))?;
                if !operations.insert(operation) {
                    return Err(format!("duplicate --operation {value:?}").into());
                }
            }
            Some("--resource") => {
                let resource = next_id(arguments, "--resource")?;
                if !resources.insert(resource.clone()) {
                    return Err(format!("duplicate --resource {:?}", resource.as_str()).into());
                }
            }
            Some("--expires-ms") => {
                expires_ms = Some(
                    next_utf8(arguments, "--expires-ms")?
                        .parse::<u64>()
                        .map_err(|_| "--expires-ms must be an unsigned integer")?,
                )
            }
            Some("--help") | Some("-h") => {
                println!(
                    "Usage: hypermid-daemon grant install --database PATH \
                     --operator-owner-id ID --grantee-principal-id ID \
                     --claimed-owner-id ID --claimed-project-id ID \
                     [--claimed-workspace-id ID] --target-owner-id ID \
                     --target-project-id ID [--target-workspace-id ID] \
                     --capability-id ID --operation OP... --resource ID... --expires-ms UNIX_MS"
                );
                return Ok(());
            }
            Some(value) => return Err(format!("unknown grant install argument {value:?}").into()),
            None => return Err("grant install arguments must be valid UTF-8".into()),
        }
    }
    let expires_at_ms = expires_ms.ok_or("--expires-ms is required")?;
    if expires_at_ms <= now_ms()? {
        return Err("--expires-ms must be in the future".into());
    }
    let enrollment = ExplicitOperatorGrant {
        operator_owner_id: operator_owner_id.ok_or("--operator-owner-id is required")?,
        grantee_principal_id: grantee_principal_id.ok_or("--grantee-principal-id is required")?,
        claimed_scope: Scope::new(
            claimed_owner_id.ok_or("--claimed-owner-id is required")?,
            claimed_project_id.ok_or("--claimed-project-id is required")?,
            claimed_workspace_id,
        ),
        target_scope: Scope::new(
            target_owner_id.ok_or("--target-owner-id is required")?,
            target_project_id.ok_or("--target-project-id is required")?,
            target_workspace_id,
        ),
        capability_id: capability_id.ok_or("--capability-id is required")?,
        operations,
        resources,
        expires_at_ms,
    };
    let receipt =
        install_explicit_operator_grant(&database.ok_or("--database is required")?, &enrollment)?;
    println!(
        "{}",
        json!({
            "status": "installed",
            "principal_id": receipt.principal_id,
            "capability_id": receipt.capability_id,
            "scope": receipt.scope,
            "revision": receipt.revision,
            "expires_at_ms": receipt.expires_at_ms,
        })
    );
    Ok(())
}

fn run_grant_revoke(
    arguments: &mut impl Iterator<Item = std::ffi::OsString>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut database = None;
    let mut operator_owner_id = None;
    let mut capability_id = None;
    while let Some(argument) = arguments.next() {
        match argument.to_str() {
            Some("--database") => database = arguments.next().map(PathBuf::from),
            Some("--operator-owner-id") => {
                operator_owner_id = Some(next_id(arguments, "--operator-owner-id")?)
            }
            Some("--capability-id") => capability_id = Some(next_id(arguments, "--capability-id")?),
            Some("--help") | Some("-h") => {
                println!(
                    "Usage: hypermid-daemon grant revoke --database PATH \
                     --operator-owner-id ID --capability-id ID"
                );
                return Ok(());
            }
            Some(value) => return Err(format!("unknown grant revoke argument {value:?}").into()),
            None => return Err("grant revoke arguments must be valid UTF-8".into()),
        }
    }
    let capability_id = capability_id.ok_or("--capability-id is required")?;
    let revoked = revoke_operator_grant(
        &database.ok_or("--database is required")?,
        &operator_owner_id.ok_or("--operator-owner-id is required")?,
        &capability_id,
    )?;
    println!(
        "{}",
        json!({
            "status": if revoked { "revoked" } else { "unchanged" },
            "capability_id": capability_id,
            "revoked": revoked,
        })
    );
    Ok(())
}

fn next_utf8(
    arguments: &mut impl Iterator<Item = std::ffi::OsString>,
    flag: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    arguments
        .next()
        .ok_or_else(|| format!("{flag} requires a value"))?
        .into_string()
        .map_err(|_| format!("{flag} must be valid UTF-8").into())
}

fn next_id(
    arguments: &mut impl Iterator<Item = std::ffi::OsString>,
    flag: &str,
) -> Result<Id, Box<dyn std::error::Error>> {
    let value = next_utf8(arguments, flag)?;
    Id::new(value).map_err(|error| format!("{flag} is invalid: {error}").into())
}

fn parse_capability_operation(value: &str) -> Option<CapabilityOperation> {
    Some(match value {
        "read" => CapabilityOperation::Read,
        "append" => CapabilityOperation::Append,
        "revise" => CapabilityOperation::Revise,
        "archive" => CapabilityOperation::Archive,
        "delete" => CapabilityOperation::Delete,
        "export" => CapabilityOperation::Export,
        "restore" => CapabilityOperation::Restore,
        "administer" => CapabilityOperation::Administer,
        "model-use" => CapabilityOperation::ModelUse,
        "network-use" => CapabilityOperation::NetworkUse,
        "artifact-install" => CapabilityOperation::ArtifactInstall,
        "artifact-update" => CapabilityOperation::ArtifactUpdate,
        _ => return None,
    })
}

fn now_ms() -> Result<u64, Box<dyn std::error::Error>> {
    let elapsed = SystemTime::now().duration_since(UNIX_EPOCH)?;
    u64::try_from(elapsed.as_millis()).map_err(|_| "system time exceeds supported range".into())
}

fn paths_conflict(left: &std::path::Path, right: &std::path::Path) -> bool {
    normalized_path(left) == normalized_path(right)
}

fn normalized_path(path: &std::path::Path) -> PathBuf {
    if let Ok(canonical) = std::fs::canonicalize(path) {
        return canonical;
    }
    if let (Some(parent), Some(name)) = (path.parent(), path.file_name()) {
        if let Ok(parent) = std::fs::canonicalize(parent) {
            return parent.join(name);
        }
    }
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("/"))
            .join(path)
    };
    let mut normalized = PathBuf::new();
    for component in absolute.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    normalized
}
