import base64
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_integration_apps import (
    PREFIX,
    register,
)
from gideon.sdk.credentials import CredentialStore
from gideon.workspace.capabilities.music.store import DomainError
from gideon.workspace.capabilities.platform.integration_apps.client import headers, seal
from gideon.workspace.capabilities.platform.integration_apps.contracts import (
    MUTATIONS,
    OPERATIONS,
    configuration,
    plan,
)
from gideon.workspace.capabilities.platform.integration_apps.store import (
    IntegrationApps,
    jira_report,
)
from gideon.workspace.capabilities.platform.integration_apps.tools import (
    IntegrationAppTools,
)


def connection(kind="jira", **changes):
    values = {
        "kind": kind,
        "label": kind.title(),
        "endpoint": {
            "jira": "https://team.atlassian.net",
            "datadog": "https://api.datadoghq.eu",
            "github": "https://api.github.com",
        }[kind],
        "credential_name": kind + "-primary",
        "aux_credential_name": "datadog-application" if kind == "datadog" else "",
        "username": "owner@example.com" if kind == "jira" else "",
    }
    values.update(changes)
    return values


@pytest.fixture
def store(tmp_path):
    return IntegrationApps(tmp_path, credential_resolver=lambda name: None)


def test_connection_contract_accepts_documented_origins_and_named_credentials():
    jira = configuration(connection("jira"))
    datadog = configuration(connection("datadog"))
    github = configuration(connection("github"))
    assert jira["endpoint"] == "https://team.atlassian.net"
    assert jira["username"] == "owner@example.com"
    assert jira["credential_name"] == "jira-primary"
    assert datadog["endpoint"] == "https://api.datadoghq.eu"
    assert datadog["aux_credential_name"] == "datadog-application"
    assert github["endpoint"] == "https://api.github.com"
    assert github["credential_name"] == "github-primary"
    assert "secret" not in json.dumps(jira).lower()
    assert "secret" not in json.dumps(datadog).lower()
    assert "secret" not in json.dumps(github).lower()


@pytest.mark.parametrize(
    "data",
    [
        connection("jira", endpoint="http://team.atlassian.net"),
        connection("jira", endpoint="https://atlassian.net"),
        connection("jira", endpoint="https://team.atlassian.net/path"),
        connection("jira", endpoint="https://user:pass@team.atlassian.net"),
        connection("jira", username=""),
        connection("datadog", endpoint="https://example.com"),
        connection("datadog", aux_credential_name=""),
        connection("github", endpoint="https://github.com"),
        connection("github", credential_name=""),
    ],
)
def test_connection_contract_rejects_undocumented_or_inline_authority(data):
    with pytest.raises(DomainError):
        configuration(data)


def test_jira_read_plans_match_documented_cloud_routes():
    auth = plan("jira", "jira_auth", {})
    projects = plan("jira", "jira_projects", {"page": 2})
    boards = plan("jira", "jira_boards", {"project": "OPS", "page": 1})
    sprints = plan("jira", "jira_sprints", {"board_id": 42})
    search = plan("jira", "jira_search", {"jql": "project = OPS", "cursor": "next"})
    issue = plan("jira", "jira_issue", {"issue": "OPS-4"})
    transitions = plan("jira", "jira_transitions", {"issue": "OPS-4"})
    assert auth == {
        "operation": "jira_auth",
        "method": "GET",
        "path": "/rest/api/3/myself",
        "params": {},
        "body": None,
        "mutates": False,
    }
    assert projects["path"] == "/rest/api/3/project/search"
    assert projects["params"] == {"startAt": 100, "maxResults": 50}
    assert boards["path"] == "/rest/agile/1.0/board"
    assert boards["params"]["projectKeyOrId"] == "OPS"
    assert sprints["path"] == "/rest/agile/1.0/board/42/sprint"
    assert search["path"] == "/rest/api/3/search/jql"
    assert search["params"]["nextPageToken"] == "next"
    assert "summary,status,assignee" in search["params"]["fields"]
    assert issue["path"] == "/rest/api/3/issue/OPS-4"
    assert transitions["path"] == "/rest/api/3/issue/OPS-4/transitions"
    assert all(
        not row["mutates"]
        for row in (auth, projects, boards, sprints, search, issue, transitions)
    )


def test_jira_write_plans_are_exact_and_marked_mutating():
    create = plan(
        "jira",
        "jira_create",
        {
            "project": "OPS",
            "issue_type": "10001",
            "summary": "Release",
            "description": "Ship safely",
        },
    )
    update = plan(
        "jira",
        "jira_update",
        {
            "issue": "OPS-4",
            "summary": "Release now",
            "description": "Reviewed",
            "labels": ["release", "approved"],
        },
    )
    comment = plan(
        "jira", "jira_comment", {"issue": "OPS-4", "comment": "Owner reviewed"}
    )
    transition = plan(
        "jira", "jira_transition", {"issue": "OPS-4", "transition_id": "31"}
    )
    delete = plan("jira", "jira_delete", {"issue": "OPS-4"})
    sprint = plan(
        "jira", "jira_sprint_assign", {"sprint_id": 9, "issues": ["OPS-4", "OPS-5"]}
    )
    assert create["method"] == "POST"
    assert create["path"] == "/rest/api/3/issue"
    assert create["body"]["fields"]["project"] == {"key": "OPS"}
    assert create["body"]["fields"]["issuetype"] == {"id": "10001"}
    assert create["body"]["fields"]["description"]["type"] == "doc"
    assert update["method"] == "PUT"
    assert update["body"]["fields"]["labels"] == ["release", "approved"]
    assert comment["path"] == "/rest/api/3/issue/OPS-4/comment"
    assert comment["body"]["body"]["version"] == 1
    assert transition["body"] == {"transition": {"id": "31"}}
    assert delete["method"] == "DELETE"
    assert sprint["path"] == "/rest/agile/1.0/sprint/9/issue"
    assert sprint["body"] == {"issues": ["OPS-4", "OPS-5"]}
    assert all(
        row["mutates"] for row in (create, update, comment, transition, delete, sprint)
    )


def test_datadog_log_search_is_bounded_and_literal_safe():
    request = plan(
        "datadog",
        "datadog_errors",
        {
            "service": "gateway",
            "environment": "production",
            "from": "2026-09-01T00:00:00Z",
            "to": "2026-09-02T00:00:00Z",
            "cursor": "page-2",
        },
    )
    assert request["method"] == "POST"
    assert request["path"] == "/api/v2/logs/events/search"
    assert (
        request["body"]["filter"]["query"]
        == 'status:error service:"gateway" env:"production"'
    )
    assert request["body"]["filter"]["from"] == "2026-09-01T00:00:00Z"
    assert request["body"]["filter"]["to"] == "2026-09-02T00:00:00Z"
    assert request["body"]["page"] == {"limit": 100, "cursor": "page-2"}
    assert request["mutates"] is False
    for service in ('bad"value', "bad\\value", "bad\nvalue"):
        with pytest.raises(DomainError):
            plan(
                "datadog",
                "datadog_errors",
                {
                    "service": service,
                    "environment": "prod",
                    "from": "2026-09-01T00:00:00Z",
                    "to": "2026-09-02T00:00:00Z",
                },
            )
    with pytest.raises(DomainError):
        plan(
            "datadog",
            "datadog_errors",
            {
                "service": "api",
                "environment": "prod",
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-09-02T00:00:00Z",
            },
        )


def test_github_repository_and_actions_secret_plans():
    repositories = plan("github", "github_repos", {"page": 3})
    secrets = plan(
        "github", "github_secrets", {"repository": "Sidiora-Labs/centra", "page": 1}
    )
    archive = plan(
        "github",
        "github_archive",
        {"repository": "Sidiora-Labs/centra", "archived": True},
    )
    sync = plan(
        "github",
        "github_secret_sync",
        {
            "repository": "Sidiora-Labs/centra",
            "name": "DEPLOY_TOKEN",
            "credential_ref": "production-deploy",
        },
    )
    assert repositories["path"] == "/user/repos"
    assert repositories["params"] == {"per_page": 100, "page": 4, "sort": "full_name"}
    assert secrets["path"] == "/repos/Sidiora-Labs/centra/actions/secrets"
    assert secrets["params"] == {"per_page": 100, "page": 2}
    assert archive["method"] == "PATCH"
    assert archive["body"] == {"archived": True}
    assert archive["mutates"] is True
    assert sync["method"] == "PUT"
    assert sync["path"].endswith("/actions/secrets/DEPLOY_TOKEN")
    assert sync["body"] == {"credential_ref": "production-deploy"}
    assert sync["mutates"] is True
    assert "production-deploy" not in json.dumps(
        headers(connection("github"), lambda name: "token")
    )
    with pytest.raises(DomainError):
        plan(
            "github",
            "github_secret_sync",
            {
                "repository": "Sidiora-Labs/centra",
                "name": "GITHUB_TOKEN",
                "credential_ref": "reserved",
            },
        )
    with pytest.raises(DomainError):
        plan("github", "github_archive", {"repository": "../escape", "archived": True})


def test_every_declared_operation_belongs_to_one_provider_and_mutation_set_is_complete():
    assert set(OPERATIONS.values()) == {"jira", "datadog", "github"}
    assert MUTATIONS <= set(OPERATIONS)
    assert OPERATIONS["jira_delete"] == "jira"
    assert OPERATIONS["jira_sprint_assign"] == "jira"
    assert OPERATIONS["datadog_errors"] == "datadog"
    assert OPERATIONS["github_secret_sync"] == "github"
    for operation, provider in OPERATIONS.items():
        wrong = next(
            value for value in ("jira", "datadog", "github") if value != provider
        )
        with pytest.raises(DomainError):
            plan(wrong, operation, {})


def test_named_credential_headers_use_real_store_and_never_enter_persistence(tmp_path):
    credentials = CredentialStore(tmp_path)
    credentials.save(
        {
            "jira-primary": {"type": "static_token", "value": "jira-private-token"},
            "datadog-primary": {"type": "api_key", "value": "datadog-private-api"},
            "datadog-application": {"type": "api_key", "value": "datadog-private-app"},
            "github-primary": {"type": "static_token", "value": "github-private-token"},
        }
    )

    def resolve(name):
        return credentials.resolve(name).secret

    jira = headers(connection("jira"), resolve)
    datadog = headers(connection("datadog"), resolve)
    github = headers(connection("github"), resolve)
    assert (
        base64.b64decode(jira["Authorization"].removeprefix("Basic ")).decode()
        == "owner@example.com:jira-private-token"
    )
    assert datadog["DD-API-KEY"] == "datadog-private-api"
    assert datadog["DD-APPLICATION-KEY"] == "datadog-private-app"
    assert github["Authorization"] == "Bearer github-private-token"
    assert github["X-GitHub-Api-Version"] == "2026-03-10"
    app = IntegrationApps(tmp_path)
    saved = app.save_connection(connection("jira"))
    persisted = app.path.read_bytes()
    assert saved["credential_name"] == "jira-primary"
    assert b"jira-private-token" not in persisted
    assert b"github-private-token" not in persisted
    assert b"datadog-private-api" not in persisted


def test_missing_named_credential_fails_before_network(store):
    saved = store.save_connection(connection("github"))
    run = store.prepare(
        saved["id"],
        {
            "request_id": "missing-credential",
            "operation": "github_archive",
            "input": {"repository": "Sidiora-Labs/centra", "archived": True},
        },
    )
    result = __import__("asyncio").run(
        store.execute(run["id"], {"revision": run["revision"], "confirm": True})
    )
    assert result["status"] == "failed"
    assert result["error"] == "Named credential unavailable"
    assert result["result"] is None
    assert result["revision"] == 3
    assert store.get(run["id"]) == result


def test_store_persists_connections_review_plans_and_idempotency(store):
    jira = store.save_connection(connection("jira"))
    assert jira["revision"] == 1
    assert store.connection(jira["id"]) == jira
    assert store.connections() == [jira]
    request = {
        "request_id": "review-1",
        "operation": "jira_create",
        "input": {
            "project": "OPS",
            "issue_type": "10001",
            "summary": "Release",
            "description": "Ship",
        },
    }
    first = store.prepare(jira["id"], request)
    replay = store.prepare(jira["id"], request)
    assert replay == first
    assert first["status"] == "prepared"
    assert first["revision"] == 1
    assert first["request"]["mutates"] is True
    assert first["request"]["method"] == "POST"
    assert first["result"] is None
    assert first["error"] is None
    assert store.get(first["id"]) == first
    assert store.runs() == [first]
    with pytest.raises(DomainError) as duplicate:
        store.prepare(
            jira["id"],
            {**request, "input": {**request["input"], "summary": "Different"}},
        )
    assert duplicate.value.status == 409
    reopened = IntegrationApps(store.home, credential_resolver=lambda name: None)
    assert reopened.connection(jira["id"]) == jira
    assert reopened.get(first["id"]) == first


def test_store_requires_exact_owner_confirmation_and_connection_revision(store):
    jira = store.save_connection(connection("jira"))
    run = store.prepare(
        jira["id"],
        {
            "request_id": "review-2",
            "operation": "jira_delete",
            "input": {"issue": "OPS-8"},
        },
    )
    with pytest.raises(DomainError) as unconfirmed:
        __import__("asyncio").run(
            store.execute(run["id"], {"revision": 1, "confirm": False})
        )
    assert unconfirmed.value.status == 409
    with pytest.raises(DomainError):
        __import__("asyncio").run(
            store.execute(run["id"], {"revision": 2, "confirm": True})
        )
    assert store.get(run["id"])["status"] == "prepared"
    updated = store.save_connection(
        {**connection("jira", label="Renamed"), "revision": 1}, jira["id"]
    )
    assert updated["revision"] == 2
    with pytest.raises(DomainError) as changed:
        __import__("asyncio").run(
            store.execute(run["id"], {"revision": 1, "confirm": True})
        )
    assert changed.value.status == 409
    assert "changed after review" in str(changed.value)
    assert store.get(run["id"])["status"] == "prepared"


def test_repository_inventory_retains_local_annotations_across_remote_refresh(store):
    github = store.save_connection(connection("github"))
    store.ingest_repos(
        github["id"],
        [{"full_name": "Sidiora-Labs/centra", "archived": False, "private": True}],
    )
    rows = store.repositories(github["id"])
    assert len(rows) == 1
    assert rows[0]["name"] == "Sidiora-Labs/centra"
    assert rows[0]["remote"]["private"] is True
    assert rows[0]["flags"] == {}
    assert rows[0]["secret_refs"] == {}
    annotated = store.annotate(
        github["id"],
        rows[0]["name"],
        {
            "revision": 1,
            "flags": {"managed": True, "deploy": False},
            "secret_refs": {"DEPLOY_TOKEN": "production-deploy"},
        },
    )
    assert annotated["revision"] == 2
    assert annotated["flags"] == {"managed": True, "deploy": False}
    assert annotated["secret_refs"] == {"DEPLOY_TOKEN": "production-deploy"}
    store.ingest_repos(
        github["id"],
        [{"full_name": "Sidiora-Labs/centra", "archived": True, "private": True}],
    )
    refreshed = store.repositories(github["id"])[0]
    assert refreshed["revision"] == 3
    assert refreshed["remote"]["archived"] is True
    assert refreshed["flags"] == annotated["flags"]
    assert refreshed["secret_refs"] == annotated["secret_refs"]
    assert b"production-deploy" in store.path.read_bytes()
    assert b"private-token" not in store.path.read_bytes()
    with pytest.raises(DomainError):
        store.annotate(
            github["id"],
            refreshed["name"],
            {"revision": 2, "flags": {}, "secret_refs": {}},
        )


def test_jira_report_is_explicitly_partial_and_counts_observed_rows():
    result = jira_report(
        {
            "issues": [
                {
                    "fields": {
                        "status": {"name": "Open"},
                        "assignee": {"displayName": "Ada"},
                    }
                },
                {"fields": {"status": {"name": "Done"}, "assignee": None}},
                {"fields": {"status": None, "assignee": {"displayName": "Ada"}}},
            ],
            "nextPageToken": "more",
        }
    )
    assert result["observed_issues"] == 3
    assert result["by_status"] == {"Open": 1, "Done": 1, "Unknown": 1}
    assert result["by_assignee"] == {"Ada": 2, "Unassigned": 1}
    assert result["partial"] is True
    with pytest.raises(DomainError):
        jira_report({"issues": "not-a-list"})
    with pytest.raises(DomainError):
        jira_report({"issues": [{}]})


def test_native_tools_require_canonical_approval_for_execution(store):
    github = store.save_connection(connection("github"))
    tools = IntegrationAppTools(store)
    definitions = {
        tool.name: tool for tool in __import__("asyncio").run(tools.list_tools())
    }
    assert definitions["integration_apps_overview"].requires_approval is False
    assert definitions["integration_apps_prepare"].requires_approval is False
    assert definitions["integration_apps_execute"].requires_approval is True
    assert definitions["integration_apps_execute"].risk_level.value == "caution"
    prepared = __import__("asyncio").run(
        tools.invoke(
            "integration_apps_prepare",
            {
                "connection_id": github["id"],
                "request_id": "native-1",
                "operation": "github_archive",
                "input": {"repository": "Sidiora-Labs/centra", "archived": True},
            },
        )
    )
    assert prepared.success is True
    receipt = json.loads(prepared.output)
    assert receipt["status"] == "prepared"
    assert receipt["request"]["mutates"] is True
    assert receipt["request"]["path"] == "/repos/Sidiora-Labs/centra"
    executed = __import__("asyncio").run(
        tools.invoke(
            "integration_apps_execute",
            {"run_id": receipt["id"], "revision": receipt["revision"]},
        )
    )
    assert executed.success is True
    failed = json.loads(executed.output)
    assert failed["status"] == "failed"
    assert failed["error"] == "Named credential unavailable"
    overview = __import__("asyncio").run(tools.invoke("integration_apps_overview", {}))
    assert overview.success is True
    assert len(json.loads(overview.output)["runs"]) == 1
    invalid = __import__("asyncio").run(
        tools.invoke("integration_apps_execute", {"run_id": receipt["id"]})
    )
    assert invalid.success is False
    assert invalid.metadata["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_owner_http_review_flow_persists_without_external_mutation(tmp_path):
    store = IntegrationApps(tmp_path, credential_resolver=lambda name: None)

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app["integration_apps_factory"] = lambda: store
    register(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(PREFIX + "/connections", json=connection("jira"))
        assert response.status == 201
        jira = await response.json()
        assert jira["kind"] == "jira"
        assert jira["revision"] == 1
        overview = await client.get(PREFIX)
        assert overview.status == 200
        assert overview.headers["Cache-Control"] == "no-store"
        assert (await overview.json())["connections"] == [jira]
        prepared_response = await client.post(
            PREFIX + f"/connections/{jira['id']}/prepare",
            json={
                "request_id": "http-1",
                "operation": "jira_comment",
                "input": {"issue": "OPS-4", "comment": "Reviewed by owner"},
            },
        )
        assert prepared_response.status == 201
        prepared = await prepared_response.json()
        assert prepared["status"] == "prepared"
        assert prepared["request"]["path"] == "/rest/api/3/issue/OPS-4/comment"
        assert prepared["request"]["mutates"] is True
        loaded = await client.get(PREFIX + f"/runs/{prepared['id']}")
        assert loaded.status == 200
        assert await loaded.json() == prepared
        rejected = await client.post(
            PREFIX + f"/runs/{prepared['id']}/execute",
            json={"revision": 1, "confirm": False},
        )
        assert rejected.status == 409
        assert (await rejected.json())[
            "error"
        ] == "Explicit reviewed execution confirmation required"
        executed = await client.post(
            PREFIX + f"/runs/{prepared['id']}/execute",
            json={"revision": 1, "confirm": True},
        )
        assert executed.status == 200
        receipt = await executed.json()
        assert receipt["status"] == "failed"
        assert receipt["error"] == "Named credential unavailable"
        assert receipt["request"] == prepared["request"]
        assert receipt["result"] is None
        final = await client.get(PREFIX)
        final_body = await final.json()
        assert final_body["runs"][0]["status"] == "failed"
        assert b"owner@example.com" in store.path.read_bytes()
        assert b"Reviewed by owner" in store.path.read_bytes()
        assert b"private-token" not in store.path.read_bytes()


def test_manifest_declares_three_aliases_and_unique_native_provider():
    path = Path(
        "runtime/gideon/extensions/apps/native/gideon-integration-apps/app.json"
    )
    manifest = json.loads(path.read_text())
    assert manifest["name"] == "gideon-integration-apps"
    assert manifest["native"] is True
    assert manifest["provider"]["type"] == "tool"
    assert (
        manifest["provider"]["implementation"]
        == "gideon.workspace.capabilities.platform.integration_apps.tools:create_provider"
    )
    assert manifest["provider"]["capabilities"] == ["jira", "datadog", "github"]
    assert set(manifest["tags"]) >= {"jira", "datadog", "github", "platform"}


def test_sealed_box_rejects_invalid_keys_and_never_returns_plaintext():
    with pytest.raises(DomainError):
        seal("private", "not-base64")
    with pytest.raises(DomainError):
        seal("private", base64.b64encode(b"too-short").decode())
    with pytest.raises(DomainError):
        seal("", base64.b64encode(b"0" * 32).decode())
    with pytest.raises(DomainError):
        seal("x" * 48001, base64.b64encode(b"0" * 32).decode())
