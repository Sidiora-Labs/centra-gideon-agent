"""SDK: the local-model management contract — ``LocalModel`` + ``LocalModelProvider``.

A model-provider app that owns locally-downloadable models implements
:class:`LocalModelProvider` (list / download / delete, optionally search) so the host
lists, downloads, deletes, and surfaces its models uniformly — the app declares the
models it introduces; core hardcodes none. The app ALSO subclasses its use-case ABC
(``SttProvider`` / ``TtsProvider`` / ``DiarizationProvider`` / ``EmbeddingProvider``) for
inference. Import these from the SDK so core internals can evolve underneath the app.
"""

from gideon.integrations.local_models.provider import LocalModelProvider  # noqa: F401
from gideon.integrations.local_models.provider import LocalModel

__all__ = ["LocalModel", "LocalModelProvider"]
