import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'tooling/ui_quality_gate.py'
spec=importlib.util.spec_from_file_location('ui_quality_gate',SCRIPT)
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.old_root=gate.ROOT
        gate.ROOT=self.root
        self.production=self.root/'production.py'
        self.tests=self.root/'test_production.py'
        self.production.write_text('value = 1\n')
        self.tests.write_text('assert value == 1\nassert value != 2\n')
        self.report=self.root/'coverage.json'
        self.folder=self.root/'evidence';self.folder.mkdir()
        self.log=self.root/'test.log';self.log.write_text('1 passed\n')
        subprocess.run(['git','init','-b','main'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','add','production.py','test_production.py'],cwd=self.root,check=True)
        self.tree=subprocess.check_output(['git','write-tree'],cwd=self.root,text=True).strip()
        self.evidence={'revision':self.tree,'production_files':['production.py'],'test_files':['test_production.py'],'coverage_report':str(self.report),'checks':[{'command':'real behavior gate','exit_code':0,'log':str(self.log)}]}
        self.coverage({'summary':{'missing_lines':0,'missing_branches':0}})

    def tearDown(self):
        gate.ROOT=self.old_root;self.tmp.cleanup()

    def coverage(self,record):
        self.report.write_text(json.dumps({'files':{'production.py':record}}))

    def save(self):
        (self.folder/'unit.json').write_text(json.dumps(self.evidence))

    def evaluate(self):
        self.save();return gate.task_gate('unit',self.folder)

    def test_real_tree_and_evidence(self):
        result=self.evaluate()
        self.assertEqual(result['production_lines'],1)
        self.assertEqual(result['test_lines'],2)
        self.assertEqual(result['ratio'],2)
        self.assertTrue(result['coverage']['complete'])

    def test_ignores_comments_and_blanks_for_ratio(self):
        self.assertEqual(gate.useful_lines('# heading\n// heading\n/* comment\n* comment\n\ncode\n'),1)

    def test_rejects_inadequate_ratio(self):
        self.tests.write_text('# no behavior\n')
        with self.assertRaisesRegex(ValueError,'below 1:1'):self.evaluate()

    def test_handles_no_executable_lines(self):
        self.production.write_text('# comment\n')
        self.assertIsNone(gate.compare_lines([self.production],[self.tests])['ratio'])

    def test_requires_existing_evidence(self):
        with self.assertRaisesRegex(ValueError,'Missing real'):gate.task_gate('unknown',self.folder)

    def test_requires_all_fields(self):
        del self.evidence['revision']
        with self.assertRaisesRegex(ValueError,'Missing evidence field'):self.evaluate()

    def test_requires_revision(self):
        self.evidence['revision']=''
        with self.assertRaisesRegex(ValueError,'revision is empty'):self.evaluate()

    def test_requires_real_git_object(self):
        self.evidence['revision']='0'*40
        with self.assertRaises(subprocess.CalledProcessError):self.evaluate()

    def test_requires_checks(self):
        self.evidence['checks']=[]
        with self.assertRaisesRegex(ValueError,'No executed checks'):self.evaluate()

    def test_requires_passed_check(self):
        self.evidence['checks'][0]['exit_code']=1
        with self.assertRaisesRegex(ValueError,'Check missing or failed'):self.evaluate()

    def test_requires_log(self):
        self.log.unlink()
        with self.assertRaisesRegex(ValueError,'Check missing or failed'):self.evaluate()

    def test_requires_command(self):
        self.evidence['checks'][0]['command']=''
        with self.assertRaisesRegex(ValueError,'Check missing or failed'):self.evaluate()

    def test_production_cannot_count_as_tests(self):
        self.evidence['test_files']=['production.py']
        with self.assertRaisesRegex(ValueError,'classified as tests'):self.evaluate()

    def test_requires_production(self):
        self.evidence['production_files']=[]
        with self.assertRaisesRegex(ValueError,'manifest is empty'):self.evaluate()

    def test_requires_manifest_files(self):
        self.production.unlink()
        with self.assertRaisesRegex(ValueError,'missing files'):self.evaluate()

    def test_coverage_missing_file(self):
        self.report.write_text('{"files": {}}')
        with self.assertRaisesRegex(ValueError,'Incomplete executable'):self.evaluate()

    def test_python_missing_lines(self):
        self.coverage({'summary':{'missing_lines':1,'missing_branches':0}})
        with self.assertRaisesRegex(ValueError,'lines/branches'):self.evaluate()

    def test_python_missing_branches(self):
        self.coverage({'summary':{'missing_lines':0,'missing_branches':1}})
        with self.assertRaisesRegex(ValueError,'lines/branches'):self.evaluate()

    def test_istanbul_complete(self):
        self.coverage({'s':{'0':2},'f':{'0':1},'b':{'0':[1,2]}})
        self.assertTrue(self.evaluate()['coverage']['complete'])

    def test_istanbul_missing_statement(self):
        self.coverage({'s':{'0':0},'f':{'0':1},'b':{'0':[1,2]}})
        with self.assertRaisesRegex(ValueError,'statements'):self.evaluate()

    def test_istanbul_missing_function(self):
        self.coverage({'s':{'0':1},'f':{'0':0},'b':{'0':[1,2]}})
        with self.assertRaisesRegex(ValueError,'functions'):self.evaluate()

    def test_istanbul_missing_branch(self):
        self.coverage({'s':{'0':1},'f':{'0':1},'b':{'0':[0,2]}})
        with self.assertRaisesRegex(ValueError,'branches'):self.evaluate()

    def test_reject_unknown_coverage(self):
        self.coverage({'arbitrary':'data'})
        with self.assertRaisesRegex(ValueError,'Unsupported'):self.evaluate()

    def test_absolute_coverage_paths(self):
        self.report.write_text(json.dumps({str(self.production):{'s':{'0':1},'f':{},'b':{}}}))
        self.assertTrue(self.evaluate()['coverage']['complete'])

if __name__=='__main__':unittest.main()

class ChangedCodeTests(unittest.TestCase):
    setUp=EvidenceTests.setUp
    tearDown=EvidenceTests.tearDown
    coverage=EvidenceTests.coverage
    save=EvidenceTests.save
    evaluate=EvidenceTests.evaluate
    def tree_after_edit(self, production, tests):
        self.production.write_text(production)
        self.tests.write_text(tests)
        subprocess.run(['git','add','production.py','test_production.py'],cwd=self.root,check=True)
        return subprocess.check_output(['git','write-tree'],cwd=self.root,text=True).strip()

    def test_diff_counts_only_new_lines(self):
        revised=self.tree_after_edit('value = 1\nother = 2\n','assert value == 1\nassert value != 2\nassert other == 2\n')
        changed=gate.changed_lines(self.tree,revised,[self.production,self.tests])
        self.assertEqual(changed[str(self.production)],{2})
        self.assertEqual(changed[str(self.tests)],{3})
        metrics=gate.compare_lines([self.production],[self.tests],changed)
        self.assertEqual(metrics['production_lines'],1)
        self.assertEqual(metrics['test_lines'],1)

    def test_deleted_lines_do_not_count_as_new_code(self):
        revised=self.tree_after_edit('','assert value == 1\n')
        changed=gate.changed_lines(self.tree,revised,[self.production,self.tests])
        self.assertEqual(changed[str(self.production)],set())
        self.assertEqual(changed[str(self.tests)],set())
        self.assertIsNone(gate.compare_lines([self.production],[self.tests],changed)['ratio'])

    def test_entire_new_file_counts(self):
        extra=self.root/'new.py';extra.write_text('first=1\nsecond=2\n')
        subprocess.run(['git','add','new.py'],cwd=self.root,check=True)
        revised=subprocess.check_output(['git','write-tree'],cwd=self.root,text=True).strip()
        self.assertEqual(gate.changed_lines(self.tree,revised,[extra])[str(extra)],{1,2})

    def test_unchanged_file_is_not_requalified(self):
        self.report.write_text('{}')
        result=gate.coverage_files(self.report,[self.production],{str(self.production):set()})
        self.assertTrue(result['complete'])

    def istanbul(self):
        loc=lambda number:{'start':{'line':number,'column':0},'end':{'line':number,'column':10}}
        return {'s':{'0':0,'1':1},'f':{'0':0,'1':1},'b':{'0':[0,0],'1':[1,1]},'statementMap':{'0':loc(1),'1':loc(2)},'fnMap':{'0':{'loc':loc(1)},'1':{'loc':loc(2)}},'branchMap':{'0':{'loc':loc(1)},'1':{'loc':loc(2)}}}

    def test_changed_istanbul_ignores_untouched_gaps(self):
        self.coverage(self.istanbul())
        self.assertTrue(gate.coverage_files(self.report,[self.production],{str(self.production):{2}})['complete'])

    def test_changed_istanbul_rejects_changed_gap(self):
        self.coverage(self.istanbul())
        with self.assertRaisesRegex(ValueError,'statements'):
            gate.coverage_files(self.report,[self.production],{str(self.production):{1}})

    def test_changed_function_decl_location_supported(self):
        record=self.istanbul()
        record['fnMap']['1']['decl']=record['fnMap']['1'].pop('loc')
        record['f']['1']=0
        self.coverage(record)
        with self.assertRaisesRegex(ValueError,'functions'):
            gate.coverage_files(self.report,[self.production],{str(self.production):{2}})

    def test_changed_branch_requires_all_outcomes(self):
        record=self.istanbul();record['b']['1']=[1,0];self.coverage(record)
        with self.assertRaisesRegex(ValueError,'branches'):
            gate.coverage_files(self.report,[self.production],{str(self.production):{2}})

    def test_changed_coverage_requires_locations(self):
        self.coverage({'s':{'0':1},'f':{},'b':{}})
        with self.assertRaisesRegex(ValueError,'location map'):
            gate.coverage_files(self.report,[self.production],{str(self.production):{1}})

    def test_empty_coverage_cannot_cover_code(self):
        self.coverage({'s':{},'f':{},'b':{}})
        with self.assertRaisesRegex(ValueError,'Empty executable'):self.evaluate()

    def test_incomplete_coverage_categories_rejected(self):
        self.coverage({'s':{'0':1}})
        with self.assertRaisesRegex(ValueError,'Incomplete Istanbul'):self.evaluate()

    def test_python_requires_branch_summary(self):
        self.coverage({'summary':{}})
        with self.assertRaisesRegex(ValueError,'Incomplete Python'):self.evaluate()

    def python_changed(self, lines, branches, enabled=True):
        self.report.write_text(json.dumps({'meta':{'branch_coverage':enabled},'files':{'production.py':{'summary':{'missing_lines':len(lines),'missing_branches':len(branches)},'missing_lines':lines,'missing_branches':branches}}}))

    def test_python_unchanged_missing_line_is_allowed(self):
        self.python_changed([1],[[1,3]])
        self.assertTrue(gate.coverage_files(self.report,[self.production],{str(self.production):{2}})['complete'])

    def test_python_changed_missing_line_rejected(self):
        self.python_changed([2],[])
        with self.assertRaisesRegex(ValueError,'lines/branches'):
            gate.coverage_files(self.report,[self.production],{str(self.production):{2}})

    def test_python_changed_branch_destination_rejected(self):
        self.python_changed([],[[1,2]])
        with self.assertRaisesRegex(ValueError,'lines/branches'):
            gate.coverage_files(self.report,[self.production],{str(self.production):{2}})

    def test_python_requires_instrumentation(self):
        self.python_changed([],[],False)
        with self.assertRaisesRegex(ValueError,'branch instrumentation'):
            gate.coverage_files(self.report,[self.production],{str(self.production):{2}})

    def test_revision_must_describe_current_files(self):
        self.evidence['baseline']=self.tree
        self.production.write_text('changed = 1\n')
        with self.assertRaisesRegex(ValueError,'differs from current file'):self.evaluate()

    def test_baseline_evidence_uses_changed_lines(self):
        revised=self.tree_after_edit('value = 1\nother = 2\n','assert value == 1\nassert value != 2\nassert other == 2\n')
        self.evidence.update(baseline=self.tree,revision=revised)
        self.python_changed([],[])
        result=self.evaluate()
        self.assertEqual(result['production_lines'],1)
        self.assertEqual(result['test_lines'],1)
        self.assertTrue(result['coverage']['complete'])
