use sha2::{Digest as _, Sha256};
use std::{
    io,
    path::{Path, PathBuf},
    process::Command,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GitCommitDocument {
    pub commit_id: String,
    pub content: String,
    pub content_digest: String,
    pub source_time_ms: i64,
    pub parent_count: usize,
}

#[derive(Clone, Debug)]
pub struct GitScan {
    pub repository: PathBuf,
    pub repository_identity_digest: String,
    pub refs: Vec<String>,
    pub refs_digest: String,
    pub documents: Vec<GitCommitDocument>,
}

pub fn scan(
    repository: &Path,
    refs: &[String],
    max_commits: usize,
    skip_merges: bool,
) -> io::Result<GitScan> {
    if refs.is_empty() || max_commits == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "refs and a positive commit limit are required",
        ));
    }
    let repository = repository.canonicalize()?;
    let common = git(&repository, &["rev-parse", "--git-common-dir"])?;
    let common = repository.join(common.trim()).canonicalize()?;
    let identity = hex_digest(common.to_string_lossy().as_bytes());
    let resolved = refs
        .iter()
        .map(|reference| git(&repository, &["rev-parse", "--verify", reference]))
        .collect::<io::Result<Vec<_>>>()?;
    let refs_digest = hex_digest(
        resolved
            .iter()
            .map(|value| value.trim())
            .collect::<Vec<_>>()
            .join("\n")
            .as_bytes(),
    );
    let mut arguments = vec![
        "log".to_string(),
        format!("--max-count={max_commits}"),
        "--format=%H%x1f%P%x1f%ct%x1f%B%x1e".to_string(),
    ];
    if skip_merges {
        arguments.push("--no-merges".to_string());
    }
    arguments.extend(resolved.iter().map(|value| value.trim().to_string()));
    let argument_refs = arguments.iter().map(String::as_str).collect::<Vec<_>>();
    let output = git(&repository, &argument_refs)?;
    let mut documents = Vec::new();
    for entry in output
        .split('\u{1e}')
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
    {
        let fields = entry.splitn(4, '\u{1f}').collect::<Vec<_>>();
        if fields.len() != 4 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "malformed git log output",
            ));
        }
        let content = fields[3].trim().to_string();
        documents.push(GitCommitDocument {
            commit_id: fields[0].to_string(),
            content_digest: hex_digest(content.as_bytes()),
            content,
            source_time_ms: fields[2]
                .parse::<i64>()
                .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid commit time"))?
                * 1000,
            parent_count: fields[1].split_whitespace().count(),
        });
    }
    if documents.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "repository has no reachable commits",
        ));
    }
    Ok(GitScan {
        repository,
        repository_identity_digest: identity,
        refs: refs.to_vec(),
        refs_digest,
        documents,
    })
}

pub fn revalidate(scan: &GitScan) -> io::Result<bool> {
    let common = git(&scan.repository, &["rev-parse", "--git-common-dir"])?;
    let identity = hex_digest(
        scan.repository
            .join(common.trim())
            .canonicalize()?
            .to_string_lossy()
            .as_bytes(),
    );
    let resolved = scan
        .refs
        .iter()
        .map(|reference| git(&scan.repository, &["rev-parse", "--verify", reference]))
        .collect::<io::Result<Vec<_>>>()?;
    let refs_digest = hex_digest(
        resolved
            .iter()
            .map(|value| value.trim())
            .collect::<Vec<_>>()
            .join("\n")
            .as_bytes(),
    );
    Ok(identity == scan.repository_identity_digest && refs_digest == scan.refs_digest)
}

fn git(repository: &Path, arguments: &[&str]) -> io::Result<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repository)
        .args(arguments)
        .output()?;
    if !output.status.success() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            String::from_utf8_lossy(&output.stderr).trim().to_string(),
        ));
    }
    String::from_utf8(output.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn hex_digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
