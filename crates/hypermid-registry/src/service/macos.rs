use super::{ServiceAction, ServiceDefinition, ServicePlatform};
use std::path::Path;

pub fn definition(executable: &Path, drain_ceiling_ms: u64) -> ServiceDefinition {
    let stop_seconds = drain_ceiling_ms.div_ceil(1000).saturating_add(5);
    ServiceDefinition {
        platform: ServicePlatform::Macos,
        relative_path: "Library/LaunchAgents/ai.gideon.hypermid.plist".into(),
        contents: format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>Label</key><string>ai.gideon.hypermid</string><key>ProgramArguments</key><array><string>{}</string><string>daemon</string></array><key>RunAtLoad</key><true/><key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict><key>ExitTimeOut</key><integer>{}</integer></dict></plist>\n",
            xml_escape(&executable.to_string_lossy()),
            stop_seconds
        ),
    }
}

pub fn command(action: ServiceAction, definition_path: &Path) -> (String, Vec<String>) {
    let path = definition_path.to_string_lossy().into_owned();
    let label = "gui/${UID}/ai.gideon.hypermid".to_owned();
    let arguments = match action {
        ServiceAction::Register => vec!["bootstrap".to_owned(), "gui/${UID}".to_owned(), path],
        ServiceAction::Reload => vec!["kickstart".to_owned(), "-k".to_owned(), label],
        ServiceAction::Start => vec!["kickstart".to_owned(), label],
        ServiceAction::Stop => vec!["kill".to_owned(), "SIGTERM".to_owned(), label],
        ServiceAction::Restart => vec!["kickstart".to_owned(), "-k".to_owned(), label],
        ServiceAction::Deregister => vec!["bootout".to_owned(), path],
    };
    ("launchctl".to_owned(), arguments)
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
