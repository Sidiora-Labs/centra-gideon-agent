use std::marker::PhantomData;

use hypermid_protocol::{parse_json, Envelope, ProtocolError, MAX_FRAME_BYTES};
use serde::{de::DeserializeOwned, Serialize};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use crate::TransportError;

pub async fn read_json_frame<R, T>(reader: &mut R) -> Result<T, TransportError>
where
    R: AsyncRead + Unpin,
    T: DeserializeOwned,
{
    let length = reader.read_u32().await? as usize;
    if length == 0 {
        return Err(ProtocolError::EmptyFrame.into());
    }
    if length > MAX_FRAME_BYTES {
        return Err(ProtocolError::FrameTooLarge {
            actual: length,
            maximum: MAX_FRAME_BYTES,
        }
        .into());
    }
    let mut body = vec![0; length];
    reader.read_exact(&mut body).await?;
    parse_json(&body).map_err(Into::into)
}

pub async fn write_json_frame<W, T>(writer: &mut W, value: &T) -> Result<(), TransportError>
where
    W: AsyncWrite + Unpin,
    T: Serialize,
{
    let body =
        serde_json::to_vec(value).map_err(|error| ProtocolError::InvalidJson(error.to_string()))?;
    if body.is_empty() || body.len() > MAX_FRAME_BYTES {
        return Err(ProtocolError::FrameTooLarge {
            actual: body.len(),
            maximum: MAX_FRAME_BYTES,
        }
        .into());
    }
    writer.write_all(&(body.len() as u32).to_be_bytes()).await?;
    writer.write_all(&body).await?;
    writer.flush().await?;
    Ok(())
}

const MAX_SAFE_SEQUENCE: u64 = 9_007_199_254_740_991;

#[derive(Clone, Debug)]
pub struct SessionSequence {
    next_inbound: u64,
    next_outbound: u64,
}

impl Default for SessionSequence {
    fn default() -> Self {
        Self {
            next_inbound: 1,
            next_outbound: 1,
        }
    }
}

impl SessionSequence {
    pub fn next_outbound(&mut self) -> Result<u64, TransportError> {
        let sequence = self.next_outbound;
        if sequence > MAX_SAFE_SEQUENCE {
            return Err(TransportError::InvalidSequence {
                expected: MAX_SAFE_SEQUENCE,
                received: sequence,
            });
        }
        self.next_outbound = sequence + 1;
        Ok(sequence)
    }

    pub fn accept_inbound(&mut self, sequence: u64) -> Result<(), TransportError> {
        if sequence != self.next_inbound || sequence > MAX_SAFE_SEQUENCE {
            return Err(TransportError::InvalidSequence {
                expected: self.next_inbound,
                received: sequence,
            });
        }
        self.next_inbound = sequence + 1;
        Ok(())
    }
}

pub struct FrameCodec<T> {
    sequence: SessionSequence,
    _message: PhantomData<T>,
}

impl<T> Default for FrameCodec<T> {
    fn default() -> Self {
        Self {
            sequence: SessionSequence::default(),
            _message: PhantomData,
        }
    }
}

impl FrameCodec<Envelope> {
    pub async fn read<R: AsyncRead + Unpin>(
        &mut self,
        reader: &mut R,
    ) -> Result<Envelope, TransportError> {
        let envelope: Envelope = read_json_frame(reader).await?;
        envelope.validate()?;
        self.sequence.accept_inbound(envelope.sequence)?;
        Ok(envelope)
    }

    pub async fn write<W: AsyncWrite + Unpin>(
        &mut self,
        writer: &mut W,
        mut envelope: Envelope,
    ) -> Result<(), TransportError> {
        envelope.sequence = self.sequence.next_outbound()?;
        envelope.validate()?;
        write_json_frame(writer, &envelope).await
    }
}
