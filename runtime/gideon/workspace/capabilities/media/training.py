import hashlib
import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .loras import LoraCatalog
from .sketches import SketchError, fields, integer


def run_process(command, log_path, stopped, attached):
    with open(log_path, 'ab') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            attached(process.pid)
            deadline = time.monotonic() + 21600
            while process.poll() is None:
                if stopped() or time.monotonic() >= deadline:
                    raise SketchError('Training interrupted; completed checkpoints are retained', 409)
                try:
                    process.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                raise SketchError('Training process failed; inspect its local diagnostics', 503)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


class DiffusersTrainer:
    def __init__(self, home, datasets, allowed=True):
        self.home, self.datasets, self.allowed = Path(home), datasets, allowed
        self.runtime = self.home / 'models/trainers/diffusers'
        self.python = self.runtime / '.venv/bin/python'
        self.script = self.runtime / 'examples/text_to_image/train_text_to_image_lora.py'
        self.runs = self.home / 'models/lora-training'

    def readiness(self):
        present = self.python.is_file() and os.access(self.python, os.X_OK) and self.script.is_file()
        return dict(available=bool(self.allowed and present), admitted=self.allowed, inference_verified=False,
                    reason='Operator training grant unavailable' if not self.allowed else 'Diffusers trainer installation missing' if not present else 'Trainer files present; dependencies, GPU and training not verified')

    def prepare(self, body):
        if not self.allowed:
            raise SketchError('Operator training grant unavailable', 503)
        fields(body, ('dataset_id', 'dataset_revision', 'steps', 'rank', 'learning_rate', 'seed'), ('dataset_id', 'dataset_revision', 'steps', 'rank', 'learning_rate', 'seed'))
        integer(body['steps'], 1, 50000)
        integer(body['rank'], 1, 128)
        integer(body['seed'], 0, 4294967295)
        rate = body['learning_rate']
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 < rate <= .01:
            raise SketchError('Learning rate must be positive and at most 0.01')
        document = self.datasets.get(body['dataset_id'], body['dataset_revision'])
        return dict(body, base_model=document['base_model'])

    def output(self, job_id):
        from uuid import UUID
        try:
            if str(UUID(job_id)) != job_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as exc:
            raise SketchError('Invalid training job ID') from exc
        target = self.runs / job_id
        if self.runs.resolve() != self.runs.absolute() or target.is_symlink():
            raise SketchError('Training paths must not contain symbolic links')
        return target

    def checkpoints(self, job_id):
        root = self.output(job_id)
        items = []
        if root.exists():
            for entry in root.glob('checkpoint-*'):
                suffix = entry.name.removeprefix('checkpoint-')
                if entry.is_dir() and not entry.is_symlink() and suffix.isdigit():
                    items.append({'name': entry.name, 'step': int(suffix), 'resume_verified': False})
        return {'items': sorted(items, key=lambda item: item['step'])}

    def command(self, request, data_dir, output_dir):
        command = [str(self.python), '-m', 'accelerate.commands.launch', str(self.script),
                   '--pretrained_model_name_or_path='+request['base_model'], '--train_data_dir='+str(data_dir),
                   '--output_dir='+str(output_dir), '--resolution=512', '--center_crop', '--train_batch_size=1',
                   '--max_train_steps='+str(request['steps']), '--rank='+str(request['rank']),
                   '--learning_rate='+str(request['learning_rate']), '--seed='+str(request['seed']),
                   '--checkpointing_steps='+str(min(100, request['steps'])), '--report_to=none']
        if self.checkpoints(output_dir.name)['items']:
            command.append('--resume_from_checkpoint=latest')
        return command

    def publish(self, source, job_id, base_model):
        LoraCatalog(source.parent).get(source.name)
        root = self.home / 'models/loras'
        if root.resolve() != root.absolute():
            raise SketchError('Adapter destination must not contain symbolic links')
        root.mkdir(parents=True, exist_ok=True)
        target = root / f'trained-{job_id}.safetensors'
        with source.open('rb') as original:
            length = int.from_bytes(original.read(8), 'little')
            header = json.loads(original.read(length))
            header.setdefault('__metadata__', {}).update(base_model=base_model, training_job_id=job_id)
            encoded = json.dumps(header).encode()
            encoded += b' ' * (-len(encoded) % 8)
            temporary = target.with_suffix('.part')
            with temporary.open('xb') as output:
                output.write(len(encoded).to_bytes(8, 'little'))
                output.write(encoded)
                while chunk := original.read(1024 * 1024):
                    output.write(chunk)
            os.replace(temporary, target)
        source.unlink()
        return LoraCatalog(root).get(target.name, base_model)

    def execute(self, request, job_id, stopped, attached):
        if not self.readiness()['available']:
            raise SketchError(self.readiness()['reason'], 503)
        if self.script.resolve() != self.script.absolute() or self.runtime.resolve() != self.runtime.absolute():
            raise SketchError('Trainer script must use its trusted installation path')
        output = self.output(job_id)
        output.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='gideon-training-data-') as directory:
            data = Path(directory)
            self.datasets.stage(request['dataset_id'], request['dataset_revision'], data)
            run_process(self.command(request, data, output), output / 'training.log', stopped, attached)
        weights = output / 'pytorch_lora_weights.safetensors'
        if not weights.is_file() or weights.is_symlink():
            raise SketchError('Trainer exited without a valid adapter checkpoint')
        adapter = self.publish(weights, job_id, request['base_model'])
        return dict(adapter_id=adapter['id'], sha256=adapter['sha256'], dataset_id=request['dataset_id'], dataset_revision=request['dataset_revision'], checkpoints=self.checkpoints(job_id)['items'], effect_verified=False)
