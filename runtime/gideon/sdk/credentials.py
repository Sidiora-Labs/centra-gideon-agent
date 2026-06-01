"""SDK: the credential store.

Stable re-export of ``gideon.llm.credentials`` — the generic, provider-
agnostic secret store an app uses to resolve an API key/token by name (the same
store core uses; despite the ``llm`` package location it's not LLM-specific). An
app imports this, not the core module, so the core path can move.
"""

from gideon.llm.credentials import Credential, CredentialStore  # noqa: F401

__all__ = ['CredentialStore', 'Credential']
