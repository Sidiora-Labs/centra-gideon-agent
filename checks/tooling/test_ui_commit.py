import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'tooling/ui_commit.py'
spec = importlib.util.spec_from_file_location('ui_commit', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class CommitFormat(unittest.TestCase):
    def setUp(self):
        self.tree = 'a' * 40
        self.body = module.message('Connect live chat', 'gu26-fm02', 'main', self.tree, '2026-09-26')

    def test_exact_format(self):
        self.assertEqual(self.body, 'gu26-leader\ngu26-fm02\n2026-09-26\nmain\n' + self.tree + '\n"Connect live chat"\n\n' + module.SIGNATURE + '\n')
        module.validate(self.body, 'main', self.tree)

    def test_identity_rotation_wraps(self):
        self.assertEqual([module.identity(i) for i in range(8)], module.IDENTITIES * 2)

    def test_reject_wrong_tree(self):
        with self.assertRaises(ValueError): module.validate(self.body, 'main', 'b' * 40)

    def test_reject_wrong_branch(self):
        with self.assertRaises(ValueError): module.validate(self.body, 'other', self.tree)

    def test_reject_wrong_author(self):
        with self.assertRaises(ValueError): module.validate(self.body, 'main', self.tree, 'incorrect', 'expected')

    def test_accept_allocated_author(self):
        module.validate(self.body, 'main', self.tree, 'expected', 'expected')

    def test_reject_bad_footer(self):
        with self.assertRaises(ValueError): module.validate(self.body.replace('Codify©', 'Codify'), 'main', self.tree)

    def test_reject_missing_line(self):
        with self.assertRaises(ValueError): module.validate(self.body.replace('\ngu26-fm02', ''), 'main', self.tree)

    def test_reject_bad_date(self):
        with self.assertRaises(ValueError): module.validate(self.body.replace('2026-09-26', '2026-99-26'), 'main', self.tree)

    def test_reject_bad_manager(self):
        with self.assertRaises(ValueError): module.message('Code', 'other', 'main', self.tree)

    def test_reject_empty_message(self):
        with self.assertRaises(ValueError): module.message('  ', 'gu26-fm00', 'main', self.tree)

    def test_reject_multiline(self):
        with self.assertRaises(ValueError): module.message('one\ntwo', 'gu26-fm00', 'main', self.tree)

    def test_reject_embedded_quote(self):
        with self.assertRaises(ValueError): module.message('A "quote"', 'gu26-fm00', 'main', self.tree)

    def test_reject_hash_shape(self):
        body = module.message('Code', 'gu26-fm00', 'main', 'not-a-hash')
        with self.assertRaises(ValueError): module.validate(body, 'main', 'not-a-hash')

    def test_reject_unquoted_description(self):
        with self.assertRaises(ValueError): module.validate(self.body.replace('"Connect live chat"', 'Unquoted'), 'main', self.tree)

    def test_reject_invalid_manager_in_body(self):
        with self.assertRaises(ValueError): module.validate(self.body.replace('gu26-fm02', 'gu26-w02'), 'main', self.tree)

class RealRepositories(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.repo = self.path / 'repo'
        self.repo.mkdir()
        self.env = os.environ | {'GIDEON_UI_COMMIT_STATE':str(self.path/'state')}
        for name in ['GIDEON_UI_AGENT','GIDEON_UI_MANAGER','GIDEON_UI_IDENTITY_INDEX','GIDEON_UI_SUMMARY']:
            self.env.pop(name,None)
        self.run_git('init','-b','main')
        self.run_git('config','user.name','Initial')
        self.run_git('config','user.email','initial@example.test')
        (self.repo/'tooling').mkdir()
        (self.repo/'tooling/ui_commit.py').write_text(SCRIPT.read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def run_git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.repo,env=self.env,text=True,stderr=subprocess.DEVNULL).strip()

    def invoke(self,agent='gu26-w01',paths=None,success=True):
        cmd=['python3',str(SCRIPT),'commit','--agent',agent,'--manager','gu26-fm01','--message','Write concrete file behavior',*(paths or [])]
        result=subprocess.run(cmd,cwd=self.repo,env=self.env,capture_output=True,text=True)
        if success:self.assertEqual(result.returncode,0,result.stderr)
        else:self.assertNotEqual(result.returncode,0)
        return result

    def write(self,name,value):
        (self.repo/name).write_text(value)

    def test_real_commit_records_staged_tree_and_identity(self):
        self.write('app.txt','first')
        self.invoke(paths=['app.txt','tooling/ui_commit.py'])
        author=self.run_git('log','-1','--format=%an <%ae>')
        self.assertEqual(author,'jg-sidioralabs <203055447+jg-sidioralabs@users.noreply.github.com>')
        body=self.run_git('log','-1','--format=%B')
        module.validate(body,'main',self.run_git('rev-parse','HEAD^{tree}'))
        self.assertEqual(self.run_git('log','-1','--format=%cn <%ce>'),author)

    def test_round_robin_per_agent(self):
        for index in range(5):
            self.write('app.txt',str(index))
            self.invoke(paths=['app.txt'])
            name,email=module.identity(index)
            self.assertEqual(self.run_git('log','-1','--format=%an <%ae>'),f'{name} <{email}>')
        self.write('other.txt','second worker')
        self.invoke(agent='gu26-w02',paths=['other.txt'])
        self.assertEqual(self.run_git('log','-1','--format=%an'),module.IDENTITIES[0][0])

    def test_failed_commit_does_not_advance(self):
        self.invoke(success=False)
        self.write('app.txt','first')
        self.invoke(paths=['app.txt'])
        self.assertEqual(self.run_git('log','-1','--format=%an'),module.IDENTITIES[0][0])

    def test_scoped_staging_preserves_unrelated_files(self):
        self.write('app.txt','selected')
        self.write('unrelated.txt','private draft')
        self.invoke(paths=['app.txt'])
        self.assertEqual(self.run_git('ls-tree','--name-only','HEAD'),'app.txt')
        self.assertIn('?? unrelated.txt',self.run_git('status','--short'))

    def test_plain_git_commit_is_refused(self):
        subprocess.run(['python3',str(SCRIPT),'install'],cwd=self.repo,env=self.env,check=True)
        self.write('app.txt','selected')
        self.run_git('add','app.txt')
        result=subprocess.run(['git','commit','-m','Bypass'],cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('fleet agent identity is required',result.stderr)

    def test_invalid_agent_is_refused(self):
        self.write('app.txt','selected')
        result=self.invoke(agent='unknown',paths=['app.txt'],success=False)
        self.assertIn('Invalid worker',result.stderr)

    def test_rotation_survives_second_repository(self):
        self.write('app.txt','first')
        self.invoke(paths=['app.txt'])
        second=self.path/'other';second.mkdir();self.repo=second
        self.run_git('init','-b','main')
        (second/'tooling').mkdir();(second/'tooling/ui_commit.py').write_text(SCRIPT.read_text())
        self.write('app.txt','second')
        self.invoke(paths=['app.txt'])
        self.assertEqual(self.run_git('log','-1','--format=%an'),module.IDENTITIES[1][0])
        ledger=json.loads((self.path/'state/ledger.json').read_text())
        self.assertEqual(len(ledger['gu26-w01']),2)

    def test_audit_known_history(self):
        self.write('app.txt','base')
        self.invoke(paths=['app.txt'])
        self.run_git('config','gu26.baseline',self.run_git('rev-parse','HEAD'))
        self.write('app.txt','second')
        self.invoke(paths=['app.txt'])
        result=subprocess.run(['python3',str(SCRIPT),'audit'],cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['audited_commits'],1)

    def test_audit_rejects_missing_commit_record(self):
        self.write('app.txt','base')
        self.invoke(paths=['app.txt'])
        self.run_git('config','gu26.baseline',self.run_git('rev-parse','HEAD'))
        self.write('app.txt','second')
        self.invoke(paths=['app.txt'])
        path=self.path/'state/ledger.json'
        value=json.loads(path.read_text());value['gu26-w01'].pop();path.write_text(json.dumps(value))
        result=subprocess.run(['python3',str(SCRIPT),'audit'],cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('absent from identity ledger',result.stderr)

    def test_audit_rejects_broken_rotation(self):
        self.write('app.txt','base')
        self.invoke(paths=['app.txt'])
        self.run_git('config','gu26.baseline',self.run_git('rev-parse','HEAD'))
        path=self.path/'state/ledger.json'
        value=json.loads(path.read_text());value['gu26-w01'][0]['identity_index']=9;path.write_text(json.dumps(value))
        result=subprocess.run(['python3',str(SCRIPT),'audit'],cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('not sequential',result.stderr)

    def test_refuses_unrelated_staged_change(self):
        self.write('other.txt','prior staged work')
        self.run_git('add','other.txt')
        self.write('app.txt','selected')
        result=self.invoke(paths=['app.txt'],success=False)
        self.assertIn('Unrelated staged paths: other.txt',result.stderr)
        self.assertEqual(self.run_git('diff','--cached','--name-only'),'other.txt')

if __name__=='__main__':unittest.main()
