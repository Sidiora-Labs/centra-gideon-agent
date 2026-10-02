use ed25519_dalek::{Signer, SigningKey};
use flate2::{write::GzEncoder, Compression};
use hypermid_artifacts::{
    stage_verified, ActivationBoundary, ActivationError, ArtifactCapability, ArtifactFile,
    ArtifactManifest, ArtifactStore, FileKind, InstallError, InstallLimits, Platform,
    SignedArtifactManifest, TrustStore,
};
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;
use std::io;
use std::path::Path;
use tar::{Builder, Header};

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn archive(files: &[(&str, &[u8])]) -> Vec<u8> {
    let gzip = GzEncoder::new(Vec::new(), Compression::default());
    let mut builder = Builder::new(gzip);
    for (path, bytes) in files {
        let mut header = Header::new_gnu();
        header.set_size(bytes.len() as u64);
        header.set_mode(0o755);
        header.set_cksum();
        builder.append_data(&mut header, path, *bytes).unwrap();
    }
    builder.into_inner().unwrap().finish().unwrap()
}

fn signed(
    archive: &[u8],
    version: &str,
    files: Vec<ArtifactFile>,
    capabilities: BTreeSet<ArtifactCapability>,
) -> (SignedArtifactManifest, TrustStore) {
    let signing = SigningKey::from_bytes(&[7_u8; 32]);
    let manifest = ArtifactManifest {
        schema_version: 1,
        artifact_id: "hypermid-tool".to_owned(),
        version: version.to_owned(),
        platform: Platform::current(),
        archive_sha256: sha(archive),
        publisher_id: "hypermid-release".to_owned(),
        source_uri: "https://downloads.example.invalid/hypermid-tool.tar.gz".to_owned(),
        source_sha256: sha(archive),
        capabilities,
        entrypoint: "bin/tool".to_owned(),
        files,
    };
    let signature = hex::encode(
        signing
            .sign(&manifest.canonical_bytes().unwrap())
            .to_bytes(),
    );
    let mut trust = TrustStore::default();
    trust
        .insert("hypermid-release", signing.verifying_key().to_bytes())
        .unwrap();
    (
        SignedArtifactManifest {
            manifest,
            signature,
        },
        trust,
    )
}

fn regular(path: &str, bytes: &[u8], executable: bool) -> ArtifactFile {
    ArtifactFile {
        path: path.to_owned(),
        kind: FileKind::Regular,
        sha256: sha(bytes),
        size: bytes.len() as u64,
        link_target: None,
        executable,
    }
}

fn staged(root: &Path, version: &str, bytes: &[u8]) -> hypermid_artifacts::VerifiedArtifact {
    let archive = archive(&[("bin/tool", bytes)]);
    let (signed, trust) = signed(
        &archive,
        version,
        vec![regular("bin/tool", bytes, true)],
        BTreeSet::new(),
    );
    stage_verified(
        &archive,
        &signed,
        &trust,
        &Platform::current(),
        &BTreeSet::new(),
        &root.join("staging"),
        InstallLimits::default(),
    )
    .unwrap()
}

#[test]
fn real_signature_digest_platform_and_capability_approval_gate_staging() {
    let root = tempfile::tempdir().unwrap();
    let bytes = b"verified data, never executed during installation\n";
    let archive = archive(&[("bin/tool", bytes)]);
    let (mut signed, trust) = signed(
        &archive,
        "1.0.0",
        vec![regular("bin/tool", bytes, true)],
        BTreeSet::from([ArtifactCapability::Network]),
    );

    let error = stage_verified(
        &archive,
        &signed,
        &trust,
        &Platform::current(),
        &BTreeSet::new(),
        &root.path().join("staging"),
        InstallLimits::default(),
    )
    .unwrap_err();
    assert!(matches!(error, InstallError::CapabilityApprovalRequired(_)));
    assert!(!root.path().join("staging").exists());

    signed.manifest.platform.os = "unsupported-os".to_owned();
    let error = stage_verified(
        &archive,
        &signed,
        &trust,
        &Platform::current(),
        &BTreeSet::from([ArtifactCapability::Network]),
        &root.path().join("staging"),
        InstallLimits::default(),
    )
    .unwrap_err();
    assert!(matches!(
        error,
        InstallError::Verification(hypermid_artifacts::VerificationError::InvalidSignature)
    ));
}

#[test]
fn archive_collision_and_escaping_link_are_rejected_inside_staging() {
    let root = tempfile::tempdir().unwrap();
    let bytes = b"one";
    let archive = archive(&[("bin/tool", bytes), ("BIN/TOOL", bytes)]);
    let (signed_collision, trust) = signed(
        &archive,
        "1.0.0",
        vec![
            regular("bin/tool", bytes, true),
            regular("BIN/TOOL", bytes, true),
        ],
        BTreeSet::new(),
    );
    assert!(matches!(
        stage_verified(
            &archive,
            &signed_collision,
            &trust,
            &Platform::current(),
            &BTreeSet::new(),
            &root.path().join("staging"),
            InstallLimits::default(),
        )
        .unwrap_err(),
        InstallError::PathCollision(_)
    ));

    let gzip = GzEncoder::new(Vec::new(), Compression::default());
    let mut builder = Builder::new(gzip);
    let mut regular_header = Header::new_gnu();
    regular_header.set_size(bytes.len() as u64);
    regular_header.set_mode(0o755);
    regular_header.set_cksum();
    builder
        .append_data(&mut regular_header, "bin/tool", &bytes[..])
        .unwrap();
    let mut link_header = Header::new_gnu();
    link_header.set_entry_type(tar::EntryType::Symlink);
    link_header.set_size(0);
    link_header.set_mode(0o777);
    link_header.set_link_name("../../../outside").unwrap();
    link_header.set_cksum();
    builder
        .append_data(&mut link_header, "bin/escape", io::empty())
        .unwrap();
    let archive = builder.into_inner().unwrap().finish().unwrap();
    let target = "../../../outside";
    let files = vec![
        regular("bin/tool", bytes, true),
        ArtifactFile {
            path: "bin/escape".to_owned(),
            kind: FileKind::Symlink,
            sha256: sha(target.as_bytes()),
            size: target.len() as u64,
            link_target: Some(target.to_owned()),
            executable: false,
        },
    ];
    let (signed, trust) = signed(&archive, "1.0.1", files, BTreeSet::new());
    assert!(matches!(
        stage_verified(
            &archive,
            &signed,
            &trust,
            &Platform::current(),
            &BTreeSet::new(),
            &root.path().join("staging"),
            InstallLimits::default(),
        )
        .unwrap_err(),
        InstallError::UnsafeLink(_)
    ));
    assert!(!root.path().join("outside").exists());
}

#[test]
fn interrupted_activation_recovers_one_version_and_keeps_session_pin() {
    for boundary in [
        ActivationBoundary::IntentDurable,
        ActivationBoundary::GenerationDurable,
        ActivationBoundary::PointerSwitched,
        ActivationBoundary::IntentCleared,
    ] {
        let root = tempfile::tempdir().unwrap();
        let store_root = root.path().join("store");
        let store = ArtifactStore::open(&store_root).unwrap();
        let old = store
            .activate(staged(root.path(), "1.0.0", b"old generation"))
            .unwrap();
        let pinned_session = store.pin().unwrap().unwrap();
        assert_eq!(pinned_session.version, "1.0.0");

        let _ =
            store.activate_with_hook(staged(root.path(), "2.0.0", b"new generation"), |reached| {
                if reached == boundary {
                    Err(ActivationError::InvalidStore)
                } else {
                    Ok(())
                }
            });
        let recovered = ArtifactStore::open(&store_root).unwrap();
        let active = recovered.pin().unwrap().unwrap();
        assert!(active.version == "1.0.0" || active.version == "2.0.0");
        assert!(active.generation_path.is_dir());
        assert!(pinned_session.generation_path.is_dir());
        assert_eq!(old.generation_path, pinned_session.generation_path);
        assert_eq!(
            std::fs::read_dir(store_root.join("generations"))
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry.path().is_dir())
                .count(),
            if active.version == "2.0.0" { 2 } else { 1 }
        );
    }
}
