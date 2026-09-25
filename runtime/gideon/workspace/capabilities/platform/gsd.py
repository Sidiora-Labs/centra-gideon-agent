"""Read and edit project planning artifacts and request explicit phase work."""
import asyncio
import hashlib
from pathlib import Path
from gideon.core.atomic_write import atomic_write
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.native import NativeTaskProvider

_DOCUMENTS = {'PROJECT.md', 'ROADMAP.md', 'STATE.md', 'CONCERNS.md', 'RETROSPECTIVE.md', 'MILESTONES.md', 'REQUIREMENTS.md'}
_lock = asyncio.Lock()


class GsdError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _project(identifier):
    project = HierarchyStore().get_project(identifier)
    if project is None:
        raise GsdError('Unknown project', 404)
    if not project.workspace_dir:
        raise GsdError('Project has no workspace directory', 409)
    root = Path(project.workspace_dir).resolve()
    planning = (root / '.planning').resolve()
    if not planning.is_relative_to(root) or not planning.is_dir():
        raise GsdError('Project planning directory is unavailable', 409)
    return project, planning


def _document(planning, name):
    if not isinstance(name, str):
        raise GsdError('Invalid planning document')
    relative = Path(name)
    allowed = name in _DOCUMENTS or (len(relative.parts) == 3 and relative.parts[0] == 'phases' and relative.suffix == '.md')
    allowed = allowed and not relative.is_absolute() and '..' not in relative.parts
    target = (planning / relative).resolve()
    if not allowed or not target.is_relative_to(planning):
        raise GsdError('Document is outside supported planning artifacts')
    if not target.is_file():
        raise GsdError('Planning document not found', 404)
    if target.stat().st_size > 262144:
        raise GsdError('Planning document exceeds 256 KiB', 413)
    raw = target.read_bytes()
    return target, {'name': name, 'content': raw.decode('utf-8'), 'sha256': hashlib.sha256(raw).hexdigest()}


def projects():
    return {'version': 1, 'projects': [{'id': project.id, 'name': project.name} for project in HierarchyStore().list_projects() if project.workspace_dir]}


def inspect(identifier, document=None):
    project, planning = _project(identifier)
    names = sorted(name for name in _DOCUMENTS if (planning / name).is_file())
    phases = []
    phase_root = planning / 'phases'
    if phase_root.is_dir():
        if not phase_root.resolve().is_relative_to(planning):
            raise GsdError('Phase directory leaves the project')
        directories = sorted(path for path in phase_root.iterdir() if path.is_dir())
        if len(directories) > 100:
            raise GsdError('Project exceeds 100 phase directories', 413)
        for directory in directories:
            if not directory.resolve().is_relative_to(planning):
                raise GsdError('Phase directory leaves the project')
            documents = sorted(str(path.relative_to(planning)) for path in directory.glob('*.md'))
            names.extend(documents)
            phases.append({'id': directory.name, 'plans': [name for name in documents if name.endswith('PLAN.md')], 'summaries': [name for name in documents if name.endswith('SUMMARY.md')]})
    if len(names) > 300:
        raise GsdError('Project exceeds 300 planning documents', 413)
    for name in names:
        _document(planning, name)
    return {'version': 1, 'project': {'id': project.id, 'name': project.name}, 'documents': names, 'phases': phases,
            'document': _document(planning, document)[1] if document else None}


async def edit(identifier, body):
    if not isinstance(body, dict) or set(body) != {'document', 'sha256', 'content'} or not isinstance(body['content'], str):
        raise GsdError('Document, observed SHA and text content are required')
    if len(body['content'].encode()) > 262144:
        raise GsdError('Planning document exceeds 256 KiB', 413)
    async with _lock:
        _, planning = _project(identifier)
        target, observed = _document(planning, body['document'])
        if body['sha256'] != observed['sha256']:
            raise GsdError('Document changed; reload before saving', 409)
        atomic_write(target, body['content'])
        return inspect(identifier, body['document'])


async def request_phase(identifier, body, actor):
    if not actor:
        raise GsdError('Authenticated phase requester required', 403)
    if not isinstance(body, dict) or set(body) != {'phase', 'action'} or body['action'] not in {'plan', 'execute', 'verify'}:
        raise GsdError('Phase and plan, execute or verify action are required')
    current = inspect(identifier)
    if body['phase'] not in {phase['id'] for phase in current['phases']}:
        raise GsdError('Unknown planning phase', 404)
    label = 'gsd-' + hashlib.sha256(f"{identifier}:{body['phase']}:{body['action']}".encode()).hexdigest()[:20]
    async with _lock:
        store = HierarchyStore()
        provider = NativeTaskProvider()
        lists = store.list_task_lists(project_id=identifier)
        task_list = next((row for row in lists if row.name == 'GSD'), None) or store.create_task_list('GSD', project_id=identifier)
        tasks, total = await provider.list_tasks(project=current['project']['name'], limit=500)
        if total > 500:
            raise GsdError('Project task list exceeds bounded phase request search', 409)
        existing = next((task for task in tasks if label in task.labels and task.status.value not in {'done', 'cancelled'}), None)
        task = existing or await provider.create_task(title=f"GSD {body['action']}: {body['phase']}", description=f"Explicit request to {body['action']} planning phase {body['phase']}. Read the project .planning artifacts before acting. This task records a request, not completed execution.", task_list_id=task_list.id, author=actor, labels=['gsd', label])
        return {'task': task.to_dict(), 'created': existing is None, 'execution': 'not_started_by_this_request'}
