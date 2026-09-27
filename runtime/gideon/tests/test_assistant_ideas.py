from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.engine.tasks.handlers import register_task_routes
from gideon.engine.tasks import registry
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import assistant_ideas
from gideon.interfaces.dashboard.handlers import capabilities_knowledge_ideas
from gideon.workspace.capabilities.knowledge.idea_format import preview, render
from gideon.workspace.capabilities.knowledge.ideas import IdeaLists


@pytest.mark.asyncio
async def test_authenticated_idea_decisions_preserve_revision_and_link_one_native_task(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    ideas = IdeaLists(store, tmp_path)
    document = {
        "id": "65a64b38-5ef1-4a74-a22d-4f6b18ef5e12",
        "title": "Personal next steps",
        "category": "personal",
        "status": "draft",
        "created": "2026-09-27T10:00:00Z",
        "modified": "2026-09-27T10:00:00Z",
        "tags": ["idea-loom"],
        "prompt": "Choose one evidence-backed next step.",
        "help": "Use the original source when planning the task.",
        "ideas": ["Build a careful study plan", "Keep the source attached"],
    }
    content = render(document)
    imported = ideas.import_list(
        {
            "request_id": "assistant-ideas-seed",
            "content": content,
            "preview_id": preview(content)["preview_id"],
            "expected_hash": "",
        }
    )
    original_hash = imported["hash"]
    first_id, second_id = [item["id"] for item in imported["items"]]
    store.update_item(first_id, content="Build a careful study plan with source notes")

    state = SimpleNamespace(_restricted_keys=set(), _sessions={}, knowledge_store=store)
    port = 18473
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=port)])
    app["port"] = port
    app["allowed_origins"] = {f"http://127.0.0.1:{port}"}
    app["state"] = state
    capabilities_knowledge_ideas.register(app)
    assistant_ideas.register_idea_decision_routes(app)
    register_task_routes(app)

    token_auth.use_ephemeral_secret()
    token_auth.revoke_all_sessions()
    token = token_auth.generate_token("idea-owner", ttl_seconds=3600)
    async with TestClient(TestServer(app)) as client:
        unauthenticated = await client.get("/api/assistant/ideas/decisions")
        assert unauthenticated.status == 403
        client.session.cookie_jar.update_cookies(
            {f"gideon_token_{port}": token}, response_url=client.make_url("/")
        )
        listed = await client.get("/api/assistant/ideas/decisions")
        assert listed.status == 200
        assert (await listed.json()) == {"items": []}

        stale_body = {
            "source_kind": "knowledge-idea-list",
            "source_list_id": imported["id"],
            "expected_revision": original_hash,
            "decision": "accept",
            "edited_prompt": "Make the source-linked study plan",
            "request_id": "assistant-ideas-stale",
        }
        stale = await client.post(
            f"/api/assistant/ideas/{first_id}/decision", json=stale_body
        )
        assert stale.status == 409
        assert "source changed" in (await stale.json())["error"]["message"]
        untouched_tasks, untouched_total = await registry.list_all_tasks(
            provider_filter="native", limit=500
        )
        assert untouched_total == 0
        assert untouched_tasks == []

        current = ideas.get(imported["id"])
        accepted_body = {
            **stale_body,
            "expected_revision": current["hash"],
            "edited_prompt": "Make the source-linked study plan",
            "request_id": "assistant-ideas-accept-001",
        }
        accepted = await client.post(
            f"/api/assistant/ideas/{first_id}/decision", json=accepted_body
        )
        assert accepted.status == 201
        accepted_record = await accepted.json()
        assert accepted_record["decision"] == "accepted"
        assert accepted_record["source_evidence"] == "Build a careful study plan with source notes"
        task = await registry.get_task(accepted_record["task_id"], provider_name="native")
        assert task is not None
        assert task.title == accepted_body["edited_prompt"]
        assert task.evidence == [
            {
                "kind": "knowledge-idea-list",
                "source_id": first_id,
                "source_list_id": imported["id"],
                "source_revision": current["hash"],
                "excerpt": "Build a careful study plan with source notes",
            }
        ]

        retried = await client.post(
            f"/api/assistant/ideas/{first_id}/decision", json=accepted_body
        )
        assert retried.status == 200
        assert (await retried.json())["task_id"] == accepted_record["task_id"]
        for changed_request in (
            {**accepted_body, "expected_revision": "different-revision"},
            {**accepted_body, "source_list_id": "different-list"},
        ):
            conflict = await client.post(
                f"/api/assistant/ideas/{first_id}/decision", json=changed_request
            )
            assert conflict.status == 409
        tasks, total = await registry.list_all_tasks(provider_filter="native", limit=500)
        assert total == 1
        assert [item.id for item in tasks] == [accepted_record["task_id"]]

        typed = await client.post(
            f"/api/assistant/ideas/{second_id}/decision",
            json={
                **accepted_body,
                "source_kind": "learning-proposal",
                "decision": "dismiss",
                "edited_prompt": "",
                "request_id": "assistant-ideas-typed-refused",
            },
        )
        assert typed.status == 400

        dismissed_body = {
            **accepted_body,
            "decision": "dismiss",
            "edited_prompt": "",
            "request_id": "assistant-ideas-dismiss-001",
        }
        dismissed = await client.post(
            f"/api/assistant/ideas/{second_id}/decision", json=dismissed_body
        )
        assert dismissed.status == 201
        assert (await dismissed.json())["decision"] == "dismissed"
        saved = await (await client.get("/api/assistant/ideas/decisions")).json()
        assert {row["decision"] for row in saved["items"]} == {"accepted", "dismissed"}

    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()
    store.close()
