"""Deterministic fixtures exercise the real multi-task dispatcher; no AI calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ada_lab.campaigns import Campaigns, aggregate, profile, POLICY
from ada_lab.engine import Engine
from ada_lab.files import ROOT, digest, inventory, read, tree_digest, write


class FixtureAdapter:
    def __init__(self):self.calls=[];self.block=None;self.failed=None
    def verify(self,engine,task,identity):engine.store.verify_task(task['revision'])
    def preflight(self,engine):pass
    def execute(self,engine,job,trial,task,*unused):
        self.calls.append(task['id'])
        if self.block==task['id']:raise RuntimeError('Deliberately unavailable fixture capability')
        arm={'complete':True,'quality':True,'tokens':100,'correction_attempts':0,'configuration_verified':True}
        with engine.store.transaction() as data:
            data['trials'][trial]['tasks'][task['id']]={'complete':True,'stable':dict(arm),'candidate':{**arm,'quality':self.failed!=task['id'],'tokens':80}}


class CampaignTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.lab').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='controlled-fixture-',dir=ROOT/'.lab')
        self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.store=Campaigns(self.base/'state');self.p=profile('fixture-model','medium','fixture-runtime')
        for index in range(3):self.make_task('fixture-'+str(index),'Specification '+str(index))
        self.suite=self.store.suite('Three fixture tasks',['fixture-0','fixture-1','fixture-2'])
        self.c=self.store.campaign(self.suite['revision'],self.p,self.p)['id']

    def make_task(self,name,text):
        folder=self.base/name;folder.mkdir(exist_ok=True);(folder/'starter').mkdir(exist_ok=True)
        (folder/'task.md').write_text(text,encoding='utf-8');write(folder/'benchmark.json',{'id':name,'criteria':['mandatory']})
        (folder/'starter'/'initial.txt').write_text('clean starter',encoding='utf-8')
        return self.store.task(name,folder,'fixture',['fixture-capability'])

    def test_exact_task_suite_identity_and_order(self):
        old=self.suite['revision']
        self.assertEqual(old,self.store.suite('Another display title',['fixture-0','fixture-1','fixture-2'])['revision'])
        self.assertNotEqual(old,self.store.suite('Reordered',['fixture-1','fixture-0','fixture-2'])['revision'])
        task_before=self.store.snapshot()['tasks']['fixture-1']['revision']
        self.make_task('fixture-1','Actual changed specification')
        self.assertNotEqual(task_before,self.store.snapshot()['tasks']['fixture-1']['revision'])
        changed=self.store.suite('Changed task',['fixture-0','fixture-1','fixture-2'])['revision']
        self.assertNotEqual(old,changed)
        self.store.verify_task(task_before)
        self.assertEqual(self.store.snapshot()['suites'][old]['tasks'][1]['revision'],task_before)

    def test_campaign_identity_profile_changes_and_global_idea_reuse(self):
        self.assertEqual(self.c,self.store.campaign(self.suite['revision'],self.p,self.p,name='Editable title')['id'])
        high=profile('fixture-model','high','fixture-runtime')
        other=self.store.campaign(self.suite['revision'],high,self.p)['id']
        self.assertNotEqual(self.c,other)
        i=self.store.idea({'title':'Fixture idea','hypothesis':'Try a bounded change','mechanism':'A distinct mechanism','tags':['fixture'],'sources':[],'scope':'One fixture change'},self.c)
        self.assertEqual(i['id'],self.store.idea(i,other)['id'])
        data=self.store.snapshot();self.assertEqual(len(data['ideas']),1);self.assertIn(i['id'],data['campaigns'][other]['queue'])
        self.assertEqual(data['campaigns'][other]['trials'],[])

    def test_source_bearing_research_reaches_critic_with_read_only_ada_path(self):
        """Exercise retrieval, durable proposals, independent criticism and ranking."""
        engine=Engine(self.store,runtime=object())
        ada=self.base/'assigned-ada'
        engine.lab=SimpleNamespace(config=lambda:{'ada':str(ada)},history=lambda:[])
        job=self.store.admit(self.c,'research',{'iterations':1},2,100)
        citation={'url':'https://arxiv.org/abs/fixture','date':'2026-10-28','quality':'fixture primary source'}
        proposals={'ideas':[{'title':'Hypothesis '+str(n),'hypothesis':'Test bounded change '+str(n),
            'mechanism':'Distinct mechanism '+str(n),'tags':['fixture'],'sources':[dict(citation)],
            'scope':'A single instruction change'} for n in range(2)],'research_limitations':['Deterministic fixture; no live research']}
        calls=[]

        def model(current,key,selected,workspace,prompt,read_roots=(),images=()):
            calls.append(key)
            self.assertEqual(current['id'],job['id'])
            self.assertEqual(read_roots,[ada])
            self.assertIsInstance(read_roots[0],Path)
            self.assertIn(str(ada),prompt)
            if key=='research-0':
                return {'message':json.dumps(proposals),'session_id':'fixture-research'}
            self.assertEqual(key,'critic-0')
            payload=read(workspace/'IDEAS.json')
            self.assertEqual(len(payload['ideas']),2)
            for idea in payload['ideas']:
                self.assertTrue(idea['sources'][0]['verified_retrieval'])
            names=('quality','resources','evidence','generality','simplicity','experimental_value','low_risk')
            return {'session_id':'fixture-independent-critic','message':json.dumps({'reviews':[
                {'id':idea['id'],'scores':dict.fromkeys(names,9-index),'confidence':'medium',
                 'objections':['Actual support and regression risk still require an experiment']}
                for index,idea in enumerate(payload['ideas'])]})}

        def retrieved(source,folder):
            self.assertEqual(source,citation)
            return {**source,'verified_retrieval':True,'retrieved_at':'fixture timestamp',
                    'excerpt':'Deterministic source fixture; no network request'}

        with (patch.object(engine,'model',side_effect=model),
              patch('ada_lab.engine.identity',return_value={'commit':'fixture','source_digest':'fixture','status':''}),
              patch('ada_lab.engine.retrieve',side_effect=retrieved) as retrieval):
            engine.research_iteration(job,0)
        self.assertEqual(calls,['research-0','critic-0'])
        retrieval.assert_called_once()
        data=Campaigns(self.store.root).snapshot()
        queue=data['campaigns'][self.c]['queue']
        self.assertEqual(len(queue),2)
        self.assertEqual([data['ideas'][i]['priority'] for i in queue],[9.0,8.0])
        for identifier in queue:
            idea=data['ideas'][identifier]
            self.assertEqual(idea['status'],'reviewed')
            self.assertEqual(idea['reviews'][0]['source'],'fixture-independent-critic')
            self.assertTrue(idea['sources'][0]['verified_retrieval'])

    def test_frozen_procedure_and_source_tampering_rejected(self):
        revision=self.suite['tasks'][0]['revision']
        (self.store.root/'tasks'/revision/'source/task.md').write_text('tampered',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'identity'):self.store.verify_task(revision)
        second=self.suite['tasks'][1]['revision']
        (self.store.root/'tasks'/second/'procedures/qa.py').write_text('tampered',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'procedure'):self.store.verify_task(second)

    def test_three_task_real_dispatcher_resume_preserves_completed_work(self):
        adapter=FixtureAdapter();engine=Engine(self.store,runtime=object(),adapters={'fixture':adapter})
        trial='fixture-trial'
        with self.store.transaction() as data:data['trials'][trial]={'tasks':{}}
        adapter.block='fixture-2'
        with self.assertRaisesRegex(RuntimeError,'unavailable'):
            engine.schedule_tasks({},trial,self.suite,None,None,None,{})
        partial=self.store.snapshot()['trials'][trial]['tasks']
        self.assertFalse(aggregate(partial,['fixture-0','fixture-1','fixture-2'])['complete'])
        self.assertEqual(adapter.calls,['fixture-0','fixture-1','fixture-2'])
        adapter.block=None;new_engine=Engine(Campaigns(self.store.root),runtime=object(),adapters={'fixture':adapter})
        new_engine.schedule_tasks({},trial,self.suite,None,None,None,{})
        self.assertEqual(adapter.calls,['fixture-0','fixture-1','fixture-2','fixture-2'])
        result=aggregate(self.store.snapshot()['trials'][trial]['tasks'],['fixture-0','fixture-1','fixture-2'])
        self.assertTrue(result['complete']);self.assertEqual(result['tokens'],{'stable':300,'candidate':240})

    def test_failed_task_cannot_hide_behind_favorable_tokens(self):
        adapter=FixtureAdapter();adapter.failed='fixture-1';engine=Engine(self.store,runtime=object(),adapters={'fixture':adapter})
        with self.store.transaction() as data:data['trials']['fixture-trial']={'tasks':{}}
        engine.schedule_tasks({},'fixture-trial',self.suite,None,None,None,{})
        result=aggregate(self.store.snapshot()['trials']['fixture-trial']['tasks'],['fixture-0','fixture-1','fixture-2'])
        self.assertEqual(result['outcome'],'Candidate quality failure');self.assertFalse(result['quality']['candidate'])

    def test_live_procedure_mismatch_is_not_a_frozen_equivalence(self):
        engine=Engine(self.store,runtime=object())
        task=self.suite['tasks'][0]
        original=Path.read_bytes
        def changed(path):return b'different installed procedure' if path==ROOT/'ada_lab/qa.py' else original(path)
        with patch.object(Path,'read_bytes',changed):
            with self.assertRaisesRegex(RuntimeError,'procedure'):engine.verify_installed(task)

    def test_atomic_admission_and_concurrent_single_job(self):
        self.store.settings(self.c,{'max_calls':20})
        results=[];errors=[]
        def admit():
            try:results.append(self.store.admit(self.c,'research',{},2,100))
            except ValueError:pass
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=admit) for _ in range(5)]
        for t in threads:t.start()
        for t in threads:t.join()
        data=self.store.snapshot();self.assertEqual(len(results),1);self.assertEqual(len(data['jobs']),1)
        self.assertEqual(errors,[])
        self.assertEqual(data['campaigns'][self.c]['usage']['reserved_calls'],2)
        self.assertEqual(next(iter(data['jobs'].values()))['reservation']['calls'],2)

    def test_reliable_budget_refusal_has_no_launch_or_leak(self):
        self.store.settings(self.c,{'max_calls':2,'token_budget':100})
        with self.assertRaisesRegex(ValueError,'token budget'):self.store.admit(self.c,'research',{},2,100)
        self.assertEqual(self.store.snapshot()['jobs'],{})
        self.assertEqual(self.store.snapshot()['campaigns'][self.c]['usage']['reserved_calls'],0)
        with self.assertRaises(ValueError):self.store.admit(self.c,'research',{},3,1)

    def test_recovery_requires_explicit_resume_and_keeps_stage_receipts(self):
        job=self.store.admit(self.c,'research',{},2,100)
        with self.store.transaction() as data:
            j=data['jobs'][job['id']];j['status']='running';j['stages']={'done':{'status':'completed','tokens':25},'uncertain':{'status':'running','tokens':None}}
        restarted=Campaigns(self.store.root);restarted.recover();data=restarted.snapshot()
        self.assertEqual(data['jobs'][job['id']]['status'],'interrupted');self.assertEqual(data['campaigns'][self.c]['research_state'],'paused')
        restarted.resume_admitted(job['id'],2,100)
        self.assertEqual(restarted.snapshot()['jobs'][job['id']]['stages']['done']['status'],'completed')
        self.assertEqual(restarted.snapshot()['jobs'][job['id']]['stages']['uncertain']['status'],'interrupted')
        self.assertEqual(restarted.snapshot()['campaigns'][self.c]['usage']['reserved_calls'],2)

    def test_pause_does_not_kill_current_model_stop_does(self):
        job=self.store.admit(self.c,'research',{},2,100);engine=Engine(self.store,runtime=object())
        self.store.control(self.c,'research','pause')
        self.assertTrue(engine.cancelled(job['id']));self.assertFalse(engine.cancel_active(job['id']))
        self.store.control(self.c,'research','stop');self.assertTrue(engine.cancel_active(job['id']))
        self.assertEqual(self.store.snapshot()['campaigns'][self.c]['usage']['reserved_calls'],0)

    def test_read_only_snapshot_does_not_rewrite_completed_state(self):
        target=self.store.root/'state.json';before=target.stat().st_mtime_ns
        self.store.snapshot();self.assertEqual(before,target.stat().st_mtime_ns)


class AggregationTests(unittest.TestCase):
    def test_unknown_tokens_never_become_zero(self):
        task={'complete':True,'stable':{'quality':True,'tokens':100},'candidate':{'quality':True,'tokens':None}}
        result=aggregate({'a':task},['a'])
        self.assertIsNone(result['tokens']['candidate']);self.assertIn('limited',result['outcome'])

    def test_missing_or_incomplete_task_cannot_pass(self):
        self.assertEqual(aggregate({},['a','b','c'])['missing'],['a','b','c'])


if __name__=='__main__':unittest.main()
