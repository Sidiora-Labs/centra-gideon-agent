use std::{
    collections::BTreeMap,
    env,
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

use hypermid_mcp::{Cancellation, InvocationOutcome, McpStdioClient, StdioBudgets};
use hypermid_sandbox::{
    FilesystemAccess, FilesystemGrant, NetworkGrant, ResourceCeilings, SandboxLaunch,
};
use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};
use tokio::time::{sleep, Instant};

#[tokio::main]
async fn main() {
    let mut args = env::args_os().skip(1);
    let scenario = args.next().and_then(|value| value.into_string().ok()).expect("scenario");
    let executable = PathBuf::from(args.next().expect("adversary executable"));
    let working_directory = PathBuf::from(args.next().expect("working directory"));
    let output = run(&scenario, &executable, &working_directory).await;
    println!("{}", serde_json::to_string(&output).expect("JSON output"));
}

async fn run(scenario: &str, executable: &Path, working_directory: &Path) -> Value {
    fs::create_dir_all(working_directory).expect("create sandbox working directory");
    let executable_sha256 = digest(executable);
    let mut environment = BTreeMap::new();
    environment.insert(OsString::from("SAFE_VALUE"), OsString::from("allowed"));
    let launch = SandboxLaunch {
        executable: executable.to_owned(),
        executable_sha256,
        arguments: vec![OsString::from(scenario)],
        environment,
        working_directory: working_directory.to_owned(),
        filesystem: vec![FilesystemGrant {
            host_path: working_directory.to_owned(),
            access: FilesystemAccess::ReadWrite,
        }],
        network: NetworkGrant::Denied,
        ceilings: ResourceCeilings {
            address_space_bytes: 256 * 1024 * 1024,
            cpu_seconds: 10,
            file_bytes: 2 * 1024 * 1024,
            open_files: 32,
            processes: 8,
        },
        deadline: Instant::now() + Duration::from_secs(10),
    };
    let budgets = StdioBudgets {
        initialization: Duration::from_secs(2),
        request: Duration::from_secs(2),
        frame_bytes: 4096,
        idle: Duration::from_secs(5),
        shutdown: Duration::from_millis(200),
        stderr_bytes: 4096,
    };
    let client = match McpStdioClient::launch(launch, budgets, "sandbox-acceptance", "1").await {
        Ok(client) => client,
        Err(error) => return json!({"launch_error": error.to_string()}),
    };
    if scenario == "cancel_unknown" {
        let cancellation = Cancellation::default();
        let trigger = cancellation.clone();
        let cancel = async move {
            for _ in 0..100 {
                if working_directory.join("effect-committed").is_file() {
                    break;
                }
                sleep(Duration::from_millis(10)).await;
            }
            trigger.cancel();
        };
        let invoke = client.call_tool("probe", json!({}), true, cancellation);
        let (result, _) = tokio::join!(invoke, cancel);
        sleep(Duration::from_millis(100)).await;
        let descendant_pid = fs::read_to_string(working_directory.join("descendant.pid"))
            .ok()
            .and_then(|value| value.trim().parse::<u32>().ok());
        let descendant_alive = descendant_pid.is_some_and(process_is_live);
        return json!({
            "unknown": matches!(result, Ok(InvocationOutcome::Unknown)),
            "effect_marker": working_directory.join("effect-committed").is_file(),
            "descendant_pid": descendant_pid,
            "descendant_alive": descendant_alive,
        });
    }
    let result = client
        .call_tool("probe", json!({}), false, Cancellation::default())
        .await;
    let _ = client.close().await;
    match result {
        Ok(InvocationOutcome::Committed(value)) => json!({"committed": value}),
        Ok(InvocationOutcome::Unknown) => json!({"unknown": true}),
        Err(error) => json!({"error": error.to_string()}),
    }
}

fn digest(path: &Path) -> String {
    let bytes = fs::read(path).expect("read executable");
    format!("{:x}", Sha256::digest(bytes))
}

fn process_is_live(pid: u32) -> bool {
    let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return false;
    };
    stat.rsplit_once(") ")
        .and_then(|(_, fields)| fields.chars().next())
        .is_some_and(|state| state != 'Z' && state != 'X')
}
