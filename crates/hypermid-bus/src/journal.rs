use std::fs::{self, File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::marker::PhantomData;
use std::path::{Path, PathBuf};

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

const HEADER_BYTES: usize = 12;
const CHECKSUM_BYTES: usize = 32;
const MAX_RECORD_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredRecord<T> {
    sequence: u64,
    value: T,
}

#[derive(Debug, thiserror::Error)]
pub enum JournalError {
    #[error("journal I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("journal record serialization failed: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("journal record {sequence} is corrupt")]
    Corrupt { sequence: u64 },
    #[error("journal record exceeds the 8 MiB bound")]
    RecordTooLarge,
    #[error("journal sequence is exhausted")]
    SequenceExhausted,
}

pub struct Journal<T> {
    path: PathBuf,
    file: File,
    next_sequence: u64,
    entries: Vec<(u64, T)>,
    _record: PhantomData<T>,
}

impl<T> Journal<T>
where
    T: Clone + DeserializeOwned + Serialize,
{
    pub fn open(path: impl AsRef<Path>) -> Result<Self, JournalError> {
        let path = path.as_ref().to_path_buf();
        prepare_parent(&path)?;
        let bytes = match fs::read(&path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(error) => return Err(error.into()),
        };
        let (entries, valid_len) = decode_records::<T>(&bytes)?;
        let next_sequence = entries.last().map_or(Ok(1), |(sequence, _)| {
            sequence
                .checked_add(1)
                .ok_or(JournalError::SequenceExhausted)
        })?;
        let mut options = OpenOptions::new();
        options.create(true).read(true).write(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&path)?;
        if valid_len != bytes.len() {
            file.set_len(valid_len as u64)?;
            file.sync_all()?;
        }
        file.seek(SeekFrom::End(0))?;
        Ok(Self {
            path,
            file,
            next_sequence,
            entries,
            _record: PhantomData,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn entries(&self) -> &[(u64, T)] {
        &self.entries
    }

    pub fn append(&mut self, value: T) -> Result<u64, JournalError> {
        let sequence = self.next_sequence;
        let payload = serde_json::to_vec(&StoredRecord {
            sequence,
            value: value.clone(),
        })?;
        if payload.is_empty() || payload.len() > MAX_RECORD_BYTES {
            return Err(JournalError::RecordTooLarge);
        }
        let checksum = checksum(sequence, &payload);
        self.file.write_all(&(payload.len() as u32).to_be_bytes())?;
        self.file.write_all(&sequence.to_be_bytes())?;
        self.file.write_all(&payload)?;
        self.file.write_all(&checksum)?;
        self.file.sync_data()?;
        self.entries.push((sequence, value));
        self.next_sequence = sequence
            .checked_add(1)
            .ok_or(JournalError::SequenceExhausted)?;
        Ok(sequence)
    }
}

fn decode_records<T>(bytes: &[u8]) -> Result<(Vec<(u64, T)>, usize), JournalError>
where
    T: DeserializeOwned,
{
    let mut entries = Vec::new();
    let mut offset = 0usize;
    let mut expected_sequence = 1u64;
    while offset < bytes.len() {
        if bytes.len() - offset < HEADER_BYTES {
            break;
        }
        let payload_len =
            u32::from_be_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        let sequence = u64::from_be_bytes(bytes[offset + 4..offset + 12].try_into().unwrap());
        if payload_len == 0 || payload_len > MAX_RECORD_BYTES || sequence != expected_sequence {
            return Err(JournalError::Corrupt { sequence });
        }
        let record_len = HEADER_BYTES
            .checked_add(payload_len)
            .and_then(|value| value.checked_add(CHECKSUM_BYTES))
            .ok_or(JournalError::RecordTooLarge)?;
        if bytes.len() - offset < record_len {
            break;
        }
        let payload_start = offset + HEADER_BYTES;
        let payload_end = payload_start + payload_len;
        let expected_checksum = checksum(sequence, &bytes[payload_start..payload_end]);
        if bytes[payload_end..payload_end + CHECKSUM_BYTES] != expected_checksum {
            return Err(JournalError::Corrupt { sequence });
        }
        let stored: StoredRecord<T> = serde_json::from_slice(&bytes[payload_start..payload_end])?;
        if stored.sequence != sequence {
            return Err(JournalError::Corrupt { sequence });
        }
        entries.push((sequence, stored.value));
        expected_sequence = expected_sequence
            .checked_add(1)
            .ok_or(JournalError::SequenceExhausted)?;
        offset += record_len;
    }
    Ok((entries, offset))
}

fn checksum(sequence: u64, payload: &[u8]) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(sequence.to_be_bytes());
    digest.update(payload);
    digest.finalize().into()
}

fn prepare_parent(path: &Path) -> Result<(), std::io::Error> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
        }
    }
    Ok(())
}
