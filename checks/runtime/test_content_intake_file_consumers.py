import asyncio
import codecs
import threading
from pathlib import Path
from types import SimpleNamespace

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.interfaces.dashboard.handlers import files, knowledge, uploads
from gideon.workspace.uploads.store import UploadStore


def test_real_file_consumers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    allowed = tmp_path / "workspace"
    allowed.mkdir(parents=True)
    monkeypatch.setenv("GIDEON_WORKSPACE", str(allowed))
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    queued = []

    async def check():
        app = web.Application()
        app["state"] = SimpleNamespace(
            knowledge_store=store,
            knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=queued.append),
        )
        parts = UploadStore(home / "uploads" / ".parts")
        app["upload_store"] = parts
        app.router.add_post("/knowledge", knowledge.ingest_file)
        app.router.add_post("/folder", files.api_file_upload)
        app.router.add_post("/uploads/init", uploads.api_uploads_init)
        app.router.add_put("/uploads/{id}/part", uploads.api_uploads_part)
        app.router.add_post("/uploads/{id}/complete", uploads.api_uploads_complete)
        async with TestClient(TestServer(app)) as client:

            async def multipart(route, data, name, mime="text/plain"):
                form = FormData()
                form.add_field("file", data, filename=name, content_type=mime)
                response = await client.post(route, data=form)
                return response.status, await response.json()

            async def resumable(data, name, target, mime="text/plain"):
                response = await client.post(
                    "/uploads/init",
                    json={
                        "filename": name,
                        "size": len(data),
                        "mime": mime,
                        "target": target,
                        "path": str(allowed),
                    },
                )
                assert response.status == 200, await response.text()
                session = await response.json()
                sid = session["uploadId"]
                response = await client.put(f"/uploads/{sid}/part?index=0", data=data)
                assert response.status == 200, await response.text()
                response = await client.post(f"/uploads/{sid}/complete")
                assert not parts._dir(sid).exists()
                return response.status, await response.json()

            plain = b"Ordinary document content for native Knowledge."
            status, answer = await multipart("/knowledge", plain, "document.txt")
            assert status == 200, answer
            item = store.get_item(answer["item_id"])
            assert Path(item["file_path"]).read_bytes() == plain
            assert item["processing_status"] == "queued" and queued == [item["id"]]
            status, duplicate = await resumable(plain, "copy.txt", "knowledge")
            assert (
                status == 200
                and duplicate["deduped"]
                and duplicate["item_id"] == item["id"]
            )
            assert queued == [item["id"]]
            code = "def double(x):\n    return x * 2\n"
            status, answer = await multipart(
                "/knowledge", codecs.BOM_UTF16_LE + code.encode("utf-16-le"), "code.py"
            )
            assert status == 200, answer
            gist = store.get_item(answer["item_id"])
            assert (
                gist["type"] == "gist"
                and gist["content"] == code
                and gist["gist_language"] == "python"
            )
            before = store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            for raw, expected in (
                (b"\0binary code", 415),
                (codecs.BOM_UTF16_LE + "safe\u202eevil".encode("utf-16-le"), 422),
            ):
                status, answer = await multipart("/knowledge", raw, "denied.py")
                assert status == expected, answer
            assert (
                store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == before
            )
            for target in ("knowledge", "attachment", "workspace"):
                status, answer = await resumable(
                    b"rm -rf / --no-preserve-root\n", "unsafe.txt", target
                )
                assert (
                    status == 422
                    and answer["error"]["code"] == "upload_content_refused"
                )
            status, answer = await resumable(
                b"Approved workspace file.", "stored.txt", "workspace"
            )
            assert (
                status == 200
                and Path(answer["paths"][0]).read_bytes() == b"Approved workspace file."
            )
            status, answer = await resumable(
                b"Overwrite attempt.", "stored.txt", "workspace"
            )
            assert (
                status == 409
                and (allowed / "stored.txt").read_bytes() == b"Approved workspace file."
            )
            status, answer = await resumable(
                b"Approved attachment bytes.",
                "file.bin",
                "attachment",
                "application/octet-stream",
            )
            assert (
                status == 200
                and Path(answer["paths"][0]).read_bytes()
                == b"Approved attachment bytes."
            )
            status, answer = await multipart(
                "/folder?path=" + str(allowed), b"Approved folder file.", "folder.txt"
            )
            assert (
                status == 200
                and (allowed / "folder.txt").read_bytes() == b"Approved folder file."
            )
            status, answer = await multipart(
                "/folder?path=" + str(allowed), b"rm -rf /\n", "denied.txt"
            )
            assert status == 422 and not (allowed / "denied.txt").exists()
            status, answer = await multipart(
                "/folder?path=/etc", b"Approved text.", "not-permitted.txt"
            )
            assert status == 400 and not Path("/etc/not-permitted.txt").exists()

    try:
        asyncio.run(check())
    finally:
        store.db.close()


def test_assembly_cancel_joins_real_writer_and_removes_staging(tmp_path, monkeypatch):
    async def check():
        store = UploadStore(tmp_path / "parts")
        session = store.init(
            filename="cancel.txt", size=5, mime="text/plain", target="attachment"
        )

        class Reader:
            sent = False

            async def read(self, n):
                if self.sent:
                    return b""
                self.sent = True
                return b"bytes"

        await store.write_part(session.id, 0, Reader())
        entered, release = threading.Event(), threading.Event()
        real = store._assemble

        def delayed(sid):
            entered.set()
            release.wait(5)
            return real(sid)

        monkeypatch.setattr(store, "_assemble", delayed)
        task = asyncio.create_task(store.assemble(session.id))
        await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and store._dir(session.id).exists()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation was lost")
        assert not store._dir(session.id).exists()

    asyncio.run(check())


def test_cancelled_snapshot_persist_preserves_existing_destination(
    tmp_path, monkeypatch
):
    from gideon.workspace.uploads.content_intake import ApprovedFile, approve_stream

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))

    async def check():
        async def chunks():
            yield b"Approved replacement bytes."

        snapshot = await approve_stream(chunks(), "new.txt", surface="test")
        dest = tmp_path / "existing.txt"
        dest.write_bytes(b"Existing owner file.")
        entered, release = threading.Event(), threading.Event()
        real = ApprovedFile._persist

        def delayed(self, path):
            entered.set()
            release.wait(5)
            return real(self, path)

        monkeypatch.setattr(ApprovedFile, "_persist", delayed)
        try:
            task = asyncio.create_task(snapshot.persist(dest))
            await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Cancellation was lost")
            assert dest.read_bytes() == b"Existing owner file."
        finally:
            snapshot.close()

    asyncio.run(check())
