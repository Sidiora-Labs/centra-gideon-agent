"""Report task records and synchronize the capability specification statuses."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import tempfile


QUALIFIED = frozenset({'locally_qualified', 'implemented_locally_qualified', 'locally_qualified_scoped_paths'})


def task_state(record: dict | None) -> str:
    if record is None:
        return 'pending'
    status = record.get('status', 'in_progress')
    if status in QUALIFIED:
        return 'done'
    if status in {'blocked', 'dependency_blocked'}:
        return 'blocked'
    return 'in_progress'


def progress(root: Path) -> tuple[dict, dict]:
    directory = root / 'spec/capability-expansion'
    backlog = json.loads((directory / 'backlog.json').read_text())
    tasks = backlog['tasks']
    identifiers = [task['id'] for task in tasks]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Duplicate backlog task identifiers')
    records = []
    for task in tasks:
        identifier = task['id']
        if '/' in identifier or '\\' in identifier or identifier in {'.', '..'}:
            raise ValueError('Invalid task identifier')
        relative = f'spec/capability-expansion/tasks/{identifier}.json'
        path = root / relative
        record = json.loads(path.read_text()) if path.exists() else None
        identity = record.get('id', record.get('task')) if record else identifier
        if record is not None and identity != identifier:
            raise ValueError(f'Task record identifier mismatch: {identifier}')
        state = task_state(record)
        task['status'] = state
        task['implementation_status'] = record.get('status', 'in_progress') if record else 'not_started'
        task['evidence_record'] = relative if record else None
        records.append({key: task[key] for key in ('id', 'title', 'status', 'implementation_status', 'evidence_record')})
    counts = Counter(task['status'] for task in tasks)
    report = {'product': backlog['product'], 'total': len(tasks),
              'counts': {state: counts[state] for state in ('done', 'in_progress', 'blocked', 'pending')},
              'definition': 'Done means a task record explicitly reports local qualification; external-pending and partial records are excluded. Publication and deployment are separate.',
              'tasks': records}
    return backlog, report


def atomic_write(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def synchronize(root: Path, backlog: dict) -> None:
    directory = root / 'spec/capability-expansion'
    lines = ['[meta]', 'feature = "capability-expansion"', 'status = "in_progress"',
             'source = "spec/capability-expansion/SPEC.md"', 'backlog = "spec/capability-expansion/backlog.json"']
    for task in backlog['tasks']:
        lines.extend(['', f'[task.{task["id"]}]'])
        for key in ('title', 'wave', 'owner_lane', 'status', 'requires', 'verify_cmd'):
            lines.append(f'{key} = {json.dumps(task[key], ensure_ascii=False)}')
        lines.append(f'implementation_status = {json.dumps(task["implementation_status"])}')
    atomic_write(directory / 'backlog.json', json.dumps(backlog, indent=2, ensure_ascii=False) + '\n')
    atomic_write(directory / 'spec.kvx', '\n'.join(lines) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--write', action='store_true', help='Synchronize backlog and specification from existing task records')
    args = parser.parse_args()
    backlog, report = progress(args.root)
    if args.write:
        synchronize(args.root, backlog)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
