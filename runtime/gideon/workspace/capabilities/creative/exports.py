"""Pinned manuscript exports to valid EPUB and print PDF bytes."""

import hashlib
import html
import io
import json
import os
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.core.sqlite_compat import sqlite3
from gideon.workspace.documents.model import Block, DocumentModel
from gideon.workspace.documents.registry import get_writer

from .series import SeriesStore
from .store import CatalogError, identifier, integer, keys, text
from .works import WorkStore


class ExportError(CatalogError):
    def __init__(self, message, status=400, code="invalid_export"):
        super().__init__(message, status)
        self.code = code


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _paragraphs(value):
    output = []
    for part in value.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        body = part.strip()
        if not body:
            continue
        lines = body.splitlines()
        first = lines[0]
        if first.startswith("#"):
            level = min(6, len(first) - len(first.lstrip("#")))
            output.append(("heading", level, first[level:].strip()))
            if len(lines) > 1:
                output.append(("paragraph", 0, "\n".join(lines[1:])))
        else:
            output.append(("paragraph", 0, body))
    return output


def epub_bytes(metadata, chapters):
    title = html.escape(metadata["title"])
    creator = html.escape(metadata["creator"])
    language = html.escape(metadata["language"])
    identity = html.escape(metadata["identifier"])
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="style" href="style.css" media-type="text/css"/>',
    ]
    spine = []
    navigation = []
    documents = []
    for index, chapter in enumerate(chapters, 1):
        name = f"chapter-{index}.xhtml"
        item = f"chapter-{index}"
        manifest.append(
            f'<item id="{item}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{item}"/>')
        navigation.append(
            f'<li><a href="{name}">{html.escape(chapter["title"])}</a></li>'
        )
        body = []
        for kind, level, value in _paragraphs(chapter["text"]):
            escaped = html.escape(value).replace("\n", "<br/>")
            body.append(
                f"<h{level}>{escaped}</h{level}>"
                if kind == "heading"
                else f"<p>{escaped}</p>"
            )
        documents.append(
            (
                name,
                (
                    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="'
                    + language
                    + '"><head><title>'
                    + html.escape(chapter["title"])
                    + '</title><link rel="stylesheet" type="text/css" href="style.css"/></head><body><h1>'
                    + html.escape(chapter["title"])
                    + "</h1>"
                    + "".join(body)
                    + "</body></html>"
                ).encode(),
            )
        )
    package = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">'
        + identity
        + "</dc:identifier><dc:title>"
        + title
        + "</dc:title><dc:language>"
        + language
        + "</dc:language><dc:creator>"
        + creator
        + '</dc:creator><meta property="dcterms:modified">'
        + metadata["modified"]
        + "</meta></metadata><manifest>"
        + "".join(manifest)
        + "</manifest><spine>"
        + "".join(spine)
        + "</spine></package>"
    ).encode()
    nav = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="'
        + language
        + '"><head><title>Contents</title></head><body><nav epub:type="toc"><h1>Contents</h1><ol>'
        + "".join(navigation)
        + "</ol></nav></body></html>"
    ).encode()
    container = b'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            b"application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED
        )
        archive.writestr(
            "OEBPS/content.opf", package, compress_type=zipfile.ZIP_DEFLATED
        )
        archive.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr(
            "OEBPS/style.css",
            b"body{font-family:serif;line-height:1.5;margin:5%;}h1{page-break-before:always;}p{orphans:2;widows:2;}",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for name, data in documents:
            archive.writestr("OEBPS/" + name, data, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def pdf_bytes(metadata, chapters):
    writer = get_writer("pdf")
    if writer is None:
        raise ExportError(
            "Print PDF renderer is unavailable; install the document PDF renderer",
            503,
            "renderer_unavailable",
        )
    blocks = []
    for index, chapter in enumerate(chapters):
        if index:
            blocks.append(Block(kind="pagebreak"))
        blocks.append(Block(kind="heading", level=1, text=chapter["title"]))
        for kind, level, value in _paragraphs(chapter["text"]):
            blocks.append(
                Block(kind="heading", level=level, text=value)
                if kind == "heading"
                else Block(kind="paragraph", text=value)
            )
    try:
        return writer(DocumentModel(title=metadata["title"], blocks=blocks))
    except Exception as exc:
        raise ExportError(
            "Print PDF rendering failed: " + type(exc).__name__, 422, "renderer_failed"
        ) from exc


class ManuscriptExports:
    def __init__(self, home=None, works=None, series=None, pdf_renderer=pdf_bytes):
        self.home = (
            Path(home)
            if home is not None
            else __import__(
                "gideon.core.config.loader", fromlist=["config_dir"]
            ).config_dir()
        )
        self.root = self.home / "capabilities/creative/exports"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "capabilities/creative/exports.sqlite3"
        self.works = works or WorkStore(self.home)
        self.series = series or SeriesStore(self.home)
        self.pdf_renderer = pdf_renderer
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,digest TEXT,record TEXT)"
            )
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _chapter_ref(self, title, work, draft):
        if work["active_draft_id"] != draft["id"]:
            raise ExportError(
                "Pinned work revision does not select the requested manuscript draft",
                409,
                "source_changed",
            )
        artifact = self.works.artifacts.get(
            draft["artifact_id"], version=draft["artifact_version"]
        )
        if artifact is None:
            raise ExportError(
                "Pinned manuscript artifact is missing", 404, "source_missing"
            )
        data = artifact.content.encode()
        return {
            "title": title or work["title"],
            "text": artifact.content,
            "work_id": work["id"],
            "work_revision": work["revision"],
            "draft_id": draft["id"],
            "artifact_id": draft["artifact_id"],
            "artifact_version": draft["artifact_version"],
            "sha256": _sha(data),
        }

    def _chapter(self, title, work_id, work_revision, draft_id):
        work = self.works.export(work_id, work_revision)
        draft = self.works.read_draft(work_id, draft_id)
        if draft["missing"]:
            raise ExportError(
                "Pinned manuscript artifact is missing", 404, "source_missing"
            )
        return self._chapter_ref(title, work, draft)

    def resolve(self, kind, source_id, revision):
        if kind == "work":
            work = self.works.export(identifier(source_id), integer(revision))
            draft = work.get("active_draft_id")
            if not draft:
                raise ExportError(
                    "Selected work revision has no active manuscript draft",
                    409,
                    "source_incomplete",
                )
            return work["title"], [
                self._chapter(work["title"], work["id"], work["revision"], draft)
            ]
        if kind != "series":
            raise ExportError("Source kind must be work or series")
        series = self.series.export(identifier(source_id), integer(revision))
        pending = []
        with self.series.connection() as db:
            for volume in series["volumes"]:
                for plan in volume["chapters"]:
                    row = db.execute(
                        "SELECT record FROM series_chapters WHERE series_id=? AND chapter_id=?",
                        (series["id"], plan["id"]),
                    ).fetchone()
                    if not row:
                        raise ExportError(
                            "Series chapter is not prepared: " + plan["title"],
                            409,
                            "source_incomplete",
                        )
                    link = json.loads(row[0])
                    work = self.works._work(db, link["work_id"])
                    draft_id = work.get("active_draft_id")
                    if not draft_id:
                        raise ExportError(
                            "Series chapter has no active manuscript: " + plan["title"],
                            409,
                            "source_incomplete",
                        )
                    draft_row = db.execute(
                        "SELECT record FROM work_drafts WHERE id=? AND work_id=?",
                        (draft_id, work["id"]),
                    ).fetchone()
                    if not draft_row:
                        raise ExportError(
                            "Series chapter manuscript reference is missing: "
                            + plan["title"],
                            404,
                            "source_missing",
                        )
                    pending.append((plan["title"], work, json.loads(draft_row[0])))
        chapters = [
            self._chapter_ref(title, work, draft) for title, work, draft in pending
        ]
        if not chapters:
            raise ExportError(
                "Series has no manuscript chapters", 409, "source_incomplete"
            )
        return series["title"], chapters

    def create(self, payload):
        keys(
            payload,
            {
                "request_id",
                "source_kind",
                "source_id",
                "source_revision",
                "title",
                "creator",
                "language",
                "identifier",
            },
        )
        request = identifier(payload.get("request_id"))
        kind = payload.get("source_kind")
        source_id = identifier(payload.get("source_id"))
        revision = integer(payload.get("source_revision"))
        supplied_title = text(payload.get("title", ""), 200)
        creator = text(payload.get("creator", ""), 200, True)
        language = text(payload.get("language"), 20, True)
        book_id = text(payload.get("identifier"), 300, True)
        digest = _sha(json.dumps(payload, sort_keys=True).encode())
        with self.db() as db:
            prior = db.execute(
                "SELECT digest,record FROM exports WHERE request_id=?", (request,)
            ).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ExportError(
                        "Export request ID already used", 409, "request_conflict"
                    )
                return json.loads(prior[1])
        source_title, chapters = self.resolve(kind, source_id, revision)
        if sum(len(row["text"].encode()) for row in chapters) > 8 * 1024 * 1024:
            raise ExportError(
                "Selected manuscript exceeds export size limit", 413, "source_too_large"
            )
        now = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        metadata = {
            "title": supplied_title.strip() or source_title,
            "creator": creator,
            "language": language,
            "identifier": book_id,
            "modified": now,
        }
        epub = epub_bytes(metadata, chapters)
        pdf = self.pdf_renderer(metadata, chapters)
        if not epub.startswith(b"PK") or not pdf.startswith(b"%PDF"):
            raise ExportError(
                "Renderer returned invalid document bytes",
                500,
                "invalid_renderer_output",
            )
        identity = str(uuid4())
        directory = self.root / identity
        directory.mkdir(mode=0o700)
        for name, data in (("manuscript.epub", epub), ("manuscript.pdf", pdf)):
            temporary = directory / (name + ".tmp")
            temporary.write_bytes(data)
            os.chmod(temporary, 0o600)
            os.replace(temporary, directory / name)
        files = {
            name: {
                "path": f"/api/capabilities/creative/exports/{identity}/{kind_name}",
                "bytes": len(data),
                "sha256": _sha(data),
                "mime": mime,
            }
            for name, kind_name, data, mime in (
                ("epub", "epub", epub, "application/epub+zip"),
                ("print", "print", pdf, "application/pdf"),
            )
        }
        record = {
            "id": identity,
            "request_id": request,
            "source_kind": kind,
            "source_id": source_id,
            "source_revision": revision,
            "metadata": metadata,
            "selections": [
                {key: value for key, value in row.items() if key != "text"}
                for row in chapters
            ],
            "files": files,
            "created_at": now,
        }
        with self.db() as db:
            prior = db.execute(
                "SELECT digest,record FROM exports WHERE request_id=?", (request,)
            ).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ExportError(
                        "Export request ID already used", 409, "request_conflict"
                    )
                return json.loads(prior[1])
            db.execute(
                "INSERT INTO exports VALUES (?,?,?,?)",
                (identity, request, digest, json.dumps(record)),
            )
        return record

    def get(self, identity):
        with self.db() as db:
            row = db.execute(
                "SELECT record FROM exports WHERE id=?", (identifier(identity),)
            ).fetchone()
        if not row:
            raise ExportError("Manuscript export not found", 404, "not_found")
        return json.loads(row[0])

    def list(self):
        with self.db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT record FROM exports ORDER BY rowid DESC LIMIT 100"
                )
            ]

    def file(self, identity, kind):
        record = self.get(identity)
        name = {"epub": "manuscript.epub", "print": "manuscript.pdf"}.get(kind)
        if not name:
            raise ExportError("Unknown export format", 404, "not_found")
        path = self.root / record["id"] / name
        data = path.read_bytes() if path.is_file() else b""
        if not data or _sha(data) != record["files"][kind]["sha256"]:
            raise ExportError(
                "Export bytes are missing or changed", 500, "artifact_corrupt"
            )
        return path, record["files"][kind]["mime"]
