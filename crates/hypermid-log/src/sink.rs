use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

pub struct CaptureSink {
    path: PathBuf,
    maximum_bytes: u64,
    generations: usize,
}

impl CaptureSink {
    pub fn new(
        path: impl Into<PathBuf>,
        maximum_bytes: u64,
        generations: usize,
    ) -> io::Result<Self> {
        let path = path.into();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        Ok(Self {
            path,
            maximum_bytes,
            generations,
        })
    }

    pub fn write_line(&mut self, line: &[u8]) -> io::Result<()> {
        require_line(line)?;
        let current = fs::metadata(&self.path).map(|meta| meta.len()).unwrap_or(0);
        if current > 0 && current.saturating_add(line.len() as u64) > self.maximum_bytes {
            self.rotate()?;
        }
        let mut options = OpenOptions::new();
        options.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&self.path)?;
        file.write_all(line)?;
        file.flush()
    }

    fn rotate(&self) -> io::Result<()> {
        if self.generations == 0 {
            return fs::remove_file(&self.path).or_else(|error| {
                if error.kind() == io::ErrorKind::NotFound {
                    Ok(())
                } else {
                    Err(error)
                }
            });
        }
        for generation in (1..=self.generations).rev() {
            let destination = rotated(&self.path, generation);
            if generation == self.generations {
                let _ = fs::remove_file(&destination);
            }
            let source = if generation == 1 {
                self.path.clone()
            } else {
                rotated(&self.path, generation - 1)
            };
            match fs::rename(source, destination) {
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }
}

fn rotated(path: &Path, generation: usize) -> PathBuf {
    PathBuf::from(format!("{}.{}", path.display(), generation))
}

fn require_line(line: &[u8]) -> io::Result<()> {
    if line.ends_with(b"\n") && !line[..line.len().saturating_sub(1)].contains(&b'\n') {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "capture write must contain one complete line",
        ))
    }
}

#[derive(Default)]
pub struct SinkHealth {
    fallback_reported: AtomicBool,
    swallowed_writes: AtomicU64,
}
impl SinkHealth {
    pub fn fallback_reported(&self) -> bool {
        self.fallback_reported.load(Ordering::Relaxed)
    }
    pub fn swallowed_writes(&self) -> u64 {
        self.swallowed_writes.load(Ordering::Relaxed)
    }
}

pub struct ResilientSink<W: Write> {
    writer: W,
    health: SinkHealth,
}
impl<W: Write> ResilientSink<W> {
    pub fn new(writer: W) -> Self {
        Self {
            writer,
            health: SinkHealth::default(),
        }
    }
    pub fn write_line(&mut self, line: &[u8]) -> bool {
        if require_line(line).is_ok() && self.writer.write_all(line).is_ok() {
            return true;
        }
        self.health.swallowed_writes.fetch_add(1, Ordering::Relaxed);
        if !self.health.fallback_reported.swap(true, Ordering::SeqCst) {
            eprintln!("hypermid.log fallback: log sink unavailable");
        }
        false
    }
    pub fn health(&self) -> &SinkHealth {
        &self.health
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capture_rotates_only_complete_lines() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("capture.log");
        let mut sink = CaptureSink::new(&path, 8, 2).unwrap();
        sink.write_line(b"one\n").unwrap();
        sink.write_line(b"two\n").unwrap();
        sink.write_line(b"three\n").unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"three\n");
        assert_eq!(fs::read(rotated(&path, 1)).unwrap(), b"one\ntwo\n");
        assert!(sink.write_line(b"partial").is_err());
    }
}
