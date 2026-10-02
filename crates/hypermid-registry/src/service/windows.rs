use super::{ServiceAction, ServiceDefinition, ServicePlatform};
use std::path::Path;

pub fn definition(executable: &Path, drain_ceiling_ms: u64) -> ServiceDefinition {
    ServiceDefinition {
        platform: ServicePlatform::Windows,
        relative_path: "AppData/Roaming/Gideon/Hypermid/hypermid-task.xml".into(),
        contents: format!(
            "<?xml version=\"1.0\" encoding=\"UTF-16\"?><Task version=\"1.4\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\"><Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers><Settings><RestartOnFailure><Interval>PT5S</Interval><Count>10</Count></RestartOnFailure><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings><Actions Context=\"Author\"><Exec><Command>{}</Command><Arguments>daemon --service-stop-timeout-ms {}</Arguments></Exec></Actions></Task>",
            xml_escape(&executable.to_string_lossy()),
            drain_ceiling_ms.saturating_add(5000)
        ),
    }
}

pub fn command(action: ServiceAction, definition_path: &Path) -> (String, Vec<String>) {
    let path = definition_path.to_string_lossy().into_owned();
    let arguments = match action {
        ServiceAction::Register | ServiceAction::Reload => vec![
            "/Create".to_owned(),
            "/TN".to_owned(),
            "Gideon Hypermid".to_owned(),
            "/XML".to_owned(),
            path,
            "/F".to_owned(),
        ],
        ServiceAction::Start => vec![
            "/Run".to_owned(),
            "/TN".to_owned(),
            "Gideon Hypermid".to_owned(),
        ],
        ServiceAction::Stop => vec![
            "/End".to_owned(),
            "/TN".to_owned(),
            "Gideon Hypermid".to_owned(),
        ],
        ServiceAction::Restart => vec![
            "/Run".to_owned(),
            "/TN".to_owned(),
            "Gideon Hypermid".to_owned(),
        ],
        ServiceAction::Deregister => vec![
            "/Delete".to_owned(),
            "/TN".to_owned(),
            "Gideon Hypermid".to_owned(),
            "/F".to_owned(),
        ],
    };
    ("schtasks.exe".to_owned(), arguments)
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
