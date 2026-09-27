from __future__ import annotations

import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_native_publish_and_graph_save_share_revision_transaction(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.automation.workflows import defs as workflow_defs
    from gideon.automation.workflows import service
    from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider, _def_path

    provider = NativeWorkflowDefProvider()
    prior_provider = workflow_defs.get_provider("native")
    workflow_defs.register_provider(provider)
    try:
        created = await provider.save_def(
            name="assistant-publish-race",
            description="Original graph",
            inputs={"source": {"type": "string"}},
            metadata={"a2a_published": False, "preserved": "metadata"},
            root={"id": "root", "kind": "transform", "config": {"expr": 1}},
        )
        starting_revision = created.version

        publish, save = await asyncio.gather(
            service.set_a2a_published(
                "assistant-publish-race", True, expected_revision=starting_revision
            ),
            provider.save_def(
                name="assistant-publish-race",
                description="Concurrent graph edit",
                inputs={"source": {"type": "string"}},
                metadata={"a2a_published": False, "preserved": "metadata"},
                root={"id": "root", "kind": "transform", "config": {"expr": 2}},
                expected_revision=starting_revision,
            ),
            return_exceptions=True,
        )

        published = not isinstance(publish, BaseException) and publish["ok"]
        saved = not isinstance(save, BaseException)
        assert published != saved
        if not published:
            assert publish["code"] == "WF_DEF_VERSION_MISMATCH"

        current = await provider.get_def("assistant-publish-race")
        assert current is not None
        assert current.version == starting_revision + 1
        persisted = json.loads(_def_path("assistant-publish-race").read_text())
        assert persisted["metadata"]["preserved"] == "metadata"
        if published:
            assert current.metadata.a2a_published is True
            assert current.description == "Original graph"
            assert current.root.config == {"expr": 1}
        else:
            assert current.metadata.a2a_published is False
            assert current.description == "Concurrent graph edit"
            assert current.root.config == {"expr": 2}
    finally:
        if prior_provider is None:
            workflow_defs.unregister_provider("native")
        else:
            workflow_defs.register_provider(prior_provider)
