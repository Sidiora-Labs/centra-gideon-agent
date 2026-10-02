from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from checks.hypermid.evidence import ObservationWriter
from gideon.hypermid.artifacts import ArtifactApproval, ArtifactApprovalError


_RUST_MATRIX_PROBE = r"""
use ed25519_dalek::{Signer, SigningKey};
use flate2::{write::GzEncoder, Compression};
use hypermid_artifacts::{
    stage_verified, ActivationBoundary, ActivationError, ArtifactCapability, ArtifactFile,
    ArtifactManifest, ArtifactStore, FileKind, InstallError, InstallLimits, Platform,
    SignedArtifactManifest, TrustStore, VerificationError,
};
use serde_json::json;
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;
use std::io;
use std::path::Path;
use std::process::Command;
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

fn verification_before_execution() -> serde_json::Value {
    let root = tempfile::tempdir().unwrap();
    let marker = root.path().join("artifact-executed");
    let script = format!("#!/bin/sh\nprintf executed > '{}'\n", marker.display());
    let archive = archive(&[("bin/tool", script.as_bytes())]);
    let (mut signed, trust) = signed(
        &archive,
        "1.0.0",
        vec![regular("bin/tool", script.as_bytes(), true)],
        BTreeSet::new(),
    );
    signed.signature = "00".repeat(64);
    let result = stage_verified(
        &archive,
        &signed,
        &trust,
        &Platform::current(),
        &BTreeSet::new(),
        &root.path().join("staging"),
        InstallLimits::default(),
    );
    let invalid_signature_refused = matches!(
        &result,
        Err(InstallError::Verification(VerificationError::InvalidSignature))
    );
    let mut execution_attempts = 0_u64;
    if let Ok(verified) = result {
        execution_attempts += 1;
        let _ = Command::new("/bin/sh")
            .arg(verified.staging_path.join("bin/tool"))
            .status();
    }
    json!({
        "invalid_signature_refused": invalid_signature_refused,
        "execution_attempts": execution_attempts,
        "execution_marker_written": marker.exists(),
    })
}

fn staging_escape() -> serde_json::Value {
    let root = tempfile::tempdir().unwrap();
    let bytes = b"verified executable";
    let gzip = GzEncoder::new(Vec::new(), Compression::default());
    let mut builder = Builder::new(gzip);
    let mut regular_header = Header::new_gnu();
    regular_header.set_size(bytes.len() as u64);
    regular_header.set_mode(0o755);
    regular_header.set_cksum();
    builder
        .append_data(&mut regular_header, "bin/tool", &bytes[..])
        .unwrap();
    let target = "../../../outside";
    let mut link_header = Header::new_gnu();
    link_header.set_entry_type(tar::EntryType::Symlink);
    link_header.set_size(0);
    link_header.set_mode(0o777);
    link_header.set_link_name(target).unwrap();
    link_header.set_cksum();
    builder
        .append_data(&mut link_header, "bin/escape", io::empty())
        .unwrap();
    let archive = builder.into_inner().unwrap().finish().unwrap();
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
    let result = stage_verified(
        &archive,
        &signed,
        &trust,
        &Platform::current(),
        &BTreeSet::new(),
        &root.path().join("staging"),
        InstallLimits::default(),
    );
    json!({
        "unsafe_link_refused": matches!(result, Err(InstallError::UnsafeLink(_))),
        "files_outside_staging": u64::from(root.path().join("outside").exists()),
    })
}

fn prior_version_retention() -> serde_json::Value {
    let boundaries = [
        ActivationBoundary::IntentDurable,
        ActivationBoundary::GenerationDurable,
        ActivationBoundary::PointerSwitched,
        ActivationBoundary::IntentCleared,
    ];
    let mut losses = 0_u64;
    let mut outcomes = Vec::new();
    for boundary in boundaries {
        let root = tempfile::tempdir().unwrap();
        let store_root = root.path().join("store");
        let store = ArtifactStore::open(&store_root).unwrap();
        let prior = store
            .activate(staged(root.path(), "1.0.0", b"old generation"))
            .unwrap();
        let pinned = store.pin().unwrap().unwrap();
        let update = store.activate_with_hook(
            staged(root.path(), "2.0.0", b"new generation"),
            |reached| {
                if reached == boundary {
                    Err(ActivationError::InvalidStore)
                } else {
                    Ok(())
                }
            },
        );
        let recovered = ArtifactStore::open(&store_root).unwrap();
        let active = recovered.pin().unwrap().unwrap();
        let retained = pinned.generation_path.is_dir()
            && prior.generation_path == pinned.generation_path
            && std::fs::read(pinned.generation_path.join("bin/tool")).unwrap()
                == b"old generation";
        if !retained {
            losses += 1;
        }
        outcomes.push(json!({
            "boundary": format!("{boundary:?}"),
            "update_refused": update.is_err(),
            "active_version": active.version,
            "prior_version_retained": retained,
        }));
    }
    json!({
        "boundaries_tested": outcomes.len(),
        "failed_update_prior_version_losses": losses,
        "outcomes": outcomes,
    })
}

fn capability_expansion() -> serde_json::Value {
    let root = tempfile::tempdir().unwrap();
    let bytes = b"network capable artifact";
    let archive = archive(&[("bin/tool", bytes)]);
    let (signed, trust) = signed(
        &archive,
        "2.0.0",
        vec![regular("bin/tool", bytes, true)],
        BTreeSet::from([
            ArtifactCapability::FilesystemRead,
            ArtifactCapability::FilesystemWrite,
            ArtifactCapability::Network,
            ArtifactCapability::Model,
            ArtifactCapability::Secret,
            ArtifactCapability::Mutation,
        ]),
    );
    let result = stage_verified(
        &archive,
        &signed,
        &trust,
        &Platform::current(),
        &BTreeSet::new(),
        &root.path().join("staging"),
        InstallLimits::default(),
    );
    let approval_required = matches!(
        &result,
        Err(InstallError::CapabilityApprovalRequired(_))
    );
    let mut expansions = 0_u64;
    if let Ok(verified) = result {
        let store = ArtifactStore::open(root.path().join("store")).unwrap();
        store.activate(verified).unwrap();
        expansions += 1;
    }
    json!({
        "approval_required": approval_required,
        "requested_capabilities": [
            "filesystem_read",
            "filesystem_write",
            "network",
            "model",
            "secret",
            "mutation",
        ],
        "unapproved_capability_expansions": expansions,
    })
}

fn main() {
    let verification = verification_before_execution();
    let escape = staging_escape();
    let retention = prior_version_retention();
    let expansion = capability_expansion();
    let matrix = json!({
        "schema_version": 1,
        "operations": {
            "verification_before_execution": verification,
            "staging_escape": escape,
            "prior_version_retention": retention,
            "capability_expansion": expansion,
        },
        "measurements": {
            "executions-before-verification": verification["execution_attempts"],
            "files-outside-staging": escape["files_outside_staging"],
            "failed-update-prior-version-losses": retention["failed_update_prior_version_losses"],
            "unapproved-capability-expansions": expansion["unapproved_capability_expansions"],
        },
    });
    println!("{}", serde_json::to_string(&matrix).unwrap());
}
"""


def manifest(*capabilities: str) -> dict[str, object]:
    return {
        "artifact_id": "hypermid-tool",
        "version": "2.0.0",
        "archive_sha256": "a" * 64,
        "publisher_id": "hypermid-release",
        "source_uri": "https://downloads.example.invalid/hypermid-tool.tar.gz",
        "capabilities": list(capabilities),
    }


def test_capability_expansion_requires_fresh_exact_manifest_approval() -> None:
    candidate = manifest("filesystem_read", "network")
    approval = ArtifactApproval.issue(
        candidate,
        approved_capabilities={"network"},
        approved_by="owner-1",
        approved_at_ms=100,
        ttl_ms=50,
    )
    assert approval.authorize_update(
        candidate, {"filesystem_read"}, now_ms=120
    ) == frozenset({"network"})

    tampered = copy.deepcopy(candidate)
    tampered["source_uri"] = "https://other.example.invalid/tool.tar.gz"
    with pytest.raises(ArtifactApprovalError) as changed:
        approval.authorize_update(tampered, {"filesystem_read"}, now_ms=120)
    assert changed.value.code == "FRESH_APPROVAL_REQUIRED"

    with pytest.raises(ArtifactApprovalError) as expired:
        approval.authorize_update(candidate, {"filesystem_read"}, now_ms=150)
    assert expired.value.code == "FRESH_APPROVAL_REQUIRED"

    assert approval.authorize_update(
        manifest("filesystem_read"), {"filesystem_read"}, now_ms=1_000
    ) == frozenset()


def test_real_artifact_operations_emit_observed_supply_chain_matrix(
    tmp_path: Path,
) -> None:
    cargo = shutil.which("cargo")
    assert cargo is not None, "cargo is required for the artifact supply-chain gate"
    workspace = Path(__file__).resolve().parents[2]
    probe = tmp_path / "artifact-matrix-probe"
    source = probe / "src"
    source.mkdir(parents=True)
    (probe / "Cargo.toml").write_text(
        textwrap.dedent(
            f"""
            [package]
            name = "hypermid-artifact-matrix-probe"
            version = "0.0.0"
            edition = "2021"

            [workspace]

            [dependencies]
            ed25519-dalek = "2"
            flate2 = "1"
            hex = "0.4"
            hypermid-artifacts = {{ path = {json.dumps(str(workspace / "crates/hypermid-artifacts"))} }}
            serde_json = "1"
            sha2 = "0.10"
            tar = "0.4"
            tempfile = "3"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (source / "main.rs").write_text(_RUST_MATRIX_PROBE, encoding="utf-8")
    completed = subprocess.run(
        [
            cargo,
            "run",
            "--quiet",
            "--offline",
            "--manifest-path",
            str(probe / "Cargo.toml"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    matrix = json.loads(completed.stdout)
    operations = matrix["operations"]
    assert operations["verification_before_execution"] == {
        "execution_attempts": 0,
        "execution_marker_written": False,
        "invalid_signature_refused": True,
    }
    assert operations["staging_escape"] == {
        "files_outside_staging": 0,
        "unsafe_link_refused": True,
    }
    retention = operations["prior_version_retention"]
    assert retention["boundaries_tested"] == 4
    assert retention["failed_update_prior_version_losses"] == 0
    assert all(outcome["update_refused"] for outcome in retention["outcomes"])
    assert all(outcome["prior_version_retained"] for outcome in retention["outcomes"])
    assert operations["capability_expansion"] == {
        "approval_required": True,
        "requested_capabilities": [
            "filesystem_read",
            "filesystem_write",
            "network",
            "model",
            "secret",
            "mutation",
        ],
        "unapproved_capability_expansions": 0,
    }
    expected = {
        "executions-before-verification": 0,
        "files-outside-staging": 0,
        "failed-update-prior-version-losses": 0,
        "unapproved-capability-expansions": 0,
    }
    assert matrix["measurements"] == expected

    matrix_path = tmp_path / "artifact-verification-matrix.json"
    matrix_path.write_text(
        json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert matrix_path.is_file() and not matrix_path.is_symlink()

    observations = ObservationWriter.from_env("artifact_supply_chain")
    if observations is not None:
        units = {
            "executions-before-verification": "executions",
            "files-outside-staging": "files",
            "failed-update-prior-version-losses": "losses",
            "unapproved-capability-expansions": "expansions",
        }
        for name, observed in expected.items():
            observations.measure(name, observed, "eq", 0, units[name])
        observations.artifact(
            "artifact-verification-matrix",
            matrix_path,
            media_type="application/json",
        )
        observations.finish(source_digest=os.environ["HYPERMID_SOURCE_DIGEST"])
