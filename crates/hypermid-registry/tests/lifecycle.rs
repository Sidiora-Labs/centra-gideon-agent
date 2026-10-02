use ed25519_dalek::{Signer, SigningKey};
use flate2::{write::GzEncoder, Compression};
use hypermid_artifacts::{
    stage_verified, ArtifactFile, ArtifactManifest, FileKind, InstallLimits, Platform,
    SignedArtifactManifest, TrustStore,
};
use hypermid_registry::service::{linux, macos, run_exact, windows};
use hypermid_registry::{
    RegistryLifecycle, ReleaseEntry, ReleaseIndex, ReleaseIndexError, SignedReleaseIndex,
    VerifiedReleaseIndex,
};
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;
use std::fs;
use tar::{Builder, Header};

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn signed_artifact(bytes: &[u8], signing: &SigningKey) -> (Vec<u8>, SignedArtifactManifest) {
    let gzip = GzEncoder::new(Vec::new(), Compression::default());
    let mut builder = Builder::new(gzip);
    let mut header = Header::new_gnu();
    header.set_size(bytes.len() as u64);
    header.set_mode(0o755);
    header.set_cksum();
    builder
        .append_data(&mut header, "bin/hypermid-tool", bytes)
        .unwrap();
    let archive = builder.into_inner().unwrap().finish().unwrap();
    let artifact_digest = sha(&archive);
    let source_digest = sha(bytes);
    let manifest = ArtifactManifest {
        schema_version: 1,
        artifact_id: "hypermid-tool".to_owned(),
        version: "1.0.0".to_owned(),
        platform: Platform::current(),
        archive_sha256: artifact_digest,
        publisher_id: "hypermid-release".to_owned(),
        source_uri: "file:///usr/bin/true".to_owned(),
        source_sha256: source_digest,
        capabilities: BTreeSet::new(),
        entrypoint: "bin/hypermid-tool".to_owned(),
        files: vec![ArtifactFile {
            path: "bin/hypermid-tool".to_owned(),
            kind: FileKind::Regular,
            sha256: sha(bytes),
            size: bytes.len() as u64,
            link_target: None,
            executable: true,
        }],
    };
    let signature = hex::encode(
        signing
            .sign(&manifest.canonical_bytes().unwrap())
            .to_bytes(),
    );
    (
        archive,
        SignedArtifactManifest {
            manifest,
            signature,
        },
    )
}

#[test]
fn signed_fresh_release_activates_authentic_binary_and_uninstall_preserves_changes() {
    let root = tempfile::tempdir().unwrap();
    let authentic_binary = fs::read("/usr/bin/true").unwrap();
    let signing = SigningKey::from_bytes(&[11_u8; 32]);
    let mut trust = TrustStore::default();
    trust
        .insert("hypermid-release", signing.verifying_key().to_bytes())
        .unwrap();
    let (archive, signed_manifest) = signed_artifact(&authentic_binary, &signing);
    let manifest_bytes = signed_manifest.manifest.canonical_bytes().unwrap();

    let index = ReleaseIndex {
        schema_version: 1,
        publisher_id: "hypermid-release".to_owned(),
        sequence: 7,
        issued_at_ms: 1_000,
        expires_at_ms: 2_000,
        releases: vec![ReleaseEntry {
            artifact_id: "hypermid-tool".to_owned(),
            version: "1.0.0".to_owned(),
            platform: Platform::current(),
            archive_sha256: sha(&archive),
            manifest_sha256: sha(&manifest_bytes),
            source_uri: "file:///usr/bin/true".to_owned(),
            protocol_min: 1,
            protocol_max: 2,
        }],
    };
    let signed_index = SignedReleaseIndex {
        signature: hex::encode(signing.sign(&index.canonical_bytes().unwrap()).to_bytes()),
        index,
    };
    let verified = VerifiedReleaseIndex::verify(signed_index, &trust, 1_500).unwrap();
    let release = verified
        .select("hypermid-tool", "1.0.0", &Platform::current(), 2)
        .unwrap();
    assert_eq!(
        release.archive_sha256,
        signed_manifest.manifest.archive_sha256
    );
    assert_eq!(release.manifest_sha256, sha(&manifest_bytes));

    let staged = stage_verified(
        &archive,
        &signed_manifest,
        &trust,
        &Platform::current(),
        &BTreeSet::new(),
        &root.path().join("staging"),
        InstallLimits::default(),
    )
    .unwrap();
    let registry_root = root.path().join("registry");
    let registry = RegistryLifecycle::open(&registry_root).unwrap();
    let active = registry.activate(staged).unwrap();
    let generation = active
        .generation_path
        .file_name()
        .unwrap()
        .to_str()
        .unwrap()
        .to_owned();
    let installed = active.generation_path.join("bin/hypermid-tool");
    assert_eq!(fs::read(&installed).unwrap(), authentic_binary);

    fs::write(&installed, b"owner modified this path").unwrap();
    let result = registry.uninstall_generation(&generation).unwrap();
    assert!(result.removed.is_empty());
    assert_eq!(
        result.preserved_modified,
        vec![format!(
            "artifacts/generations/{generation}/bin/hypermid-tool"
        )]
    );
    assert_eq!(fs::read(installed).unwrap(), b"owner modified this path");
}

#[test]
fn stale_or_incompatible_release_index_stops_before_install() {
    let signing = SigningKey::from_bytes(&[13_u8; 32]);
    let mut trust = TrustStore::default();
    trust
        .insert("hypermid-release", signing.verifying_key().to_bytes())
        .unwrap();
    let index = ReleaseIndex {
        schema_version: 1,
        publisher_id: "hypermid-release".to_owned(),
        sequence: 1,
        issued_at_ms: 100,
        expires_at_ms: 200,
        releases: Vec::new(),
    };
    let signed = SignedReleaseIndex {
        signature: hex::encode(signing.sign(&index.canonical_bytes().unwrap()).to_bytes()),
        index,
    };
    assert!(matches!(
        VerifiedReleaseIndex::verify(signed, &trust, 200).unwrap_err(),
        ReleaseIndexError::StaleMetadata
    ));
}

#[test]
fn service_definitions_preserve_drain_and_exact_manager_refusal() {
    let executable = std::path::Path::new("/opt/gideon/bin/hypermid");
    let linux = linux::definition(executable, 30_500);
    assert!(linux.contents.contains("Restart=on-failure"));
    assert!(linux.contents.contains("KillMode=control-group"));
    assert!(linux.contents.contains("TimeoutStopSec=36"));

    let macos = macos::definition(executable, 30_500);
    assert!(macos.contents.contains("<key>RunAtLoad</key><true/>"));
    assert!(macos
        .contents
        .contains("<key>ExitTimeOut</key><integer>36</integer>"));

    let windows = windows::definition(executable, 30_500);
    assert!(windows.contents.contains("<LogonTrigger>"));
    assert!(windows.contents.contains("--service-stop-timeout-ms 35500"));

    let error = run_exact(
        "/bin/sh",
        &[
            "-c".to_owned(),
            "printf 'native manager refusal' >&2; exit 17".to_owned(),
        ],
    )
    .unwrap_err();
    let text = error.to_string();
    assert!(text.contains("native manager refusal"));
    assert!(text.contains("17"));
}
