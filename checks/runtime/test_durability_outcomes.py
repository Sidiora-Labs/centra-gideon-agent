"""Durable outcome records, retry fingerprints and notification delivery."""
from gideon.operations.durability import service, shards


def test_failure_skip_and_recovery_keep_last_success(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    events = []
    def notify(*args, **kwargs):
        events.append(args)
    service.persist_job_result(service.JobResult('incremental_export', ok=True), at=10, notifier=notify)
    failure = service.JobResult('incremental_export', ok=False, detail='permission denied', extra={'failure':'unwritable','folder':str(tmp_path)})
    service.persist_job_result(failure, at=20, notifier=notify)
    service.persist_job_result(failure, at=30, notifier=notify)
    status = service.backup_status(now=30)['export']
    assert status['last_run'] == 30 and status['last_success'] == 10
    assert status['ok'] is False and status['problem']['failures'] == 2
    assert len(events) == 1
    service.persist_job_result(service.JobResult('incremental_export', skipped='busy'), at=40, notifier=notify)
    assert service.backup_status(now=40)['export']['last_run'] == 30
    service.persist_job_result(service.JobResult('incremental_export', ok=True), at=50, notifier=notify)
    assert len(events) == 2 and events[-1][1] == 'Export is working again'
    assert service.backup_status(now=50)['export']['problem'] is None


def test_sync_skip_retains_failure_and_drill_notifies_once(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    state = {}
    events = []
    def notify(*args, **kwargs):
        events.append(args)
    service.record_job(service.JobResult('sync', ok=False, extra={'failure':'pull','reason':'offline'}),state,at=10,notifier=notify)
    service.record_job(service.JobResult('sync',skipped='disabled'),state,at=20,notifier=notify)
    assert state['last_sync'] == 20 and state['last_sync_run'] == 10
    assert state['last_sync_ok'] is False
    service.record_job(service.JobResult('restore_drill',ok=True,detail='verified',extra={'snapshot':'archive.tar.gz'}),state,at=30,notifier=notify)
    assert [event[1] for event in events] == ['Sync failed','Backup restore drill passed']


def test_changed_fingerprints_wait_for_successful_export(tmp_path):
    (tmp_path/'config.json').write_text('{"timezone":"UTC"}')
    state = tmp_path/'export_state.json'
    changes = shards.dirty_entries(tmp_path,state)
    assert 'config' in changes and not state.exists()
    assert 'config' in shards.dirty_entries(tmp_path,state)
    shards.mark_exported(state,changes,skipped=('config/unreadable',))
    assert 'config' in shards.dirty_entries(tmp_path,state)
    shards.mark_exported(state,changes)
    assert 'config' not in shards.dirty_entries(tmp_path,state)


def test_problem_reason_redacted_bounded_and_changed_reason_notifies(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path))
    state = {}
    events = []
    def notify(*args, **kwargs):
        events.append(args)
    for at, reason in ((10,'first'),(20,'second')):
        service.record_job(service.JobResult('sync',ok=False,extra={'failure':'pull','reason':reason}),state,at=at,notifier=notify)
    assert len(events) == 2
    quoted = service._quoted('x'*500+'\nrest')
    assert len(quoted) == 240 and '\n' not in quoted


def test_problem_redacts_url_logins():
    text = service._quoted('https://owner:secretpass@example.test/storage')
    assert 'secretpass' not in text


def test_export_reports_unreadable_json_and_keeps_store_dirty(tmp_path):
    (tmp_path/'config.json').write_text('{invalid')
    state = tmp_path/'export_state.json'
    changes = shards.dirty_entries(tmp_path,state)
    result = shards.export_shards(tmp_path,tmp_path/'shards',entries=['config'])
    assert result.skipped.get('config') == 'JSON file unreadable'
    shards.mark_exported(state,changes,skipped=result.skipped)
    assert 'config' in shards.dirty_entries(tmp_path,state)
