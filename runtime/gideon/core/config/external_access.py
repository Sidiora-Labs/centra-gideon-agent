"""Config sections for external access: the surfaces reachable from outside the host.

One domain, three sections plus the surface table they all key off. Grouped because the
per-surface shape is shared: ``EXTERNAL_ACCESS_SURFACES`` names the surfaces once, and both
the access and the capture sections derive their per-surface defaults from it, so a new
surface is added in one place instead of three.

Every flag here opens something, so they all parse fail-CLOSED through ``_expose_flag``.

Deliberately NO ``from __future__ import annotations``: ``config/schema.py`` resolves a
STRING annotation by ``eval``-ing it in ``config.loader``'s namespace with a silent
``except: return str`` fallback, so postponed annotations here would degrade this file's
schema types to ``string`` without any error. Real type objects cannot take that path.
"""

from dataclasses import dataclass, field

from gideon.core.config.coercion import _meta, _num

EXTERNAL_ACCESS_SURFACES: tuple[str, ...] = (
    "openai",
    "mcp",
    "a2a",
    "capture",
    "bridge",
)


def _ea_surface_data(section: dict, surface: str) -> dict:
    from gideon.core.config.codec import ConfigInput

    return ConfigInput(section).section(surface)


def _capture_retention(section: dict) -> float:
    options = (
        _ea_surface_data(section, "capture").get("retention_days"),
        section.get("capture_retention_days"),
    )
    selected = next((value for value in options if value is not None), None)
    return _num(selected, 30)


@dataclass
class ExternalAccessSurfaceConfig:
    """One inbound surface's switches (EXTERNAL-ACCESS §1.1).

    Both default to the CLOSED position. `enabled` in particular is fail-closed by
    design: a missing or corrupt value reads False, because an inbound network
    surface that turns itself on when config is unreadable fails in the wrong
    direction. Do not "fix" this to be lenient."""

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Enabled",
            "Expose this inbound surface. Off by default; also requires the master "
            "external_access.enabled switch AND a ≥32-byte surface token "
            "(gideon inbound token create <surface>). Loopback-only unless "
            "allow_remote is on AND external_access.public_url is set.",
        ),
    )
    allow_remote: bool = field(
        default=False,
        metadata=_meta(
            "Allow remote",
            "Permit non-loopback callers. Requires external_access.public_url, and the "
            "request's Host must match it exactly. Prefer an SSH tunnel to loopback. "
            "The control bridge ignores this flag entirely — loopback-only forever.",
        ),
    )


@dataclass
class CaptureSurfaceConfig(ExternalAccessSurfaceConfig):
    """The capture proxy's surface switches PLUS its two operator knobs (§7.1, §7.2).

    Capture is the one surface that owns durable state and outbound forwarding, so it
    needs two fields the other four do not: how long recorded sessions are kept, and
    which upstream hosts the streaming client may egress to. It inherits
    `enabled`/`allow_remote` unchanged — same fields, same fail-CLOSED parsing — rather
    than restating them, so a future change to the shared pair cannot drift here.

    `upstream_allowlist` is fail-closed in the strong sense: empty means the streaming
    proxy has no approved egress hosts, not "allow anything". §7.1 requires an
    operator-visible allow-list precisely so upstream forwarding is never
    hand-rolled unguarded egress, and a default that allowed all hosts would make the
    guard decorative.
    """

    retention_days: int = field(
        default=30,
        metadata=_meta(
            "Capture retention (days)",
            "How long recorded external-agent sessions are kept before the curator "
            "tick prunes them. 0 = keep forever (NOT 'delete immediately') — the "
            "reading that cannot silently destroy data.",
        ),
    )
    upstream_allowlist: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Upstream allow-list",
            "Hosts the capture proxy's streaming client may forward to. Empty = no "
            "approved upstreams; every forward is guard-evaluated against this list, "
            "so an empty list denies rather than permits.",
        ),
    )

    def __post_init__(self) -> None:
        self.retention_days = min(3650, max(0, int(self.retention_days)))
        hosts = (str(host).strip() for host in self.upstream_allowlist)
        self.upstream_allowlist = [host.lower() for host in hosts if host]


@dataclass
class ExternalAccessConfig:
    """The shared inbound access seam (EXTERNAL-ACCESS §1, §11). Off unless configured.

    Replaces MCP-READONLY-INBOUND's single-surface `InboundConfig` outright (clean
    break, no `inbound` back-read): that section could only describe one surface, and
    four more were arriving. The kill switches are LAYERED — master, per-surface,
    per-client (`inbound_clients.json`) and the guardrails incident flag — and every
    one of them parses fail-CLOSED, the inverse of `guard_flag`, because for an
    *inbound* surface OFF is the safe state.
    """

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Enabled",
            "Master switch for every inbound surface. Off unmounts all five within one "
            "config read. Fail-closed: an unreadable value reads OFF.",
        ),
    )
    openai: ExternalAccessSurfaceConfig = field(
        default_factory=ExternalAccessSurfaceConfig,
        metadata=_meta(
            "OpenAI-compatible API", "POST /v1/* — an agent behind an OpenAI dialect."
        ),
    )
    mcp: ExternalAccessSurfaceConfig = field(
        default_factory=ExternalAccessSurfaceConfig,
        metadata=_meta("MCP surface", "POST /mcp — JSON-RPC read-only tool surface."),
    )
    a2a: ExternalAccessSurfaceConfig = field(
        default_factory=ExternalAccessSurfaceConfig,
        metadata=_meta("A2A gateway", "Agent-to-agent inbound dialect."),
    )
    capture: CaptureSurfaceConfig = field(
        default_factory=CaptureSurfaceConfig,
        metadata=_meta(
            "Capture proxy", "External-agent capture proxy (records full prompts)."
        ),
    )
    bridge: ExternalAccessSurfaceConfig = field(
        default_factory=ExternalAccessSurfaceConfig,
        metadata=_meta(
            "Control bridge", "Self-describing MCP control bridge. Loopback-only."
        ),
    )
    public_url: str = field(
        default="",
        metadata=_meta(
            "Public URL",
            "The URL this instance answers to (e.g. https://gideon.example.com). Required "
            "for any non-loopback inbound access; the request Host must match it. NOT "
            "PATCH-editable — it is a security boundary, not a display setting.",
        ),
    )
    rate_rps: float = field(
        default=1.0,
        metadata=_meta(
            "Rate (req/s)", "Sustained per-client request rate (§1.3 cap override)."
        ),
    )
    rate_burst: int = field(
        default=20,
        metadata=_meta(
            "Burst", "Per-client token-bucket capacity, so a panel can batch a few."
        ),
    )
    rate_concurrent: int = field(
        default=4,
        metadata=_meta("Concurrency", "Per-client in-flight request ceiling."),
    )
    auto_disable_after_breaches: int = field(
        default=10,
        metadata=_meta(
            "Auto-disable after",
            "Cap breaches within an hour before a client is auto-disabled (0 = never).",
        ),
    )
    capture_retention_days: int = field(
        default=30,
        metadata=_meta(
            "Capture retention (days)",
            "Legacy flat spelling of external_access.capture.retention_days, kept "
            "because the settings PATCH key and the ExternalAccessPanel control both "
            "already write it. `load()` resolves ONE value and mirrors it into both, "
            "so the two can never disagree and the shipped operator control genuinely "
            "governs pruning. Collapsing to the nested field alone is the clean break "
            "(see the note in load()).",
        ),
    )

    def __post_init__(self) -> None:
        policies = {
            "rate_rps": (float, 0.01, 1000.0),
            "rate_burst": (int, 1, 10000),
            "rate_concurrent": (int, 1, 256),
            "auto_disable_after_breaches": (int, 0, 10000),
            "capture_retention_days": (int, 0, 3650),
        }
        for key, (convert, minimum, maximum) in policies.items():
            value = convert(getattr(self, key))
            setattr(self, key, max(minimum, min(maximum, value)))
