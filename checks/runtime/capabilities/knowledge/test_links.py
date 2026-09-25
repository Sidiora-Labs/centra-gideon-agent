import asyncio
import io
import json
import tarfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_links import register
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.links import LinkStudies, link_url
from gideon.workspace.capabilities.knowledge.repository_intake import RepositoryReader, repository_url, study


@pytest.fixture
def service(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    home.mkdir()
    store = KnowledgeStore(str(home / "knowledge.db"))
    links = LinkStudies(store, home)
    yield links
    store.close()


def bucket(service, name="Research"):
    return service.create_bucket({"name": name, "icon": "link"})


def add(service, bucket_id, suffix="one"):
    return service.add_link({"url": f"https://example.com/{suffix}", "title": suffix.title(), "bucket_id": bucket_id})


@pytest.mark.parametrize("value,expected", [
    ("https://example.com", "https://example.com/"),
    ("https://EXAMPLE.com/path#section", "https://example.com/path"),
    ("http://example.com:80/a?q=1", "http://example.com:80/a?q=1"),
])
def test_link_url_canonicalizes_public_links(value, expected):
    assert link_url(value) == expected


@pytest.mark.parametrize("value", ["", "file:///tmp/a", "mailto:a@example.com", "https://user:pass@example.com/a", "https://example.com:444/a", 3, None])
def test_link_url_rejects_credentials_ports_and_nonweb_values(value):
    with pytest.raises(CaptureError):
        link_url(value)


@pytest.mark.parametrize("value,expected", [
    ("https://github.com/octocat/Hello-World", ("github.com", "octocat/Hello-World", "https://github.com/octocat/Hello-World")),
    ("https://github.com/octocat/Hello-World.git", ("github.com", "octocat/Hello-World", "https://github.com/octocat/Hello-World")),
    ("https://gitlab.com/group/sub/repo", ("gitlab.com", "group/sub/repo", "https://gitlab.com/group/sub/repo")),
])
def test_repository_url_accepts_only_canonical_supported_hosts(value, expected):
    assert repository_url(value) == expected


@pytest.mark.parametrize("value", [
    "http://github.com/a/b", "https://github.com/a", "https://github.com/a/b?ref=main",
    "https://github.com/a/b#readme", "https://user@github.com/a/b", "https://git.example/a/b",
    "https://github.com:444/a/b", "https://github.com/a/b/c/d/e/f/g/h/i/j/k",
])
def test_repository_url_rejects_ambiguous_or_credentialed_sources(value):
    with pytest.raises(CaptureError, match="credential-free"):
        repository_url(value)


def test_bucket_is_real_manual_collection_with_ordered_links(service):
    created = bucket(service)
    first = add(service, created["id"], "first")
    second = add(service, created["id"], "second")
    detail = service.buckets()[0]
    assert detail["kind"] == "manual"
    assert detail["query"].startswith("link-bucket:")
    assert [row["id"] for row in detail["links"]] == [first["id"], second["id"]]
    assert service.store.get_collection(created["id"])["id"] == created["id"]
    assert [row["id"] for row in service.store.resolve_collection(created["id"])] == [second["id"], first["id"]]


def test_link_identity_deduplicates_across_buckets(service):
    one = bucket(service, "One")
    two = bucket(service, "Two")
    first = add(service, one["id"], "same")
    second = add(service, two["id"], "same")
    assert first["id"] == second["id"]
    assert service.store.collections_for_item(first["id"])[0]["id"] == one["id"]
    assert {row["id"] for row in service.store.collections_for_item(first["id"])} == {one["id"], two["id"]}
    assert service.db.execute("SELECT count(*) FROM items WHERE guid LIKE 'link:%'").fetchone()[0] == 1


def test_link_order_survives_service_reconstruction(service):
    created = bucket(service)
    first = add(service, created["id"], "first")
    second = add(service, created["id"], "second")
    assert [row["id"] for row in service.reorder_links(created["id"], [second["id"], first["id"]])] == [second["id"], first["id"]]
    reopened = LinkStudies(service.store, service.home)
    assert [row["id"] for row in reopened.links(created["id"])] == [second["id"], first["id"]]


def test_link_reorder_is_exact_and_atomic(service):
    created = bucket(service)
    first = add(service, created["id"], "first")
    second = add(service, created["id"], "second")
    before = [row["id"] for row in service.links(created["id"])]
    for invalid in ([first["id"]], [first["id"], first["id"]], [first["id"], "missing"]):
        with pytest.raises(CaptureError, match="every current"):
            service.reorder_links(created["id"], invalid)
        assert [row["id"] for row in service.links(created["id"])] == before
    assert [row["id"] for row in service.reorder_links(created["id"], [second["id"], first["id"]])] == [second["id"], first["id"]]


def test_bucket_order_is_exact_and_durable(service):
    first = bucket(service, "First")
    second = bucket(service, "Second")
    with pytest.raises(CaptureError, match="every current"):
        service.reorder_buckets([first["id"]])
    ordered = service.reorder_buckets([second["id"], first["id"]])
    assert [row["id"] for row in ordered] == [second["id"], first["id"]]
    assert [row["id"] for row in LinkStudies(service.store, service.home).buckets()] == [second["id"], first["id"]]


def test_bucket_update_preserves_marker_and_members(service):
    created = bucket(service)
    item = add(service, created["id"])
    changed = service.update_bucket(created["id"], {"name": "Renamed", "icon": "book"})
    assert changed["name"] == "Renamed"
    assert changed["icon"] == "book"
    assert changed["query"] == created["query"]
    assert changed["links"][0]["id"] == item["id"]


def test_deleting_bucket_preserves_canonical_bookmarks(service):
    created = bucket(service)
    item = add(service, created["id"])
    result = service.delete_bucket(created["id"])
    assert result == {"deleted": created["id"], "preserved_item_ids": [item["id"]]}
    assert service.store.get_item(item["id"])["title"] == "One"
    assert service.store.get_collection(created["id"]) is None
    assert service.db.execute("SELECT count(*) FROM capability_knowledge_link_order").fetchone()[0] == 0


@pytest.mark.parametrize("body", [{}, {"name": "", "icon": "x"}, {"name": "x"}, {"name": "x", "icon": "x", "extra": 1}])
def test_bucket_create_requires_exact_bounded_shape(service, body):
    with pytest.raises(CaptureError):
        service.create_bucket(body)


@pytest.mark.parametrize("body", [{}, {"url": "https://example.com", "title": "x"}, {"url": "https://example.com", "title": "", "bucket_id": "x"}, {"url": "https://example.com", "title": "x", "bucket_id": "x", "home": "/tmp"}])
def test_add_link_requires_exact_shape_and_existing_bucket(service, body):
    with pytest.raises(CaptureError):
        service.add_link(body)


def archive_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in entries:
            info = tarfile.TarInfo("snapshot-root/" + name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def test_real_archive_extract_and_study_produces_revision_bound_report(tmp_path):
    payload = archive_bytes([
        ("README.md", b"# Small project\n\nActual repository documentation."),
        ("src/main.py", b"print('hello')\n"),
        ("src/types.ts", b"export type ID = string\n"),
        ("pyproject.toml", b"[project]\nname='small'\n"),
    ])
    destination = tmp_path / "checkout"
    RepositoryReader.extract(payload, destination)
    structured, markdown = study(destination, "a" * 40, "https://github.com/example/small")
    assert (destination / "src/main.py").read_text() == "print('hello')\n"
    assert structured["revision"] == "a" * 40
    assert structured["file_count"] == 4
    assert structured["languages"] == {"Markdown": 1, "Python": 1, "TypeScript": 1, "TOML": 1}
    assert structured["top_level"] == {"src": 2, "README.md": 1, "pyproject.toml": 1}
    assert "Actual repository documentation" in structured["readme_excerpt"]
    assert "Revision: `" + "a" * 40 + "`" in markdown
    assert "## Languages" in markdown


def test_archive_replaces_previous_snapshot_atomically(tmp_path):
    destination = tmp_path / "checkout"
    RepositoryReader.extract(archive_bytes([("old.txt", b"old")]), destination)
    assert (destination / "old.txt").read_text() == "old"
    RepositoryReader.extract(archive_bytes([("new.txt", b"new")]), destination)
    assert not (destination / "old.txt").exists()
    assert (destination / "new.txt").read_text() == "new"


def test_archive_refuses_path_traversal_and_preserves_existing_checkout(tmp_path):
    destination = tmp_path / "checkout"
    RepositoryReader.extract(archive_bytes([("safe.txt", b"safe")]), destination)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        info = tarfile.TarInfo("root/../escaped.txt")
        info.size = 3
        archive.addfile(info, io.BytesIO(b"bad"))
    with pytest.raises(CaptureError, match="unsafe"):
        RepositoryReader.extract(payload.getvalue(), destination)
    assert (destination / "safe.txt").read_text() == "safe"
    assert not (tmp_path / "escaped.txt").exists()


def test_archive_refuses_multiple_roots_empty_and_oversized_members(tmp_path):
    multi = io.BytesIO()
    with tarfile.open(fileobj=multi, mode="w:gz") as archive:
        for name in ("one/a", "two/b"):
            info = tarfile.TarInfo(name); info.size = 1; archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(CaptureError, match="single safe root"):
        RepositoryReader.extract(multi.getvalue(), tmp_path / "multi")
    empty = io.BytesIO()
    with tarfile.open(fileobj=empty, mode="w:gz"): pass
    with pytest.raises(CaptureError, match="file count"):
        RepositoryReader.extract(empty.getvalue(), tmp_path / "empty")


def test_study_refuses_missing_empty_and_oversized_checkout(tmp_path):
    with pytest.raises(CaptureError, match="unavailable"):
        study(tmp_path / "missing", "a" * 40, "https://github.com/a/b")
    empty = tmp_path / "empty"; empty.mkdir()
    with pytest.raises(CaptureError, match="no readable"):
        study(empty, "a" * 40, "https://github.com/a/b")
    huge = tmp_path / "huge"; huge.mkdir(); (huge / "large.bin").write_bytes(b"x" * 4194305)
    with pytest.raises(CaptureError, match="no readable"):
        study(huge, "a" * 40, "https://github.com/a/b")


@pytest.mark.parametrize("target", [
    "http://github.com/a/b",
    "https://example.com/a/b",
    "https://user@github.com/a/b",
    "https://github.com:444/a/b",
    "file:///tmp/repository",
])
def test_repository_reader_refuses_nonfixed_transfer_targets_before_network(target):
    reader = RepositoryReader("dashboard:ui")
    with pytest.raises(CaptureError, match="fixed public"):
        reader.validate(target)


def test_missing_bucket_repo_and_report_reads_are_explicit(service):
    with pytest.raises(CaptureError, match="bucket not found"):
        service.links("missing")
    with pytest.raises(CaptureError, match="intake not found"):
        service.repo("missing")
    created = bucket(service)
    assert service.links(created["id"]) == []
    assert service.repos() == {"items": []}


def test_report_refuses_job_without_landed_study(service, tmp_path):
    identity = landed_repo(service, tmp_path)
    job = service.repo(identity)
    assert job["report_id"] is None
    assert job["revision"] == "b" * 40
    with pytest.raises(CaptureError, match="unavailable"):
        service.report(identity)


def test_repository_list_is_newest_first_and_retains_exact_stage(service, tmp_path):
    first = landed_repo(service, tmp_path)
    service.db.execute("UPDATE capability_knowledge_repo_jobs SET id='repo-job-two',request_id='request-two',created_at='2026-02-01' WHERE id=?", (first,))
    service.db.execute("UPDATE capability_knowledge_repo_events SET job_id='repo-job-two' WHERE job_id=?", (first,))
    service.db.execute("INSERT INTO capability_knowledge_repo_jobs(id,request_id,payload,bookmark_id,url,status,stage,revision,checkout_path,created_at,updated_at) SELECT 'repo-job-old','request-old',payload,bookmark_id,url,'failed','fetching','', '', '2026-01-01','2026-01-01' FROM capability_knowledge_repo_jobs WHERE id='repo-job-two'")
    service.db.commit()
    items = service.repos()["items"]
    assert [item["id"] for item in items] == ["repo-job-two", "repo-job-old"]
    assert items[0]["events"][0]["detail"] == "Studying"
    assert items[1]["status"] == "failed"


def landed_repo(service, tmp_path):
    url = "https://github.com/example/small"
    bookmark = service.store.create_typed_item(item_type="bookmark", title="example/small", content="", guid="repo:github.com:example/small", extra={"url": url})
    identity = "repo-job"
    path = tmp_path / "landed"; path.mkdir(); (path / "README.md").write_text("# Landed\nActual files")
    service.db.execute("INSERT INTO capability_knowledge_repo_jobs(id,request_id,payload,bookmark_id,url,status,stage,revision,checkout_path,created_at,updated_at) VALUES (?,?,?,?,?,'scanning','scanning',?,?,?,?)", (identity, "request-landed", "{}", bookmark, url, "b" * 40, str(path), "2026-01-01", "2026-01-01"))
    service.db.commit()
    service._event(identity, "scanning", "scanning", "Studying")
    return identity


def test_repository_report_is_canonical_knowledge_note_and_refreshes(service, tmp_path):
    identity = landed_repo(service, tmp_path)
    result = service.restudy(identity)
    report = service.report(identity)
    item = service.store.get_item(report["report_id"])
    assert result["status"] == "completed"
    assert item["item_type"] == "note"
    assert item["guid"].startswith("repo-report:")
    assert item["file_metadata"]["repository_study"]["revision"] == "b" * 40
    assert report["source_link"] == "#/knowledge/item/" + item["id"]
    path = Path(service.repo(identity)["checkout_path"])
    (path / "main.py").write_text("print('changed')")
    repeated = service.restudy(identity)
    assert repeated["report"]["report_id"] == report["report_id"]
    assert service.report(identity)["study"]["file_count"] == 2
    assert service.db.execute("SELECT count(*) FROM items WHERE guid LIKE 'repo-report:%'").fetchone()[0] == 1


def test_repo_events_are_ordered_and_restart_interrupts_active(service, tmp_path):
    identity = landed_repo(service, tmp_path)
    service._event(identity, "fetching", "fetching", "Downloading")
    assert [event["sequence"] for event in service.repo(identity)["events"]] == [1, 2]
    reopened = LinkStudies(service.store, service.home)
    job = reopened.repo(identity)
    assert job["status"] == "interrupted"
    assert "Runtime stopped" in job["error"]


@pytest.mark.asyncio
async def test_intake_rejects_invalid_repository_before_job_or_write(service):
    with pytest.raises(CaptureError, match="GitHub or GitLab"):
        await service.intake({"request_id": "bad-repo", "url": "https://example.com/a/b"}, "dashboard:ui")
    assert service.db.execute("SELECT count(*) FROM capability_knowledge_repo_jobs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_intake_request_shape_and_home_binding_fail_closed(service, monkeypatch, tmp_path):
    with pytest.raises(CaptureError, match="requires"):
        await service.intake({"url": "https://github.com/a/b"}, "dashboard:ui")
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "other"))
    with pytest.raises(CaptureError, match="Runtime home changed"):
        await service.intake({"request_id": "bound-repo", "url": "https://github.com/a/b"}, "dashboard:ui")
    assert service.db.execute("SELECT count(*) FROM capability_knowledge_repo_jobs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_http_bucket_link_order_delete_and_repository_guards(tmp_path, monkeypatch):
    home = tmp_path / "http-home"; home.mkdir(); monkeypatch.setenv("GIDEON_HOME", str(home))
    store = KnowledgeStore(str(home / "knowledge.db"))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0); state._knowledge_store = store
    app = web.Application(); app["state"] = state; register(app)
    client = TestClient(TestServer(app)); await client.start_server()
    headers = {"X-Session-Key": "dashboard:ui"}
    try:
        created = await (await client.post("/api/capabilities/knowledge/links/buckets", json={"name": "HTTP", "icon": "link"}, headers=headers)).json()
        first = await (await client.post("/api/capabilities/knowledge/links/links", json={"url": "https://example.com/first", "title": "First", "bucket_id": created["id"]}, headers=headers)).json()
        second = await (await client.post("/api/capabilities/knowledge/links/links", json={"url": "https://example.com/second", "title": "Second", "bucket_id": created["id"]}, headers=headers)).json()
        reordered = await (await client.post(f"/api/capabilities/knowledge/links/buckets/{created['id']}/links-reorder", json={"ids": [second["id"], first["id"]]}, headers=headers)).json()
        assert [row["id"] for row in reordered["links"]] == [second["id"], first["id"]]
        listing = await (await client.get("/api/capabilities/knowledge/links/buckets", headers=headers)).json()
        assert listing["buckets"][0]["links"][0]["title"] == "Second"
        deleted = await (await client.delete(f"/api/capabilities/knowledge/links/buckets/{created['id']}/bucket", headers=headers)).json()
        assert set(deleted["preserved_item_ids"]) == {first["id"], second["id"]}
        assert store.get_item(first["id"])
        assert (await client.post("/api/capabilities/knowledge/links/repositories", json={"request_id": "no-session", "url": "https://github.com/a/b"})).status == 403
        assert (await client.get("/api/capabilities/knowledge/links/buckets?home=/tmp", headers=headers)).status == 400
    finally:
        await client.close(); store.close()
