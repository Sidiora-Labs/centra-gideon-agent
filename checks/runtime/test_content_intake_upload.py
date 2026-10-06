import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard.handlers.files import api_upload_file
from gideon.workspace.uploads import content_intake as intake


def test_actual_upload_scanner_and_exact_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

    async def check():
        app = web.Application(client_max_size=2 * 1024 * 1024)
        app.router.add_post('/api/upload/file', api_upload_file)
        async with TestClient(TestServer(app)) as client:
            async def upload(data, name='photo.png'):
                form = FormData()
                form.add_field('file', data, filename=name, content_type='image/png')
                response = await client.post('/api/upload/file', data=form)
                return response.status, await response.json()

            data = b'Ordinary uploaded material.\n'
            status, answer = await upload(data)
            assert status == 200
            stored = Path(answer['paths'][0])
            assert stored.read_bytes() == data and stored.stat().st_mode & 0o777 == 0o600
            stored.unlink()
            for bad in (b'rm -rf / --no-preserve-root\n', 'safe\u202ereversed\u202c'.encode(),
                        b'x' * (intake.SCAN_WINDOW + 50) + b'\nrm -rf /\n'):
                status, answer = await upload(bad)
                assert status == 422 and answer['error']['code'] == 'upload_content_refused'
                assert not list((tmp_path / 'home' / 'uploads').iterdir())
            status, answer = await upload(b'')
            assert status == 400 and answer['error']['code'] == 'upload_empty'
            monkeypatch.setenv('GIDEON_UPLOAD_LIMIT_IMAGE', '10')
            status, answer = await upload(b'eleven bytes')
            assert status == 413 and answer['error']['code'] == 'upload_too_large'
            monkeypatch.delenv('GIDEON_UPLOAD_LIMIT_IMAGE')
            # Missing installed scanner is an actual failed child import, never a safe verdict.
            monkeypatch.setattr(intake, 'CHILD_MODULE', 'gideon.workspace.uploads.missing_scanner_dependency')
            status, answer = await upload(data)
            assert status == 503 and answer['error']['code'] == 'upload_content_unchecked'
            assert not list((tmp_path / 'home' / 'uploads').iterdir())
            assert not list(tmp_path.glob('gideon-intake-*'))
    asyncio.run(check())


def test_snapshot_owned_readonly_and_text_scan(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

    async def check():
        original = bytearray(b'Original bytes, unchanged after approval.')
        async def chunks():
            yield original
        approved = await intake.approve_stream(chunks(), 'readme.txt', surface='test')
        original[:] = b'Mutated caller buffer after scanning.'
        try:
            with pytest.raises(OSError):
                os.write(approved._fd, b'unscanned')
            dest = tmp_path / 'kept.txt'
            await approved.persist(dest)
            assert approved.digest == hashlib.sha256(dest.read_bytes()).hexdigest()
            assert dest.read_bytes() == b'Original bytes, unchanged after approval.'
        finally:
            approved.close()
        text = await intake.approve_text('Ordinary external note', surface='test')
        assert text.text == 'Ordinary external note'
        with pytest.raises(intake.IntakeRefused) as refused:
            await intake.approve_text('\0safe\u202eevil\u202c', surface='test')
        assert refused.value.code == 'upload_content_refused'
    asyncio.run(check())


def test_actual_scan_child_cancel_reaps_and_cleans_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

    async def check():
        started = asyncio.Event()
        children = []
        original_spawn = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            started.set()
            return child
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        async def chunks():
            yield b'ordinary text\n' * 20000
        task = asyncio.create_task(intake.approve_stream(chunks(), 'readme.txt', surface='test'))
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(children[0].pid, 0)
        assert not list(tmp_path.glob('gideon-intake-*'))
        assert not (tmp_path / 'home' / 'uploads').exists()
    asyncio.run(check())
