#!/usr/bin/env python3
"""Serialize fleet commits and validate their required authorship metadata."""
import argparse
import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

IDENTITIES = [
    ('jg-sidioralabs', '203055447+jg-sidioralabs@users.noreply.github.com'),
    ('dev-paxeer', '220528561+dev-paxeer@users.noreply.github.com'),
    ('gavin-sidiora', '331247230+gavin-sidiora@users.noreply.github.com'),
    ('paxlabs-inc', 'info@sidioralabs.com'),
]
SIGNATURE = 'Signed By Gideon Agent | Powered by GPT Astra 6 and Codify©'

def git(*args, env=None):
    return subprocess.check_output(['git', *args], text=True, env=env).strip()

def state_dir():
    return Path(os.environ.get('GIDEON_UI_COMMIT_STATE', '/root/private-neo-v1/.gu26-commit-state'))

def identity(index):
    return IDENTITIES[index % len(IDENTITIES)]

def message(summary, manager, branch, tree, date=None):
    if not re.fullmatch(r'gu26-fm\d{2}', manager):
        raise ValueError('A gu26-fmNN manager is required')
    if not summary.strip() or '\n' in summary or '"' in summary:
        raise ValueError('Use one concise description without quotes or newlines')
    day = date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    return f'gu26-leader\n{manager}\n{day}\n{branch}\n{tree}\n"{summary.strip()}"\n\n{SIGNATURE}\n'

def validate(body, branch, tree, author=None, expected=None):
    lines = body.splitlines()
    if len(lines) != 8 or lines[0] != 'gu26-leader' or not re.fullmatch(r'gu26-fm\d{2}', lines[1]):
        raise ValueError('Invalid fleet identity or message structure')
    datetime.date.fromisoformat(lines[2])
    if lines[3] != branch or lines[4] != tree or not re.fullmatch(r'[0-9a-f]{40,64}', tree):
        raise ValueError('Branch or staged tree differs from message')
    if not re.fullmatch(r'"[^"\n]+"', lines[5]) or lines[6] or lines[7] != SIGNATURE:
        raise ValueError('Description or exact signature missing')
    if expected is not None and author != expected:
        raise ValueError('Author does not match allocated round-robin identity')

def hook(mode, filename):
    agent = os.environ.get('GIDEON_UI_AGENT', '')
    manager = os.environ.get('GIDEON_UI_MANAGER', '')
    if not re.fullmatch(r'gu26-(?:leader|fm\d{2}|w\d{2})', agent):
        raise ValueError('Commit using tooling/ui_commit.py; fleet agent identity is required')
    branch, tree = git('branch', '--show-current'), git('write-tree')
    path = Path(filename)
    if mode == 'prepare':
        existing = path.read_text()
        summary = os.environ.get('GIDEON_UI_SUMMARY')
        if not summary:
            summary = next((s.strip() for s in existing.splitlines() if s.strip() and not s.startswith('#')), '')
        path.write_text(message(summary, manager, branch, tree))
    name, email = identity(int(os.environ['GIDEON_UI_IDENTITY_INDEX']))
    author = git('var', 'GIT_AUTHOR_IDENT').rsplit(' ', 2)[0]
    validate(path.read_text(), branch, tree, author, f'{name} <{email}>')

def install():
    root = Path(git('rev-parse', '--show-toplevel'))
    common = Path(git('rev-parse', '--git-common-dir')).resolve()
    hooks = common / 'gu26-hooks'
    hooks.mkdir(exist_ok=True)
    for name, mode in [('prepare-commit-msg', 'prepare'), ('commit-msg', 'validate')]:
        target = hooks / name
        target.write_text('#!/bin/sh\nexec python3 "$(git rev-parse --show-toplevel)/tooling/ui_commit.py" hook '+mode+' "$1"\n')
        target.chmod(0o755)
    push = hooks / 'pre-push'
    push.write_text('#!/bin/sh\nexec python3 "$(git rev-parse --show-toplevel)/tooling/ui_commit.py" audit\n')
    push.chmod(0o755)
    existing = subprocess.run(['git','config','--get','gu26.baseline'],text=True,capture_output=True)
    if not existing.stdout.strip():
        head = subprocess.run(['git','rev-parse','HEAD'],text=True,capture_output=True)
        if head.returncode == 0: git('config','gu26.baseline',head.stdout.strip())
    git('config', 'core.hooksPath', str(hooks))
    return root

def commit(args):
    if not re.fullmatch(r'gu26-(?:leader|fm\d{2}|w\d{2})', args.agent):
        raise ValueError('Invalid worker/manager identity')
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = directory / 'ledger.json'
        state = json.loads(ledger.read_text()) if ledger.exists() else {}
        index = len(state.get(args.agent, []))
        name, email = identity(index)
        env = os.environ | dict(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email, GIDEON_UI_AGENT=args.agent, GIDEON_UI_MANAGER=args.manager, GIDEON_UI_IDENTITY_INDEX=str(index), GIDEON_UI_SUMMARY=args.message)
        if args.paths:
            staged = git('diff', '--cached', '--name-only', '-z').split('\0')
            selected = [p.rstrip('/') for p in args.paths]
            unrelated = [p for p in staged if p and not any(p == q or p.startswith(q + '/') for q in selected)]
            if unrelated:
                raise ValueError('Unrelated staged paths: ' + ', '.join(unrelated))
            git('add', '--', *args.paths)
        if not args.command and not git('diff', '--cached', '--name-only'):
            raise ValueError('No staged change to commit')
        install()
        command = args.command or ['git', 'commit', '-m', args.message]
        subprocess.run(command, env=env, check=True)
        # cg operations may commit in a different linked worktree.
        refs = git('log', '--all', '--since=10.minutes', '--format=%H%x09%an <%ae>%x09%B%x00').split('\x00')
        prior = {r['sha'] for records in state.values() for r in records}
        found = []
        for entry in refs:
            line = entry.strip()
            if not line: continue
            sha, author, body = line.split('\t', 2)
            if sha in prior or not body.startswith('gu26-leader\n'): continue
            if author != f'{name} <{email}>': continue
            tree = git('show', '-s', '--format=%T', sha)
            validate(body, body.splitlines()[3], tree, author, f'{name} <{email}>')
            found.append({'sha':sha, 'tree':tree, 'identity_index':index, 'manager':args.manager})
        if len(found) != 1:
            raise ValueError('Expected exactly one newly created fleet commit; inspect ledger before retry')
        state.setdefault(args.agent, []).extend(found)
        fd, temporary = tempfile.mkstemp(dir=directory)
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, indent=2)
        os.replace(temporary, ledger)
        print(found[0]['sha'])

def audit():
    base = git('config','--get','gu26.baseline')
    ledger = json.loads((state_dir()/'ledger.json').read_text())
    indexed = {}
    for agent, records in ledger.items():
        for index, record in enumerate(records):
            if record['identity_index'] != index:
                raise ValueError('Rotation ledger is not sequential for '+agent)
            indexed[record['sha']] = identity(index)
    commits = git('rev-list',base+'..HEAD').splitlines()
    for sha in commits:
        body=git('show','-s','--format=%B',sha)
        if sha not in indexed:raise ValueError('Commit absent from identity ledger: '+sha)
        name,email=indexed[sha]
        author=git('show','-s','--format=%an <%ae>',sha)
        committer=git('show','-s','--format=%cn <%ce>',sha)
        if committer != f'{name} <{email}>':raise ValueError('Incorrect committer: '+sha)
        lines=body.splitlines()
        validate(body,lines[3] if len(lines)>3 else '',git('show','-s','--format=%T',sha),author,f'{name} <{email}>')
    print(json.dumps({'audited_commits':len(commits)}))

def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='mode',required=True)
    sub.add_parser('install');sub.add_parser('audit')
    h=sub.add_parser('hook');h.add_argument('stage');h.add_argument('file')
    c=sub.add_parser('commit');c.add_argument('--agent',required=True);c.add_argument('--manager',required=True);c.add_argument('--message',required=True);c.add_argument('paths',nargs='*');c.set_defaults(command=None)
    r=sub.add_parser('run');r.add_argument('--agent',required=True);r.add_argument('--manager',required=True);r.add_argument('--message',required=True);r.add_argument('command',nargs=argparse.REMAINDER);r.set_defaults(paths=[])
    args=parser.parse_args()
    if args.mode=='install': install()
    elif args.mode=='audit': audit()
    elif args.mode=='hook': hook(args.stage,args.file)
    else:
        if args.command and args.command[0]=='--':args.command=args.command[1:]
        commit(args)

if __name__=='__main__':
    try: main()
    except (ValueError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr);sys.exit(1)
