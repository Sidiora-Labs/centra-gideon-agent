"""Gideon App SDK — the STABLE surface an installable app may import.

This is the published core/app boundary (workspace-core-app-split, §3). A separated
app imports ONLY from ``gideon.sdk.*`` — never deep core internals like
``gideon.dashboard`` or ``gideon.agents.native`` — so the core can evolve
its internals without breaking apps, and an import-lint can enforce the boundary.

Each submodule re-exports one provider-type's ABC + its data types from the live core
``base.py`` (so there is ONE definition; the SDK is a thin, versioned facade over it):

    from gideon.sdk.search import SearchProvider, SearchResult
    def create_provider(config): ...

Submodules: ``search``, ``channel``, ``model``, ``memory``, ``embedding``, ``inbox``,
``knowledge``, ``prompt``, ``tool``, ``action`` (the 10 provider ABCs) + ``manifest``
(AppManifest/ProviderConfig, type-only) + ``util`` (the few cross-cutting helpers apps
need: config_dir / app_data_dir / sandbox wrap).

The ~4 AMBIGUOUS action providers (run-prompt/run-workflow/invoke-agent/create-task)
need a ``gideon.sdk.runtime`` (stable spawn/run/create calls) before they can
leave core; that submodule is intentionally not published yet.
"""

# The SDK's own version — bump on a breaking change to any re-exported surface.
# Apps gate compatibility via app.json ``minGideonVersion``.
SDK_VERSION = "1.0"

__all__ = ["SDK_VERSION"]
