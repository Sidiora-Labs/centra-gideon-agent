"""Built-in security controls — deny list, sensitive path protection, and audit scanning."""

import fnmatch
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs

from gideon.security.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)


BUILTIN_DENY_PATTERNS: list[str] = [
    "get_secret*",
    "read_secret*",
    "*delete_stack*",
    "*terminate_instance*",
    "*drop_table*",
    "*delete_bucket*",
    "*git*push*",
]

_DENY_EXCEPTIONS: dict[str, list[str]] = {
    "*git*push*": ["* stash push*"],
}

_CMD_SEPARATOR_RE = re.compile(r"[;\n`]|\|\|?|&&|\$\(")


_SENSITIVE_HOME_DIRS: list[str] = [
    ".aws",
    ".ssh",
    ".gnupg",
    ".gpg",
    ".config/gcloud",
    ".azure",
    ".docker/config.json",
    ".kube/config",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "Library/Keychains",
    ".gideon/.env",
    ".gideon/governance",
]

#: ``_SENSITIVE_GIDEON_HOME_ENTRIES``. The split is by a real property (is the name ours
OWN_SECRET_BASENAMES: frozenset[str] = frozenset(
    {
        "sel_hmac.key",
        ".local_secret",
        "telemetry_salt",
    }
)

HOME_SECRET_FILE_BASENAMES: frozenset[str] = frozenset(
    {
        ".env",
        "session_key",
        "sessions.json",
    }
)

HOME_SECRET_DIRS: frozenset[str] = frozenset(
    {
        "auth",
        "credentials",
        "governance",
    }
)

_SENSITIVE_GIDEON_HOME_ENTRIES: tuple[str, ...] = tuple(
    sorted(HOME_SECRET_FILE_BASENAMES | HOME_SECRET_DIRS)
)


def _gideon_home_sensitive_paths() -> list[str]:
    """Absolute paths of the secret-bearing entries in the ACTIVE Gideon home.

    Resolved per call because ``GIDEON_HOME`` is read from the environment and a
    process can legitimately see it change (tests, a dev gateway). Best-effort: if the
    home cannot be resolved the caller still has the ``$HOME``-relative tier, so a
    config hiccup narrows the guard rather than removing it.

    🔴 Deliberately NOT ``config.loader.config_dir()``, which calls ``_ensure_dir`` and so
    CREATES the directory. A read-only path predicate that makes a directory is a bug in
    itself, and it was measurably one: with ~70 call sites, merely checking a path
    materialized the home. It surfaced as a file-listing test failing on an unexpected
    ``.gideon`` entry appearing inside a fixture's fake ``$HOME`` — the guard had
    created it mid-assertion. This mirrors the same resolution rules without the mkdir.
    """
    override = os.environ.get("GIDEON_HOME")
    try:
        if override:
            home = Path(override).expanduser()
        else:
            home = Path.home() / ".gideon"
    except (OSError, ValueError, RuntimeError):
        return []
    return [str(home / entry) for entry in _SENSITIVE_GIDEON_HOME_ENTRIES]


_READ_CMDS = (
    r"(?:cat|bat|head|tail|less|more|strings|xxd|od|hexdump|nl|base64|cp|scp|rsync|tar|"
    r"zip|gzip|dd|grep|egrep|fgrep|rg|ag|awk|sed|cut|paste|tr|sort|uniq|wc|jq|yq|diff|"
    r"cmp|open|vi|vim|nano|emacs|code)\s"
)

_SCRIPT_OPEN = r"(?:python|ruby|perl|node|deno|bun|php|osascript)\S*\s"


_HOME_EXPRESSIONS = (
    "$HOME",
    "~",
    "process.env.HOME",
    "os.environ[HOME]",
    "os.path.expanduser",
    "Path.home()",
    "os.homedir()",
    "ENV[HOME]",
    "%USERPROFILE%",
    "$env:USERPROFILE",
)


def strip_shell_quotes(command: str) -> str:
    """Remove quote characters so a quoted respelling reads as the path it is.

    🔴 Measured against the shipped guard: `cat ~/'.ssh'/id_rsa` and `cat ~/.s''sh/id_rsa`
    were both ALLOWED, and both are ordinary shell that reads the file — the quotes are
    invisible to the shell and opaque to a regex. Dropping them first collapses that whole
    family into the plain spelling.

    It cannot close the CONCATENATION family (`'/.s' + 'sh/id_rsa'`, `$'\x2e'ssh`): no regex
    over a command string can, because the string that names the file never exists in the
    text. That is the documented limit of this control and the reason the OS sandbox
    bind-mounts empty dirs over `~/.aws`, `~/.gnupg` and friends — the guard here is
    defence in depth, not the fence.
    """
    return command.replace("'", "").replace('"', "").replace("\\", "")


def _build_sensitive_regex() -> re.Pattern[str]:
    """Build a compiled regex matching bash reads of sensitive paths."""
    home = str(Path.home())
    home_alts = "(?:" + "|".join(re.escape(h) for h in (home, *_HOME_EXPRESSIONS)) + ")"
    escaped_dirs = [re.escape(d) for d in _SENSITIVE_HOME_DIRS]
    dirs_pattern = "|".join(escaped_dirs)
    return re.compile(
        rf"(?:{_READ_CMDS}.*|{_SCRIPT_OPEN}.*|.*[<>|]\s*){home_alts}[+,\s]*/(?:{dirs_pattern})"
        rf"(?:/|\s|$|['\"),])",
        re.IGNORECASE,
    )


def _build_own_secret_regex() -> re.Pattern[str]:
    """Bash reads of Gideon's own auth/audit files, by basename and at any path.

    A second pattern rather than a new entry in :func:`_build_sensitive_regex`, because that
    one anchors every alternative on ``$HOME`` — and these files follow
    ``GIDEON_HOME``, so a ``$HOME``-anchored alternative would miss every custom home.
    Measured before the fix: ``wc -c < <home>/sel_hmac.key`` was clean.

    Only :data:`OWN_SECRET_BASENAMES` is used here. Names that are ordinary elsewhere are
    refused by path instead, so this pattern cannot fire on a file the user owns.
    """
    names = "|".join(re.escape(n) for n in sorted(OWN_SECRET_BASENAMES))
    return re.compile(
        rf"(?:{_READ_CMDS}.*|{_SCRIPT_OPEN}.*|.*[<>|]\s*)\S*(?:{names})(?:/|\s|$|['\"),;])",
        re.IGNORECASE,
    )


_SENSITIVE_RE: re.Pattern[str] | None = None
_OWN_SECRET_RE: re.Pattern[str] | None = None


def _get_sensitive_re() -> re.Pattern[str]:
    global _SENSITIVE_RE
    if _SENSITIVE_RE is None:
        _SENSITIVE_RE = _build_sensitive_regex()
    return _SENSITIVE_RE


def _get_own_secret_re() -> re.Pattern[str]:
    global _OWN_SECRET_RE
    if _OWN_SECRET_RE is None:
        _OWN_SECRET_RE = _build_own_secret_regex()
    return _OWN_SECRET_RE


def is_sensitive_path(path_str: str) -> bool:
    """Return True if the path points to a sensitive location.

    Works for both absolute paths and ~/relative paths.
    Used by hooks to block fs_read/ReadFile of credential files.
    """
    if "\x00" in path_str:
        return True
    expanded = os.path.expanduser(os.path.expandvars(path_str))
    try:
        resolved = str(Path(expanded).resolve())
    except (OSError, ValueError):
        resolved = expanded

    try:
        home = str(Path.home().resolve())
    except (OSError, ValueError):
        home = str(Path.home())
    resolved_cmp = resolved.casefold()
    if os.path.basename(resolved_cmp) in {n.casefold() for n in OWN_SECRET_BASENAMES}:
        return True
    for sensitive_dir in _SENSITIVE_HOME_DIRS:
        sensitive_path = os.path.join(home, sensitive_dir).casefold()
        if resolved_cmp == sensitive_path or resolved_cmp.startswith(
            sensitive_path + os.sep
        ):
            return True
    for entry in _gideon_home_sensitive_paths():
        try:
            entry_cmp = str(Path(entry).resolve()).casefold()
        except (OSError, ValueError):
            entry_cmp = entry.casefold()
        if resolved_cmp == entry_cmp or resolved_cmp.startswith(entry_cmp + os.sep):
            return True
    return False


_SYSTEM_SUBTREES: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/root",
    "/System",
    "/Library",
    "/Applications",
    "/cores",
    "/Network",
    "/private/etc",
    "/private/usr",
    "/private/var/root",
)
_SYSTEM_PARENTS: tuple[str, ...] = (
    "/",
    "/Volumes",
    "/private",
    "/var",
    "/opt",
    "/mnt",
    "/media",
    "/private/var",
    "/private/tmp",
    "/tmp",
)


def is_system_path(path_str: str) -> bool:
    """True if *path_str* resolves to an OS/system root a user must never create into
    or bind as a workspace. Whole-subtree roots reject their children; mount/temp
    parents reject only the bare dir (children like /Volumes/disk/repo are fine).

    Resolves ~ and symlinks first, so ``..``/symlink forms can't bypass the check.
    Single source of truth shared by the Code workspace validation and the file
    handlers, so those surfaces can never drift apart on what counts as a system path.
    """
    expanded = os.path.expanduser(os.path.expandvars(path_str or ""))
    try:
        resolved = str(Path(expanded).resolve())
    except (OSError, ValueError):
        resolved = expanded
    if not resolved:
        return True
    resolved_cmp = resolved.casefold()
    if resolved_cmp in {p.casefold() for p in _SYSTEM_PARENTS}:
        return True
    for root in _SYSTEM_SUBTREES:
        root_cmp = root.casefold()
        if resolved_cmp == root_cmp or resolved_cmp.startswith(root_cmp + os.sep):
            return True
    return False


_CHAIN_SPLIT_RE = re.compile(r"(?:&&|\|\||;|\||\n)")

_HOME_CD_RE = re.compile(r"^\s*cd\s*(?:~|\$HOME|\$\{HOME\})?\s*$")

_DOT_RELATIVE_RE = re.compile(r"(?<![\w/~$.])(\.[A-Za-z0-9_.-]+/)")


def _normalise_for_matching(command: str) -> str:
    """Rewrite spellings that name a sensitive path without spelling it the guard's way.

    Two respellings reached credentials past the regex, both measured against the shipped guard:

    * ``cat ${HOME}/.ssh/id_rsa`` — ALLOWED, while the unbraced ``$HOME`` form was blocked. The
      brace is pure syntax, so it is collapsed before matching.
    * ``cd ~ && cat .ssh/id_rsa`` — ALLOWED. The path never appears home-qualified in the text;
      the ``cd`` put the shell there. When a segment of the chain moves to home, later segments'
      dot-relative paths are rewritten as home-relative so the existing patterns see them.

    Only ever makes the guard block MORE, and only when a home-cd is actually present: without
    one, a dot-relative path is left exactly as written.
    """
    text = re.sub(r"\$\{(\w+)\}", r"$\1", command)
    segments = _CHAIN_SPLIT_RE.split(text)
    if not any(_HOME_CD_RE.match(seg) for seg in segments):
        return text
    out: list[str] = []
    at_home = False
    for seg in segments:
        if _HOME_CD_RE.match(seg):
            at_home = True
            out.append(seg)
            continue
        out.append(_DOT_RELATIVE_RE.sub(r"~/\1", seg) if at_home else seg)
    return " && ".join(out)


def is_sensitive_bash_command(command: str) -> str | None:
    """Check if a bash command reads sensitive paths.

    Returns denial reason string, or None if clean.
    """
    normalised = strip_shell_quotes(_normalise_for_matching(command))
    if _get_sensitive_re().search(normalised):
        return "Blocked: command accesses sensitive credential path"
    if _get_own_secret_re().search(normalised):
        return "Blocked: command accesses Gideon's own credential or audit key"
    return None


_URL_RE = re.compile(r"https?://([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})(:\d+)?(/[^\s)\"'>]*)?")

_EXFIL_QUERY_MIN_LEN = 200

_EXFIL_PATTERNS = re.compile(
    r"(?:"
    r"[A-Za-z0-9+/=]{40,}"
    r"|%[0-9A-Fa-f]{2}(?:%[0-9A-Fa-f]{2}){20,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|(?:ssh-rsa|ssh-ed25519)[\s+%]"
    r"|BEGIN[\s+%](?:RSA|DSA|EC|OPENSSH)[\s+%]PRIVATE[\s+%]KEY"
    r"|xox[bpas]-[0-9a-zA-Z-]+"
    r")",
    re.IGNORECASE,
)

_S3_PRESIGNED_RE = re.compile(
    r"X-Amz-Algorithm=AWS4-HMAC-SHA256"
    r".*X-Amz-Credential=(?:AKIA|ASIA)[A-Z0-9]{16}(?:%2F|/)"
    r".*X-Amz-Expires=\d{1,6}"
    r".*X-Amz-Signature=[0-9a-f]{64}",
    re.IGNORECASE,
)

_S3_PRESIGNED_PARAMS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "X-Amz-Security-Token",
    }
)


_STS_TOKEN_RE = re.compile(r"^(?:FwoGZX|IQoJb3JpZ2lu)[A-Za-z0-9+/=%]{1,2000}$")
_CREDENTIAL_RE = re.compile(
    r"^(?:AKIA|ASIA)[A-Z0-9]{16}(?:%2F|/)[0-9]{8}"
    r"(?:%2F|/)[a-z0-9-]+(?:%2F|/)s3(?:%2F|/)aws4_request$"
)
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")

_STRUCTURAL_VALIDATORS = {
    "X-Amz-Credential": _CREDENTIAL_RE,
    "X-Amz-Signature": _SIGNATURE_RE,
    "X-Amz-Security-Token": _STS_TOKEN_RE,
}


def _is_safe_presigned(domain: str, query: str) -> bool:
    """Return True if the URL is a valid S3 presigned URL with no extra parameters."""
    if not domain.endswith(".amazonaws.com"):
        return False
    if not _S3_PRESIGNED_RE.search(query):
        return False
    params = parse_qs(query, keep_blank_values=True)
    if not _S3_PRESIGNED_PARAMS.issuperset(params.keys()):
        return False
    for key, values in params.items():
        validator = _STRUCTURAL_VALIDATORS.get(key)
        if validator:
            for val in values:
                if not validator.match(val):
                    return False
        else:
            for val in values:
                if _EXFIL_PATTERNS.search(val):
                    return False
    return True


_SAFE_DOMAIN_SUFFIXES: tuple[str, ...] = ()


def scan_exfiltration_urls(text: str) -> list[str]:
    """Scan text for URLs that may be exfiltrating data via query params.

    Domain-agnostic — only inspects query string content for secret patterns.
    Returns list of warning strings, empty if clean.
    """
    warnings: list[str] = []
    for match in _URL_RE.finditer(text):
        domain = match.group(1)
        path_and_query = match.group(3) or ""
        qmark = path_and_query.find("?")
        if qmark == -1:
            continue

        query = path_and_query[qmark + 1 :]

        if any(domain.endswith(s) for s in _SAFE_DOMAIN_SUFFIXES):
            if _EXFIL_PATTERNS.search(query):
                warnings.append(
                    f"Suspicious URL with credential-like query data: {domain}"
                )
            continue

        if len(query) >= _EXFIL_QUERY_MIN_LEN:
            if _is_safe_presigned(domain, query):
                continue
            warnings.append(
                f"Suspicious URL with long query params ({len(query)} chars): "
                f"{domain}{path_and_query[:60]}..."
            )
        elif _EXFIL_PATTERNS.search(query):
            if _is_safe_presigned(domain, query):
                continue
            warnings.append(f"Suspicious URL with credential-like query data: {domain}")
    return warnings


def redact_exfiltration_urls(text: str) -> tuple[str, list[str]]:
    """Scan and redact suspicious exfiltration URLs from text.

    Returns (cleaned_text, list_of_warnings).
    """
    warnings = scan_exfiltration_urls(text)
    if not warnings:
        return text, []

    result = text
    for match in _URL_RE.finditer(text):
        domain = match.group(1)
        full_url = match.group(0)
        path_and_query = match.group(3) or ""
        qmark = path_and_query.find("?")
        if qmark == -1:
            continue

        query = path_and_query[qmark + 1 :]

        if any(domain.endswith(s) for s in _SAFE_DOMAIN_SUFFIXES):
            if _EXFIL_PATTERNS.search(query):
                result = result.replace(
                    full_url, f"[REDACTED: suspicious URL to {domain}]"
                )
            continue

        if len(query) >= _EXFIL_QUERY_MIN_LEN or _EXFIL_PATTERNS.search(query):
            if _is_safe_presigned(domain, query):
                continue
            result = result.replace(full_url, f"[REDACTED: suspicious URL to {domain}]")

    return result, warnings


_CREDENTIAL_PATTERNS = re.compile(
    r"(?:"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|(?:SecretAccessKey|aws_secret_access_key)\s*[:=]\s*\S+"
    r"|(?:SessionToken|aws_session_token)\s*[:=]\s*\S+"
    r"|(?:AccessKeyId|aws_access_key_id)\s*[:=]\s*\S+"
    r"|BEGIN[\s](?:RSA|DSA|EC|OPENSSH)[\s]PRIVATE[\s]KEY"
    r"|xox[bpas]-[0-9a-zA-Z-]{10,}"
    r"|sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{20,}"
    r"|sk-proj-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9]{32,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|sk-[A-Za-z0-9][A-Za-z0-9_-]{20,}"
    r"|(?i:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret"
    r"|password|passwd|private[_-]?key)\s*[:=]\s*[^\s,;'\"]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{16,}=*"
    r")",
)

_B64_CHUNK_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _b64_credential(chunk: str) -> str:
    """Decode ONE already-isolated base64 chunk; return it if it holds a credential, else ''.

    Split out from `_decode_b64_safe` because its only production caller already holds a
    complete `_B64_CHUNK_RE` match and was paying for a second scan of it (#2717). Re-scanning
    a match is provably a no-op here — `_B64_CHUNK_RE` is `[A-Za-z0-9+/]{40,}={0,2}` with no
    anchor and no lookaround, so running it over one of its own greedy matches yields exactly
    that match back — but "provably a no-op" is a reason to remove the scan, not to keep it.
    """
    import base64

    try:
        decoded = base64.b64decode(chunk, validate=True).decode(
            "utf-8", errors="ignore"
        )
    except Exception:
        return ""
    return decoded if _CREDENTIAL_PATTERNS.search(decoded) else ""


def _decode_b64_safe(text: str) -> str:
    """Try to base64-decode chunks in text; return decoded content or ''.

    Kept as the general entry point (arbitrary text, unknown chunk boundaries). The
    per-chunk work lives in `_b64_credential` so the caller that already has a chunk can
    skip the scan.
    """
    for m in _B64_CHUNK_RE.finditer(text):
        decoded = _b64_credential(m.group())
        if decoded:
            return decoded
    return ""


_URL_USERINFO_CORE_RE = re.compile(r"://(?P<userinfo>[^/?#\s@]++)@")

_SCHEME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
_SCHEME_FIRST_RE = re.compile(r"[A-Za-z]")


def _scheme_run_start(text: str, sep: int) -> int:
    """Index where the maximal run of scheme characters ending just before `sep` begins.

    Walks backward in doubling windows, so an arbitrarily long run stays linear and the common case
    (`https`, five characters) costs one small slice. No cap on the run length: a cap would be a
    silent behaviour change, and this is a security primitive.
    """
    lo = sep
    window = 64
    while True:
        start = max(0, lo - window)
        head = text[start:lo].rstrip(_SCHEME_CHARS)
        if head or start == 0:
            return start + len(head)
        lo = start
        window *= 2


_URL_USERINFO_TAG = "[REDACTED: url credential]"


def redact_url_userinfo(text: str) -> tuple[str, list[str]]:
    """Replace `<scheme>://userinfo@` with a redaction tag, keeping scheme and host.

    Separate and public so a caller that only handles URLs (an audit `resources` field, a source
    URL about to be persisted) can use it without the shape-based sweep, and so its behaviour is
    testable on its own.
    """
    warnings: list[str] = []
    out: list[str] = []
    pos = 0

    for m in _URL_USERINFO_CORE_RE.finditer(text):
        sep = m.start()
        first = _SCHEME_FIRST_RE.search(text, _scheme_run_start(text, sep), sep)
        if first is None:
            continue
        scheme = text[first.start() : sep]
        out.append(text[pos : first.start()])
        out.append(f"{scheme}://{_URL_USERINFO_TAG}@")
        warnings.append(f"Redacted credential in a {scheme} URL")
        pos = m.end()

    if not warnings:
        return text, []
    out.append(text[pos:])
    return "".join(out), warnings


def redact_credentials(text: str) -> tuple[str, list[str]]:
    """Redact raw credential patterns from text, including base64-encoded.

    Returns (cleaned_text, list_of_warnings).
    """
    warnings: list[str] = []

    result, url_warnings = redact_url_userinfo(text)
    warnings.extend(url_warnings)

    def _tag_credential(m: "re.Match[str]") -> str:
        warnings.append(f"Redacted credential pattern: {m.group()[:20]}...")
        return "[REDACTED: credential]"

    result = _CREDENTIAL_PATTERNS.sub(_tag_credential, result)

    for m in _B64_CHUNK_RE.finditer(text):
        chunk = m.group()
        if _b64_credential(chunk):
            result = result.replace(chunk, "[REDACTED: encoded credential]", 1)
            warnings.append(f"Redacted base64-encoded credential ({len(chunk)} chars)")

    return result, warnings


_MASK_RE = re.compile(r"\[REDACTED:[^\]\n]*\]")


def redact_for_display(text: str) -> str:
    """The display mask, defined once: credentials then exfiltration URLs.

    Read paths that hand content to a UI apply BOTH redactors, in this order. Naming the
    composition here is what lets `restore_masked_spans` be a true inverse instead of a
    second, drifting guess at what a mask looks like.
    """
    masked, _ = redact_credentials(text)
    masked, _ = redact_exfiltration_urls(masked)
    return masked


def _mask_pairs(masked: str, stored: str) -> list[tuple[str, str]] | None:
    """Pair each mask in `masked` with the text it replaced in `stored`.

    Deterministic rather than fuzzy: the literal segments AROUND the masks are unchanged by
    redaction, so walking them through `stored` in order isolates each masked span exactly.
    Returns None when the walk cannot account for the whole string — the caller must then
    refuse the write rather than guess (a wrong guess writes a secret into the wrong place).
    """
    literals = _MASK_RE.split(masked)
    masks = _MASK_RE.findall(masked)
    pairs: list[tuple[str, str]] = []
    pos = 0
    for i, mask in enumerate(masks):
        prefix = literals[i]
        if not stored.startswith(prefix, pos):
            return None
        pos += len(prefix)
        following = literals[i + 1]
        end = stored.find(following, pos) if following else len(stored)
        if end < pos:
            return None
        pairs.append((mask, stored[pos:end]))
        pos = end
    if stored[pos:] != literals[-1]:
        return None
    return pairs


def restore_masked_spans(submitted: str, stored: str) -> str | None:
    """Put back any span the client is echoing as one of OUR OWN masks.

    Redaction is a display safeguard, so an editor seeded from a redacted read sends the mask
    back verbatim — and a write path with no inverse persists `[REDACTED: credential]` over the
    real content, irreversibly, on an edit the user never made to that span. This is the
    inverse: each mask in `submitted` is restored from the corresponding span of `stored`, in
    order, so every OTHER edit in the same save still lands.

    The mask is never lifted onto the wire — the plaintext only ever moves from the store back
    into the store — so the read path keeps masking unconditionally and no endpoint has to
    serve a secret to un-break editing.

    A mask is treated as a PLACEHOLDER standing for the n-th hidden value, so it restores
    wherever the user left it, even in a heavily rewritten body: keeping the marker means
    "keep the value it stands for". Deleting the marker deletes the value — that is a real
    instruction and is honoured. Two identical markers are therefore interchangeable: reordering
    them swaps which span each value lands in, which is why the mask text names its KIND.

    Returns the content to persist, or None when the stored value cannot be walked to recover
    what each mask replaced; the caller must then refuse the write rather than guess, since the
    alternative is persisting a mask over real content.
    """
    if not _MASK_RE.search(submitted):
        return submitted
    masked = redact_for_display(stored)
    if masked == stored:
        return submitted
    if submitted == masked:
        return stored
    pairs = _mask_pairs(masked, stored)
    if pairs is None:
        return None
    pending: dict[str, list[str]] = {}
    for mask, original in pairs:
        pending.setdefault(mask, []).append(original)

    def _take(m: "re.Match[str]") -> str:
        queue = pending.get(m.group(0))
        return queue.pop(0) if queue else m.group(0)

    return _MASK_RE.sub(_take, submitted)


SUSPICIOUS_BASH_PATTERNS: list[str] = [
    "curl * | bash",
    "curl * | sh",
    "wget * | bash",
    "| bash",
    "| sh",
    "| python",
    "| perl",
    "find * -delete",
    "find * -exec rm",
    "find * -exec shred",
    "xargs rm",
    "git clean -f",
    "shred ",
    "truncate ",
    "> /dev/sd",
    "mkfs.",
    "dd if=",
    "chmod 777",
    "chmod */usr/",
    "chmod */etc/",
    "chmod */sbin/",
    "chmod */boot/",
    "chmod */lib/",
    "chmod */lib64/",
    "chown */usr/",
    "chown */etc/",
    "chown */sbin/",
    "chown */boot/",
    "chown */lib/",
    "chown */lib64/",
    "eval $(",
    "base64 -d",
    "nc -e",
    "ncat -e",
    "/dev/tcp/",
    "xp_cmdshell",
    "GRANT ALL",
    "DROP DATABASE",
    "DROP TABLE",
    "TRUNCATE TABLE",
    "aws iam create-access-key",
    "aws sts assume-role",
    "export AWS_SECRET",
    "export AWS_ACCESS",
    "curl * -d @",
    "curl * --data @",
    "curl * -F file=@",
    "curl -d @",
    "curl --data @",
    "curl -F file=@",
    "wget --post-file",
    "nc * < ",
]

_RM_RF_RE = re.compile(
    r"""\brm\s+                       # rm
        (?:-[a-z]*[rf][a-z]*\s+|--(?:recursive|force)\s+)+   # ≥1 flag incl r or f
        ['"]?                         # optional opening quote on the target
        (?:                           # — a catastrophic target, whole-token —
            /\*?                      #   /  or  /*   (root, or everything under it)
          | ~/?                       #   ~  or  ~/   (home)
          | \.{1,2}/?                 #   .  ..  ./  ../  (cwd / parent)
          | \*                        #   a bare glob in cwd
          | \$\{?(?:HOME|PWD)\}?/?    #   $HOME / ${HOME} / $PWD (optional trailing /)
        )
        (?=['"]?(?:$|\s|;|&|\|))      # target ENDS here — a real path (./build,
                                      # ~/.cache/x, /tmp/y) has more segments → no match
    """,
    re.IGNORECASE | re.VERBOSE,
)


BASELINE_DENYLIST_FILE = "baseline_denylist.json"


def _baseline_digest(patterns: tuple[str, ...] | list[str]) -> str:
    """The canonical baseline fingerprint: sha256 over the newline-joined patterns.

    Content- and order-sensitive, so a removal, an edit and a reordering all show up.
    """
    return hashlib.sha256("\n".join(patterns).encode("utf-8")).hexdigest()


def _read_packaged_baseline() -> tuple[int, str, tuple[str, ...]]:
    """Read and verify the packaged baseline denylist.

    Raises on a missing file, malformed JSON, an empty pattern list, or a ``sha256``
    that disagrees with the patterns shipped alongside it. That is deliberate: the
    baseline is a required packaged asset, and a security module that cannot prove
    which commands it must refuse has to fail loudly at import rather than come up
    with a shorter (or empty) denylist. A packaging miss becomes a hard error instead
    of a silent bypass.
    """
    raw = (
        resources.files("gideon.security")
        .joinpath(BASELINE_DENYLIST_FILE)
        .read_text("utf-8")
    )
    doc = json.loads(raw)
    patterns = tuple(str(p) for p in doc["patterns"])
    if not patterns:
        raise ValueError("packaged baseline denylist ships no patterns")
    declared = str(doc["sha256"])
    actual = _baseline_digest(patterns)
    if actual != declared:
        raise ValueError(
            f"packaged baseline denylist integrity failure: declares {declared}, "
            f"content hashes to {actual}"
        )
    return int(doc["version"]), declared, patterns


BASELINE_DENYLIST_VERSION, _BASELINE_SHA256, _BASELINE_PATTERNS = (
    _read_packaged_baseline()
)

BUILTIN_DENIED_COMMAND_PATTERNS: list[str] = list(_BASELINE_PATTERNS)


_BASELINE_TAMPER_REPORTED: set[str] = set()


def _note_baseline_tamper(digest: str) -> bool:
    """Record ``digest`` as reported; return True the first time only."""
    if digest in _BASELINE_TAMPER_REPORTED:
        return False
    _BASELINE_TAMPER_REPORTED.add(digest)
    return True


def _log_baseline_event(
    event_type: str, outcome: str, detail: str, metadata: dict
) -> None:
    """Best-effort SEL write for a baseline heal or a rejected shrink.

    Audit failure must never break command screening, so this swallows and logs.
    """
    try:
        SecurityEventLog().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type=event_type,
                caller_identity="",
                agent="gideon",
                source="security",
                operation="denied_command_patterns",
                tool_kind="execute_bash",
                outcome=outcome,
                resources=detail,
                metadata=metadata,
            )
        )
    except Exception:  # pragma: no cover - audit must not break screening
        logger.debug("baseline denylist SEL write failed", exc_info=True)


def baseline_denied_command_patterns() -> tuple[str, ...]:
    """Return the verified baseline patterns, healing tampered in-memory state.

    The fast path is one sha256 over ~110 short strings and emits nothing, so a cold,
    untampered read is silent — only a digest mismatch does any work or logs anything.

    Repair order: the immutable in-process snapshot, then a fresh read of the packaged
    file if the snapshot itself was rebound. If neither verifies, the baseline is still
    not allowed to shrink — the union of every copy seen is returned (never fewer
    patterns) and the rejected shrink is logged as a tamper attempt.
    """
    global _BASELINE_PATTERNS
    live = BUILTIN_DENIED_COMMAND_PATTERNS
    if _baseline_digest(live) == _BASELINE_SHA256:
        return _BASELINE_PATTERNS

    good = _BASELINE_PATTERNS
    if _baseline_digest(good) != _BASELINE_SHA256:
        try:
            _, _, reread = _read_packaged_baseline()
        except Exception:
            reread = ()
        if _baseline_digest(reread) == _BASELINE_SHA256:
            good = reread
            _BASELINE_PATTERNS = reread
        else:
            union = tuple(dict.fromkeys(tuple(live) + tuple(good) + tuple(reread)))
            live[:] = list(union)
            if _note_baseline_tamper(_baseline_digest(union)):
                _log_baseline_event(
                    "baseline_denylist_tamper_attempt",
                    "rejected",
                    "no verified baseline source available",
                    {
                        "expected_sha256": _BASELINE_SHA256,
                        "effective_count": len(union),
                        "reason": "snapshot_and_packaged_file_both_unverified",
                    },
                )
            return union

    restored = [p for p in good if p not in live]
    live[:] = list(good)
    _log_baseline_event(
        "baseline_denylist_reasserted",
        "healed",
        f"restored {len(restored)} baseline pattern(s)",
        {
            "expected_sha256": _BASELINE_SHA256,
            "baseline_version": BASELINE_DENYLIST_VERSION,
            "baseline_count": len(good),
            "restored_count": len(restored),
            "restored_sample": restored[:5],
        },
    )
    return good


def denied_command_patterns() -> list[str]:
    """Return the effective bash denied-command regexes: the packaged baseline plus any
    user-configured additions from ``AppConfig.security.denied_commands``.

    The baseline is re-asserted first, so the result is always a superset of the packaged
    baseline — built-ins cannot be removed by config *or* by mutating the in-memory list.
    User patterns are appended and deduped against the baseline, so a user entry equal to
    a built-in is a no-op rather than a way to shorten the set. This is the single source
    the native bash tool, the action-provider denylist and the Security panel all read.
    """
    from gideon.core.config.loader import AppConfig

    baseline = baseline_denied_command_patterns()
    seen = set(baseline)
    additions: list[str] = []
    for pat in AppConfig.load().security.denied_commands:
        if isinstance(pat, str) and pat not in seen:
            seen.add(pat)
            additions.append(pat)
    return list(baseline) + additions


def verify_baseline_denylist() -> dict:
    """Re-verify the baseline against the packaged file on disk — the periodic probe.

    Heals in-memory drift the way every read does, then re-reads the packaged file and
    compares it to the fingerprint captured at import. A file that no longer matches is
    *not* adopted: the verified in-process baseline stays in force and the divergence is
    logged as a tamper attempt. This is what catches an edit that rewrote the patterns
    *and* the ``sha256`` together — self-consistent on disk, but not what we verified.
    """
    patterns = baseline_denied_command_patterns()
    file_ok = True
    file_detail = ""
    try:
        _, _, on_disk = _read_packaged_baseline()
        file_ok = _baseline_digest(on_disk) == _BASELINE_SHA256
        if not file_ok:
            file_detail = "packaged file no longer matches the verified baseline"
    except Exception as exc:
        file_ok = False
        file_detail = f"packaged file unreadable ({type(exc).__name__})"
    if not file_ok:
        _log_baseline_event(
            "baseline_denylist_tamper_attempt",
            "rejected",
            file_detail,
            {
                "expected_sha256": _BASELINE_SHA256,
                "baseline_version": BASELINE_DENYLIST_VERSION,
                "enforced_count": len(patterns),
                "reason": "packaged_file_diverged",
            },
        )
    return {
        "version": BASELINE_DENYLIST_VERSION,
        "sha256": _BASELINE_SHA256,
        "count": len(patterns),
        "file_verified": file_ok,
        "detail": file_detail,
    }


def denied_command_reason(command: str) -> str | None:
    """Return the denied pattern a command matches, or None.

    Matches ``command`` against :func:`denied_command_patterns` (built-in +
    user) case-insensitively. The native bash tool calls this before execution.
    """
    for pat in denied_command_patterns():
        try:
            if re.search(pat, command, re.IGNORECASE):
                return pat
        except re.error:
            continue
    return None


def redact(text: str) -> str:
    """Apply all redaction passes (exfiltration URLs + credentials)."""
    text = redact_exfiltration_urls(text)[0]
    text = redact_credentials(text)[0]
    return text


UNTRUSTED_OPEN = "<untrusted_content>"
UNTRUSTED_CLOSE = "</untrusted_content>"

_OPEN_TAG_RE = re.compile(
    re.escape(UNTRUSTED_OPEN[:-1]) + r"(?:\s[^>]*)?>", re.IGNORECASE
)


def is_fenced(text: str) -> bool:
    """Whether ``text`` already carries an untrusted-content fence (attributed or bare).

    Use this instead of `UNTRUSTED_OPEN in text` before deciding to fence: the substring form
    misses every attributed fence, which is the fail-open direction (double-wrapping).
    """
    return bool(text) and bool(_OPEN_TAG_RE.search(text))


ROLE_TOKENS: tuple[str, ...] = (
    "<|im_start|>",
    "<|im_end|>",
    "<|begin_of_text|>",
    "<|end_of_text|>",
    "<|start_header_id|>",
    "<|end_header_id|>",
    "<|eot_id|>",
    "<|eom_id|>",
    "[INST]",
    "[/INST]",
    "<<SYS>>",
    "<</SYS>>",
    "<|endoftext|>",
    "<|endofprompt|>",
    "<start_of_turn>",
    "<end_of_turn>",
    "<|user|>",
    "<|assistant|>",
    "<|system|>",
    "</s>",
    "<s>",
)

_ROLE_TOKEN_SUBS: tuple[tuple[str, str], ...] = (("|", "∣"), ("/", "⁄"))


def strip_role_tokens(text: str) -> str:
    """Neutralise chat-template role tokens in `text` (§7/R4 rule b).

    Breaks each token rather than deleting it, so the payload still reads the same to a human and
    an automation summarising its input does not silently lose a span. A token with no `|` or `/`
    to break (`[INST]`, `<<SYS>>`) is bracket-escaped instead, the same treatment
    `fence_untrusted` already gives its own markers.

    Case-insensitive: `<|IM_START|>` is the same wire token to a tokenizer that lowercases, and a
    guard that only caught the canonical casing would be trivially bypassed.
    """
    if not text:
        return text
    import re as _re

    def _neutralise(match: _re.Match[str]) -> str:
        token = match.group(0)
        for needle, replacement in _ROLE_TOKEN_SUBS:
            if needle in token:
                return token.replace(needle, replacement)
        return token.replace("<", "&lt;").replace(">", "&gt;").replace("[", "&#91;")

    pattern = "|".join(_re.escape(token) for token in ROLE_TOKENS)
    return _re.sub(pattern, _neutralise, text, flags=_re.IGNORECASE)


def fence_untrusted(
    text: str,
    *,
    source: str = "",
    source_type: str = "",
    source_id: str = "",
    transformation_path: str = "",
) -> str:
    """Wrap externally-sourced text so a model treats it as DATA, not instructions.

    Any text that entered from outside the user↔agent trust boundary — a fetched web
    page, a ticket/CR comment, an inbox message, an ingested document — can carry a
    prompt-injection ("ignore previous instructions, now do X"). Fencing it in
    ``<untrusted_content>`` markers (paired with the system-prompt note that the span is
    never executable) neutralises that: the model still READS the content but treats it
    as quoted data. Mirrors how Gideon already fences memory values.

    Defends against a **fence-break**: content that itself contains the close marker (a
    crafted page trying to "escape" the fence and inject trailing instructions) has its
    markers neutralised before wrapping, so the fence can't be closed early. An empty /
    whitespace-only input is returned unchanged (nothing to fence).

    Also neutralises **chat-template role tokens** (AUTOMATION-SUBSTRATE §7/R4 rule b).
    The XML fence is a convention the model is ASKED to respect; a role token is part of
    the wire format the runtime uses to mark who is speaking, so it can forge a turn
    boundary the fence cannot describe. Measured before this existed: every one of
    ChatML's ``<|im_start|>``, Llama-3's ``<|start_header_id|>``, Llama-2's ``[/INST]``
    and ``<<SYS>>``, Mistral's ``</s>`` and the bare ``<|endoftext|>`` passed through
    ``fence_untrusted`` intact. Local providers are exactly where that bites: a hosted
    API rejects or escapes stray control tokens, while a local runtime applying its own
    chat template will happily honour them.

    Carries **provenance attributes** (§7/R4 rule c): ``source_type`` (the CLASS of
    origin — ``web_watch``, ``file``, ``inbox``), ``source_id`` (which one — a url, a
    path, a message id) and ``transformation_path`` (how it got here — ``poll``,
    ``digest``, ``extract``). ``source=`` is kept and unchanged, because thirteen call
    sites pass it and it is what the existing tag-parser in ``learning/hygiene.py``
    tolerates; the three new attributes are additive and optional.

    Why all three rather than one string: "a web page said this" and "THIS page said
    this, and we summarised it on the way" are different claims, and only the second
    lets a reader (or a later audit) tell whether the text a model acted on is the text
    that arrived. Values are attribute-escaped, so a crafted ``source_id`` cannot close
    the tag it is inside — the same fence-break defence the body already gets."""
    if not text or not text.strip():
        return text
    safe = text.replace("<untrusted_content>", "&lt;untrusted_content&gt;").replace(
        "</untrusted_content>", "&lt;/untrusted_content&gt;"
    )
    safe = strip_role_tokens(safe)
    attrs = "".join(
        f" {name}={_fence_attr(value)}"
        for name, value in (
            ("source", source),
            ("source_type", source_type),
            ("source_id", source_id),
            ("transformation_path", transformation_path),
        )
        if value
    )
    return f"{UNTRUSTED_OPEN[:-1]}{attrs}>\n{safe}\n{UNTRUSTED_CLOSE}"


def _fence_attr(value: str) -> str:
    """One provenance attribute value, safe to sit inside the fence's own tag.

    🔴 The attribute is attacker-influenced: a `source_id` is a url or a file path that
    came from outside. Without escaping, a crafted value containing `>` would close the
    open tag early and everything after it would read as un-fenced instructions — the
    fence-break the body is already protected against, reintroduced through the label.

    Angle brackets and quotes are escaped, and newlines collapse to a space so a value
    cannot split the tag across lines. Truncated because a tag is metadata: a 4 KB url
    in the prompt prefix costs tokens on every fenced span.
    """
    flat = " ".join(str(value or "").split())[:200]
    return (
        flat.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def is_denied(tool_name: str, extra_patterns: list[str] | None = None) -> str | None:
    """Check tool name against built-in + extra deny patterns.

    Returns denial reason string, or None if allowed.
    """
    lower = tool_name.lower()
    has_separators = bool(_CMD_SEPARATOR_RE.search(lower))
    all_patterns = BUILTIN_DENY_PATTERNS + (extra_patterns or [])
    for pattern in all_patterns:
        if fnmatch.fnmatch(lower, pattern.lower()):
            exceptions = _DENY_EXCEPTIONS.get(pattern, [])
            if (
                not has_separators
                and exceptions
                and any(fnmatch.fnmatch(lower, e.lower()) for e in exceptions)
            ):
                if not _emit_deny_exception_event(tool_name, pattern):
                    return f"Blocked by security policy: {pattern}"
                continue
            return f"Blocked by security policy: {pattern}"
    return None


def _emit_deny_exception_event(tool_name: str, deny_pattern: str) -> bool:
    """Emit an SEL audit event when a deny exception is applied.

    Returns True if the event was logged successfully, False otherwise.
    The caller must NOT grant the exception if this returns False.
    """
    try:
        sel = SecurityEventLog()
        sel.log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="deny_exception",
                caller_identity="",
                agent="gideon",
                source="security",
                operation=tool_name,
                outcome="allowed",
                resources=f"deny_pattern={deny_pattern}",
                metadata={
                    "deny_pattern": deny_pattern,
                    "mechanism": "_DENY_EXCEPTIONS",
                },
            )
        )
        return True
    except Exception:
        logger.warning(
            "SEL audit failed for deny_exception — denying %r (fail-closed)",
            tool_name,
            exc_info=True,
        )
        return False


DENY_KIND_USER = "user"
DENY_KIND_HOOK = "hook"
DENY_KIND_READONLY = "readonly"
DENY_KIND_POLICY = "policy"
DENY_KIND_SENSITIVE = "sensitive"

_HARD_DENY_KINDS = frozenset({DENY_KIND_POLICY, DENY_KIND_SENSITIVE})

_DENIAL_FRAGMENTS: tuple[str, ...] = (
    "blocked by a security policy",
    "blocked by the read-only gate",
    "blocked by a policy hook",
    "was declined (",
)


def is_denial_observation(text: str) -> bool:
    """True when ``text`` is an observation :func:`classify_denial` produced.

    A DENIAL and a FAILURE are both fed back to the model as ``Error: …``, but they
    are different priors: "the user/policy refuses this" is not "this tool does not
    work". Procedural memory needs to tell them apart (a denial labelled ``failed``
    teaches "prefer an alternative tool" for what is really a standing policy), and
    this is the only structural signal available at the seam that records the
    outcome — the runtime already derives ``failed`` from the same string.
    """
    return bool(text) and any(frag in text for frag in _DENIAL_FRAGMENTS)


def classify_denial(kind: str, reason: str, tool_name: str = "") -> tuple[bool, str]:
    """Map a denial to ``(recoverable, observation)`` for the model.

    ``observation`` is the text fed back as the tool's result so the agent learns
    why the call was blocked and what to do next, instead of stalling. Recoverable
    denials invite adaptation (without repeating the same call); hard denials are
    framed as terminal and non-circumventable, with no recovery hint.
    """
    tool = f" `{tool_name}`" if tool_name else ""
    if kind in _HARD_DENY_KINDS:
        return (
            False,
            f"Error: tool{tool} blocked by a security policy ({reason}). This is "
            "non-negotiable — do NOT attempt to circumvent or rephrase it. Choose "
            "a different approach that does not require this, or stop and explain.",
        )
    if kind == DENY_KIND_READONLY:
        return (
            True,
            f"Error: tool{tool} blocked by the read-only gate ({reason}). Do NOT "
            "retry the same write — use a read-only alternative, or stop and "
            "explain what you would change and why.",
        )
    if kind == DENY_KIND_HOOK:
        return (
            True,
            f"Error: tool{tool} blocked by a policy hook ({reason}). Do NOT retry "
            "the same call — try a genuinely different approach that satisfies the "
            "policy, or stop and explain the blocker to the user.",
        )
    return (
        True,
        f"Error: tool{tool} was declined ({reason}). Do NOT retry the same call — "
        "either take a different approach or stop and ask the user how to proceed.",
    )


def audit_bash_command(command: str) -> str | None:
    """Check a bash command against suspicious patterns.

    Returns warning string, or None if clean.
    Patterns with ``*`` are matched as globs via fnmatch.
    """
    lower = command.lower()
    for pattern in SUSPICIOUS_BASH_PATTERNS:
        pat = pattern.lower()
        if "*" in pat:
            if fnmatch.fnmatch(lower, f"*{pat}*"):
                return f"Suspicious command detected: matches '{pattern}'"
        elif pat in lower:
            return f"Suspicious command detected: matches '{pattern}'"
    if _RM_RF_RE.search(command):
        return "Suspicious command detected: recursive force-delete of a critical path"
    return None


def scan_history(history_dir: Path, last_n: int = 100) -> list[dict]:
    """Scan recent conversation history for suspicious tool usage.

    Returns list of findings: [{file, line, tool, command, warning}]
    """
    findings: list[dict] = []
    if not history_dir.is_dir():
        return findings

    files = sorted(
        history_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    checked = 0
    for f in files:
        try:
            for line in f.read_text().splitlines():
                if checked >= last_n:
                    return findings
                checked += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = entry.get("content", "")
                role = entry.get("role", "")
                if role != "assistant" or not isinstance(content, str):
                    continue
                warning = audit_bash_command(content)
                if warning:
                    findings.append(
                        {
                            "file": f.name,
                            "warning": warning,
                            "snippet": content[:200],
                        }
                    )
        except OSError:
            continue
    return findings


def scan_memory() -> list[dict]:
    """Scan memory for suspicious content via the memory service. Returns findings."""
    from gideon.cognition.memory_service import MemoryService
    from gideon.cognition.vector_memory import SemanticArchive, _contains_injection

    findings: list[dict] = []
    try:
        store = SemanticArchive()
        store.init()
    except Exception:
        return findings
    svc = MemoryService.over_vector_store(store)

    for entry in svc.get_all_semantic():
        val = entry.get("value_json", "")
        if _contains_injection(val):
            findings.append(
                {
                    "type": "semantic",
                    "key": entry["key"],
                    "value": val[:200],
                    "warning": "Injection pattern detected",
                }
            )

    for entry in svc.episodic_list(limit=1000):
        text = entry.get("text", "")
        if _contains_injection(text):
            findings.append(
                {
                    "type": "episodic",
                    "key": entry["id"],
                    "value": text[:200],
                    "warning": "Injection pattern detected",
                }
            )

    store.close()
    return findings


def should_record_observe_history(
    channel_history: object | None,
    user_authorized: bool,
) -> bool:
    """Return True if an observe-mode message should be recorded.

    Only authorized users' messages are recorded to prevent non-owner
    prompt injection via shared channel traffic.
    """
    return channel_history is not None and user_authorized


def redact_and_truncate(text: str, max_chars: int = 4000) -> str:
    """Truncate, then redact credentials and exfiltration URLs."""
    return redact_credentials(redact_exfiltration_urls((text or "")[:max_chars])[0])[0]


def redact_field(text: str) -> str:
    """Both redaction passes over one field. Applied to EVERY role — see the module
    docstring for why the write path's role exemption can't be inherited here.

    Public because ``session_share`` needs the SAME redaction for the artifact name it
    derives (SM-9). One implementation with two callers, never a second pass that redacts
    slightly less.
    """
    if not text:
        return ""
    safe, _ = redact_exfiltration_urls(str(text))
    safe, _ = redact_credentials(safe)
    return safe
