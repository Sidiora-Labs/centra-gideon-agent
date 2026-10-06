import json
import os
import tarfile

import pytest

from gideon.workspace.snapshot import restore_apply


@pytest.mark.parametrize('kind', ['hard', 'symbolic'])
def test_actual_restore_refuses_linked_history_and_continues_safe_shard(tmp_path, monkeypatch, kind):
    home = tmp_path / 'home'
    history = home / 'cron-history'
    history.mkdir(parents=True)
    monkeypatch.setenv('GIDEON_HOME', str(home))
    outside = tmp_path / 'outside.jsonl'
    outside.write_text(json.dumps({'run_id': 'old'}) + '\n')
    blocked = history / 'a-blocked.jsonl'
    if kind == 'hard':
        os.link(outside, blocked)
    else:
        blocked.symlink_to(outside)
    safe = history / 'z-safe.jsonl'
    safe.write_text(json.dumps({'run_id': 'safe-old'}) + '\n')
    source = tmp_path / 'snapshot'
    (source / 'cron-history').mkdir(parents=True)
    (source / 'cron-history/a-blocked.jsonl').write_text(json.dumps({'run_id': 'new-blocked'}) + '\n')
    (source / 'cron-history/z-safe.jsonl').write_text(json.dumps({'run_id': 'safe-new'}) + '\n')
    archive = tmp_path / 'snapshot.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(source, arcname='snapshot')
    before = outside.read_bytes()
    result = restore_apply(archive, 'merge', ['crons'])
    assert result['ok'] is True
    assert result['partial'] is True
    assert any('a-blocked.jsonl' in item for item in result['left_unchanged'])
    assert outside.read_bytes() == before
    assert [json.loads(line)['run_id'] for line in safe.read_text().splitlines()] == ['safe-old', 'safe-new']
    if kind == 'hard':
        blocked.unlink()
    else:
        blocked.unlink()
    blocked.write_text(before.decode())
    result = restore_apply(archive, 'merge', ['crons'])
    assert result['partial'] is False
    assert [json.loads(line)['run_id'] for line in blocked.read_text().splitlines()] == ['old', 'new-blocked']
    assert [json.loads(line)['run_id'] for line in safe.read_text().splitlines()] == ['safe-old', 'safe-new']
