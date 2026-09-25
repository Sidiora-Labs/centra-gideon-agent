"""Managed adapter lifecycle over the authoritative runner catalog."""
import asyncio
import json
import re
import shutil
from pathlib import Path
from gideon.core.atomic_write import atomic_write
from gideon.engine.agents import runners
from gideon.integrations.llm.registry import get_default_registry

_lock = asyncio.Lock()


class HarnessError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def dependents(runner):
    binaries = set()
    if runner.adapter:
        binaries = {str((runners.managed_adapter_prefix() / 'node_modules/.bin' / name).resolve()) for name in runner.adapter.bin_names}
    result = []
    for entry in get_default_registry().list_entries():
        command = entry.options.get('command') or []
        executable = str(Path(command[0]).resolve()) if isinstance(command, list) and command and isinstance(command[0], str) else None
        if entry.name == runner.runtime_id or executable in binaries:
            result.append(entry.name)
    return result


def inventory():
    rows = []
    for key, runner in runners.catalog().items():
        pin = runner.adapter
        facts = runners.installed_adapter_facts(pin.npm_pkg) if pin else {}
        rows.append({'id': key, 'name': runner.display_name, 'runtime_id': runner.runtime_id,
                     'package': pin.npm_pkg if pin else None, 'installed': facts or None,
                     'latest': None, 'authentication': 'unchecked',
                     'dependents': dependents(runner),
                     'lifecycle': bool(pin)})
    return {'version': 1, 'harnesses': rows}


async def _npm(*args):
    executable = shutil.which('npm')
    if not executable:
        raise HarnessError('npm is unavailable', 503)
    process = await asyncio.create_subprocess_exec(executable, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise HarnessError('Adapter operation did not complete', 503)
    if process.returncode:
        raise HarnessError('Package manager rejected the operation', 502)
    return stdout.decode()


async def change(identifier, body):
    if not isinstance(body, dict) or set(body) - {'action', 'version', 'expected_version'}:
        raise HarnessError('Invalid lifecycle request')
    action = body.get('action')
    if action not in {'install', 'update', 'remove'}:
        raise HarnessError('Expected install, update or remove')
    runner = runners.catalog().get(identifier)
    if runner is None:
        raise HarnessError('Unknown harness', 404)
    pin = runner.adapter
    if not pin:
        raise HarnessError('This harness has no managed adapter lifecycle', 409)
    version = body.get('version')
    if action != 'remove' and (not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version)):
        raise HarnessError('An exact package version is required')
    async with _lock:
        facts = runners.installed_adapter_facts(pin.npm_pkg)
        if body.get('expected_version') != facts.get('version'):
            raise HarnessError('Adapter changed; refresh before continuing', 409)
        if action == 'install' and facts or action in {'update', 'remove'} and not facts:
            raise HarnessError('Operation does not match installed state', 409)
        if dependents(runner):
            raise HarnessError('Disable the dependent runtime before changing its adapter', 409)
        prefix = runners.managed_adapter_prefix()
        prefix.mkdir(parents=True, exist_ok=True)
        if action == 'remove':
            await _npm('uninstall', '--prefix', str(prefix), '--ignore-scripts', '--no-audit', '--no-fund', pin.npm_pkg)
            ledger = runners._read_lock()
            ledger.pop(pin.npm_pkg, None)
            atomic_write(runners.adapter_lock_path(), json.dumps(ledger) + '\n')
        else:
            metadata = json.loads(await _npm('view', f'{pin.npm_pkg}@{version}', 'version', 'dist.integrity', '--json'))
            integrity = metadata.get('dist.integrity', '')
            if metadata.get('version') != version or not integrity.startswith('sha512-'):
                raise HarnessError('Registry did not provide exact package integrity', 502)
            await _npm('install', '--prefix', str(prefix), '--save-exact', '--ignore-scripts', '--no-audit', '--no-fund', f'{pin.npm_pkg}@{version}')
            if not runners.record_provenance(pin.npm_pkg, pin=runners.AdapterPin(npm_pkg=pin.npm_pkg, version=version, integrity=integrity)):
                raise HarnessError('Installed package did not match the resolved integrity', 502)
        return inventory()
