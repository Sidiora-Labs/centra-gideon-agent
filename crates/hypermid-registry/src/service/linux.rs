use super::{ServiceAction, ServiceDefinition, ServicePlatform};
use std::path::Path;

pub fn definition(executable: &Path, drain_ceiling_ms: u64) -> ServiceDefinition {
    let stop_seconds = drain_ceiling_ms.div_ceil(1000).saturating_add(5);
    ServiceDefinition {
        platform: ServicePlatform::Linux,
        relative_path: ".config/systemd/user/hypermid.service".into(),
        contents: format!(
            "[Unit]\nDescription=Hypermid daemon\nAfter=network.target\n\n[Service]\nType=simple\nExecStart={} daemon\nRestart=on-failure\nKillMode=control-group\nTimeoutStopSec={}\n\n[Install]\nWantedBy=default.target\n",
            systemd_escape(executable),
            stop_seconds
        ),
    }
}

pub fn command(action: ServiceAction, definition_path: &Path) -> (String, Vec<String>) {
    let unit = definition_path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("hypermid.service")
        .to_owned();
    let arguments = match action {
        ServiceAction::Register => vec!["--user", "enable", &unit],
        ServiceAction::Reload => vec!["--user", "daemon-reload"],
        ServiceAction::Start => vec!["--user", "start", &unit],
        ServiceAction::Stop => vec!["--user", "stop", &unit],
        ServiceAction::Restart => vec!["--user", "restart", &unit],
        ServiceAction::Deregister => vec!["--user", "disable", &unit],
    };
    (
        "systemctl".to_owned(),
        arguments.into_iter().map(str::to_owned).collect(),
    )
}

fn systemd_escape(path: &Path) -> String {
    let text = path.to_string_lossy();
    format!("\"{}\"", text.replace('\\', "\\\\").replace('"', "\\\""))
}
