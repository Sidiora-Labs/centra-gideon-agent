"""Portable project collection, bounded ZIP transfer and authenticated envelopes."""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from gideon.automation.workflows.project_export import (
    MANIFEST_SCHEMA,
    PORTABLE_DIRS,
    PORTABLE_FILES,
    ExportPlan,
    ImportIssue,
    ImportPlan,
    artifact_digest,
    excluded,
    plan_export,
    plan_import,
    run_digest,
    safe_member,
)

logger = logging.getLogger(__name__)
MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "project/"
MAX_MEMBERS = 5000
MAX_TOTAL_EXTRACTED = 64 * 1024 * 1024
ENCRYPTION_MAGIC = b"GIDEONPRJ1"
SALT_BYTES = 16
NONCE_BYTES = 12
KDF_ITERATIONS = 600_000


class ArchiveRefused(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


class EncryptionUnavailable(Exception):
    pass


@dataclass
class ExtractedArchive:
    manifest: dict[str, Any] = field(default_factory=dict)
    contents: dict[str, bytes] = field(default_factory=dict)
    refused: list[ImportIssue] = field(default_factory=list)


class ProjectSource:
    def __init__(self, root):
        self.root = root

    def candidates(self):
        yield from (self.root / name for name in PORTABLE_FILES)
        for name in PORTABLE_DIRS:
            directory = self.root / name
            if directory.is_dir():
                yield from sorted(directory.rglob("*"))

    def read(self):
        files, seen = {}, set()
        if not self.root.is_dir():
            return files
        for path in self.candidates():
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(self.root).as_posix()
            except (OSError, ValueError):
                continue
            if relative in seen:
                continue
            seen.add(relative)
            excluded(relative)
            try:
                files[relative] = path.read_bytes()
            except OSError:
                pass
        return files


class ProjectPackage:
    def __init__(self, project_id, root, name, artifacts, runs):
        self.files = read_project_files(root)
        self.artifacts = [artifact_digest(row) for row in artifacts or []]
        self.runs = [run_digest(row) for row in runs or []]
        self.plan = plan_export(
            project_id,
            project_name=name,
            files=self.files,
            artifact_metadata=self.artifacts,
            run_digests=self.runs,
            secret_names=_vault_secret_names(project_id),
        )

    def encode(self, passphrase):
        data = write_archive(
            self.plan,
            self.files,
            artifact_metadata=self.artifacts,
            run_digests=self.runs,
        )
        return encrypt_archive(data, passphrase) if passphrase else data


def read_project_files(project_root: Path) -> dict[str, bytes]:
    return ProjectSource(project_root).read()


def collect_project(
    project_id: str,
    *,
    project_root: Path,
    project_name: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
) -> ExportPlan:
    return ProjectPackage(project_id, project_root, project_name, artifacts, runs).plan


def _vault_secret_names(project_id: str) -> list[str]:
    from gideon.security.secrets_vault import project_secret_names

    try:
        result = project_secret_names(project_id)
    except Exception:
        logger.warning(
            "credential store unreadable while listing project %s's secret names; the export's presence flags will under-report what must be re-entered",
            project_id,
        )
        return []
    return result


def _entity_payloads(artifact_metadata, run_digests) -> dict[str, bytes]:
    from gideon.automation.workflows.project_export import _canonical

    return {
        name: _canonical(records)
        for name, records in (
            ("artifacts.json", artifact_metadata),
            ("runs.json", run_digests),
        )
        if records
    }


class ArchiveWriter:
    def __init__(self, plan, files, metadata):
        self.plan, self.files, self.metadata = plan, files, metadata

    def payload(self, path):
        if path in self.metadata:
            return self.metadata[path]
        if path in self.files:
            return self.files[path]
        raise ArchiveRefused(
            "incomplete_plan",
            f"{path} is in the manifest but its contents were not supplied",
        )

    def encode(self):
        destination = io.BytesIO()
        with zipfile.ZipFile(
            destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(self.plan.manifest(), indent=2, sort_keys=True),
            )
            for entry in self.plan.entries:
                archive.writestr(PAYLOAD_PREFIX + entry.path, self.payload(entry.path))
        return destination.getvalue()


def write_archive(
    plan: ExportPlan,
    files: dict[str, bytes],
    *,
    artifact_metadata: list[dict[str, Any]] | None = None,
    run_digests: list[dict[str, Any]] | None = None,
) -> bytes:
    return ArchiveWriter(
        plan, files, _entity_payloads(artifact_metadata, run_digests)
    ).encode()


def export_project_archive(
    project_id: str,
    *,
    project_root: Path,
    project_name: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
    passphrase: str = "",
) -> tuple[bytes, ExportPlan]:
    package = ProjectPackage(project_id, project_root, project_name, artifacts, runs)
    return package.encode(passphrase), package.plan


def _open_archive(path: Path, passphrase: str = "") -> tuple[Path, Path | None]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ArchiveRefused("unreadable", str(exc)) from exc
    if not is_encrypted(content):
        return path, None
    if not passphrase:
        raise ArchiveRefused(
            "passphrase_required",
            "this archive is encrypted; import it with the passphrase it was created with",
        )
    plaintext = decrypt_archive(content, passphrase)
    temporary = Path(tempfile.mkdtemp(prefix="gideon-project-decrypt-"))
    target = temporary / "archive.zip"
    target.write_bytes(plaintext)
    os.chmod(str(target), 0o600)
    return target, temporary


class ArchiveReader:
    def __init__(self, directory):
        self.directory = directory
        self.result = ExtractedArchive()
        self.manifest_bytes = None

    @staticmethod
    def admit(infos):
        count = len(infos)
        if count > MAX_MEMBERS:
            raise ArchiveRefused(
                "too_many_members", f"{count} members exceeds the {MAX_MEMBERS} ceiling"
            )
        size = sum(max(0, item.file_size) for item in infos)
        if size > MAX_TOTAL_EXTRACTED:
            raise ArchiveRefused(
                "too_large",
                f"{size} declared bytes exceeds the {MAX_TOTAL_EXTRACTED} ceiling",
            )

    def member(self, archive, info):
        name = info.filename
        if info.is_dir():
            return
        if name == MANIFEST_NAME:
            self.manifest_bytes = _extract_one(archive, info, self.directory, name)
            return
        if not name.startswith(PAYLOAD_PREFIX):
            self.result.refused.append(
                ImportIssue(
                    name,
                    "unexpected_member",
                    f"member is outside {PAYLOAD_PREFIX!r} and is not the manifest",
                )
            )
            return
        relative = name.removeprefix(PAYLOAD_PREFIX)
        safe, reason = safe_member(relative)
        if not safe:
            self.result.refused.append(ImportIssue(relative, "unsafe_member", reason))
            return
        payload = _extract_one(archive, info, self.directory, relative)
        if payload is None:
            self.result.refused.append(
                ImportIssue(
                    relative,
                    "escapes_destination",
                    "member resolved outside the extraction directory",
                )
            )
        else:
            self.result.contents[relative] = payload

    def finish(self):
        if self.manifest_bytes is None:
            raise ArchiveRefused("no_manifest", f"the archive has no {MANIFEST_NAME}")
        try:
            manifest = json.loads(self.manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveRefused("bad_manifest", str(exc)) from exc
        if not isinstance(manifest, dict):
            raise ArchiveRefused("bad_manifest", "the manifest is not an object")
        self.result.manifest = manifest
        return self.result

    def read(self, path):
        try:
            archive = zipfile.ZipFile(str(path))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ArchiveRefused(
                "not_an_archive", f"not a readable .zip: {exc}"
            ) from exc
        with archive:
            infos = archive.infolist()
            self.admit(infos)
            for info in infos:
                self.member(archive, info)
            return self.finish()


def extract_archive(path: Path, *, passphrase: str = "") -> ExtractedArchive:
    source, decrypted = _open_archive(path, passphrase)
    work = Path(tempfile.mkdtemp(prefix="gideon-project-import-"))
    try:
        return ArchiveReader(work).read(source)
    finally:
        for directory in (work, decrypted):
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)


def _extract_one(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, work: Path, rel: str
) -> bytes | None:
    target, root = (work / rel).resolve(), work.resolve()
    if target != root and root not in target.parents:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info) as source, target.open("wb") as destination:
        size = 0
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            size += len(chunk)
            if size > MAX_TOTAL_EXTRACTED:
                raise ArchiveRefused(
                    "too_large", f"{rel} expands past the {MAX_TOTAL_EXTRACTED} ceiling"
                )
            destination.write(chunk)
    return target.read_bytes()


def read_archive_plan(
    path: Path, *, existing_names: list[str] | None = None, passphrase: str = ""
) -> tuple[ImportPlan, ExtractedArchive]:
    extracted = extract_archive(path, passphrase=passphrase)
    admission = plan_import(
        extracted.manifest, extracted.contents, existing_names=existing_names
    )
    admission.refused += extracted.refused
    return admission, extracted


class ImportDestination:
    def __init__(self, root):
        self.directory, self.resolved = root, root.resolve()

    def target(self, relative):
        if not safe_member(relative)[0]:
            return None
        destination = (self.directory / relative).resolve()
        if destination == self.resolved or self.resolved in destination.parents:
            return destination
        return None

    def commit(self, accepted, contents):
        written = []
        for relative in accepted:
            if not safe_member(relative)[0]:
                continue
            content = contents.get(relative)
            if content is None:
                continue
            destination = self.target(relative)
            if destination is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                written.append(relative)
        return written


def commit_import(
    plan: ImportPlan, archive: ExtractedArchive, *, project_root: Path
) -> list[str]:
    return ImportDestination(project_root).commit(plan.accepted, archive.contents)


def encryption_available() -> bool:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        return False
    return AESGCM is not None


def is_encrypted(data: bytes) -> bool:
    return data.startswith(ENCRYPTION_MAGIC)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    parameters: dict = dict(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS
    )
    return PBKDF2HMAC(**parameters).derive(passphrase.encode("utf-8"))


@dataclass
class ArchiveEnvelope:
    salt: bytes
    nonce: bytes
    ciphertext: bytes = b""

    @property
    def header(self):
        return b"".join((ENCRYPTION_MAGIC, self.salt, self.nonce))

    @classmethod
    def decode(cls, data):
        if not is_encrypted(data):
            raise ArchiveRefused(
                "not_encrypted", "this archive carries no encryption header"
            )
        start = len(ENCRYPTION_MAGIC)
        nonce_start, payload_start = (
            start + SALT_BYTES,
            start + SALT_BYTES + NONCE_BYTES,
        )
        envelope = cls(
            data[start:nonce_start],
            data[nonce_start:payload_start],
            data[payload_start:],
        )
        if (
            len(envelope.salt) != SALT_BYTES
            or len(envelope.nonce) != NONCE_BYTES
            or not envelope.ciphertext
        ):
            raise ArchiveRefused("truncated", "the encryption header is incomplete")
        return envelope


def encrypt_archive(data: bytes, passphrase: str) -> bytes:
    if not encryption_available():
        raise EncryptionUnavailable(
            "AES-GCM needs the optional `cryptography` extra: pip install 'gideon-agent-harness[oauth2]'"
        )
    if not passphrase:
        raise ValueError("an empty passphrase would encrypt nothing")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    envelope = ArchiveEnvelope(os.urandom(SALT_BYTES), os.urandom(NONCE_BYTES))
    cipher = AESGCM(_derive_key(passphrase, envelope.salt))
    return envelope.header + cipher.encrypt(envelope.nonce, data, envelope.header)


def decrypt_archive(data: bytes, passphrase: str) -> bytes:
    if not encryption_available():
        raise EncryptionUnavailable(
            "this archive is encrypted and AES-GCM needs the optional `cryptography` extra"
        )
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    envelope = ArchiveEnvelope.decode(data)
    cipher = AESGCM(_derive_key(passphrase, envelope.salt))
    try:
        return cipher.decrypt(envelope.nonce, envelope.ciphertext, envelope.header)
    except InvalidTag as exc:
        raise ArchiveRefused(
            "decrypt_failed",
            "wrong passphrase, or the archive was modified after it was created",
        ) from exc


def project_component_paths(home: Path) -> list[str]:
    root, result = home / "projects", []
    if root.is_dir():
        for path in sorted(root.iterdir()):
            try:
                include = path.is_dir() and not path.is_symlink()
            except OSError:
                include = False
            if include:
                result.append("projects/" + path.name)
    return result


def portable_project_members(project_root: Path) -> list[str]:
    return sorted(read_project_files(project_root))


def manifest_schema() -> int:
    return MANIFEST_SCHEMA


def is_project_archive_member(name: str) -> bool:
    return name.startswith(PAYLOAD_PREFIX) or name == MANIFEST_NAME


def archive_filename(
    project_name: str, project_id: str, *, encrypted: bool = False
) -> str:
    characters = (
        char if char.isalnum() or char in "-_" else "-" for char in project_name or ""
    )
    words = "".join(characters).strip("-").split("-")
    stem = "-".join(filter(None, words))[:60] or str(project_id or "project")
    extension = ".zip.enc" if encrypted else ".zip"
    return "gideon-project-" + stem + extension


def summarize_export(plan: ExportPlan) -> dict[str, Any]:
    return {
        **{key: getattr(plan, key) for key in ("project_id", "project_name")},
        "entries": len(plan.entries),
        **{
            key: getattr(plan, key)
            for key in ("total_bytes", "artifact_count", "run_count")
        },
        "skipped": plan.skipped.copy(),
        "secrets_present": sorted(plan.secrets_present),
        "schema": MANIFEST_SCHEMA,
    }


def normalize_member(name: str) -> str:
    return (
        name.removeprefix(PAYLOAD_PREFIX)
        if name.startswith(PAYLOAD_PREFIX)
        else PurePosixPath(name).as_posix()
    )
