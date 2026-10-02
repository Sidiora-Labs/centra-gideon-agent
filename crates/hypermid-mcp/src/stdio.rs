use std::{
    collections::HashSet,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    time::Duration,
};

use hypermid_sandbox::{spawn, SandboxError, SandboxLaunch, SandboxedChild};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{ChildStdin, ChildStdout},
    sync::{Mutex, Notify},
    task::JoinHandle,
    time::{timeout, Instant},
};

const JSONRPC: &str = "2.0";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InvocationKind {
    Query,
    Mutation,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum InvocationOutcome {
    Committed(Value),
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StdioBudgets {
    pub initialization: Duration,
    pub request: Duration,
    pub frame_bytes: usize,
    pub idle: Duration,
    pub shutdown: Duration,
    pub stderr_bytes: usize,
}

impl Default for StdioBudgets {
    fn default() -> Self {
        Self {
            initialization: Duration::from_secs(15),
            request: Duration::from_secs(60),
            frame_bytes: 1024 * 1024,
            idle: Duration::from_secs(600),
            shutdown: Duration::from_secs(2),
            stderr_bytes: 64 * 1024,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ToolDefinition {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(rename = "inputSchema")]
    pub input_schema: Value,
}

#[derive(Clone, Default)]
pub struct Cancellation {
    cancelled: Arc<AtomicBool>,
    notify: Arc<Notify>,
}

impl Cancellation {
    pub fn cancel(&self) {
        if !self.cancelled.swap(true, Ordering::AcqRel) {
            self.notify.notify_waiters();
        }
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    async fn cancelled(&self) {
        if !self.is_cancelled() {
            self.notify.notified().await;
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum McpError {
    #[error(transparent)]
    Sandbox(#[from] SandboxError),
    #[error("MCP stdio I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("MCP initialization timed out")]
    InitializationTimeout,
    #[error("MCP request timed out")]
    RequestTimeout,
    #[error("MCP request was cancelled")]
    Cancelled,
    #[error("MCP child ended before replying")]
    Disconnected,
    #[error("MCP frame exceeds its byte ceiling")]
    FrameTooLarge,
    #[error("MCP peer sent malformed JSON: {0}")]
    MalformedFrame(String),
    #[error("MCP peer sent an unsolicited or mismatched response")]
    UnsolicitedResponse,
    #[error("MCP peer returned JSON-RPC error {code}: {message}")]
    Remote { code: i64, message: String },
    #[error("MCP tool catalog is invalid: {0}")]
    InvalidCatalog(&'static str),
    #[error("MCP client is closed")]
    Closed,
}

struct State {
    child: Mutex<SandboxedChild>,
    writer: Mutex<Option<ChildStdin>>,
    reader: Mutex<BufReader<ChildStdout>>,
    serial: Mutex<()>,
    next_id: AtomicU64,
    closed: AtomicBool,
    budgets: StdioBudgets,
    last_activity: Mutex<Instant>,
}

pub struct McpStdioClient {
    state: Arc<State>,
    stderr_task: JoinHandle<()>,
}

impl McpStdioClient {
    pub async fn launch(
        launch: SandboxLaunch,
        budgets: StdioBudgets,
        client_name: &str,
        client_version: &str,
    ) -> Result<Self, McpError> {
        validate_budgets(budgets)?;
        let mut child = spawn(launch).await?;
        let writer = child.take_stdin()?;
        let reader = child.take_stdout()?;
        let mut stderr = child.take_stderr()?;
        let state = Arc::new(State {
            child: Mutex::new(child),
            writer: Mutex::new(Some(writer)),
            reader: Mutex::new(BufReader::new(reader)),
            serial: Mutex::new(()),
            next_id: AtomicU64::new(1),
            closed: AtomicBool::new(false),
            budgets,
            last_activity: Mutex::new(Instant::now()),
        });
        let stderr_state = Arc::clone(&state);
        let stderr_task = tokio::spawn(async move {
            let mut total = 0_usize;
            let mut buffer = [0_u8; 4096];
            loop {
                let Ok(read) = stderr.read(&mut buffer).await else {
                    break;
                };
                if read == 0 {
                    break;
                }
                total = total.saturating_add(read);
                if total > budgets.stderr_bytes {
                    terminate_state(&stderr_state).await;
                    break;
                }
            }
        });
        let client = Self { state, stderr_task };
        let initialize = json!({
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": client_version}
        });
        let initialization = client
            .round_trip("initialize", initialize, budgets.initialization)
            .await;
        match initialization {
            Ok(_) => {
                client
                    .notification("notifications/initialized", json!({}))
                    .await?;
                Ok(client)
            }
            Err(McpError::RequestTimeout) => {
                client.terminate().await;
                Err(McpError::InitializationTimeout)
            }
            Err(error) => {
                client.terminate().await;
                Err(error)
            }
        }
    }

    pub async fn list_tools(&self) -> Result<Vec<ToolDefinition>, McpError> {
        let result = self
            .invoke(
                "tools/list",
                json!({}),
                InvocationKind::Query,
                Cancellation::default(),
            )
            .await?;
        let InvocationOutcome::Committed(result) = result else {
            return Err(McpError::Disconnected);
        };
        let tools = result
            .get("tools")
            .cloned()
            .ok_or(McpError::InvalidCatalog("tools array is missing"))?;
        let definitions: Vec<ToolDefinition> = serde_json::from_value(tools)
            .map_err(|_| McpError::InvalidCatalog("tool entry does not match the MCP schema"))?;
        validate_catalog(&definitions, self.state.budgets.frame_bytes)?;
        Ok(definitions)
    }

    pub async fn call_tool(
        &self,
        name: &str,
        arguments: Value,
        mutation: bool,
        cancellation: Cancellation,
    ) -> Result<InvocationOutcome, McpError> {
        validate_tool_name(name)?;
        if !arguments.is_object() {
            return Err(McpError::InvalidCatalog("tool arguments must be an object"));
        }
        self.invoke(
            "tools/call",
            json!({"name": name, "arguments": arguments}),
            if mutation {
                InvocationKind::Mutation
            } else {
                InvocationKind::Query
            },
            cancellation,
        )
        .await
    }

    pub async fn invoke(
        &self,
        method: &str,
        params: Value,
        kind: InvocationKind,
        cancellation: Cancellation,
    ) -> Result<InvocationOutcome, McpError> {
        if self.state.closed.load(Ordering::Acquire) {
            return Err(McpError::Closed);
        }
        if cancellation.is_cancelled() {
            return Err(McpError::Cancelled);
        }
        let _serial = self.state.serial.lock().await;
        let id = self.state.next_id.fetch_add(1, Ordering::AcqRel);
        let request = json!({"jsonrpc": JSONRPC, "id": id, "method": method, "params": params});
        let frame = encode_line(&request, self.state.budgets.frame_bytes)?;
        if let Err(error) = self.write_frame(&frame).await {
            self.terminate().await;
            return match kind {
                InvocationKind::Mutation => Ok(InvocationOutcome::Unknown),
                InvocationKind::Query => Err(error),
            };
        }
        *self.state.last_activity.lock().await = Instant::now();

        let read = read_response(&self.state, id);
        tokio::select! {
            biased;
            _ = cancellation.cancelled() => {
                let _ = self.send_cancel(id, "cancelled").await;
                self.terminate().await;
                match kind {
                    InvocationKind::Mutation => Ok(InvocationOutcome::Unknown),
                    InvocationKind::Query => Err(McpError::Cancelled),
                }
            }
            result = timeout(self.state.budgets.request, read) => {
                match result {
                    Ok(Ok(value)) => {
                        *self.state.last_activity.lock().await = Instant::now();
                        Ok(InvocationOutcome::Committed(value))
                    }
                    Ok(Err(error @ McpError::Remote { .. })) => Err(error),
                    Ok(Err(error)) => {
                        self.terminate().await;
                        match kind {
                            InvocationKind::Mutation => Ok(InvocationOutcome::Unknown),
                            InvocationKind::Query => Err(error),
                        }
                    }
                    Err(_) => {
                        let _ = self.send_cancel(id, "deadline exceeded").await;
                        self.terminate().await;
                        match kind {
                            InvocationKind::Mutation => Ok(InvocationOutcome::Unknown),
                            InvocationKind::Query => Err(McpError::RequestTimeout),
                        }
                    }
                }
            }
        }
    }

    pub async fn shed_if_idle(&self, now: Instant) -> Result<bool, McpError> {
        let last = *self.state.last_activity.lock().await;
        if now.saturating_duration_since(last) < self.state.budgets.idle {
            return Ok(false);
        }
        self.close().await?;
        Ok(true)
    }

    pub async fn close(&self) -> Result<(), McpError> {
        if self.state.closed.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        self.state.writer.lock().await.take();
        let result = self
            .state
            .child
            .lock()
            .await
            .terminate_and_reap(self.state.budgets.shutdown)
            .await;
        result.map(|_| ()).map_err(Into::into)
    }

    async fn round_trip(
        &self,
        method: &str,
        params: Value,
        budget: Duration,
    ) -> Result<Value, McpError> {
        let _serial = self.state.serial.lock().await;
        let id = self.state.next_id.fetch_add(1, Ordering::AcqRel);
        let request = json!({"jsonrpc": JSONRPC, "id": id, "method": method, "params": params});
        self.write_frame(&encode_line(&request, self.state.budgets.frame_bytes)?)
            .await?;
        timeout(budget, read_response(&self.state, id))
            .await
            .map_err(|_| McpError::RequestTimeout)?
    }

    async fn notification(&self, method: &str, params: Value) -> Result<(), McpError> {
        let message = json!({"jsonrpc": JSONRPC, "method": method, "params": params});
        self.write_frame(&encode_line(&message, self.state.budgets.frame_bytes)?)
            .await
    }

    async fn send_cancel(&self, id: u64, reason: &str) -> Result<(), McpError> {
        self.notification(
            "notifications/cancelled",
            json!({"requestId": id, "reason": reason}),
        )
        .await
    }

    async fn write_frame(&self, frame: &[u8]) -> Result<(), McpError> {
        let mut writer = self.state.writer.lock().await;
        let writer = writer.as_mut().ok_or(McpError::Closed)?;
        writer.write_all(frame).await?;
        writer.flush().await?;
        Ok(())
    }

    async fn terminate(&self) {
        terminate_state(&self.state).await;
    }
}

impl Drop for McpStdioClient {
    fn drop(&mut self) {
        self.stderr_task.abort();
    }
}

async fn terminate_state(state: &State) {
    state.closed.store(true, Ordering::Release);
    state.writer.lock().await.take();
    let _ = state
        .child
        .lock()
        .await
        .terminate_and_reap(state.budgets.shutdown)
        .await;
}

async fn read_response(state: &State, expected_id: u64) -> Result<Value, McpError> {
    let mut reader = state.reader.lock().await;
    let bytes = read_bounded_line(&mut *reader, state.budgets.frame_bytes).await?;
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|error| McpError::MalformedFrame(error.to_string()))?;
    let object = value
        .as_object()
        .ok_or_else(|| McpError::MalformedFrame("top-level value is not an object".into()))?;
    if object.get("jsonrpc") != Some(&Value::String(JSONRPC.into()))
        || object.get("id").and_then(Value::as_u64) != Some(expected_id)
    {
        return Err(McpError::UnsolicitedResponse);
    }
    match (object.get("result"), object.get("error")) {
        (Some(result), None) => Ok(result.clone()),
        (None, Some(error)) => {
            let code = error.get("code").and_then(Value::as_i64).ok_or_else(|| {
                McpError::MalformedFrame("JSON-RPC error has no integer code".into())
            })?;
            let message = error
                .get("message")
                .and_then(Value::as_str)
                .ok_or_else(|| McpError::MalformedFrame("JSON-RPC error has no message".into()))?;
            Err(McpError::Remote {
                code,
                message: message.chars().take(2048).collect(),
            })
        }
        _ => Err(McpError::MalformedFrame(
            "response must contain exactly one of result or error".into(),
        )),
    }
}

async fn read_bounded_line<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    maximum: usize,
) -> Result<Vec<u8>, McpError> {
    let mut result = Vec::new();
    loop {
        let available = reader.fill_buf().await?;
        if available.is_empty() {
            return Err(McpError::Disconnected);
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let consumed = newline.map(|index| index + 1).unwrap_or(available.len());
        let content = newline.unwrap_or(consumed);
        if result.len().saturating_add(content) > maximum {
            return Err(McpError::FrameTooLarge);
        }
        result.extend_from_slice(&available[..consumed]);
        reader.consume(consumed);
        if result.last() == Some(&b'\n') {
            result.pop();
            if result.last() == Some(&b'\r') {
                result.pop();
            }
            if result.is_empty() {
                return Err(McpError::MalformedFrame("empty stdio frame".into()));
            }
            return Ok(result);
        }
    }
}

fn encode_line(value: &Value, maximum: usize) -> Result<Vec<u8>, McpError> {
    let mut body =
        serde_json::to_vec(value).map_err(|error| McpError::MalformedFrame(error.to_string()))?;
    if body.is_empty() || body.len() > maximum {
        return Err(McpError::FrameTooLarge);
    }
    body.push(b'\n');
    Ok(body)
}

fn validate_budgets(budgets: StdioBudgets) -> Result<(), McpError> {
    if budgets.initialization.is_zero()
        || budgets.request.is_zero()
        || budgets.frame_bytes == 0
        || budgets.frame_bytes > 8 * 1024 * 1024
        || budgets.idle.is_zero()
        || budgets.shutdown.is_zero()
        || budgets.stderr_bytes == 0
    {
        return Err(McpError::InvalidCatalog("stdio budget is invalid"));
    }
    Ok(())
}

fn validate_catalog(tools: &[ToolDefinition], maximum: usize) -> Result<(), McpError> {
    if tools.len() > 4096 {
        return Err(McpError::InvalidCatalog("tool catalog is too large"));
    }
    let mut names = HashSet::new();
    for tool in tools {
        validate_tool_name(&tool.name)?;
        if !names.insert(&tool.name) {
            return Err(McpError::InvalidCatalog("tool names are not unique"));
        }
        if tool.description.chars().count() > 8192 || !tool.input_schema.is_object() {
            return Err(McpError::InvalidCatalog("tool schema is invalid"));
        }
    }
    if serde_json::to_vec(tools)
        .map_err(|_| McpError::InvalidCatalog("tool catalog is not serializable"))?
        .len()
        > maximum
    {
        return Err(McpError::InvalidCatalog(
            "tool catalog exceeds the frame ceiling",
        ));
    }
    Ok(())
}

fn validate_tool_name(name: &str) -> Result<(), McpError> {
    if name.is_empty()
        || name.len() > 160
        || name.starts_with('.')
        || name
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
    {
        return Err(McpError::InvalidCatalog("tool name is invalid"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn stdio_reader_refuses_oversized_and_unsolicited_frames() {
        let oversized = vec![b'x'; 65];
        let mut input = oversized.as_slice();
        assert!(matches!(
            read_bounded_line(&mut input, 64).await,
            Err(McpError::FrameTooLarge)
        ));

        let response = b"{\"jsonrpc\":\"2.0\",\"id\":8,\"result\":{}}\n";
        let mut reader = BufReader::new(response.as_slice());
        let bytes = read_bounded_line(&mut reader, 1024).await.unwrap();
        let value: Value = serde_json::from_slice(&bytes).unwrap();
        assert_ne!(value.get("id").and_then(Value::as_u64), Some(7));
    }
}
