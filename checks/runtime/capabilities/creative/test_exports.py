import asyncio
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pdfplumber
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative_exports import (
    PREFIX,
    register,
)
from gideon.workspace.capabilities.creative.export_tools import ExportTools
from gideon.workspace.capabilities.creative.exports import (
    ExportError,
    ManuscriptExports,
    epub_bytes,
    pdf_bytes,
)
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.works import WorkStore


def work(
    store,
    title="A Canonical Work",
    body="# Opening\n\nFirst paragraph.\n\nSecond & final <line>.",
    request="work",
):
    row = store.create(
        {
            "request_id": request,
            "title": title,
            "kind": "work",
            "prompt": "Write it",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    result = store.draft(
        row["id"],
        {
            "request_id": request + "-draft",
            "revision": row["revision"],
            "text": body,
            "note": "selected manuscript",
        },
    )
    return result["work"], result["draft"]


def payload(row, **changes):
    value = {
        "request_id": "export-one",
        "source_kind": "work",
        "source_id": row["id"],
        "source_revision": row["revision"],
        "title": "Published Title",
        "creator": "Ada Author",
        "language": "en",
        "identifier": "urn:isbn:9780000000001",
    }
    value.update(changes)
    return value


@pytest.fixture
def prepared(tmp_path):
    works = WorkStore(tmp_path)
    row, draft = work(works)
    return tmp_path, works, row, draft, ManuscriptExports(tmp_path, works=works)


def test_epub_has_required_container_metadata_navigation_spine_and_assets():
    data = epub_bytes(
        {
            "title": "A & B",
            "creator": "Ada <Writer>",
            "language": "en",
            "identifier": "urn:test:1",
            "modified": "2026-09-25T00:00:00Z",
        },
        [
            {"title": "One & Only", "text": "# Scene\n\nA <safe> & correct paragraph."},
            {"title": "Two", "text": "Another chapter."},
        ],
    )
    assert data.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert names == [
            "mimetype",
            "META-INF/container.xml",
            "OEBPS/content.opf",
            "OEBPS/nav.xhtml",
            "OEBPS/style.css",
            "OEBPS/chapter-1.xhtml",
            "OEBPS/chapter-2.xhtml",
        ]
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(
            "{urn:oasis:names:tc:opendocument:xmlns:container}rootfiles/{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
        )
        assert rootfile.attrib["full-path"] == "OEBPS/content.opf"
        package = ElementTree.fromstring(archive.read(rootfile.attrib["full-path"]))
        assert package.attrib["unique-identifier"] == "book-id"
        dc = "{http://purl.org/dc/elements/1.1/}"
        metadata = package.find("{http://www.idpf.org/2007/opf}metadata")
        assert metadata.find(dc + "title").text == "A & B"
        assert metadata.find(dc + "creator").text == "Ada <Writer>"
        assert metadata.find(dc + "language").text == "en"
        assert metadata.find(dc + "identifier").text == "urn:test:1"
        manifest = package.find("{http://www.idpf.org/2007/opf}manifest")
        items = {row.attrib["id"]: row.attrib for row in manifest}
        assert items["nav"]["properties"] == "nav"
        assert items["style"]["media-type"] == "text/css"
        assert items["chapter-1"]["media-type"] == "application/xhtml+xml"
        spine = package.find("{http://www.idpf.org/2007/opf}spine")
        assert [row.attrib["idref"] for row in spine] == ["chapter-1", "chapter-2"]
        nav = ElementTree.fromstring(archive.read("OEBPS/nav.xhtml"))
        links = nav.findall(".//{http://www.w3.org/1999/xhtml}a")
        assert [(row.attrib["href"], row.text) for row in links] == [
            ("chapter-1.xhtml", "One & Only"),
            ("chapter-2.xhtml", "Two"),
        ]
        chapter = archive.read("OEBPS/chapter-1.xhtml").decode()
        assert "<h1>Scene</h1>" in chapter
        assert "A &lt;safe&gt; &amp; correct paragraph." in chapter
        assert "../" not in "".join(names)


def test_work_export_pins_exact_immutable_source_and_real_readable_documents(prepared):
    home, works, row, draft, exports = prepared
    result = exports.create(payload(row))
    assert result["source_kind"] == "work"
    assert result["source_id"] == row["id"]
    assert result["source_revision"] == row["revision"]
    assert result["metadata"]["title"] == "Published Title"
    assert len(result["selections"]) == 1
    selected = result["selections"][0]
    assert selected["work_id"] == row["id"]
    assert selected["work_revision"] == row["revision"]
    assert selected["draft_id"] == draft["id"]
    assert selected["artifact_id"] == draft["artifact_id"]
    assert selected["artifact_version"] == 1
    assert (
        selected["sha256"]
        == hashlib.sha256(works.get(row["id"])["text"].encode()).hexdigest()
    )
    epub_path, epub_mime = exports.file(result["id"], "epub")
    pdf_path, pdf_mime = exports.file(result["id"], "print")
    assert epub_mime == "application/epub+zip"
    assert pdf_mime == "application/pdf"
    assert epub_path.name == "manuscript.epub"
    assert pdf_path.name == "manuscript.pdf"
    assert result["files"]["epub"]["bytes"] == epub_path.stat().st_size
    assert result["files"]["print"]["bytes"] == pdf_path.stat().st_size
    assert (
        hashlib.sha256(epub_path.read_bytes()).hexdigest()
        == result["files"]["epub"]["sha256"]
    )
    assert (
        hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        == result["files"]["print"]["sha256"]
    )
    with zipfile.ZipFile(epub_path) as archive:
        assert archive.testzip() is None
    with pdfplumber.open(pdf_path) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
        assert "Published Title" in text
        assert "A Canonical Work" in text
        assert "First paragraph." in text
        assert "Second & final <line>." in text
    identified = subprocess.run(
        ["file", "--brief", "--mime-type", str(pdf_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert identified.stdout.strip() == "application/pdf"
    assert exports.get(result["id"]) == result
    assert exports.list() == [result]
    reopened = ManuscriptExports(home, works=WorkStore(home))
    assert reopened.get(result["id"]) == result
    assert reopened.file(result["id"], "epub")[0].read_bytes() == epub_path.read_bytes()


def test_request_replay_is_idempotent_and_conflicts_without_rewriting(prepared):
    home, works, row, draft, exports = prepared
    first = exports.create(payload(row))
    before = {
        kind: exports.file(first["id"], kind)[0].read_bytes()
        for kind in ("epub", "print")
    }
    replay = exports.create(payload(row))
    assert replay == first
    assert exports.list() == [first]
    assert {
        kind: exports.file(first["id"], kind)[0].read_bytes() for kind in before
    } == before
    with pytest.raises(ExportError) as conflict:
        exports.create(payload(row, title="Changed after request reuse"))
    assert conflict.value.status == 409
    assert conflict.value.code == "request_conflict"
    assert exports.list() == [first]


def test_export_uses_selected_work_revision_after_later_draft_changes(prepared):
    home, works, row, draft, exports = prepared
    pinned = row
    later = works.draft(
        row["id"],
        {
            "request_id": "later-draft",
            "revision": row["revision"],
            "text": "Entirely later manuscript",
            "note": "later",
        },
    )["work"]
    assert later["revision"] == pinned["revision"] + 1
    result = exports.create(payload(pinned))
    assert result["selections"][0]["draft_id"] == draft["id"]
    with zipfile.ZipFile(exports.file(result["id"], "epub")[0]) as archive:
        chapter = archive.read("OEBPS/chapter-1.xhtml").decode()
        assert "First paragraph." in chapter
        assert "Entirely later manuscript" not in chapter
    current = exports.create(payload(later, request_id="current-export"))
    assert current["selections"][0]["draft_id"] != draft["id"]
    with zipfile.ZipFile(exports.file(current["id"], "epub")[0]) as archive:
        assert (
            "Entirely later manuscript"
            in archive.read("OEBPS/chapter-1.xhtml").decode()
        )


def test_ordered_series_export_pins_each_current_chapter_source(tmp_path):
    series = SeriesStore(tmp_path)
    planned = series.create(
        {
            "request_id": "series",
            "title": "Ordered Saga",
            "synopsis": "A sequence",
            "volumes": [
                {
                    "id": "v1",
                    "title": "Volume One",
                    "chapters": [
                        {"id": "c1", "title": "First Chapter", "prompt": "first"},
                        {"id": "c2", "title": "Second Chapter", "prompt": "second"},
                    ],
                }
            ],
            "arcs": [],
            "author_ref": None,
            "universe_ref": None,
        }
    )
    first = series.prepare(planned["id"], "c1", {"revision": planned["revision"]})
    first_draft = asyncio.run(
        series.draft(
            planned["id"],
            "c1",
            {
                "request_id": "c1-draft",
                "revision": planned["revision"],
                "work_revision": first["revision"],
                "mode": "authored",
                "text": "First manuscript body.",
                "note": "first",
                "instruction": "",
            },
        )
    )
    series.review(
        planned["id"],
        "c1",
        {
            "revision": planned["revision"],
            "work_revision": first_draft["work"]["revision"],
        },
    )
    second = series.prepare(planned["id"], "c2", {"revision": planned["revision"]})
    second_draft = asyncio.run(
        series.draft(
            planned["id"],
            "c2",
            {
                "request_id": "c2-draft",
                "revision": planned["revision"],
                "work_revision": second["revision"],
                "mode": "authored",
                "text": "Second manuscript body.",
                "note": "second",
                "instruction": "",
            },
        )
    )
    exports = ManuscriptExports(tmp_path, series=series, works=series.works)
    result = exports.create(
        {
            "request_id": "series-export",
            "source_kind": "series",
            "source_id": planned["id"],
            "source_revision": planned["revision"],
            "title": "",
            "creator": "Series Author",
            "language": "en-US",
            "identifier": "urn:uuid:ordered-saga",
        }
    )
    assert result["metadata"]["title"] == "Ordered Saga"
    assert [row["title"] for row in result["selections"]] == [
        "First Chapter",
        "Second Chapter",
    ]
    assert [row["work_id"] for row in result["selections"]] == [
        first["id"],
        second["id"],
    ]
    assert [row["work_revision"] for row in result["selections"]] == [
        first_draft["work"]["revision"],
        second_draft["work"]["revision"],
    ]
    assert [row["draft_id"] for row in result["selections"]] == [
        first_draft["draft"]["id"],
        second_draft["draft"]["id"],
    ]
    with zipfile.ZipFile(exports.file(result["id"], "epub")[0]) as archive:
        assert (
            "First manuscript body." in archive.read("OEBPS/chapter-1.xhtml").decode()
        )
        assert (
            "Second manuscript body." in archive.read("OEBPS/chapter-2.xhtml").decode()
        )
    with pdfplumber.open(exports.file(result["id"], "print")[0]) as document:
        extracted = "\n".join(page.extract_text() or "" for page in document.pages)
        assert extracted.index("First Chapter") < extracted.index("Second Chapter")


def test_incomplete_missing_and_changed_sources_fail_without_artifacts(
    tmp_path, prepared
):
    empty = WorkStore(tmp_path / "empty")
    row = empty.create(
        {
            "request_id": "empty",
            "title": "Empty",
            "kind": "work",
            "prompt": "",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    exports = ManuscriptExports(tmp_path / "empty", works=empty)
    with pytest.raises(ExportError) as incomplete:
        exports.create(payload(row, source_id=row["id"]))
    assert incomplete.value.code == "source_incomplete"
    assert exports.list() == []
    home, works, pinned, draft, ready = prepared
    artifact = works.artifacts.get(draft["artifact_id"], version=1)
    works.artifacts.delete(artifact.slug)
    with pytest.raises(ExportError) as missing:
        ready.create(payload(pinned))
    assert missing.value.code == "source_missing"
    assert ready.list() == []


def test_optional_print_renderer_and_invalid_renderer_output_fail_clearly(prepared):
    home, works, row, draft, exports = prepared
    unavailable = ManuscriptExports(
        home / "unavailable",
        works=works,
        pdf_renderer=lambda metadata, chapters: (_ for _ in ()).throw(
            ExportError(
                "Print PDF renderer is unavailable; install the document PDF renderer",
                503,
                "renderer_unavailable",
            )
        ),
    )
    with pytest.raises(ExportError) as absent:
        unavailable.create(payload(row))
    assert absent.value.status == 503
    assert absent.value.code == "renderer_unavailable"
    assert unavailable.list() == []
    invalid = ManuscriptExports(
        home / "invalid",
        works=works,
        pdf_renderer=lambda metadata, chapters: b"placeholder",
    )
    with pytest.raises(ExportError) as fake:
        invalid.create(payload(row))
    assert fake.value.status == 500
    assert fake.value.code == "invalid_renderer_output"
    assert invalid.list() == []


def test_changed_or_missing_export_bytes_fail_integrity_check(prepared):
    home, works, row, draft, exports = prepared
    result = exports.create(payload(row))
    pdf = exports.file(result["id"], "print")[0]
    pdf.write_bytes(b"%PDF-tampered")
    with pytest.raises(ExportError) as corrupt:
        exports.file(result["id"], "print")
    assert corrupt.value.code == "artifact_corrupt"
    epub = exports.file(result["id"], "epub")[0]
    epub.unlink()
    with pytest.raises(ExportError) as missing:
        exports.file(result["id"], "epub")
    assert missing.value.code == "artifact_corrupt"


@pytest.mark.asyncio
async def test_owner_http_creates_and_downloads_real_bytes_without_exposing_filesystem(
    prepared,
):
    home, works, row, draft, exports = prepared

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app["creative_exports_factory"] = lambda: exports
    register(app)
    async with TestClient(TestServer(app)) as client:
        created = await client.post(PREFIX, json=payload(row))
        assert created.status == 201
        receipt = await created.json()
        assert str(home) not in json.dumps(receipt)
        assert receipt["files"]["epub"]["path"].startswith(PREFIX + "/")
        listed = await client.get(PREFIX)
        assert listed.status == 200
        assert (await listed.json())["items"] == [receipt]
        loaded = await client.get(PREFIX + "/" + receipt["id"])
        assert loaded.status == 200
        assert await loaded.json() == receipt
        epub = await client.get(receipt["files"]["epub"]["path"])
        assert epub.status == 200
        epub_data = await epub.read()
        assert epub.headers["Content-Type"].startswith("application/epub+zip")
        assert (
            epub.headers["Content-Disposition"]
            == 'attachment; filename="manuscript.epub"'
        )
        with zipfile.ZipFile(io.BytesIO(epub_data)) as archive:
            assert archive.testzip() is None
        pdf = await client.get(receipt["files"]["print"]["path"])
        assert pdf.status == 200
        pdf_data = await pdf.read()
        assert pdf_data.startswith(b"%PDF")
        assert pdf.headers["Cache-Control"] == "private, no-store"
        assert (
            hashlib.sha256(epub_data).hexdigest() == receipt["files"]["epub"]["sha256"]
        )
        assert (
            hashlib.sha256(pdf_data).hexdigest() == receipt["files"]["print"]["sha256"]
        )
        missing_payload = payload(
            row, request_id="missing-source", source_id="missing-work"
        )
        missing = await client.post(PREFIX, json=missing_payload)
        assert missing.status == 404
        assert await missing.json() == {
            "error": "Work not found",
            "code": "source_error",
        }
        assert len((await (await client.get(PREFIX)).json())["items"]) == 1


def test_native_manifest_and_tools_require_approval_for_render(prepared):
    home, works, row, draft, exports = prepared
    tools = ExportTools(exports)
    definitions = {tool.name: tool for tool in asyncio.run(tools.list_tools())}
    assert definitions["creative_manuscript_exports"].requires_approval is False
    assert definitions["creative_manuscript_export"].requires_approval is True
    assert definitions["creative_manuscript_export"].risk_level.value == "caution"
    made = asyncio.run(tools.invoke("creative_manuscript_export", payload(row)))
    assert made.success
    receipt = json.loads(made.output)
    assert receipt["files"]["epub"]["mime"] == "application/epub+zip"
    listed = asyncio.run(tools.invoke("creative_manuscript_exports", {}))
    assert listed.success
    assert json.loads(listed.output)["items"] == [receipt]
    invalid = asyncio.run(tools.invoke("wrong", {}))
    assert not invalid.success
    assert invalid.metadata["code"] == "invalid_export"
    manifest = json.loads(
        Path(
            "runtime/gideon/extensions/apps/native/gideon-creative-exports/app.json"
        ).read_text()
    )
    assert manifest["name"] == "gideon-creative-exports"
    assert (
        manifest["provider"]["implementation"]
        == "gideon.workspace.capabilities.creative.export_tools:create_provider"
    )
    assert manifest["provider"]["capabilities"] == ["creative-manuscript-export"]
