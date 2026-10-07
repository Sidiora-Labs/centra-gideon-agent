"""Project archive HTTP transfer and temporary upload ownership."""

import asyncio
import logging

from aiohttp import web
from aiohttp.multipart import BodyPartReader

from gideon.http_download import download_headers

logger = logging.getLogger(__name__)


def refusal(message, *, reason=None):
    payload = {"error": message}
    if reason is not None:
        payload["reason"] = reason
    return web.json_response(payload, status=400)


class ArchiveUpload:
    @staticmethod
    async def receive(request):
        content_type = request.headers.get("Content-Type", "").lower()
        if not content_type.startswith("multipart/"):
            return None, refusal("multipart/form-data with a 'file' field is required")
        try:
            reader = await request.multipart()
        except (ValueError, AssertionError, RuntimeError) as exc:
            return None, refusal(f"failed to parse multipart body: {exc}")
        part = await reader.next()
        if not isinstance(part, BodyPartReader) or part.name != "file":
            return None, refusal("file field required")
        from gideon.workspace.uploads.content_intake import (
            IntakeRefused,
            approve_stream,
        )

        async def chunks():
            while chunk := await part.read_chunk(65536):
                yield chunk

        snapshot = None
        try:
            snapshot = await approve_stream(
                chunks(),
                part.filename or "import.zip",
                "application/zip",
                surface="archive_import",
            )
            return await snapshot.stage_file(), None
        except IntakeRefused as exc:
            return None, exc.response()
        finally:
            if snapshot is not None:
                snapshot.close()


class ProjectExport:
    def __init__(self, project, project_root, passphrase):
        self.project = project
        self.root = project_root
        self.passphrase = passphrase

    def artifact_metadata(self):
        from gideon.workspace.artifacts import registry

        try:
            provider = registry.get_provider()
            return (
                []
                if provider is None
                else [
                    record.to_dict()
                    for record in provider.list(project_id=self.project.id)
                ]
            )
        except Exception:
            logger.warning(
                "project export: artifact metadata unavailable for %s", self.project.id
            )
            return []

    def run_metadata(self):
        from gideon.automation.workflows import store

        try:
            records, _ = store.list_runs(project_id=self.project.id, limit=1000)
            return [record.to_dict() for record in records]
        except Exception:
            logger.warning(
                "project export: run digests unavailable for %s", self.project.id
            )
            return []

    async def response(self):
        from gideon.automation.workflows import project_archive as archive

        if self.passphrase and not archive.encryption_available():
            return refusal("encryption needs the optional `cryptography` extra")
        artifacts = self.artifact_metadata()
        runs = self.run_metadata()
        try:
            data, plan = await asyncio.to_thread(
                archive.export_project_archive,
                self.project.id,
                project_root=self.root,
                project_name=self.project.name,
                artifacts=artifacts,
                runs=runs,
                passphrase=self.passphrase,
            )
        except archive.ArchiveRefused as exc:
            return refusal(str(exc), reason=exc.reason)
        name = archive.archive_filename(
            self.project.name, self.project.id, encrypted=bool(self.passphrase)
        )
        return web.Response(
            body=data,
            content_type="application/zip",
            headers={
                **download_headers(name),
                "Content-Length": str(len(data)),
                "X-Gideon-Entities": str(len(plan.entries)),
                "X-Gideon-Skipped": str(len(plan.skipped)),
                "X-Gideon-Secrets-Expected": ",".join(sorted(plan.secrets_present)),
            },
        )


class ProjectImport:
    def __init__(self, request, store_factory, upload_reader, describe):
        self.request = request
        self.store_factory = store_factory
        self.upload_reader = upload_reader
        self.describe = describe

    async def response(self):
        from gideon.automation.workflows import project_archive as archive
        from gideon.core.config.loader import config_dir

        upload, error = await self.upload_reader(self.request)
        if error is not None:
            return error
        assert upload is not None
        preview = self.request.query.get("preview", "") in ("1", "true", "yes")
        passphrase = self.request.query.get("passphrase", "")
        from gideon.operations.durability.home_paths import LinkInTheWay

        try:
            archive.projects_folder(config_dir())
        except LinkInTheWay as link:
            upload.unlink(missing_ok=True)
            return refusal(str(link), reason="link")
        store = self.store_factory()
        existing_names = [project.name for project in store.list_projects()]
        try:
            from functools import partial

            from gideon.cognition.knowledge.file_items import _owned_io

            plan, contents = await _owned_io(
                partial(
                    archive.read_archive_plan,
                    upload,
                    existing_names=existing_names,
                    passphrase=passphrase,
                )
            )
        except archive.ArchiveRefused as exc:
            return refusal(str(exc), reason=exc.reason)
        except archive.EncryptionUnavailable as exc:
            return refusal(str(exc), reason="encryption_unavailable")
        finally:
            upload.unlink(missing_ok=True)
        payload = {**plan.to_dict(), "summary": self.describe(plan)}
        if preview:
            return web.json_response({**payload, "preview": True})
        if not plan.ok:
            return web.json_response(
                {**payload, "error": "the archive contributed nothing importable"},
                status=400,
            )
        try:
            project, written, left = await asyncio.to_thread(
                archive.import_project, plan, contents, store=store, home=config_dir()
            )
        except LinkInTheWay as link:
            return refusal(str(link), reason="link")
        return web.json_response(
            {
                **payload,
                "preview": False,
                "project_id": project.id,
                "written": written,
                "partial": bool(left),
                "left_unchanged": left,
            },
            status=201,
        )
