"""Controller regression tests. Synthetic receipts test mechanics, not agent outcomes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import socket
import time
from unittest.mock import patch
from http.client import HTTPConnection

from ada_lab.files import ROOT, copy_tree, digest, lock, read, tree_digest, write
from ada_lab.lab import Lab, COMPARISON_POLICY


def review(subject, observer='independent-test-fixture', status='PASS'):
    return {'observer':observer, 'session_ref':'synthetic-unit-session', 'source_ref':'synthetic-unit-source',
            'status':status, 'subject_digest':subject, 'evidence':['unit fixture only'], 'findings':[]}


class CoreTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.lab').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.lab')
        self.lab = Lab(self.temp.name)
        self.lab.initialize(ROOT.parent/'Ada')

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, max_sessions=12):
        return self.lab.prepare(model='unit-fixture-model', max_sessions=max_sessions)['experiment']

    def test_atomic_state_survives_new_process(self):
        path = Path(self.temp.name)/'state.json'
        write(path, {'status':'paused','cost':None})
        output = subprocess.check_output([sys.executable,'-c','import json,sys;print(json.load(open(sys.argv[1]))["status"])',str(path)],text=True)
        self.assertEqual(output.strip(),'paused')
        self.assertIsNone(read(path)['cost'])
        self.assertEqual(list(path.parent.glob('*.tmp')),[])

    def test_digests_bind_content_and_copies(self):
        source = Path(self.temp.name)/'src';source.mkdir();(source/'app.py').write_text('version=1')
        target = Path(self.temp.name)/'copy';copy_tree(source,target)
        self.assertEqual(tree_digest(source),tree_digest(target))
        (target/'app.py').write_text('version=2')
        self.assertNotEqual(tree_digest(source),tree_digest(target))

    def test_lock_prevents_lost_update(self):
        identifier=self.prepare()
        with lock(self.lab.folder(identifier)/'controller.lock'):
            with self.assertRaises(RuntimeError):self.lab.compare(identifier)

    def test_prepare_and_resume_never_launch_ai(self):
        identifier=self.prepare()
        resumed=self.lab.resume(identifier)
        self.assertEqual(resumed['active_sessions'],[])
        self.assertEqual(self.lab.load(identifier)['sessions'],[])
        self.assertEqual(self.lab.config()['execution'],'current-session')

    def test_pause_persists_and_blocks_work(self):
        identifier=self.prepare()
        self.lab.pause(identifier)
        with self.assertRaisesRegex(RuntimeError,'paused'):self.lab.run(identifier,'stable')
        self.assertEqual(self.lab.load(identifier)['status'],'paused')
        self.assertEqual(self.lab.resume(identifier)['status'],'working')

    def test_unknown_metrics_remain_unknown(self):
        identifier=self.prepare()
        session=self.lab.begin(identifier,'lead','lead','stable',source_ref='unit fixture')
        self.lab.finish(identifier,session['id'],{'status':'failed','source_ref':'unit fixture'})
        values=self.lab.measurements(self.lab.load(identifier))
        self.assertIsNone(values['input_tokens'])
        self.assertIsNone(values['monetary_cost'])
        self.assertEqual(values['failed_work_segments'],1)

    def test_admission_limit_includes_failed_sessions(self):
        identifier=self.prepare(max_sessions=1)
        session=self.lab.begin(identifier,'lead','lead',source_ref='unit fixture')
        self.lab.finish(identifier,session['id'],{'status':'interrupted','source_ref':'unit fixture'})
        with self.assertRaisesRegex(RuntimeError,'budget'):self.lab.begin(identifier,'lead','lead',source_ref='unit fixture')

    def test_finish_cannot_rewrite_previous_accounting(self):
        identifier=self.prepare()
        session=self.lab.begin(identifier,'lead','lead',source_ref='unit fixture')
        receipt={'status':'completed','source_ref':'unit fixture','input_tokens':5,'cached_input_tokens':3}
        self.lab.finish(identifier,session['id'],receipt)
        with self.assertRaises(ValueError):self.lab.finish(identifier,session['id'],receipt)
        self.assertEqual(self.lab.measurements(self.lab.load(identifier))['input_tokens'],5)

    def test_changed_frozen_inputs_rejected(self):
        identifier=self.prepare()
        source=self.lab.folder(identifier)/'stable'/'README.md'
        source.write_text(source.read_text(encoding='utf-8')+'\nMutation',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'snapshot'):self.lab.resume(identifier)

    def test_candidate_freeze_recovers_partial_copy(self):
        identifier=self.prepare()
        folder=self.lab.folder(identifier)
        source=folder/'candidate-working'
        with (source/'README.md').open('a',encoding='utf-8') as stream:stream.write('\nCandidate unit fixture\n')
        copy_tree(source,folder/'candidate')  # crash window before record write
        candidate=self.lab.freeze_candidate(identifier,review(tree_digest(source)),'observable fixture gate')
        self.assertEqual(candidate['source_digest'],tree_digest(source))
        self.assertEqual(self.lab.freeze_candidate(identifier,{},'')['source_digest'],candidate['source_digest'])

    def test_independent_review_rejects_self_and_stale_evidence(self):
        identifier=self.prepare()
        packet=self.lab.run(identifier,'stable')
        workspace=Path(packet['workspace']);(workspace/'PLAN.md').write_text('Frozen plan',encoding='utf-8')
        self.lab.begin(identifier,'implementer','lead','stable',source_ref='unit fixture')
        with self.assertRaisesRegex(ValueError,'Independent'):self.lab.add_review(identifier,'stable','design',review(digest('Frozen plan'),'implementer'))
        with self.assertRaisesRegex(ValueError,'binding'):self.lab.add_review(identifier,'stable','behavior',review('stale'))

    def test_partial_comparison_is_inconclusive(self):
        identifier=self.prepare()
        record=self.lab.load(identifier);record['runs']['stable']={'status':'prepared','reviews':{}}
        self.lab.save(record)
        self.assertEqual(self.lab.compare(identifier)['outcome'],'Inconclusive result')

    def test_blocked_evaluator_is_not_a_regression(self):
        identifier=self.prepare()
        artifact=Path(self.temp.name)/'artifact';artifact.mkdir();(artifact/'app.py').write_text('unit fixture')
        run={'artifact':str(artifact),'artifact_digest':tree_digest(artifact),
             'evaluation':{'artifact_digest':tree_digest(artifact),'quality_pass':True,'criteria':{'browser':{'status':'PASS'}}}}
        record=self.lab.load(identifier);record['runs']={'stable':run,'candidate':json.loads(json.dumps(run))}
        for value in record['runs'].values():
            value['evaluation'].update({key:record[key] for key in ('benchmark_revision','criteria_digest','evaluator_version')})
        record['runs']['candidate']['evaluation'].update(quality_pass=False,criteria={'browser':{'status':'BLOCKED'}})
        self.lab.save(record)
        self.assertEqual(self.lab.compare(identifier)['outcome'],'Inconclusive result')
        record['runs']['candidate']['evaluation']['criteria']['browser']['status']='FAIL'
        self.lab.save(record)
        self.assertEqual(self.lab.compare(identifier)['outcome'],'Regression observed')

    def test_new_submission_cannot_reuse_previous_artifact_evaluation(self):
        identifier=self.prepare()
        packet=self.lab.run(identifier,'single');workspace=Path(packet['workspace'])
        (workspace/'app.py').write_text('unit_fixture = 1',encoding='utf-8')
        record=self.lab.load(identifier)
        write(self.lab.state/'calibration.json',{'status':'PASS','evaluator_version':record['evaluator_version'],'benchmark_revision':record['benchmark_revision']})
        def observed_fixture(artifact,*args):
            return {'artifact_digest':tree_digest(artifact),'evaluator_version':record['evaluator_version'],'quality_pass':False,'criteria':{},
                    'accounting':{'category':'synthetic-unit-fixture','wall_seconds':0},'evidence':'synthetic-unit-fixture'}
        with patch('ada_lab.lab.evaluator.evaluate',side_effect=observed_fixture) as execute:
            self.lab.submit(identifier,'single');first=self.lab.evaluate_run(identifier,'single')
            self.assertEqual(self.lab.evaluate_run(identifier,'single'),first)
            self.assertEqual(execute.call_count,1)
            (workspace/'app.py').write_text('unit_fixture = 2',encoding='utf-8')
            self.lab.submit(identifier,'single')
            self.assertNotIn('evaluation',self.lab.load(identifier)['runs']['single'])
            second=self.lab.evaluate_run(identifier,'single')
            self.assertEqual(execute.call_count,2)
            self.assertNotEqual(first['artifact_digest'],second['artifact_digest'])
        self.assertEqual(self.lab.load(identifier)['runs']['single']['attempts'][0]['evaluation'],first)
        history=self.lab.measurements(self.lab.load(identifier))['evaluation_history']
        self.assertEqual(len(history),2)
        self.assertTrue(all(entry['status']=='completed' for entry in history))

    def test_paused_evaluation_never_invokes_evaluator(self):
        identifier=self.prepare();packet=self.lab.run(identifier,'single')
        (Path(packet['workspace'])/'app.py').write_text('unit_fixture = 1',encoding='utf-8')
        self.lab.submit(identifier,'single');self.lab.pause(identifier)
        with patch('ada_lab.lab.evaluator.evaluate') as execute:
            with self.assertRaisesRegex(RuntimeError,'paused'):self.lab.evaluate_run(identifier,'single')
            execute.assert_not_called()
        self.assertEqual(self.lab.load(identifier)['status'],'paused')

    def test_actual_browser_versions_are_comparison_inputs(self):
        identifier=self.prepare();record=self.lab.load(identifier)
        artifact=Path(self.temp.name)/'browser-fixture';artifact.mkdir();(artifact/'app.py').write_text('unit fixture')
        evaluation={'artifact_digest':tree_digest(artifact),'quality_pass':True,'criteria':{'ui':{'status':'PASS'}},
                    **{key:record[key] for key in ('benchmark_revision','criteria_digest','evaluator_version')}}
        record['runs']={name:{'artifact':str(artifact),'artifact_digest':tree_digest(artifact),
                             'evaluation':{**evaluation,'browser_version':browser}}
                        for name,browser in [('stable','A'),('candidate','B')]}
        self.lab.save(record)
        result=self.lab.compare(identifier)
        self.assertEqual(result['outcome'],'Invalid experiment')
        self.assertIn('observed_execution_environment',result['differences'])

    def test_nonfinite_and_fractional_token_metrics_rejected(self):
        identifier=self.prepare();session=self.lab.begin(identifier,'lead','lead',source_ref='unit fixture')
        for values in ({'input_tokens':1.5},{'wall_seconds':float('nan')},{'monetary_cost':float('inf')}):
            with self.assertRaises(ValueError):
                self.lab.finish(identifier,session['id'],{'status':'completed','source_ref':'unit fixture',**values})

    def test_controller_timing_is_prospective_and_separate_by_role(self):
        identifier=self.prepare()
        lead=self.lab.begin(identifier,'lead','lead','stable',source_ref='unit fixture',timed=True,stage='implementation')
        self.lab.finish(identifier,lead['id'],{'status':'completed','source_ref':'unit fixture'})
        values=self.lab.measurements(self.lab.load(identifier),'stable')
        self.assertGreaterEqual(values['development_wall_seconds'],0)
        self.assertIsNone(values['review_wall_seconds'])
        self.assertTrue(self.lab.load(identifier)['sessions'][0]['timer']['continuity_verified'])
        timed=self.lab.begin(identifier,'reviewer','reviewer','stable',source_ref='unit fixture',timed=True)
        with self.assertRaises(ValueError):
            self.lab.finish(identifier,timed['id'],{'status':'completed','source_ref':'unit fixture','wall_seconds':3})

    def test_clock_discontinuity_stays_unknown(self):
        identifier=self.prepare()
        segment=self.lab.begin(identifier,'lead','lead','stable',source_ref='unit fixture',timed=True)
        with patch('ada_lab.lab.time.monotonic_ns',return_value=1):
            self.lab.finish(identifier,segment['id'],{'status':'completed','source_ref':'unit fixture'})
        self.assertIsNone(self.lab.load(identifier)['sessions'][0]['wall_seconds'])

    def comparison_fixture(self):
        identifier=self.prepare();record=self.lab.load(identifier)
        artifact=Path(self.temp.name)/'efficiency-fixture';artifact.mkdir();(artifact/'app.py').write_text('unit fixture')
        record['sessions']=[]
        for variant,seconds,corrections in [('stable',120,1),('candidate',60,0)]:
            evaluation={'artifact_digest':tree_digest(artifact),'quality_pass':True,'browser_version':'same',
                        'criteria':{'ui':{'status':'PASS'}},**{key:record[key] for key in ('benchmark_revision','criteria_digest','evaluator_version')}}
            record['runs'][variant]={'artifact':str(artifact),'artifact_digest':tree_digest(artifact),'evaluation':evaluation,
                                     'corrections':[{'synthetic':True}]*corrections,'autonomy':{'human_interventions':0}}
            record['sessions'].append({'id':variant,'observer':'unit fixture','role':'lead','variant':variant,
                'model':record['model'],'status':'completed','wall_seconds':seconds,'input_tokens':None,
                'output_tokens':None,'cached_input_tokens':None,'model_calls':None,'monetary_cost':None})
        return identifier,record

    def test_equal_quality_compare_uses_observed_dimensions(self):
        identifier,record=self.comparison_fixture();self.lab.save(record)
        result=self.lab.compare(identifier)
        self.assertEqual(result['outcome'],'Improvement observed')
        self.assertEqual(result['metric_comparisons']['development_wall_seconds']['delta'],-60)

    def test_active_work_blocks_final_completion(self):
        identifier,record=self.comparison_fixture();record['sessions'][0]['status']='active';self.lab.save(record)
        self.assertEqual(self.lab.compare(identifier)['outcome'],'Inconclusive result')
        self.assertNotEqual(self.lab.load(identifier)['status'],'completed')

    def test_changed_conclusion_is_archived_and_legacy_policy_retained(self):
        identifier,record=self.comparison_fixture();record.pop('comparison_policy')
        record['comparison']={'outcome':'Earlier synthetic fixture'};self.lab.save(record)
        result=self.lab.compare(identifier)
        self.assertEqual(result['outcome'],'No demonstrated advantage')
        saved=self.lab.load(identifier)
        self.assertEqual(saved['comparison_history'][0]['comparison']['outcome'],'Earlier synthetic fixture')
        self.lab.compare(identifier)
        self.assertEqual(len(self.lab.load(identifier)['comparison_history']),1)


class EfficiencyTests(unittest.TestCase):
    def values(self,development=None,review_time=None,corrections=None):
        return {'development_wall_seconds':development,'review_wall_seconds':review_time,'correction_attempts':corrections,
                'autonomy':{},'autonomy_scope':'variant'}

    def test_equal_quality_measured_time_advantage_not_vetoed_by_unknown_tokens(self):
        outcome,metrics=Lab.efficiency_comparison(self.values(120,10,1),self.values(60,10,0),COMPARISON_POLICY)
        self.assertEqual(outcome,'Improvement observed')
        self.assertEqual(metrics['development_wall_seconds']['percent'],-50)
        self.assertEqual(metrics['input_tokens']['direction'],'unknown')

    def test_adverse_and_mixed_directions(self):
        outcome,_=Lab.efficiency_comparison(self.values(60,10,0),self.values(120,10,1),COMPARISON_POLICY)
        self.assertEqual(outcome,'Regression observed')
        outcome,_=Lab.efficiency_comparison(self.values(120,10,1),self.values(60,30,0),COMPARISON_POLICY)
        self.assertEqual(outcome,'Trade-off observed')

    def test_ties_and_no_evidence(self):
        outcome,_=Lab.efficiency_comparison(self.values(100,10,0),self.values(101,10,0),COMPARISON_POLICY)
        self.assertEqual(outcome,'No demonstrated advantage')
        outcome,_=Lab.efficiency_comparison(self.values(),self.values(),COMPARISON_POLICY)
        self.assertEqual(outcome,'Inconclusive result')

    def test_zero_baseline_has_no_invented_percentage(self):
        _,metrics=Lab.efficiency_comparison(self.values(corrections=0),self.values(corrections=1),COMPARISON_POLICY)
        self.assertIsNone(metrics['correction_attempts']['percent'])


class WrapperIntegrationTests(unittest.TestCase):
    def test_actual_http_adapter_forwards_bytes_and_status(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.lab') as temp:
            folder=Path(temp)
            (folder/'server.py').write_bytes((ROOT/'benchmarks/repair-queue-v1/starter/server.py').read_bytes())
            (folder/'app.py').write_text("def handle_request(method,target,body,headers,database):\n return 201, {'Content-Type':'text/plain'}, method.encode()+b' '+target.encode()+b' '+body\n",encoding='utf-8')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            with (folder/'server.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,'server.py','--port',str(port),'--data',str(folder/'unused.sqlite')],cwd=folder,stdout=log,stderr=log)
                try:
                    deadline=time.monotonic()+5
                    while True:
                        try:
                            connection=HTTPConnection('127.0.0.1',port,timeout=1)
                            try:
                                connection.request('POST','/actual?x=1',body=b'payload')
                                response=connection.getresponse()
                                self.assertEqual(response.status,201)
                                self.assertEqual(response.read(),b'POST /actual?x=1 payload')
                            finally:
                                connection.close()
                            break
                        except OSError:
                            if time.monotonic()>deadline:raise
                            time.sleep(.05)
                finally:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':unittest.main()
