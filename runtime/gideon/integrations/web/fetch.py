"""web_fetch pipeline — the layered, guarded fetch behind the `web_fetch` tool.

    ① URL-provenance gate   — the URL must have appeared in context (returned by a
                              prior web_search / web_fetch in this session). A
                              mitigation against model-fabricated-URL exfil, layered
                              with ② (not relied on alone).
    ② egress chokepoint     — net.fetch (SSRF-safe: IP-pinned, redirect-revalidated).
    ③ extract               — the shared trafilatura/nh3 core (web/extract.py).
    ④ token economy         — cap to max_tokens; offset pagination (start_index →
                              next_index) so a large page is read in chunks.

Provenance is tracked per-session in-process: web_search records the URLs it surfaced
(:func:`record_seen_urls`) and web_fetch records the page it fetched, so a follow-up
fetch of a link found mid-conversation passes the gate. The gate is advisory — every
fetch still goes through the egress guard regardless.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urlparse

from gideon.integrations.web.extract import extract_main_content
from gideon.security.net import STRICT, EgressBlocked, egress_policy_for
from gideon.security.net import fetch as net_fetch
from gideon.security.net.policy import EgressPolicy

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_DEFAULT_MAX_TOKENS = 5000

_seen_by_session: dict[str, set[str]] = {}
_MAX_SEEN_PER_SESSION = 2000


def _canonical(url: str) -> str:
    """Canonicalize a URL for provenance comparison (drop fragment + trailing slash)."""
    u, _ = urldefrag((url or "").strip())
    return u.rstrip("/")


def record_seen_urls(session_key: str, urls) -> None:
    """Record URLs surfaced to a session (by web_search results / a fetched page) so a
    later web_fetch of one passes the provenance gate."""
    if not session_key:
        return
    seen = _seen_by_session.setdefault(session_key, set())
    for u in urls:
        c = _canonical(u)
        if c:
            seen.add(c)
    if len(seen) > _MAX_SEEN_PER_SESSION:
        _seen_by_session[session_key] = set(list(seen)[-_MAX_SEEN_PER_SESSION:])


def url_has_provenance(session_key: str, url: str) -> bool:
    """Whether ``url`` was previously surfaced to this session."""
    return _canonical(url) in _seen_by_session.get(session_key, set())


def clear_session(session_key: str) -> None:
    """Drop a session's provenance record (session end)."""
    _seen_by_session.pop(session_key, None)


@dataclass
class FetchOutcome:
    """The result of the web_fetch pipeline (the tool maps this to a ToolResult)."""

    ok: bool
    url: str = ""
    title: str = ""
    content: str = ""
    char_count: int = 0
    total_chars: int = 0
    start_char: int = 0
    end_char: int = 0
    truncated: bool = False
    next_index: int | None = None
    extractor: str = ""
    error: str = ""
    recovery_hints: list[str] = field(default_factory=list)
    risk_level: str = "safe"


async def web_fetch(
    url: str,
    *,
    session_key: str = "",
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    start_index: int = 0,
    require_provenance: bool = True,
    render: bool = False,
    policy: EgressPolicy = STRICT,
) -> FetchOutcome:
    """Fetch + extract a URL through the guarded pipeline.

    ``require_provenance`` gates on the URL having been surfaced to ``session_key``
    first (skipped when there is no session context, or for an explicit caller that
    opts out — e.g. a user-pasted URL flow). The egress guard always applies.

    ``render`` runs the page through a headless browser (Playwright) so client-rendered
    (JS) content is captured. The egress guard is enforced before the browser navigates;
    if Playwright isn't installed it falls back to the plain HTTP fetch.
    """
    url = (url or "").strip()
    if not url:
        return FetchOutcome(
            ok=False,
            error="url is required",
            recovery_hints=["Pass a non-empty 'url'."],
        )
    policy = egress_policy_for(policy)
    from gideon.security.guardrails.policy import profile_for_session
    from gideon.security.net.policy import egress_policy_for_profile

    _tier = profile_for_session(session_key).egress_tier
    _narrowed = egress_policy_for_profile(policy, _tier)
    if _narrowed is None:
        return FetchOutcome(
            ok=False,
            url=url,
            risk_level="destructive",
            error=f"egress is off for this run (safety profile egress tier {_tier!r})",
            recovery_hints=[
                "This run's safety posture denies all network egress.",
                "Widen the egress tier in the governance ceiling, or run the fetch from an "
                "interactive session.",
            ],
        )
    policy = _narrowed
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        return FetchOutcome(
            ok=False,
            url=url,
            error="url must be http(s)",
            recovery_hints=["Provide an http or https URL."],
        )

    if require_provenance and session_key and not url_has_provenance(session_key, url):
        return FetchOutcome(
            ok=False,
            url=url,
            risk_level="caution",
            error=(
                "url has no provenance in this session — it was not returned by a "
                "prior web_search or web_fetch"
            ),
            recovery_hints=[
                "Run web_search first and fetch a URL from its results.",
                "Only fetch URLs surfaced in the conversation, not ones constructed from memory.",
            ],
        )

    final_url = url
    if render:
        from gideon.integrations.web.render import render_url

        rendered = await render_url(url, policy=policy)
        if rendered.ok:
            html_body, ctype, final_url = rendered.html, "text/html", rendered.url
        elif rendered.unavailable:
            logger.info(
                "web_fetch render requested but Playwright unavailable; using HTTP fetch"
            )
            render = False
        else:
            return FetchOutcome(
                ok=False,
                url=rendered.url or url,
                error=rendered.error,
                recovery_hints=list(rendered.recovery_hints or []),
                risk_level=rendered.risk_level,
            )

    if not render:
        try:
            resp = await net_fetch(url, policy=policy)
        except EgressBlocked as exc:
            return FetchOutcome(
                ok=False,
                url=url,
                error=str(exc),
                recovery_hints=list(exc.recovery_hints),
                risk_level=exc.risk_level,
            )
        except Exception as exc:
            logger.warning(
                "web_fetch network error for %s: %s", url, exc, exc_info=True
            )
            return FetchOutcome(
                ok=False,
                url=url,
                error=f"fetch failed: {exc}",
                recovery_hints=[
                    "The site may be down or slow; retry, or fetch a different source."
                ],
            )
        html_body, ctype, final_url = (
            resp.text,
            resp.headers.get("Content-Type", ""),
            resp.url,
        )

    if "html" in ctype.lower():
        doc = extract_main_content(html_body, url=final_url)
        full_text, title, extractor = doc.text, doc.title, doc.extractor
    else:
        full_text, title, extractor = html_body, "", "raw"

    record_seen_urls(session_key, [final_url])

    total = len(full_text)
    budget = max(1, max_tokens) * _CHARS_PER_TOKEN
    start = max(0, start_index)
    window = full_text[start : start + budget]
    end = start + len(window)
    truncated = end < total
    return FetchOutcome(
        ok=True,
        url=final_url,
        title=title,
        content=window,
        char_count=len(window),
        total_chars=total,
        start_char=start,
        end_char=end,
        truncated=truncated,
        next_index=end if truncated else None,
        extractor=extractor,
    )


_EXTRACT_CONTENT_CHARS = 24000


@dataclass
class ExtractOutcome:
    """The result of web_extract (the tool maps this to a ToolResult)."""

    ok: bool
    url: str = ""
    title: str = ""
    data: dict | None = None
    error: str = ""
    recovery_hints: list[str] = field(default_factory=list)
    risk_level: str = "safe"


async def web_extract(
    url: str,
    instructions: str,
    *,
    session_key: str = "",
    require_provenance: bool = True,
    policy: EgressPolicy = STRICT,
) -> ExtractOutcome:
    """Fetch a page (through the guarded web_fetch pipeline) and extract STRUCTURED
    data from it with an LLM, per the caller's ``instructions`` (a description of the
    fields/shape wanted). Returns a parsed JSON object.

    Reuses the existing pieces — the SSRF-safe fetch + the shared extractor for the
    page text, and the system's configured model (one_shot_completion) for the
    structured extraction — so there's no new fetch path or model wiring.
    """
    if not (instructions or "").strip():
        return ExtractOutcome(
            ok=False,
            url=url,
            error="instructions are required",
            recovery_hints=["Describe the fields / shape to extract."],
        )

    fetched = await web_fetch(
        url,
        session_key=session_key,
        max_tokens=_EXTRACT_CONTENT_CHARS // _CHARS_PER_TOKEN,
        require_provenance=require_provenance,
        policy=policy,
    )
    if not fetched.ok:
        return ExtractOutcome(
            ok=False,
            url=fetched.url or url,
            error=fetched.error,
            recovery_hints=fetched.recovery_hints,
            risk_level=fetched.risk_level,
        )

    content = fetched.content[:_EXTRACT_CONTENT_CHARS]
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt
    from gideon.security.security import fence_untrusted

    prompt = (
        render_use_case_prompt(
            "web_extract",
            {
                "instructions": instructions.strip(),
                "title": fetched.title or "(none)",
                "url": fetched.url,
                "content": fence_untrusted(
                    content, source=fetched.url or "web-extract"
                ),
            },
        )
        or ""
    )
    from gideon.integrations.llm_helpers import one_shot_completion, parse_llm_json
    from gideon.security.guardrails.failure import OutputContractError

    try:
        raw = await one_shot_completion(prompt, use_case="reasoning", output_type=dict)
    except OutputContractError:
        return ExtractOutcome(
            ok=False,
            url=fetched.url,
            title=fetched.title,
            error="the model did not return a parseable JSON object",
            recovery_hints=[
                "Retry, or simplify the requested shape.",
                "web_fetch returns the raw page content if structured extraction isn't needed.",
            ],
        )
    except Exception as exc:
        logger.warning(
            "web_extract LLM call failed for %s: %s", url, exc, exc_info=True
        )
        return ExtractOutcome(
            ok=False,
            url=fetched.url,
            title=fetched.title,
            error=f"extraction model call failed: {exc}",
            recovery_hints=[
                "Ensure a chat/reasoning model is configured in Settings → Models."
            ],
        )

    data = parse_llm_json(raw) or {}
    return ExtractOutcome(ok=True, url=fetched.url, title=fetched.title, data=data)
