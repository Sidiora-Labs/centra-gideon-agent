"""CLI chat subcommand."""

import gc
import json
import sys

from gideon.core.config import AppConfig
from gideon.core.config import loader as config_loader
from gideon.core.constants import DATA_WARNING
from gideon.integrations.acp.errors import AcpError, AcpTimeoutError
from gideon.integrations.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, ModelProvider


def config_path():
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


BANNER = r"""
   __  __         _    ___ _
  |  \/  |___ ___| |_ / __| |__ ___ __ __
  | |\/| / -_|_-<| ' \ (__| / _` \ V  V /
  |_|  |_\___/__/|_||_\___|_\__,_|\_/\_/

  Your personal AI agent
"""


async def _chat(message: str | None, model: str | None) -> None:
    """Run a single message or interactive chat session."""
    cfg = AppConfig.load()
    provider: ModelProvider = cfg.create_provider_factory()(
        "cli_chat", agent=cfg.default_agent or None, model_override=model or None
    )
    await provider.start()

    if message:
        await _send_and_print(provider, message)
    else:
        await _interactive(provider, cfg)

    await provider.shutdown()
    gc.collect()


async def _send_and_print(provider: ModelProvider, message: str) -> None:
    """Stream a single message to stdout, handling errors and timeouts."""
    try:
        async for event in provider.stream(message):
            if event.kind == EVENT_TEXT_CHUNK:
                print(event.text, end="", flush=True)
            elif event.kind == EVENT_COMPLETE:
                from gideon.operations.usage_ledger import record_from_event

                _m = getattr(getattr(provider, "client", None), "_model", "") or ""
                record_from_event(
                    event,
                    source="cli",
                    session_key="cli_chat",
                    provider="acp",
                    model=_m if isinstance(_m, str) and _m != "auto" else "",
                )
                break
        print()
    except AcpTimeoutError as e:
        if e.partial_output:
            print(e.partial_output)
        print("\n⏱️  Response timed out.", file=sys.stderr)
        sys.exit(1)
    except AcpError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        sys.exit(1)


async def _interactive(provider: ModelProvider, cfg: AppConfig) -> None:
    """REPL loop — read user input, stream responses, auto-compact at configured threshold."""
    print(BANNER)
    print(DATA_WARNING)
    print()
    print("Type your message (Ctrl+D or 'exit' to quit)\n")

    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not message:
            continue
        if message.lower() in ("exit", "quit", "/exit", "/quit", ":q"):
            print("Bye!")
            break

        await _send_and_print(provider, message)

        pct = provider.context_usage_pct()

        if pct is not None and pct >= cfg.session.autocompact_pct:
            reason = f"context at {pct:.0f}%"
            print(f"\n🔄 Compacting — {reason}", file=sys.stderr)
            try:
                await provider.compact()
            except Exception:
                pass
            await provider.shutdown()
            await provider.start()
        elif pct is not None and pct >= 75.0:
            print(f"\n⚠️  Context at {pct:.0f}%", file=sys.stderr)

        print()


def _ensure_default_agent_in_config() -> None:
    """Ensure config.json includes a default Gideon agent for fresh installs."""
    p = config_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    if not data.get("agents"):
        data["agents"] = {
            "default": {
                "provider_agent": "gideon",
                "workspace": "default",
                "memory_store": "default",
            }
        }
        data["default_agent"] = "default"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
