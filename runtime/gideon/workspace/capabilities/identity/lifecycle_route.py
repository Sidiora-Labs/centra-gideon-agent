"""Local route approval fingerprints without credentials."""

import hashlib
import json

from gideon.core.config import config_dir


def require_route(loop):
    if not loop.agent and not loop.model and not loop.provider:
        raise ValueError(
            "Bind an explicit agent or model route before enabling this identity"
        )


def route_fingerprint(loop):
    route = {
        key: getattr(loop, key, None)
        for key in (
            "agent",
            "model",
            "provider",
            "provider_agent",
            "reasoning_effort",
            "execution",
            "roster",
            "kind",
        )
    }
    home = config_dir()
    for name in ("config.json", "config.yaml", "config.toml", "routing_policy.json"):
        path = home / name
        if path.is_file():
            route[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    route["settings"] = {
        str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
        for directory in (home / "use_case_settings", home / "agents")
        if directory.is_dir()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }
    return hashlib.sha256(
        json.dumps(route, sort_keys=True, default=str).encode()
    ).hexdigest()
