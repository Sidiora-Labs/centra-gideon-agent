from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_authenticated_idea_decisions_preserve_revision_and_link_one_native_task(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.engine.tasks import registry
    from gideon.engine.tasks.handlers import register_task_routes
    from gideon.interfaces.dashboard import token_auth
    from gideon.interfaces.dashboard.handlers import (
        assistant_ideas,
        capabilities_knowledge_ideas,
    )
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.workspace.capabilities.knowledge.idea_format import preview, render
    from gideon.workspace.capabilities.knowledge.ideas import IdeaLists

    state = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    store = state.knowledge_store
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
        "ideas": [
            "Build a careful study plan",
            "Keep the source attached",
            "Recover a saved task",
        ],
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
    first_id, second_id, third_id = [item["id"] for item in imported["items"]]
    store.update_item(first_id, content="Build a careful study plan with source notes")

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
        assert (await listed.json()) == {"items": [], "pending": []}

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
        native_create = registry.create_task

        async def delayed_native_create(**fields):
            await asyncio.sleep(0.05)
            return await native_create(**fields)

        monkeypatch.setattr(registry, "create_task", delayed_native_create)
        concurrent = await asyncio.wait_for(
            asyncio.gather(
                client.post(
                    f"/api/assistant/ideas/{first_id}/decision", json=accepted_body
                ),
                client.post(
                    f"/api/assistant/ideas/{first_id}/decision", json=accepted_body
                ),
                client.get("/api/assistant/ideas/decisions"),
            ),
            timeout=5,
        )
        accepted, duplicate, concurrent_list = concurrent
        assert accepted.status in (200, 201)
        assert duplicate.status in (200, 201)
        assert concurrent_list.status == 200
        accepted_record = await accepted.json()
        duplicate_record = await duplicate.json()
        assert duplicate_record["task_id"] == accepted_record["task_id"]
        monkeypatch.setattr(registry, "create_task", native_create)
        assert accepted_record["decision"] == "accepted"
        assert (
            accepted_record["source_evidence"]
            == "Build a careful study plan with source notes"
        )
        task = await registry.get_task(
            accepted_record["task_id"], provider_name="native"
        )
        assert task is not None
        assert task.title == accepted_body["edited_prompt"]
        assert task.evidence == [
            {
                "kind": "knowledge-idea-list",
                "source_id": first_id,
                "source_list_id": imported["id"],
                "source_revision": current["hash"],
                "excerpt": "Build a careful study plan with source notes",
                "edited_prompt": accepted_body["edited_prompt"],
                "request_id": accepted_body["request_id"],
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
        tasks, total = await registry.list_all_tasks(
            provider_filter="native", limit=500
        )
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

        recovery_revision = ideas.get(imported["id"])["hash"]
        recovery_body = {
            **accepted_body,
            "expected_revision": recovery_revision,
            "request_id": "assistant-ideas-recover-001",
            "edited_prompt": "Recover this source-linked task after an interrupted response",
        }
        records = app["assistant_idea_records"]
        with records.transaction() as db:
            records.stage(
                db,
                source_kind="knowledge-idea-list",
                source_id=third_id,
                source_list_id=imported["id"],
                source_revision=recovery_revision,
                source_title="Recover a saved task",
                source_evidence="Recover a saved task",
                decision="accepted",
                edited_prompt=recovery_body["edited_prompt"],
                request_id=recovery_body["request_id"],
            )
        app["assistant_idea_records"] = assistant_ideas.RecommendationRecords(tmp_path)
        staged = await (await client.get("/api/assistant/ideas/decisions")).json()
        assert staged["pending"] == [
            {
                "source_kind": "knowledge-idea-list",
                "source_id": third_id,
                "source_list_id": imported["id"],
                "source_revision": recovery_revision,
                "source_title": "Recover a saved task",
                "source_evidence": "Recover a saved task",
                "decision": "accepted",
                "edited_prompt": recovery_body["edited_prompt"],
                "task_id": "",
                "request_id": recovery_body["request_id"],
                "created_at": staged["pending"][0]["created_at"],
            }
        ]
        store.update_item(
            third_id, content="The saved task source changed before task creation"
        )
        stale_recovery = await client.post(
            f"/api/assistant/ideas/{third_id}/decision", json=recovery_body
        )
        assert stale_recovery.status == 409
        assert (
            "source changed before a native task was created"
            in (await stale_recovery.json())["error"]["message"]
        )
        assert (await (await client.get("/api/assistant/ideas/decisions")).json())[
            "pending"
        ] == []
        recovery_revision = ideas.get(imported["id"])["hash"]
        recovery_body["expected_revision"] = recovery_revision
        with records.transaction() as db:
            records.stage(
                db,
                source_kind="knowledge-idea-list",
                source_id=third_id,
                source_list_id=imported["id"],
                source_revision=recovery_revision,
                source_title="Recover a saved task",
                source_evidence="The saved task source changed before task creation",
                decision="accepted",
                edited_prompt=recovery_body["edited_prompt"],
                request_id=recovery_body["request_id"],
            )
        recovery_db = sqlite_connection(records.path)
        try:
            pending = records.pending(
                recovery_db,
                "knowledge-idea-list",
                imported["id"],
                third_id,
            )
        finally:
            recovery_db.close()
        recovery_task = await native_create(
            provider_name="native",
            title=recovery_body["edited_prompt"],
            description="Interrupted native task creation recovery",
            labels=[assistant_ideas._intent_label(pending)],
            evidence=[assistant_ideas._task_evidence(pending)],
        )
        app["assistant_idea_records"] = assistant_ideas.RecommendationRecords(tmp_path)
        recovered = await client.post(
            f"/api/assistant/ideas/{third_id}/decision", json=recovery_body
        )
        assert recovered.status == 201
        recovered_record = await recovered.json()
        assert recovered_record["task_id"] == recovery_task.id
        tasks, total = await registry.list_all_tasks(
            provider_filter="native", limit=500
        )
        assert total == 2
        assert (
            sum(assistant_ideas._intent_label(pending) in task.labels for task in tasks)
            == 1
        )
        saved = await (await client.get("/api/assistant/ideas/decisions")).json()
        assert {row["decision"] for row in saved["items"]} == {"accepted", "dismissed"}
        assert saved["pending"] == []

    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()
    store.close()


def sqlite_connection(path):
    import sqlite3

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection
