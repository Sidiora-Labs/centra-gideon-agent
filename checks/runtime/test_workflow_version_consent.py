"""Native version questions and protected owner grants retain their shown closure."""

from __future__ import annotations

import pytest

from gideon.automation.triggers import grants
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows import automation_versions as consent
from gideon.automation.workflows import native_defs, store, versions
from gideon.automation.workflows.models import OriginKind, RunOrigin, WorkflowRun
from gideon.security.approval_answer import YOU, agent
from gideon.security.owner_grants import GrantBook


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    return tmp_path / "gideon"


def save(name, root=None, *, owner=False):
    return native_defs.DefinitionDraft(
        {
            "name": name,
            "root": root
            or {"id": "done", "kind": "transform", "config": {"expr": "1"}},
            "provenance": "user" if owner else "chat",
            "_version_source": (
                versions.SOURCE_USER if owner else versions.SOURCE_REFINER
            ),
            "_owner_saved": owner,
        }
    ).save()


def trigger():
    return Trigger(
        id="version-consent",
        name="Version consent",
        kind="manual",
        workflow={"provider": "run-workflow", "config": {"workflow": "root"}},
    )


def test_changed_descendant_rejects_shown_answer_and_fresh_question_is_required(home):
    save("leaf")
    save("root", {"id": "child", "kind": "subworkflow", "config": {"ref": "leaf"}})
    row = trigger()
    shown = grants.question(row)
    assert shown.shown["workflows"]["leaf"]["version"] == 1
    save("leaf")
    assert not grants.grant(
        row, confirmed_revision=shown.revision, principal=YOU, shown=shown.shown
    )
    assert not grants.is_granted(row)
    fresh = grants.question(row)
    assert fresh.shown["workflows"]["leaf"]["version"] == 2
    assert grants.grant(
        row, confirmed_revision=fresh.revision, principal=YOU, shown=fresh.shown
    )
    assert grants.is_granted(row)


def test_real_store_and_grant_book_keep_pins_after_agent_edits(home):
    save("leaf")
    save("root", {"id": "child", "kind": "subworkflow", "config": {"ref": "leaf"}})
    rows = TriggerStore()
    row = trigger()
    rows.upsert(row)
    row = rows.get(row.id).trigger
    question = grants.question(row)
    assert grants.grant(
        row, confirmed_revision=question.revision, principal=YOU, shown=question.shown
    )
    rows.upsert(row)
    reloaded = rows.get(row.id).trigger
    assert grants.is_granted(reloaded)
    assert GrantBook(grants.GRANT_BOOK).holds(
        row.id, consent.grant_content(question.revision, question.shown)
    )
    save("leaf")
    save("root")
    rows.upsert(reloaded)
    assert grants.is_granted(rows.get(row.id).trigger)
    bounds = consent.trigger_bounds(row.id, "root")
    assert consent.pinned_spec("root", bounds)["version"] == 1
    assert consent.selected_spec("leaf", bounds)["version"] == 1
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="root",
            origin=RunOrigin(kind=OriginKind.HOOK),
            extra={consent.BOUNDS_KEY: bounds},
        )
    )
    assert consent.session_bounds(f"workflow:{run.id}:child") == bounds
    save("leaf", owner=True)
    assert consent.selected_spec("leaf", bounds)["version"] == 3
    save("leaf")
    assert consent.selected_spec("leaf", bounds)["version"] == 3
    save("unlisted", owner=True)
    with pytest.raises(consent.VersionConsentError):
        consent.selected_spec("unlisted", bounds)
    versions._version_path("root", 1).unlink()
    consent._spec_path(bounds["workflows"]["root"]["digest"]).unlink()
    with pytest.raises(consent.VersionConsentError):
        consent.pinned_spec("root", bounds)


def test_missing_shown_and_nonowner_cannot_allow_workflow(home):
    save("root")
    row = trigger()
    question = grants.question(row)
    assert not grants.grant(row, confirmed_revision=question.revision, principal=YOU)
    assert not grants.grant(
        row,
        confirmed_revision=question.revision,
        principal=agent("a"),
        shown=question.shown,
    )
    assert not grants.is_granted(row)


def test_missing_automated_ancestor_fails_closed(home):
    run = store.create(
        WorkflowRun(id="", workflow_name="root", origin=RunOrigin(kind=OriginKind.HOOK))
    )
    with pytest.raises(consent.VersionConsentError):
        consent.run_bounds(run.id)
    with pytest.raises(consent.VersionConsentError):
        consent.run_bounds("missing")


def test_owner_save_captures_descendants_and_new_version_question_pins_them(home):
    save("leaf")
    save("root")
    row = trigger()
    question = grants.question(row)
    assert grants.grant(
        row, confirmed_revision=question.revision, principal=YOU, shown=question.shown
    )
    save(
        "root",
        {"id": "child", "kind": "subworkflow", "config": {"ref": "leaf"}},
        owner=True,
    )
    save("leaf")
    bounds = row.capabilities[grants.SEAL_KEY]["workflows"]
    spec, inherited = consent.selection("root", bounds)
    assert spec["version"] == 2
    assert consent.pinned_spec("leaf", inherited)["version"] == 1
    fresh = grants.question(row)
    assert fresh and fresh.shown["workflows"]["leaf"]["version"] == 2
    save("leaf")
    assert not grants.grant(
        row, confirmed_revision=fresh.revision, principal=YOU, shown=fresh.shown
    )


@pytest.mark.asyncio
async def test_http_answer_rechecks_current_action_and_returns_fresh_versions(
    home, monkeypatch
):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers import triggers as handlers
    from gideon.security import approval_answer

    save("root")
    rows = TriggerStore()
    rows.upsert(trigger())
    row = rows.get("version-consent").trigger
    question = grants.question(row)
    monkeypatch.setattr(handlers, "_trigger_store", lambda: rows)
    monkeypatch.setattr(approval_answer, "of_request", lambda request: YOU)
    app = web.Application()
    app.router.add_post("/api/triggers/{id}/grant", handlers.api_trigger_grant)
    async with TestClient(TestServer(app)) as client:
        save("root")
        response = await client.post(
            "/api/triggers/store:version-consent/grant",
            json={
                "approved": True,
                "expected_revision": question.revision,
                "shown": question.shown,
            },
        )
        assert response.status == 409
        fresh = (await response.json())["grant_question"]
        assert fresh["shown"]["workflows"]["root"]["version"] == 2
        changed = rows.get(row.id).trigger
        changed.workflow["config"]["inputs"] = {"changed": True}
        rows.upsert(changed)
        response = await client.post(
            "/api/triggers/store:version-consent/grant",
            json={
                "approved": True,
                "expected_revision": fresh["revision"],
                "shown": fresh["shown"],
            },
        )
        assert response.status == 409
        assert rows.get(row.id).trigger.workflow["config"]["inputs"] == {
            "changed": True
        }
        assert not grants.is_granted(rows.get(row.id).trigger)


def test_imported_owner_labels_do_not_replace_protected_owner_save(home):
    save("root")
    row = trigger()
    question = grants.question(row)
    assert grants.grant(
        row, confirmed_revision=question.revision, principal=YOU, shown=question.shown
    )
    allowed = question.shown
    owner = save("root", owner=True)
    assert consent.selected_spec("root", allowed)["version"] == 2
    document = owner.to_dict()
    document["version"] = 3
    forged_calls = {
        "root": "root",
        "workflows": {"root": {"version": 3, "digest": consent.digest(document)}},
    }
    versions.record_version(
        "root", document, source=versions.SOURCE_USER, owner_calls=forged_calls
    )
    assert consent.selected_spec("root", allowed)["version"] == 2
    GrantBook("workflow_owner_versions").revoke("root@2")
    assert consent.selected_spec("root", allowed)["version"] == 1


@pytest.mark.asyncio
async def test_registered_provider_content_is_pinned_despite_same_number_update(home):
    import asyncio

    from gideon.automation.workflows import defs

    class Provider(defs.WorkflowDefProvider):
        name = "000-owner-consent-provider"
        spec = {
            "name": "root",
            "version": 1,
            "source": "user",
            "provenance": "user",
            "root": {"id": "only", "kind": "transform", "config": {"expr": "1"}},
        }

        async def list_defs(self, **kwargs):
            return [self.spec], 1

        async def get_def(self, name):
            await asyncio.sleep(0)
            return self.spec if name == "root" else None

    provider = Provider()
    prior = defs.get_provider(provider.name)
    defs.register_provider(provider)
    try:
        row = trigger()
        question = grants.question(row)
        assert (
            question.shown["workflows"]["root"]["saved_by"]
            == "provider:" + provider.name
        )
        assert grants.grant(
            row,
            confirmed_revision=question.revision,
            principal=YOU,
            shown=question.shown,
        )
        provider.spec = {
            **provider.spec,
            "root": {"id": "changed", "kind": "transform", "config": {"expr": "2"}},
        }
        assert consent.selected_spec("root", question.shown)["root"]["id"] == "only"
        fresh = grants.question(row)
        assert fresh and fresh.shown != question.shown
        path = consent._spec_path(question.shown["workflows"]["root"]["digest"])
        path.write_text("{}")
        with pytest.raises((consent.VersionConsentError, ValueError, KeyError)):
            consent.pinned_spec("root", question.shown)
    finally:
        defs.unregister_provider(provider.name)
        if prior:
            defs.register_provider(prior)


def test_bundled_definition_allow_uses_actual_provider_and_persisted_snapshot(home):
    from gideon.automation.workflows import bundled_defs, defs

    provider = bundled_defs.BundledWorkflowDefProvider()
    prior = defs.get_provider(provider.name)
    defs.register_provider(provider)
    try:
        names = bundled_defs.template_names()
        name = next(
            name for name in names if bundled_defs.read_template(name) is not None
        )
        row = trigger()
        row.workflow["config"]["workflow"] = name
        question = grants.question(row)
        assert question is not None
        entry = question.shown["workflows"][name]
        assert entry["provider"] == "bundled" and entry["saved_by"] == "shipped"
        assert grants.grant(
            row,
            confirmed_revision=question.revision,
            principal=YOU,
            shown=question.shown,
        )
        assert consent.pinned_spec(name, question.shown)["name"] == name
        assert consent._spec_path(entry["digest"]).is_file()
    finally:
        defs.unregister_provider(provider.name)
        if prior:
            defs.register_provider(prior)


def test_unavailable_and_dynamic_children_gain_no_permission_but_root_can_run(home):
    save(
        "root",
        {
            "id": "steps",
            "kind": "sequence",
            "children": [
                {"id": "missing", "kind": "subworkflow", "config": {"ref": "missing"}},
                {
                    "id": "dynamic",
                    "kind": "subworkflow",
                    "config": {"ref": "{{ inputs.name }}"},
                },
            ],
        },
    )
    row = trigger()
    question = grants.question(row)
    assert set(question.shown["workflows"]) == {"root"}
    assert grants.grant(
        row, confirmed_revision=question.revision, principal=YOU, shown=question.shown
    )
    assert consent.pinned_spec("root", question.shown)["name"] == "root"
    with pytest.raises(consent.VersionConsentError):
        consent.selected_spec("missing", question.shown)


@pytest.mark.asyncio
async def test_native_schedule_and_lifecycle_owner_routes_and_actual_hook_dispatch(
    home, monkeypatch
):
    import json
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.automation.workflows import defs
    from gideon.automation.workflows.models import RunStatus
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.engine.hooks import ScriptHookStore, set_global_hook_store
    from gideon.integrations.action_providers import registry
    from gideon.integrations.action_providers.run_workflow_provider import (
        RunWorkflowActionProvider,
    )
    from gideon.interfaces.dashboard.handlers import triggers as handlers
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        token_auth_middleware,
    )
    from gideon.security.guardrails.rungs import ROUTE_EXECUTE, RungRoute

    native = native_defs.NativeWorkflowDefProvider()
    await native.save_def(
        name="root",
        root={"id": "done", "kind": "transform", "config": {"expr": "1"}},
        on_overlap="queue",
        provenance="chat",
        _version_source=versions.SOURCE_REFINER,
    )
    prior_provider = defs.get_provider("native")
    defs.register_provider(native)
    monkeypatch.setitem(
        registry._providers, "run-workflow", RunWorkflowActionProvider()
    )
    rows = TriggerStore()
    scheduled = trigger()
    scheduled.kind = "clock"
    scheduled.spec = {"kind": "interval", "interval_secs": 60}
    rows.upsert(scheduled)
    hooks = ScriptHookStore(rows.base_dir)
    hook = hooks.create(
        {
            "id": "workflow-hook",
            "event": "UserPromptSubmit",
            "provider": "run-workflow",
            "provider_config": {"workflow": "root"},
        }
    )
    set_global_hook_store(hooks)
    monkeypatch.setattr(handlers, "_trigger_store", lambda: rows)
    monkeypatch.setattr(handlers, "_hook_store", lambda state: hooks)
    monkeypatch.setattr(handlers, "_used_by_index", lambda: {})
    monkeypatch.setattr(
        "gideon.integrations.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=WorkflowWatchdog()),
    )
    monkeypatch.setattr(
        "gideon.security.guardrails.rungs.route_provider_action",
        lambda *args, **kwargs: RungRoute(route=ROUTE_EXECUTE, reason="owner policy"),
    )
    monkeypatch.setenv("GIDEON_AUTH_MODE", "local-token")
    app = web.Application(middlewares=[token_auth_middleware(port=0)])
    app["state"] = None
    app.router.add_post("/api/triggers/{id}/grant", handlers.api_trigger_grant)
    owner = generate_token("consent-owner", kind="browser")
    app_token = generate_token("consent-app", app="consent-app")
    try:
        denied = await hooks.fire_for_ids("UserPromptSubmit", {hook.id})
        assert denied and "owner must allow" in denied[0].error
        async with TestClient(TestServer(app)) as client:
            for trigger_id, candidate in [
                ("schedule:" + scheduled.id, rows.get(scheduled.id).trigger),
                ("lifecycle:" + hook.id, grants.hook_trigger(hooks.get(hook.id))),
            ]:
                question = grants.question(candidate)
                payload = {
                    "approved": True,
                    "expected_revision": question.revision,
                    "shown": question.shown,
                }
                denied = await client.post(
                    "/api/triggers/" + trigger_id + "/grant",
                    json=payload,
                    headers={"Authorization": "Bearer " + app_token},
                )
                assert denied.status == 403
                response = await client.post(
                    "/api/triggers/" + trigger_id + "/grant",
                    json=payload,
                    headers={"Authorization": "Bearer " + owner},
                )
                assert response.status == 200, await response.text()
                wire = (await response.json())["trigger"]
                assert wire["grant_satisfied"] is True
        reloaded = ScriptHookStore(rows.base_dir)
        assert grants.is_granted(grants.hook_trigger(reloaded.get(hook.id)))
        active = store.create(
            WorkflowRun(id="", workflow_name="root", status=RunStatus.RUNNING)
        )
        store.write_spec(active.id, native_defs._read("root").to_dict())
        results = await hooks.fire_for_ids(
            "UserPromptSubmit", {hook.id}, context="this is free-form text"
        )
        assert results and not results[0].error, (
            results[0].error if results else "no result"
        )
        child = store.get(json.loads(results[0].stdout)["run_id"])
        assert child.extra[consent.BOUNDS_KEY]["root"] == "root"
        assert store.read_spec(child.id)["version"] == 1
        hooks.update(
            hook.id,
            {"provider_config": {"workflow": "root", "inputs": {"changed": True}}},
        )
        assert not grants.is_granted(grants.hook_trigger(hooks.get(hook.id)))
    finally:
        set_global_hook_store(None)
        defs.unregister_provider("native")
        if prior_provider:
            defs.register_provider(prior_provider)


@pytest.mark.asyncio
async def test_authenticated_owner_save_and_agent_save_keep_distinct_authority(
    home, monkeypatch
):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.automation.workflows import defs, handlers
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        token_auth_middleware,
    )
    from gideon.security import session_credentials
    from gideon.security.approval_answer import agent

    monkeypatch.setenv("GIDEON_AUTH_MODE", "local-token")
    native = native_defs.NativeWorkflowDefProvider()
    prior = defs.get_provider("native")
    defs.register_provider(native)
    app = web.Application(
        middlewares=[
            token_auth_middleware(
                port=0,
                internal_secret="native-test-secret",
                mixed_internal_routes=frozenset({"POST /api/workflows"}),
            )
        ]
    )
    app["local_secret"] = "native-test-secret"
    app.router.add_post("/api/workflows", handlers.api_def_save)
    owner_token = generate_token("consent-owner", kind="browser")
    credential = session_credentials.begin_turn(
        "subagent:consent-agent",
        agent("consent-agent"),
        turn_id="save",
        memory_mode="temporary",
    )
    root = {"id": "done", "kind": "transform", "config": {"expr": "1"}}
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/api/workflows",
                json={
                    "name": "owner-save",
                    "root": root,
                    "strict": False,
                    "create_only": True,
                },
                headers={"Authorization": "Bearer " + owner_token},
            )
            assert response.status == 201, await response.text()
            owner_record = versions.get_version("owner-save", 1)
            assert consent.owner_saved("owner-save", owner_record)
            response = await client.post(
                "/api/workflows",
                json={
                    "name": "agent-save",
                    "root": root,
                    "strict": False,
                    "create_only": True,
                    "provenance": "user",
                },
                headers={
                    "X-Internal-Secret": "native-test-secret",
                    "X-Session-Proof": credential.bearer,
                    "X-Session-Key": credential.work.session_key,
                },
            )
            assert response.status == 201, await response.text()
            agent_record = versions.get_version("agent-save", 1)
            assert agent_record.source == versions.SOURCE_REFINER
            assert not consent.owner_saved("agent-save", agent_record)
        imported = native_defs.DefinitionDraft(
            {
                "name": "import-save",
                "root": root,
                "provenance": "user",
                "_version_source": versions.SOURCE_USER,
            }
        ).save()
        assert not consent.owner_saved(
            "import-save", versions.get_version("import-save", imported.version)
        )
    finally:
        session_credentials.end_turn(credential)
        defs.unregister_provider("native")
        if prior:
            defs.register_provider(prior)


@pytest.mark.asyncio
async def test_verified_run_actor_carries_pins_and_distinct_initiator_before_native_launch(
    home,
):
    from aiohttp.test_utils import make_mocked_request

    from gideon.automation.workflows import defs, handlers, service
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.security import session_credentials
    from gideon.security.approval_answer import principal_record
    from gideon.security.approval_answer import run as run_actor

    native = native_defs.NativeWorkflowDefProvider()
    prior = defs.get_provider("native")
    defs.register_provider(native)
    save("root")
    row = trigger()
    question = grants.question(row)
    assert grants.grant(
        row, confirmed_revision=question.revision, principal=YOU, shown=question.shown
    )
    ancestor = store.create(
        WorkflowRun(
            id="",
            workflow_name="root",
            origin=RunOrigin(kind=OriginKind.HOOK),
            extra={consent.BOUNDS_KEY: question.shown},
        )
    )
    save("root")
    actor = run_actor(ancestor.id)
    proof = session_credentials.begin_turn(
        "subagent:run-worker",
        YOU,
        turn_id="child",
        memory_mode="persistent",
        work_actor=actor,
    )
    request = make_mocked_request(
        "POST",
        "/api/workflows/runs",
        headers={
            "X-Session-Key": proof.work.session_key,
            "X-Session-Proof": proof.bearer,
        },
    )
    request["_session_work_proof"] = proof.work
    supervisor = WorkflowWatchdog()
    try:
        context = handlers._work_context(request)
        assert context["work_principal"] == actor and context["work_initiator"] == YOU
        result = await service.start_run(
            name="root", supervisor=supervisor, skip_preflight=True, **context
        )
        assert result["ok"], result
        child = store.get(result["run_id"])
        assert store.read_spec(child.id)["version"] == 1
        assert child.parent_run_id == ancestor.id
        assert child.extra["work_principal"] == principal_record(actor)
        assert child.extra["work_initiator"] == principal_record(YOU)
        assert child.extra[consent.BOUNDS_KEY] == question.shown
        controller = supervisor.controller(child.id)
        await controller.wait_for_terminal(timeout=3)
        denied = await service.start_draft(
            child.id, supervisor=supervisor, work_principal=actor
        )
        assert denied["code"] == "WF_RUN_NOT_ALLOWED"
    finally:
        session_credentials.end_turn(proof)
        defs.unregister_provider("native")
        if prior:
            defs.register_provider(prior)


def test_owner_save_of_root_does_not_approve_an_agent_edit_of_existing_child(home):
    save("leaf")
    root = {"id": "child", "kind": "subworkflow", "config": {"ref": "leaf"}}
    save("root", root)
    row = trigger()
    question = grants.question(row)
    assert grants.grant(
        row, confirmed_revision=question.revision, principal=YOU, shown=question.shown
    )
    save("leaf")
    save("root", root, owner=True)
    spec, carried = consent.selection("root", question.shown)
    assert spec["version"] == 2
    assert consent.selected_spec("leaf", carried)["version"] == 1
    save("leaf", owner=True)
    assert consent.selected_spec("leaf", carried)["version"] == 3
