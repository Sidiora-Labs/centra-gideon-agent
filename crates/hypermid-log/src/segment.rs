use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SegmentError {
    #[error("daily log segment exceeded its configured size")]
    Oversized,
    #[error("log segment I/O failed: {0}")]
    Io(#[from] io::Error),
    #[error("UTC date must use YYYY-MM-DD")]
    InvalidDate,
    #[error("log write must contain exactly one complete line")]
    IncompleteLine,
}

pub struct DailySegmentWriter {
    root: PathBuf,
    maximum_bytes: u64,
    oversized_reported: AtomicBool,
}

impl DailySegmentWriter {
    pub fn new(root: impl Into<PathBuf>, maximum_bytes: u64) -> io::Result<Self> {
        let root = root.into();
        fs::create_dir_all(&root)?;
        set_owner_only(&root)?;
        Ok(Self {
            root,
            maximum_bytes,
            oversized_reported: AtomicBool::new(false),
        })
    }

    pub fn append(&self, utc_date: &str, line: &[u8]) -> Result<(), SegmentError> {
        validate_date(utc_date)?;
        if !line.ends_with(b"\n") || line[..line.len().saturating_sub(1)].contains(&b'\n') {
            return Err(SegmentError::IncompleteLine);
        }
        let path = self.root.join(format!("hypermid-{utc_date}.log"));
        let mut options = OpenOptions::new();
        options.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(path)?;
        file.write_all(line)?;
        if file.metadata()?.len() > self.maximum_bytes
            && !self.oversized_reported.swap(true, Ordering::SeqCst)
        {
            return Err(SegmentError::Oversized);
        }
        Ok(())
    }

    pub fn prune_before(&self, cutoff_utc_date: &str) -> Result<Vec<PathBuf>, SegmentError> {
        validate_date(cutoff_utc_date)?;
        let mut removed = Vec::new();
        for entry in fs::read_dir(&self.root)? {
            let entry = entry?;
            let name = entry.file_name();
            let name = name.to_string_lossy();
            let Some(date) = name
                .strip_prefix("hypermid-")
                .and_then(|value| value.strip_suffix(".log"))
            else {
                continue;
            };
            if validate_date(date).is_ok() && date < cutoff_utc_date {
                fs::remove_file(entry.path())?;
                removed.push(entry.path());
            }
        }
        Ok(removed)
    }
}

fn validate_date(value: &str) -> Result<(), SegmentError> {
    if value.len() == 10
        && value.bytes().enumerate().all(|(index, byte)| {
            if index == 4 || index == 7 {
                byte == b'-'
            } else {
                byte.is_ascii_digit()
            }
        })
    {
        Ok(())
    } else {
        Err(SegmentError::InvalidDate)
    }
}

fn set_owner_only(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}
