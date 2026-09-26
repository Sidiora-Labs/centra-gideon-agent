#!/usr/bin/env python3
"""Evaluate recorded task evidence without manufacturing test or coverage results."""
import argparse
import json
import re
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

def changed_lines(baseline, revision, paths):
    result={str(Path(path).resolve()):set() for path in paths}
    for path in paths:
        relative=str(Path(path).resolve().relative_to(ROOT))
        patch=subprocess.check_output(['git','diff','--no-ext-diff','--unified=0',baseline,revision,'--',relative],cwd=ROOT,text=True)
        for match in re.finditer(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@',patch,re.M):
            start=int(match[1]);count=int(match[2]) if match[2] is not None else 1
            result[str(Path(path).resolve())].update(range(start,start+count))
    return result

def compare_lines(production, tests, changed=None):
    def count(path):
        lines=Path(path).read_text().splitlines()
        if changed is not None:
            selected=changed.get(str(Path(path).resolve()),set())
            lines=[line for number,line in enumerate(lines,1) if number in selected]
        return useful_lines('\n'.join(lines))
    prod=sum(count(p) for p in production)
    test=sum(count(p) for p in tests)
    if prod and test < prod:
        raise ValueError(f'Test/code ratio {test}/{prod} is below 1:1')
    return {'production_lines':prod,'test_lines':test,'ratio':test/prod if prod else None}

def coverage_files(report, production, changed=None):
    data=read_json(report)
    files=data.get('files',data)
    missing=[]
    for name in production:
        path=Path(name).resolve()
        selected=changed.get(str(path),set()) if changed is not None else None
        if selected == set():continue
        relative=path.relative_to(ROOT).as_posix()
        matches=[value for key,value in files.items() if Path(key).resolve()==path or key==relative or key.endswith('/'+relative)]
        if len(matches)!=1:
            missing.append(str(path)+': absent or ambiguous');continue
        value=matches[0]
        def relevant(location):
            if selected is None:return True
            if not location:raise ValueError('Coverage location map is required for changed-code metrics')
            return any(location['start']['line']<=line<=location['end']['line'] for line in selected)
        if 's' in value:
            if not all(key in value for key in ['s','f','b']):raise ValueError('Incomplete Istanbul coverage record')
            if not value['s'] and useful_lines(path.read_text()):raise ValueError('Empty executable coverage record')
            for key,hit in value['s'].items():
                if relevant(value.get('statementMap',{}).get(key)) and hit==0:missing.append(str(path)+': statements')
            for key,hit in value['f'].items():
                loc=value.get('fnMap',{}).get(key,{})
                if relevant(loc.get('loc',loc.get('decl'))) and hit==0:missing.append(str(path)+': functions')
            for key,hits in value['b'].items():
                loc=value.get('branchMap',{}).get(key,{})
                if relevant(loc.get('loc')) and any(hit==0 for hit in hits):missing.append(str(path)+': branches')
        elif 'summary' in value:
            summary=value['summary']
            if not all(key in summary for key in ['missing_lines','missing_branches']):raise ValueError('Incomplete Python branch coverage record')
            if selected is None:
                failed=summary['missing_lines'] or summary['missing_branches']
            else:
                if not data.get('meta',{}).get('branch_coverage'):raise ValueError('Python branch instrumentation is required')
                failed=bool(selected.intersection(value.get('missing_lines',[]))) or any(start in selected or end in selected for start,end in value.get('missing_branches',[]))
            if failed:missing.append(str(path)+': lines/branches')
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
    changed=None
    if evidence.get('baseline'):
        for path in production+tests:
            relative=path.relative_to(ROOT).as_posix()
            recorded=subprocess.check_output(['git','show',revision+':'+relative],cwd=ROOT)
            if recorded!=path.read_bytes():raise ValueError('Evidence revision differs from current file: '+relative)
        changed=changed_lines(evidence['baseline'],revision,production+tests)
    metrics=compare_lines(production,tests,changed)
    metrics['coverage']=coverage_files(evidence['coverage_report'],production,changed)
    return metrics

def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--task');g.add_argument('--feature');g.add_argument('--integration',action='store_true');g.add_argument('--release',action='store_true')
    args=p.parse_args()
    if args.task:print(json.dumps(task_gate(args.task)));return
    branch=subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()
    lane=re.fullmatch(r'(?:feature|wave)/(gideon-ui-[^/]+)(?:/[^/]+)?',branch)
    name=args.feature or (lane[1] if lane and not args.release else 'gideon-ui-rebuild')
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
