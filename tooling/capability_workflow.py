#!/usr/bin/env python3
"""Run declared capability task checks and retain exact local evidence."""
from __future__ import annotations
import argparse
import ast
import hashlib
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

TASK = re.compile(r"[a-z]+\.[0-9]{2}")
CODE = {'.py', '.ts', '.tsx', '.js', '.jsx'}


def load_task(root: Path, task_id: str) -> tuple[dict, dict]:
    if TASK.fullmatch(task_id) is None:
        raise ValueError('invalid task identifier')
    document = json.loads((root/'spec/capability-expansion/backlog.json').read_text())
    rows = [row for row in document['tasks'] if row['id'] == task_id]
    if len(rows) != 1:
        raise ValueError('task must exist exactly once')
    path = root/f'spec/capability-expansion/tasks/{task_id}.json'
    detail = json.loads(path.read_text()) if path.exists() else {}
    return rows[0], detail


def owned_files(root: Path, task: dict, detail: dict) -> tuple[list[Path], list[Path]]:
    lane = task['owner_lane']
    candidates = [root/f'runtime/gideon/workspace/capabilities/{lane}',
                  root/f'runtime/gideon/interfaces/dashboard/handlers/capabilities_{lane}.py',
                  root/f'apps/console/src/features/capabilities/{lane}',
                  root/f'checks/runtime/capabilities/{lane}']
    if 'production_files' in detail and 'test_files' in detail:
        candidates = []
    for key in ('production_files', 'test_files', 'extra_files'):
        for value in detail.get(key, []):
            path = Path(value)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('declared files must be repository-relative')
            candidates.append(root/path)
    found = set()
    for candidate in candidates:
        if candidate.is_dir(): found.update(p for p in candidate.rglob('*') if p.is_file())
        elif candidate.is_file(): found.add(candidate)
    prod, tests = [], []
    for path in sorted(found):
        if path.suffix not in CODE or '__pycache__' in path.parts: continue
        relative = path.relative_to(root)
        if path.is_symlink(): raise ValueError(f'code cannot be symlinked: {relative}')
        if 'checks' in relative.parts or path.name.startswith('test_') or '.test.' in path.name:
            tests.append(path)
        else: prod.append(path)
    return prod, tests


def executable_lines(text: str, suffix: str) -> set[int]:
    path = Path('source' + suffix)
    if path.suffix == '.py':
        tree = ast.parse(text, filename=str(path))
        ignored = set()
        for node in ast.walk(tree):
            body = getattr(node, 'body', None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    ignored.update(range(body[0].lineno, body[0].end_lineno+1))
        return {i for i,line in enumerate(text.splitlines(),1)
                if line.strip() and not line.lstrip().startswith('#') and i not in ignored}
    result, block = set(), False
    for number,line in enumerate(text.splitlines(),1):
        value = line.strip()
        if block:
            if '*/' not in value: continue
            value=value.split('*/',1)[1].strip();block=False
        while '/*' in value:
            before, after = value.split('/*',1)
            if '*/' in after: value=before+after.split('*/',1)[1]
            else: value=before;block=True;break
        if value and not value.startswith('//'):result.add(number)
    return result

def code_lines(path: Path) -> int:
    return len(executable_lines(path.read_text(), path.suffix))

def changed_lines(root: Path, path: Path, detail: dict) -> int:
    current=path.read_text()
    baseline=None
    relative=path.relative_to(root)
    if detail.get('baseline_dir'):
        base=Path(detail['baseline_dir'])
        if not base.is_dir(): raise ValueError('baseline_dir does not exist')
        previous=base/relative
        if previous.is_file(): baseline=previous.read_text()
    elif detail.get('base_revision'):
        revision=detail['base_revision']
        if re.fullmatch(r'[0-9a-f]{7,40}', revision) is None: raise ValueError('base_revision must be a commit hash')
        check=subprocess.run(['git','cat-file','-t',revision],cwd=root,capture_output=True,text=True)
        if check.returncode or check.stdout.strip()!='commit': raise ValueError('base_revision must resolve to a commit')
        result=subprocess.run(['git','show',f'{revision}:{relative}'],cwd=root,capture_output=True,text=True)
        if result.returncode==0: baseline=result.stdout
    lines=executable_lines(current,path.suffix)
    if baseline is None: return len(lines)
    changed=set()
    for tag,a,b,c,d in difflib.SequenceMatcher(None,baseline.splitlines(),current.splitlines(),autojunk=False).get_opcodes():
        if tag!='equal': changed.update(range(c+1,d+1))
    return len(lines & changed)


def inspect_task(root: Path, task_id: str) -> dict:
    task,detail=load_task(root,task_id)
    prod,tests=owned_files(root,task,detail)
    p=sum(changed_lines(root,path,detail) for path in prod);t=sum(changed_lines(root,path,detail) for path in tests)
    return {'task':task_id,'production_files':[str(x.relative_to(root)) for x in prod],
            'test_files':[str(x.relative_to(root)) for x in tests],
            'production_lines':p,'test_lines':t,'ratio':t/p if p else None,
            'ratio_pass':p>0 and t>=p,'detail':detail}


def run_command(command: list[str], root: Path, env: dict, log: Path) -> int:
    with log.open('w') as stream:
        result=subprocess.run(command,cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT)
    return result.returncode


def ui_config_args(root: Path, detail: dict) -> list[str]:
    value=detail.get('ui_config')
    if value is None: return []
    if not isinstance(value,str): raise ValueError('ui_config must be a repository-relative file')
    relative=Path(value)
    path=root/relative
    if relative.is_absolute() or '..' in relative.parts or not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('ui_config must be a repository-relative file')
    return ['--config',str(path)]


def typescript_compile_command(root: Path, paths: list[str], output: Path) -> list[str]:
    console = root/'apps/console'
    base_config = console/'tsconfig.json'
    if not base_config.is_file():
        raise ValueError('apps/console/tsconfig.json is required for declared TypeScript files')
    candidates = (console/'node_modules/typescript/bin/tsc', root/'node_modules/typescript/bin/tsc')
    compiler = next((path for path in candidates if path.is_file()), None)
    if compiler is None:
        raise ValueError('installed TypeScript compiler unavailable; dependencies were not modified')
    config = output/'tsconfig.capability.json'
    temporary = output/'tsconfig.capability.json.tmp'
    ambient = sorted((console/'src').rglob('*.d.ts'))
    files = [*ambient, *(root/path for path in paths)]
    document = {
        'extends': str(base_config),
        'compilerOptions': {
            'typeRoots': [str(console/'node_modules/@types'), str(root/'node_modules/@types')],
        },
        'files': list(dict.fromkeys(str(path) for path in files)),
        'include': [],
    }
    temporary.write_text(json.dumps(document, indent=2)+'\n')
    os.replace(temporary, config)
    return ['node', str(compiler), '--project', str(config), '--pretty', 'false']


def verify(root: Path, task_id: str, stage: str = "all") -> tuple[int,dict]:
    if stage not in {"all", "compile", "runtime", "ui"}:
        raise ValueError("invalid verification stage")
    info=inspect_task(root,task_id)
    output=Path(tempfile.mkdtemp(prefix=f'gideon-{task_id}-checks-'))
    evidence={'task':task_id,'root':str(root),'started_at':time.time(),
              'production_lines':info['production_lines'],'test_lines':info['test_lines'],
              'ratio':info['ratio'],'stage':stage,'commands':[],'status':'failed','evidence_dir':str(output)}
    if not info['ratio_pass']:
        evidence['error']='test executable lines must be at least production executable lines, with nonempty production'
        (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
        return 1,evidence
    env=os.environ.copy()
    env['PYTHONPATH']=str(root/'runtime')
    env['GIDEON_HOME']=str(output/'home')
    env['GIDEON_SKIP_APP_BACKENDS']='1'
    py=os.environ.get('GIDEON_TEST_PYTHON','/tmp/gideon-runtime-venv/bin/python')
    if not Path(py).exists():py=sys.executable
    env['GIDEON_TEST_PYTHON']=py
    python_paths=[value for value in info['production_files']+info['test_files'] if value.endswith('.py')]
    compile_cmd=[py,'-c','import ast,sys,pathlib; [ast.parse(pathlib.Path(p).read_text(),filename=p) for p in sys.argv[1:]]',*python_paths]
    commands=[('compile',compile_cmd)]
    typescript_paths=[value for value in info['production_files']+info['test_files']
                      if value.endswith(('.ts','.tsx')) and changed_lines(root,root/value,info['detail'])>0]
    if typescript_paths:
        try: commands.append(('compile_typescript',typescript_compile_command(root,typescript_paths,output)))
        except ValueError as exc:
            evidence['error']=str(exc)
            (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
            return 1,evidence
    runtime=info['detail'].get('runtime_test_files', [value for value in info['test_files'] if value.endswith('.py') and Path(value).name.startswith('test_')])
    ui=info['detail'].get('ui_test_files', [value for value in info['test_files'] if '.test.' in value and value.endswith(('.ts','.tsx','.js','.jsx'))])
    for value in runtime+ui:
        if value not in info['test_files']: raise ValueError('test selection must be declared in test_files')
    if not runtime and stage != 'ui':
        evidence['error']='real runtime behavior tests are required'
        (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
        return 1,evidence
    if runtime:
        commands.append(('runtime',[py,'-m','pytest',*[str(root/value) for value in runtime],'-q']))
    if any(value.endswith(('.tsx','.jsx')) for value in info['production_files']) and not ui:
        evidence['error']='UI behavior tests are required for UI implementation';return 1,evidence
    if ui and stage in {'all', 'ui'}:
        vitest=root/'node_modules/vitest/vitest.mjs'
        if not vitest.exists():
            evidence['error']='installed vitest unavailable; dependencies were not modified';return 1,evidence
        command=['node',str(vitest),'run',*ui_config_args(root,info['detail']),*[str(root/value) for value in ui]]
        commands.append(('ui',command))
    if stage != 'all':
        commands=[row for row in commands if row[0] == stage or row[0].startswith(stage+'_')]
        if not commands:
            evidence['error']='requested stage has no declared tests'
            (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
            return 1,evidence
    for name,command in commands:
        cwd=root/'apps/console' if name=='ui' else root
        log=output/f'{name}.log'
        code=run_command(command,cwd,env,log)
        evidence['commands'].append({'name':name,'argv':command,'exit_code':code,'log':str(log)})
        if code:
            evidence['error']=f'{name} failed';break
    else:evidence['status']='locally_qualified' if stage == 'all' else 'stage_qualified'
    evidence['completed_at']=time.time()
    evidence['files']={value:hashlib.sha256((root/value).read_bytes()).hexdigest() for value in info['production_files']+info['test_files']}
    (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return (0 if evidence['status'] in {'locally_qualified','stage_qualified'} else 1),evidence


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['inspect','verify','ready'])
    parser.add_argument('task',nargs='?')
    parser.add_argument('--stage',choices=['all','compile','runtime','ui'],default='all')
    parser.add_argument('--root',type=Path,default=Path.cwd())
    args=parser.parse_args(argv)
    root=args.root.resolve()
    try:
        if args.operation=='ready':
            doc=json.loads((root/'spec/capability-expansion/backlog.json').read_text())
            states={row['id']:row['status'] for row in doc['tasks']}
            result=[row['id'] for row in doc['tasks'] if row['status']=='pending' and all(states.get(dep)=='done' for dep in row['requires'])]
            code=0
        elif not args.task:raise ValueError('task identifier is required')
        elif args.operation=='inspect':result=inspect_task(root,args.task);code=0
        else:code,result=verify(root,args.task,args.stage)
        print(json.dumps(result,indent=2));return code
    except (ValueError,OSError,KeyError,SyntaxError,json.JSONDecodeError) as exc:
        print(json.dumps({'error':str(exc)}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
