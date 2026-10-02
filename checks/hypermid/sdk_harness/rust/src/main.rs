use std::{
    env,
    path::PathBuf,
    pin::Pin,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    task::{Context, Poll},
};

use hypermid_client::{
    connect_record, open_tls, ClientError, EffectKind, HypermidClient, LocalTlsStream,
};
use hypermid_contracts::{Id, Scope};
use hypermid_protocol::ConnectionClass;
use hypermid_transport::ConnectionRecord;
use serde_json::json;
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};

struct DisconnectAfterFlush<S> {
    inner: S,
    armed: Arc<AtomicBool>,
    shutting_down: bool,
}

impl<S: AsyncRead + Unpin> AsyncRead for DisconnectAfterFlush<S> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<std::io::Result<()>> {
        if self.shutting_down {
            return Poll::Ready(Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "transport disconnected after mutation dispatch",
            )));
        }
        Pin::new(&mut self.inner).poll_read(context, buffer)
    }
}

impl<S: AsyncWrite + Unpin> AsyncWrite for DisconnectAfterFlush<S> {
    fn poll_write(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<std::io::Result<usize>> {
        Pin::new(&mut self.inner).poll_write(context, bytes)
    }

    fn poll_flush(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
    ) -> Poll<std::io::Result<()>> {
        if !self.shutting_down {
            match Pin::new(&mut self.inner).poll_flush(context) {
                Poll::Ready(Ok(())) if self.armed.swap(false, Ordering::AcqRel) => {
                    self.shutting_down = true;
                }
                other => return other,
            }
        }
        Pin::new(&mut self.inner).poll_shutdown(context)
    }

    fn poll_shutdown(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
    ) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(context)
    }
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = env::args_os().skip(1);
    let record_path = PathBuf::from(arguments.next().ok_or("record path is required")?);
    let invalid_record_path =
        PathBuf::from(arguments.next().ok_or("invalid record path is required")?);
    let scope = Scope::new(
        Id::new("sdk-owner")?,
        Id::new("sdk-project")?,
        Some(Id::new("sdk-workspace")?),
    );

    let invalid =
        connect_record(&invalid_record_path, scope.clone(), ConnectionClass::Client).await;
    if !matches!(invalid, Err(ClientError::Protocol(_))) {
        return Err("invalid protocol connection record was accepted".into());
    }

    let client = connect_record(&record_path, scope.clone(), ConnectionClass::Client).await?;
    let description = client.describe().await?;
    if description.get("protocol").and_then(|value| value.as_str()) != Some("hypermid.v1") {
        return Err("describe did not report hypermid.v1".into());
    }
    let marker = json!({"language": "rust", "value": 7});
    if client.passthrough(marker.clone()).await? != marker {
        return Err("passthrough payload changed".into());
    }
    client
        .request(
            "events.publish",
            json!({
                "event_id": "sdk-rust-event",
                "topic": "events.sdk",
                "scope": scope,
                "at_ms": 1,
                "schema_name": "sdk.client",
                "schema_version": 1,
                "payload": marker,
            }),
            EffectKind::Mutation,
            None,
        )
        .await?;
    let consumer_id = Id::new("sdk-rust-consumer")?;
    let mut subscription = client
        .subscribe_events(
            consumer_id.clone(),
            scope.clone(),
            "events.*",
            None,
            None,
        )
        .await?;
    let event = subscription.next_event().await?;
    if !subscription.acknowledge(&event).await?.acknowledged {
        return Err("durable event was not acknowledged".into());
    }
    let cursor = subscription.resume_point().cursor;
    subscription.close().await?;
    client.close().await?;

    let resumed = connect_record(&record_path, scope.clone(), ConnectionClass::Client).await?;
    let resumed_subscription = resumed
        .resume_events(
            consumer_id.clone(),
            scope.clone(),
            "events.*",
            cursor,
            None,
        )
        .await?;
    if resumed_subscription.resume_point().cursor != cursor {
        return Err("cursor resume changed the authoritative cursor".into());
    }
    let foreign_cursor = hypermid_contracts::Cursor::new(cursor.epoch + 1, cursor.sequence)?;
    match resumed
        .resume_events(
            consumer_id,
            scope.clone(),
            "events.*",
            foreign_cursor,
            None,
        )
        .await
    {
        Err(ClientError::Remote(error))
            if error.code == "CURSOR_GAP" || error.code == "CONSUMER_RESUME_MISMATCH" => {}
        Err(error) => return Err(format!("foreign cursor returned {error}").into()),
        Ok(_) => return Err("foreign cursor was accepted".into()),
    }
    resumed_subscription.close().await?;
    resumed.close().await?;

    let record = ConnectionRecord::load(&record_path)?;
    let secret = record.secret_bytes()?;
    let armed = Arc::new(AtomicBool::new(false));
    let stream: LocalTlsStream = open_tls(&record).await?;
    let disconnecting = DisconnectAfterFlush {
        inner: stream,
        armed: Arc::clone(&armed),
        shutting_down: false,
    };
    let mutation_client =
        HypermidClient::connect(disconnecting, &secret, scope, ConnectionClass::Client).await?;
    armed.store(true, Ordering::Release);
    let outcome = mutation_client
        .request("diagnostics.rerun", json!({}), EffectKind::Mutation, None)
        .await;
    if !matches!(outcome, Err(ClientError::OutcomeUnknown)) {
        return Err(format!("mutation disconnect returned {outcome:?}").into());
    }

    println!(
        "{}",
        json!({
            "language": "rust",
            "tls": true,
            "describe": true,
            "passthrough": true,
            "durable_ack": true,
            "reconnect_resume": true,
            "wrong_epoch_refused": true,
            "invalid_version_refused": true,
            "mutation_outcome_unknown": true
        })
    );
    Ok(())
}
