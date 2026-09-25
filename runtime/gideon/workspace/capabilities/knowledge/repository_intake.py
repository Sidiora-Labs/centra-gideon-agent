"""Guarded repository snapshots and deterministic source study."""

import io
import json
import os
import re
import shutil
import tarfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import quote, urlparse

from gideon.security.guardrails.policy import profile_for_session
from gideon.security.net import CONNECTOR, egress_policy_for, evaluate, fetch
from gideon.security.net.policy import egress_policy_for_profile

from .capture import CaptureError

HOSTS = {
    "github.com": ("api.github.com", "codeload.github.com"),
    "gitlab.com": ("gitlab.com",),
}
LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".sh": "Shell",
    ".md": "Markdown",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
}


def repository_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise CaptureError("Repository URL is required")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise CaptureError(
            "Repository must be a credential-free HTTPS GitHub or GitLab URL"
        )
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if (
        host not in HOSTS
        or len(parts) < 2
        or len(parts) > 10
        or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
    ):
        raise CaptureError(
            "Repository must be a credential-free HTTPS GitHub or GitLab URL"
        )
    parts[-1] = re.sub(r"\.git$", "", parts[-1], flags=re.I)
    if not parts[-1]:
        raise CaptureError("Repository name is invalid")
    slug = "/".join(parts)
    return host, slug, f"https://{host}/{slug}"


class RepositoryReader:
    def __init__(self, session_key):
        self.policy = egress_policy_for_profile(
            egress_policy_for(CONNECTOR), profile_for_session(session_key).egress_tier
        )
        if self.policy is None:
            raise CaptureError("Session egress policy disables repository intake", 403)
        self.bytes = 0

    def validate(self, url):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        allowed = {"github.com", "api.github.com", "codeload.github.com", "gitlab.com"}
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or host not in allowed
        ):
            raise CaptureError(
                "Repository transfer left fixed public repository endpoints"
            )
        if not evaluate(url, self.policy).allow:
            raise CaptureError(
                "Repository transfer was blocked by session egress policy", 403
            )

    async def read(self, url, max_bytes):
        self.validate(url)
        policy = replace(
            self.policy,
            allow_private=False,
            loopback_only=False,
            allow_hosts=(),
            allow_only=False,
            pin_resolved_ip=True,
            max_bytes=min(self.policy.max_bytes, max_bytes),
            max_redirects=min(self.policy.max_redirects, 5),
            timeout_s=min(self.policy.timeout_s, 30),
        )
        response = await fetch(
            url,
            policy=policy,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Gideon-RepositoryStudy/1.0",
            },
            validate_url=self.validate,
        )
        self.bytes += len(response.body)
        if response.truncated or self.bytes > 67108864:
            raise CaptureError("Repository transfer exceeded its byte limit", 413)
        if response.status != 200:
            raise CaptureError(
                f"Repository endpoint returned HTTP {response.status}", 502
            )
        return response

    async def snapshot(self, url, destination):
        host, slug, canonical = repository_url(url)
        if host == "github.com":
            api = f"https://api.github.com/repos/{slug}/commits/HEAD"
            meta = json.loads((await self.read(api, 1048576)).text)
            revision = meta.get("sha", "")
            archive = f"https://api.github.com/repos/{slug}/tarball/{revision}"
        else:
            encoded = quote(slug, safe="")
            api = (
                f"https://gitlab.com/api/v4/projects/{encoded}/repository/commits/HEAD"
            )
            meta = json.loads((await self.read(api, 1048576)).text)
            revision = meta.get("id", "")
            archive = f"https://gitlab.com/api/v4/projects/{encoded}/repository/archive.tar.gz?sha={revision}"
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", str(revision)):
            raise CaptureError(
                "Repository endpoint did not return a valid revision", 502
            )
        payload = (await self.read(archive, 33554432)).body
        self.extract(payload, destination)
        return {
            "url": canonical,
            "revision": revision.lower(),
            "transfer_bytes": self.bytes,
        }

    @staticmethod
    def extract(payload, destination):
        stage = Path(mkdtemp(prefix=".repo-stage-", dir=destination.parent))
        total = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                members = [member for member in archive.getmembers() if member.isfile()]
                if not members or len(members) > 5000:
                    raise CaptureError(
                        "Repository archive file count is outside1..5000", 413
                    )
                prefixes = {
                    Path(member.name).parts[0]
                    for member in members
                    if Path(member.name).parts
                }
                if len(prefixes) != 1:
                    raise CaptureError("Repository archive has no single safe root")
                prefix = next(iter(prefixes))
                for member in members:
                    parts = Path(member.name).parts
                    relative = (
                        Path(*parts[1:])
                        if parts and parts[0] == prefix
                        else Path(member.name)
                    )
                    if (
                        not relative.parts
                        or relative.is_absolute()
                        or ".." in relative.parts
                        or member.issym()
                        or member.islnk()
                        or member.size > 4194304
                    ):
                        raise CaptureError(
                            "Repository archive contains an unsafe entry", 413
                        )
                    total += member.size
                    if total > 134217728:
                        raise CaptureError(
                            "Repository archive expands beyond128MiB", 413
                        )
                    target = stage / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise CaptureError(
                            "Repository archive entry is unreadable", 502
                        )
                    with target.open("wb") as output:
                        shutil.copyfileobj(source, output)
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(stage, destination)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def study(path, revision, url):
    root = Path(path).resolve()
    if not root.is_dir():
        raise CaptureError("Repository checkout is unavailable", 404)
    files, total, languages, top = [], 0, Counter(), Counter()
    readme = ""
    risks = []
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.relative_to(root)
        size = candidate.stat().st_size
        if size > 4194304:
            risks.append(f"Skipped oversized file: {relative}")
            continue
        files.append(str(relative))
        total += size
        top[relative.parts[0]] += 1
        language = LANGUAGES.get(candidate.suffix.lower())
        if language:
            languages[language] += 1
        if not readme and candidate.name.lower().startswith("readme"):
            readme = candidate.read_text(errors="replace")[:2000].strip()
        if len(files) > 10000 or total > 134217728:
            raise CaptureError("Repository study exceeds file or byte limits", 413)
    if not files:
        raise CaptureError("Repository checkout contains no readable files")
    structured = {
        "revision": revision,
        "url": url,
        "file_count": len(files),
        "bytes": total,
        "languages": dict(languages.most_common()),
        "top_level": dict(top.most_common(20)),
        "readme_excerpt": readme,
        "risk_notes": risks[:50],
    }
    lines = [
        "# Repository study",
        "",
        f"Source: {url}",
        f"Revision: `{revision}`",
        f"Files: {len(files)}",
        f"Bytes: {total}",
        "",
        "## Languages",
    ]
    lines += [f"- {name}: {count}" for name, count in languages.most_common()] or [
        "- No recognized source extensions"
    ]
    lines += ["", "## Top-level entries"] + [
        f"- {name}: {count} files" for name, count in top.most_common(20)
    ]
    lines += [
        "",
        "## README excerpt",
        readme or "No README text found.",
        "",
        "## Risk notes",
    ] + (risks[:50] or ["- No bounded-scan warnings."])
    return structured, "\n".join(lines) + "\n"
