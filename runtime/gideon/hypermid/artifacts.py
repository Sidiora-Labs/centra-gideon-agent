from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Mapping


CAPABILITY_CLASSES = frozenset(
    {
        "filesystem_read",
        "filesystem_write",
        "network",
        "model",
        "secret",
        "mutation",
    }
)


class ArtifactApprovalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ArtifactApproval:
    artifact_id: str
    version: str
    manifest_sha256: str
    approved_capabilities: frozenset[str]
    approved_by: str
    approved_at_ms: int
    expires_at_ms: int

    @classmethod
    def issue(
        cls,
        manifest: Mapping[str, object],
        *,
        approved_capabilities: Iterable[str],
        approved_by: str,
        approved_at_ms: int,
        ttl_ms: int,
    ) -> "ArtifactApproval":
        capabilities = frozenset(approved_capabilities)
        if ttl_ms <= 0 or not capabilities <= CAPABILITY_CLASSES:
            raise ArtifactApprovalError("INVALID_APPROVAL", "approval is not bounded")
        return cls(
            artifact_id=str(manifest["artifact_id"]),
            version=str(manifest["version"]),
            manifest_sha256=hashlib.sha256(_canonical(manifest)).hexdigest(),
            approved_capabilities=capabilities,
            approved_by=approved_by,
            approved_at_ms=approved_at_ms,
            expires_at_ms=approved_at_ms + ttl_ms,
        )

    def authorize_update(
        self,
        manifest: Mapping[str, object],
        current_capabilities: Iterable[str],
        *,
        now_ms: int,
    ) -> frozenset[str]:
        requested = frozenset(str(item) for item in manifest.get("capabilities", ()))
        current = frozenset(current_capabilities)
        expansion = requested - current
        if not expansion:
            return expansion
        digest = hashlib.sha256(_canonical(manifest)).hexdigest()
        if (
            now_ms >= self.expires_at_ms
            or self.artifact_id != manifest.get("artifact_id")
            or self.version != manifest.get("version")
            or self.manifest_sha256 != digest
            or not expansion <= self.approved_capabilities
        ):
            raise ArtifactApprovalError(
                "FRESH_APPROVAL_REQUIRED",
                "artifact capability expansion requires approval for this exact manifest",
            )
        return expansion

