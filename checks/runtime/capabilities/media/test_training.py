import io
import json
import os
import signal
import struct
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker, identity
from gideon.workspace.capabilities.media.sketches import SketchStore, SketchError
from gideon.workspace.capabilities.media.training import DiffusersTrainer, run_process


@pytest.fixture
def jobs(tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / 'artifacts')
    sketches = SketchStore(tmp_path / 'capabilities/media/sketches.sqlite3', artifacts)
    return MediaJobs(tmp_path / 'capabilities/media/jobs.sqlite3', sketches)


def training_input(jobs):
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(output, 'PNG')
    artifact = jobs.sketches.artifacts.create_binary(name='Training source', data=output.getvalue(), mime='image/png')
    dataset = jobs.datasets.save({'title': 'Training set', 'base_model': 'owner/model', 'request_id': str(uuid4()), 'entries': [{'artifact_id': artifact.slug, 'version': 1, 'caption': 'Blue square'}]})
    return dict(dataset_id=dataset['id'], dataset_revision=1, steps=20, rank=4, learning_rate=.0001, seed=0)


def test_real_missing_trainer_is_explicit_and_durable_worker_never_succeeds(jobs):
    request = training_input(jobs)
    ready = jobs.trainer.readiness()
    assert ready['available'] is False
    assert ready['admitted'] is True
    assert ready['inference_verified'] is False
    assert 'installation missing' in ready['reason']
    assert str(jobs.trainer.home) not in json.dumps(ready)
    job = jobs.submit({'operation': 'lora_train', 'request_id': 'train', 'input': request})
    assert job['status'] == 'queued'
    assert job['input']['base_model'] == 'owner/model'
    assert job['input']['dataset_revision'] == 1
    MediaWorker(jobs).run_once(WorkerContext('gideon-media', 'default', WorkerControl()))
    failed = jobs.get(job['id'])
    assert failed['status'] == 'failed'
    assert failed['attempt'] == 1
    assert failed['result'] is None
    assert 'installation missing' in failed['error']
    assert jobs.trainer.checkpoints(job['id'])['items'] == []
    assert not jobs.trainer.runs.exists()
    retry = jobs.retry(job['id'], {'state_revision': failed['state_revision']})
    assert retry['status'] == 'queued'
    assert retry['input'] == job['input']
    assert retry['events'][-2]['error'] == failed['error']


def test_operator_admission_is_checked_on_both_submit_and_existing_job_execution(jobs):
    request = training_input(jobs)
    queued = jobs.submit({'operation': 'lora_train', 'request_id': 'before-revoke', 'input': request})
    restricted = MediaJobs(jobs.path, jobs.sketches, training_allowed=False)
    ready = restricted.trainer.readiness()
    assert ready['admitted'] is False
    assert ready['available'] is False
    assert ready['reason'] == 'Operator training grant unavailable'
    with pytest.raises(SketchError) as denied:
        restricted.submit({'operation': 'lora_train', 'request_id': 'after-revoke', 'input': request})
    assert denied.value.status == 503
    assert len(jobs.list()['items']) == 1
    MediaWorker(restricted).run_once(WorkerContext('gideon-media', 'default', WorkerControl()))
    failed = jobs.get(queued['id'])
    assert failed['status'] == 'failed'
    assert failed['error'] == 'Operator training grant unavailable'
    assert failed['result'] is None
    assert not restricted.trainer.runs.exists()


@pytest.mark.parametrize('key,value', [('steps', 0), ('steps', 50001), ('rank', 0), ('rank', 129), ('seed', True), ('seed', -1), ('learning_rate', 0), ('learning_rate', float('nan')), ('learning_rate', .1), ('learning_rate', True), ('dataset_revision', 99)])
def test_invalid_training_parameters_do_not_queue(jobs, key, value):
    request = training_input(jobs)
    with pytest.raises(SketchError):
        jobs.submit({'operation': 'lora_train', 'request_id': 'invalid', 'input': dict(request, **{key: value})})
    assert jobs.list()['items'] == []


def test_documented_diffusers_command_has_owned_paths_and_preserves_resume(jobs, tmp_path):
    request = jobs.trainer.prepare(training_input(jobs))
    job_id = str(uuid4())
    output = jobs.trainer.output(job_id)
    data = tmp_path / 'staged-data'
    command = jobs.trainer.command(request, data, output)
    assert command[:3] == [str(jobs.trainer.python), '-m', 'accelerate.commands.launch']
    assert command[3] == str(jobs.trainer.script)
    assert '--pretrained_model_name_or_path=owner/model' in command
    assert '--train_data_dir='+str(data) in command
    assert '--output_dir='+str(output) in command
    assert '--max_train_steps=20' in command
    assert '--rank=4' in command
    assert '--learning_rate=0.0001' in command
    assert '--seed=0' in command
    assert '--checkpointing_steps=20' in command
    assert '--report_to=none' in command
    assert '--resume_from_checkpoint=latest' not in command
    (output / 'checkpoint-20').mkdir(parents=True)
    (output / 'checkpoint-3').mkdir()
    (output / 'checkpoint-invalid').mkdir()
    checkpoints = jobs.trainer.checkpoints(job_id)['items']
    assert [item['step'] for item in checkpoints] == [3, 20]
    assert all(item['resume_verified'] is False for item in checkpoints)
    assert '--resume_from_checkpoint=latest' in jobs.trainer.command(request, data, output)
    assert '--push_to_hub' not in command
    assert not any(argument.startswith('--hub_token') for argument in command)


def test_training_paths_reject_caller_escape_and_symlink_runs(jobs, tmp_path):
    for job_id in ('../escape', '/tmp/escape', '', 'not-a-uuid'):
        with pytest.raises(SketchError):
            jobs.trainer.output(job_id)
    outside = tmp_path / 'outside'
    outside.mkdir()
    jobs.trainer.runs.parent.mkdir(parents=True)
    jobs.trainer.runs.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SketchError):
        jobs.trainer.output(str(uuid4()))
    assert list(outside.iterdir()) == []


def test_real_child_process_runner_captures_exit_and_owned_identity(jobs, tmp_path):
    request = training_input(jobs)
    job = jobs.submit({'operation': 'lora_train', 'request_id': 'child-lifecycle', 'input': request})
    jobs.claim()
    log = tmp_path / 'actual-child.log'
    run_process([sys.executable, '-c', 'print("actual child completed")'], log,
                lambda: jobs.get(job['id'])['status'] == 'cancel_requested', lambda pid: jobs.attach_child(job['id'], pid))
    assert log.read_text().strip() == 'actual child completed'
    internal = jobs.get(job['id'], internal=True)
    assert internal['child_pid']
    assert internal['child_identity']
    assert identity(internal['child_pid']) is None
    public = jobs.get(job['id'])
    assert 'child_pid' not in public
    assert 'child_identity' not in public
    jobs.finish(job['id'], error='Lifecycle-only child test; no training performed')
    assert jobs.get(job['id'])['status'] == 'failed'


def test_real_child_cancellation_terminates_group_and_retains_diagnostics(jobs, tmp_path):
    request = training_input(jobs)
    job = jobs.submit({'operation': 'lora_train', 'request_id': 'cancel-child', 'input': request})
    running = jobs.claim()
    jobs.cancel(job['id'], {'state_revision': running['state_revision']})
    log = tmp_path / 'cancelled-child.log'
    with pytest.raises(SketchError, match='interrupted'):
        run_process([sys.executable, '-c', 'import time; time.sleep(20)'], log,
                    lambda: jobs.get(job['id'])['status'] == 'cancel_requested', lambda pid: jobs.attach_child(job['id'], pid))
    internal = jobs.get(job['id'], internal=True)
    assert identity(internal['child_pid']) is None
    assert log.exists()
    finished = jobs.finish(job['id'], error='Cancelled before training')
    assert finished['status'] == 'cancelled'
    assert finished['result'] is None
    assert finished['events'][-1]['error'] == 'Cancelled before training'


def test_actual_nonzero_child_is_not_reported_as_training_success(jobs, tmp_path):
    with pytest.raises(SketchError, match='process failed'):
        run_process([sys.executable, '-c', 'raise SystemExit(7)'], tmp_path / 'failure.log', lambda: False, lambda pid: None)
    assert (tmp_path / 'failure.log').exists()


def test_structural_adapter_publication_preserves_payload_and_adds_provenance(jobs, tmp_path):
    job_id = str(uuid4())
    output = jobs.trainer.output(job_id)
    output.mkdir(parents=True)
    header = {'lora_A.weight': {'dtype': 'F32', 'shape': [1, 1], 'data_offsets': [0, 4]}, 'lora_B.weight': {'dtype': 'F32', 'shape': [1, 1], 'data_offsets': [4, 8]}}
    encoded = json.dumps(header).encode()
    payload = struct.pack('<ff', 1, 2)
    source = output / 'pytorch_lora_weights.safetensors'
    source.write_bytes(len(encoded).to_bytes(8, 'little')+encoded+payload)
    result = jobs.trainer.publish(source, job_id, 'owner/model')
    assert result['id'] == f'trained-{job_id}.safetensors'
    assert result['base_model'] == 'owner/model'
    assert result['compatibility'] == 'metadata_match'
    assert result['effect_verified'] is False
    assert result['tensor_count'] == 2
    assert not source.exists()
    installed = jobs.trainer.home / 'models/loras' / result['id']
    data = installed.read_bytes()
    length = int.from_bytes(data[:8], 'little')
    updated = json.loads(data[8:8+length])
    assert updated['__metadata__']['training_job_id'] == job_id
    assert updated['__metadata__']['base_model'] == 'owner/model'
    assert data[8+length:] == payload
    assert jobs.trainer.checkpoints(job_id)['items'] == []
