#!/usr/bin/env python3
import datetime,os,pathlib,re,subprocess,sys
SIGN='Signed By Gideon Agent | Powered by GPT Astra 6 and Codify'
GROUPS={'runtime':1,'experience':2,'automation':3,'integrations':4}
def git(*a):return subprocess.check_output(['git',*a],text=True).strip()
def identities(branch):
 m=re.fullmatch(r'wave/gideon-(runtime|experience|automation|integrations)/(\d+)',branch)
 if m:
  g=GROUPS[m[1]];return f'gf26-w{(g-1)*4+int(m[2]):02}',f'gf26-fm{g:02}'
 m=re.fullmatch(r'feature/gideon-(runtime|experience|automation|integrations)',branch)
 if m:
  g=GROUPS[m[1]];return f'gf26-integrator{g:02}',f'gf26-fm{g:02}'
 return 'gf26-leader','gf26-fm00'
def context():
 branch=git('symbolic-ref','--short','HEAD');w,m=identities(branch)
 return w,m,datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),branch,git('write-tree')
mode=sys.argv[1];p=pathlib.Path(sys.argv[2]);body=p.read_text();ctx=context()
if mode=='prepare':
 lines=[x.strip() for x in body.splitlines() if x.strip() and not x.startswith('#') and x.strip()!=SIGN]
 summary=lines[0] if lines else ''
 if summary.startswith('gf26-'):summary=' '.join(summary.split(' ')[5:])
 if summary.startswith(('Merge ', 'merge ')):
  try:
   incoming=git('log','-1','--format=%s','MERGE_HEAD')
   if incoming.startswith('gf26-'):incoming=' '.join(incoming.split(' ')[5:])
   summary='Integrate '+incoming[0].lower()+incoming[1:]
  except subprocess.CalledProcessError:
   raise SystemExit('Provide an explicit merge description stating which code behavior was integrated.')
 if not summary:raise SystemExit('Fleet commit requires a concise description of the actual code change.')
 p.write_text(' '.join(ctx)+' '+summary+'\n\n'+SIGN+'\n')
else:
 lines=body.splitlines();prefix=' '.join(ctx)+' '
 if not lines or not lines[0].startswith(prefix) or not lines[0][len(prefix):].strip() or body.count(SIGN)!=1 or lines[-1].strip()!=SIGN:
  raise SystemExit('Rejected: fleet commit must include assigned worker manager UTC date branch staged tree hash actual change summary and exact Gideon signature. Do not bypass.')
