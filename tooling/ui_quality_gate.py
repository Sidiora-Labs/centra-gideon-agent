#!/usr/bin/env python3
"""Evaluate recorded task evidence without manufacturing test or coverage results."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

def read_json(path):
    return json.loads(Path(path).read_text())

def useful_lines(text):
    count = 0
    for line in text.splitlines():
        value=line.strip()
        if value and not value.startswith(('#','//','/*','*','<!--')):
            count+=1
    return count

def compare_lines(production, tests):
    prod=sum(useful_lines(Path(p).read_text()) for p in production)
    test=sum(useful_lines(Path(p).read_text()) for p in tests)
    if prod and test < prod:
        raise ValueError(f'Test/code ratio {test}/{prod} is below 1:1')
    return {'production_lines':prod,'test_lines':test,'ratio':test/prod if prod else None}

def coverage_files(report, production):
    data=read_json(report)
    files=data.get('files',data)
    missing=[]
    for name in production:
        path=Path(name).resolve()
        matches=[value for key,value in files.items() if Path(key).resolve()==path or key==str(path.relative_to(ROOT))]
        if not matches:
            missing.append(str(path));continue
        value=matches[0]
        if 's' in value:
            if any(hit==0 for hit in value.get('s',{}).values()):missing.append(str(path)+': statements')
            if any(hit==0 for hit in value.get('f',{}).values()):missing.append(str(path)+': functions')
            if any(hit==0 for hits in value.get('b',{}).values() for hit in hits):missing.append(str(path)+': branches')
        elif 'summary' in value:
            summary=value['summary']
            if summary.get('missing_lines',0) or summary.get('missing_branches',0):missing.append(str(path)+': lines/branches')
        else:
            raise ValueError('Unsupported coverage record: '+str(path))
    if missing:raise ValueError('Incomplete executable coverage: '+', '.join(missing))
    return {'files':len(production),'complete':True}

def task_gate(task, evidence_dir=None):
    folder=Path(evidence_dir) if evidence_dir else ROOT/'spec/gideon-ui-rebuild/evidence'
    path=folder/(task+'.json')
    if not path.exists():raise ValueError('Missing real task evidence: '+str(path))
    evidence=read_json(path)
    for key in ['revision','production_files','test_files','coverage_report','checks']:
        if key not in evidence:raise ValueError('Missing evidence field '+key)
    revision=evidence['revision']
    if not revision:raise ValueError('Evidence revision is empty')
    subprocess.run(['git','cat-file','-e',revision+'^{tree}'],cwd=ROOT,check=True,capture_output=True)
    if not evidence['checks']:raise ValueError('No executed checks recorded')
    for check in evidence['checks']:
        if check.get('exit_code')!=0 or not check.get('command') or not Path(check.get('log','')).is_file():
            raise ValueError('Check missing or failed: '+str(check))
    production=[ROOT/p for p in evidence['production_files']]
    tests=[ROOT/p for p in evidence['test_files']]
    if set(production)&set(tests):raise ValueError('Production cannot be classified as tests')
    if not production:raise ValueError('Production manifest is empty')
    if any(not p.is_file() for p in production+tests):raise ValueError('Evidence contains missing files')
    metrics=compare_lines(production,tests)
    metrics['coverage']=coverage_files(evidence['coverage_report'],production)
    return metrics

def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--task');g.add_argument('--feature');g.add_argument('--integration',action='store_true');g.add_argument('--release',action='store_true')
    args=p.parse_args()
    if args.task:print(json.dumps(task_gate(args.task)));return
    name=args.feature or 'gideon-ui-rebuild'
    spec=tomllib.loads((ROOT/'spec'/name/'spec.kvx').read_text())
    tasks=spec.get('task',{})
    pending=[key for key,value in tasks.items() if value.get('status')!='done']
    if pending:raise ValueError('Incomplete tasks: '+', '.join(pending))
    for key,value in tasks.items():
        if value.get('task_kind')!='integration':task_gate(key)
    print(json.dumps({'feature':name,'tasks':len(tasks),'complete':True}))

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr);sys.exit(1)
