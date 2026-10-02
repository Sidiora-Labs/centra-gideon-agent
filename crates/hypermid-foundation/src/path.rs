use hypermid_contracts::{Id, Scope};
use serde::{Deserialize, Serialize};
use std::{
    ffi::OsStr,
    path::{Component, Path, PathBuf},
};

const MAX_RECOVERY_COMPONENTS: usize = 64;
const MAX_SYMLINK_COMPONENTS: usize = 40;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Admission {
    Live,
    RecoveryOnly,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CanonicalProjectPath {
    path: PathBuf,
    admission: Admission,
    missing_components: usize,
}

impl CanonicalProjectPath {
    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn admission(&self) -> Admission {
        self.admission
    }

    pub fn missing_components(&self) -> usize {
        self.missing_components
    }

    pub fn permits_new_state(&self) -> bool {
        self.admission == Admission::Live
    }
}

#[derive(Clone, Debug, Deserialize, Eq, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProjectRoot {
    pub scope: Scope,
    pub canonical_path: PathBuf,
    pub admission: Admission,
}

impl ProjectRoot {
    pub fn resolve(
        scope: Scope,
        path: impl AsRef<Path>,
        admission: Admission,
    ) -> Result<Self, PathViolation> {
        let resolved = canonicalize_project_path(path, admission)?;
        Ok(Self {
            scope,
            canonical_path: resolved.path,
            admission,
        })
    }

    pub fn require_live(&self) -> Result<(), PathViolation> {
        if self.admission != Admission::Live {
            return Err(PathViolation::RecoveryCannotCreateState);
        }
        Ok(())
    }
}

pub fn canonicalize_project_path(
    input: impl AsRef<Path>,
    admission: Admission,
) -> Result<CanonicalProjectPath, PathViolation> {
    let input = input.as_ref();
    if input.as_os_str().is_empty() {
        return Err(PathViolation::EmptyPath);
    }
    let absolute = if input.is_absolute() {
        lexical_normalize(input)
    } else {
        lexical_normalize(&std::env::current_dir()?.join(input))
    };

    match admission {
        Admission::Live => {
            let path = std::fs::canonicalize(&absolute).map_err(|source| {
                if source.kind() == std::io::ErrorKind::NotFound {
                    PathViolation::MissingLivePath(absolute.clone())
                } else {
                    PathViolation::Io(source)
                }
            })?;
            ensure_bounded_symlinks(&absolute)?;
            Ok(CanonicalProjectPath {
                path: normalize_native_result(path),
                admission,
                missing_components: 0,
            })
        }
        Admission::RecoveryOnly => recover_missing_path(&absolute),
    }
}

fn recover_missing_path(absolute: &Path) -> Result<CanonicalProjectPath, PathViolation> {
    let mut existing = absolute.to_path_buf();
    let mut tail = Vec::new();
    while !existing.exists() {
        if tail.len() >= MAX_RECOVERY_COMPONENTS {
            return Err(PathViolation::RecoveryTailTooDeep);
        }
        let name = existing
            .file_name()
            .ok_or_else(|| PathViolation::NoExistingPrefix(absolute.to_path_buf()))?;
        tail.push(name.to_os_string());
        if !existing.pop() {
            return Err(PathViolation::NoExistingPrefix(absolute.to_path_buf()));
        }
    }
    ensure_bounded_symlinks(&existing)?;
    let mut path = std::fs::canonicalize(existing)?;
    for component in tail.iter().rev() {
        path.push(component);
    }
    Ok(CanonicalProjectPath {
        path: normalize_native_result(lexical_normalize(&path)),
        admission: Admission::RecoveryOnly,
        missing_components: tail.len(),
    })
}

fn ensure_bounded_symlinks(path: &Path) -> Result<(), PathViolation> {
    let mut current = PathBuf::new();
    let mut symlinks = 0;
    for component in path.components() {
        current.push(component.as_os_str());
        if std::fs::symlink_metadata(&current)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            symlinks += 1;
            if symlinks > MAX_SYMLINK_COMPONENTS {
                return Err(PathViolation::SymlinkLimitExceeded);
            }
        }
    }
    Ok(())
}

fn lexical_normalize(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if !normalized.pop() && !path.is_absolute() {
                    normalized.push(component.as_os_str());
                }
            }
            _ => normalized.push(component.as_os_str()),
        }
    }
    normalized
}

fn normalize_native_result(path: PathBuf) -> PathBuf {
    #[cfg(windows)]
    {
        PathBuf::from(normalize_windows_spelling(&path.to_string_lossy()))
    }
    #[cfg(not(windows))]
    {
        path
    }
}

pub fn normalize_windows_spelling(input: &str) -> String {
    let replaced = input.replace('/', "\\");
    let mut value = if let Some(rest) = replaced.strip_prefix("\\\\?\\UNC\\") {
        format!("\\\\{rest}")
    } else if let Some(rest) = replaced.strip_prefix("\\\\?\\") {
        rest.to_owned()
    } else {
        replaced
    };
    if value.as_bytes().get(1) == Some(&b':') {
        value.replace_range(0..1, &value[0..1].to_ascii_uppercase());
    }
    while value.len() > 3 && value.ends_with('\\') {
        value.pop();
    }
    value
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HomeResolution {
    pub path: PathBuf,
    pub is_relative: bool,
}

pub fn resolve_home(value: Option<&OsStr>, fallback: impl AsRef<Path>) -> HomeResolution {
    let selected = value
        .filter(|candidate| !candidate.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| fallback.as_ref().to_path_buf());
    HomeResolution {
        is_relative: selected.is_relative(),
        path: selected,
    }
}

pub fn module_path(home: &HomeResolution, module_id: &Id) -> PathBuf {
    home.path.join(module_id.as_str())
}

#[derive(Debug, thiserror::Error)]
pub enum PathViolation {
    #[error("project path is empty")]
    EmptyPath,
    #[error("live project path does not exist: {0}")]
    MissingLivePath(PathBuf),
    #[error("recovery path has no existing prefix: {0}")]
    NoExistingPrefix(PathBuf),
    #[error("recovery path exceeds the bounded missing-component limit")]
    RecoveryTailTooDeep,
    #[error("path exceeds the bounded symlink-component limit")]
    SymlinkLimitExceeded,
    #[error("recovery-only identity cannot create durable state")]
    RecoveryCannotCreateState,
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovery_retains_a_missing_tail_without_admitting_state() {
        let directory = tempfile::tempdir().unwrap();
        let missing = directory.path().join("future").join("project");
        assert!(matches!(
            canonicalize_project_path(&missing, Admission::Live),
            Err(PathViolation::MissingLivePath(_))
        ));
        let recovered = canonicalize_project_path(&missing, Admission::RecoveryOnly).unwrap();
        assert_eq!(recovered.missing_components(), 2);
        assert!(!recovered.permits_new_state());
        assert!(recovered.path().is_absolute());
    }

    #[test]
    fn windows_spelling_is_stable() {
        assert_eq!(
            normalize_windows_spelling(r"\\?\c:\work\project\\"),
            r"C:\work\project"
        );
        assert_eq!(
            normalize_windows_spelling(r"\\?\UNC\server\share\project\\"),
            r"\\server\share\project"
        );
    }

    #[cfg(unix)]
    #[test]
    fn aliases_converge_but_distinct_directories_do_not() {
        use std::os::unix::fs::symlink;

        let directory = tempfile::tempdir().unwrap();
        let first = directory.path().join("first");
        let second = directory.path().join("second");
        std::fs::create_dir(&first).unwrap();
        std::fs::create_dir(&second).unwrap();
        let alias = directory.path().join("alias");
        symlink(&first, &alias).unwrap();

        let first = canonicalize_project_path(&first, Admission::Live).unwrap();
        let alias = canonicalize_project_path(alias.join("."), Admission::Live).unwrap();
        let second = canonicalize_project_path(&second, Admission::Live).unwrap();
        assert_eq!(first.path(), alias.path());
        assert_ne!(first.path(), second.path());
    }
}
