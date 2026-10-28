"""Reuse accepted ADA sources across controller revisions without model calls."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ada_lab.campaigns import Campaigns, profile
from ada_lab.engine import Engine,CandidateRejected
from ada_lab.files import ROOT, copy_tree, digest, inventory, read, tree_digest,write


class CandidateReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='candidate-reuse-fixture-',dir=ROOT/'.lab')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.store=Campaigns(self.root/'state')
        self.stable=self.root/'stable';self.stable.mkdir()
        for name in ('README.md','check.py','evaluate.py'):
            (self.stable/name).write_text('Trusted fixture '+name,encoding='utf-8')
        self.baseline={'commit':'fixture','status':'','source_digest':tree_digest(self.stable)}
        self.candidate=self.root/'accepted-candidate';copy_tree(self.stable,self.candidate)
        (self.candidate/'README.md').write_text('Independently accepted fixture change',encoding='utf-8')
        old=profile('fixture','medium','fixture-runtime',execution_policy='old-controller',environment={'os':'fixture'})
        new=profile('fixture','medium','fixture-runtime',execution_policy='new-controller',environment={'os':'fixture'})
        with self.store.transaction() as data:
            data['suites']['suite']={'revision':'suite','name':'Fixture suite','tasks':[]}
        self.old=self.store.campaign('suite',old,old)['id']
        self.current=self.store.campaign('suite',new,new)['id']
        self.idea=self.store.idea({'title':'Reusable fixture','hypothesis':'Bounded fixture improvement','mechanism':'Fixture instructions',
            'scope':'README','tags':['fixture'],'sources':[]},self.current)['id']
        review_value={'status':'PASS','findings':[],'evidence':['Actual fixture independent source/behavior observations']}
        saved={'digest':tree_digest(self.candidate),'path':str(self.candidate),'intervention':{'change':'Accepted fixture'},
            'review':{'configuration_verified':True,'session_id':'fixture-final-review','evidence':'fixture review evidence',
                      'message':json.dumps(review_value)},'review_value':review_value,
            'design_review':{'session_id':'fixture-prospective-review','evidence':'fixture prospective evidence',
                'design_digest':'fixture-proposal','plan_digest':'fixture-plan',
                'verdict':{'status':'PASS','findings':[],'evidence':['Actual fixture prospective criticism'],'verification_cases':['Retain failures']}}}
        with self.store.transaction() as data:
            data['ideas'][self.idea]['status']='reviewed'
            data['trials']['prior']={'id':'prior','campaign':self.old,'idea':self.idea,'suite':'suite','stable':self.baseline,
                'profiles':{'development':old,'reviewer':old},'candidate':saved,'complete':False,'status':'blocked',
                'tasks':{'old-application':{'complete':False,'fixture':'Must not import application work'}}}
            data['campaigns'][self.old]['trials'].append('prior')
        self.engine=Engine(self.store,object())
        self.engine.lab=SimpleNamespace(config=lambda:{'ada':str(self.stable)})

    def validate(self,data=None,campaign=None,observed=None):
        data=data or self.store.snapshot();campaign=campaign or data['campaigns'][self.current]
        with patch('ada_lab.engine.identity',return_value=observed or self.baseline):
            return self.engine.reusable_candidate(data,campaign,'prior')

    def test_reuse_carries_exact_candidate_and_receipts_without_candidate_calls(self):
        with patch('ada_lab.engine.identity',return_value=self.baseline):
            job=self.engine.enqueue(self.current,'trial',{'idea':self.idea,'source_trial':'prior','reuse_accepted':True})
            self.assertEqual(job['reservation']['calls'],1)  # Zero candidate calls; only configured final reserve for empty fixture suite.
            self.engine.model=lambda *args,**kwargs:self.fail('Accepted candidate creation/review must not replay')
            self.engine.step=lambda *args,**kwargs:{'returncode':0}  # Native structural call is a deterministic fixture.
            scheduled=[]
            self.engine.schedule_tasks=lambda current,trial,suite,candidate,*unused:scheduled.append((trial,Path(candidate)))
            self.engine.finish_trial=lambda *args:None
            self.engine.trial(job)
        data=self.store.snapshot();trial=data['trials'][self.engine.current(job['id'])['trial']]
        self.assertEqual(trial['candidate']['reused_candidate_from'],'prior')
        self.assertEqual(trial['candidate']['review'],data['trials']['prior']['candidate']['review'])
        self.assertEqual(trial['candidate']['design_review'],data['trials']['prior']['candidate']['design_review'])
        self.assertEqual(tree_digest(trial['candidate']['path']),data['trials']['prior']['candidate']['digest'])
        self.assertNotEqual(trial['candidate']['path'],str(self.candidate))
        self.assertEqual(trial['tasks'],{});self.assertEqual(len(scheduled),1)

    def test_changed_baseline_or_suite_rejects_reuse(self):
        with self.assertRaisesRegex(ValueError,'same exact suite and pinned Stable'):
            self.validate(observed={**self.baseline,'source_digest':'changed'})
        campaign=copy.deepcopy(self.store.snapshot()['campaigns'][self.current]);campaign['suite']='different-suite'
        with self.assertRaisesRegex(ValueError,'same exact suite and pinned Stable'):self.validate(campaign=campaign)

    def test_model_effort_runtime_permissions_environment_cannot_mix(self):
        for role in ('development','reviewer'):
            for field,value in [('model','other'),('effort','high'),('runtime','other'),('provider','other')]:
                with self.subTest(role=role,field=field):
                    campaign=copy.deepcopy(self.store.snapshot()['campaigns'][self.current]);campaign[role][field]=value
                    with self.assertRaisesRegex(ValueError,'profile differs'):self.validate(campaign=campaign)
            for field,value in [('permissions','other-boundary'),('environment',{'os':'other'})]:
                with self.subTest(role=role,setting=field):
                    campaign=copy.deepcopy(self.store.snapshot()['campaigns'][self.current]);campaign[role]['settings'][field]=value
                    with self.assertRaisesRegex(ValueError,'profile differs'):self.validate(campaign=campaign)

    def test_changed_candidate_bytes_reject_reuse(self):
        (self.candidate/'README.md').write_text('Unreviewed modification',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'source changed'):self.validate()

    def test_missing_or_negative_final_proof_rejects_reuse(self):
        for field,value in [('review',{}),('review_value',{'status':'FAIL','findings':['Actual defect']})]:
            with self.subTest(field=field):
                data=self.store.snapshot();data['trials']['prior']['candidate'][field]=value
                with self.assertRaisesRegex(ValueError,'accepted independent'):self.validate(data=data)

    def test_missing_or_contradictory_prospective_proof_rejects_reuse(self):
        for proof in ({},{'verdict':{'status':'PASS','findings':['Unresolved material objection']}}):
            with self.subTest(proof=proof):
                data=self.store.snapshot();data['trials']['prior']['candidate']['design_review']=proof
                with self.assertRaisesRegex(ValueError,'prospective independent'):self.validate(data=data)

    def test_prospective_session_source_and_plan_bindings_are_required(self):
        for field in ('session_id','evidence','plan_digest','design_digest'):
            with self.subTest(field=field):
                data=self.store.snapshot();data['trials']['prior']['candidate']['design_review'].pop(field)
                with self.assertRaisesRegex(ValueError,'complete prospective independent'):self.validate(data=data)

    def test_reuse_cannot_attach_another_hypothesis(self):
        with patch('ada_lab.engine.identity',return_value=self.baseline):
            with self.assertRaisesRegex(ValueError,'another hypothesis'):
                self.engine.enqueue(self.current,'trial',{'idea':'another-idea','source_trial':'prior','reuse_accepted':True})
        self.assertEqual(self.store.snapshot()['jobs'],{})

    def qa_revision_fixture(self):
        source=self.root/'task';(source/'starter').mkdir(parents=True)
        (source/'task.md').write_text('Unchanged frozen assignment',encoding='utf-8')
        write(source/'benchmark.json',{'id':'fixture-web','criteria':[{'id':'mandatory','description':'Unchanged behavior'}]})
        (source/'starter/initial.txt').write_text('Unchanged starter',encoding='utf-8')
        current=self.store.task('fixture-web',source,'repair_queue_v1')['revision']
        old=self.snapshot_variant(current,'journeys.js',b'// Earlier trusted QA interpreter fixture\n')
        with self.store.transaction() as data:data['tasks']['fixture-web']['revision']=old
        before=self.store.suite('Before QA repair',['fixture-web'])['revision']
        with self.store.transaction() as data:data['tasks']['fixture-web']['revision']=current
        after=self.store.suite('After QA repair',['fixture-web'])['revision']
        data=self.store.snapshot();old_profile=data['campaigns'][self.old]['development'];new_profile=data['campaigns'][self.current]['development']
        old_campaign=self.store.campaign(before,old_profile,old_profile)['id']
        self.current=self.store.campaign(after,new_profile,new_profile)['id']
        with self.store.transaction() as data:
            data['trials']['prior'].update(suite=before,campaign=old_campaign)
            data['campaigns'][old_campaign]['trials'].append('prior')
        return before,after,current

    def snapshot_variant(self,revision,procedure=None,content=None,source_file=None,dependency=False):
        temporary=self.root/('snapshot-'+str(len(list(self.root.glob('snapshot-*')))))
        copy_tree(self.store.root/'tasks'/revision,temporary)
        identity=read(temporary/'identity.json')
        if procedure:
            (temporary/'procedures'/procedure).write_bytes(content)
            identity['procedures'][procedure]=hashlib.sha256(content).hexdigest()
        if source_file:
            (temporary/'source'/source_file).write_text('Changed mandatory input',encoding='utf-8')
            identity['files']=inventory(temporary/'source')
        if dependency:
            path=temporary/'dependencies/pyproject.toml'
            path.parent.mkdir(exist_ok=True);path.write_text('Changed frozen dependency manifest',encoding='utf-8')
            identity['dependencies']={**identity.get('dependencies',{}),'pyproject.toml':hashlib.sha256(path.read_bytes()).hexdigest()}
        new=digest(identity);write(temporary/'identity.json',identity)
        temporary.rename(self.store.root/'tasks'/new)
        self.store.verify_task(new)
        return new

    def test_qa_only_transfer_has_exact_diff_and_cannot_change_other_inputs(self):
        before,after,current=self.qa_revision_fixture();data=self.store.snapshot()
        result=self.engine.candidate_compatibility(data,before,after)
        self.assertEqual(result['kind'],'qa-only-source-transfer')
        self.assertEqual([x['procedure'] for x in result['changes']],['journeys.js'])
        self.assertNotEqual(result['changes'][0]['before'],result['changes'][0]['after'])
        self.assertIn('--- before/journeys.js',result['changes'][0]['diff'])
        with_qa=self.snapshot_variant(current,'qa.py',b'# Explicitly allowed QA action validator fixture\n')
        changed=copy.deepcopy(data);changed['suites'][after]['tasks'][0]['revision']=with_qa
        combined=self.engine.candidate_compatibility(changed,before,after)
        self.assertEqual({item['procedure'] for item in combined['changes']},{'qa.py','journeys.js'})
        variants=[('other-procedure',self.snapshot_variant(current,'evaluator.py',b'# Changed mandatory evaluator\n')),
                  ('task',self.snapshot_variant(current,source_file='task.md')),
                  ('criteria',self.snapshot_variant(current,source_file='benchmark.json')),
                  ('starter',self.snapshot_variant(current,source_file='starter/initial.txt')),
                  ('dependencies',self.snapshot_variant(current,dependency=True))]
        for label,revision in variants:
            with self.subTest(change=label):
                changed=copy.deepcopy(data);changed['suites'][after]['tasks'][0]['revision']=revision
                with self.assertRaises(ValueError):self.engine.candidate_compatibility(changed,before,after)
        for name in self.store.verify_task(current)['procedures'].keys()-{'qa.py','journeys.js'}:
            with self.subTest(forbidden_procedure=name):
                revision=self.snapshot_variant(current,name,b'Forbidden procedure delta fixture\n')
                changed=copy.deepcopy(data);changed['suites'][after]['tasks'][0]['revision']=revision
                with self.assertRaisesRegex(ValueError,'allowlist'):
                    self.engine.candidate_compatibility(changed,before,after)
        changed=copy.deepcopy(data);changed['suites'][after]['policy']={'quality':'weakened'}
        with self.assertRaisesRegex(ValueError,'same exact suite'):self.engine.candidate_compatibility(changed,before,after)
        changed=copy.deepcopy(data)
        second={**changed['suites'][before]['tasks'][0],'id':'second-task'}
        changed['suites'][before]['tasks'].append(second)
        changed['suites'][after]['tasks'].insert(0,second)
        with self.assertRaisesRegex(ValueError,'ordered task selection'):self.engine.candidate_compatibility(changed,before,after)

    def test_qa_transfer_requires_fresh_verified_pass_before_clean_task_dispatch(self):
        before,after,current=self.qa_revision_fixture()
        for outcome,verified in [('PASS',True),('FAIL',True),('BLOCKED',True),('PASS',False)]:
            with self.subTest(outcome=outcome,verified=verified):
                order=[]
                with patch('ada_lab.engine.identity',return_value=self.baseline):
                    job=self.engine.enqueue(self.current,'trial',{'idea':self.idea,'source_trial':'prior','reuse_accepted':True})
                    self.assertEqual(job['reservation']['calls'],22)  # 20 app stages + 1 format reserve + 1 fresh compatibility stage.
                    def model(current,key,selected,workspace,prompt,roots=(),images=()):
                        self.assertEqual(key,'candidate-compatibility');order.append(key)
                        evidence=workspace.parent/'candidate-compatibility-evidence'
                        self.assertIn(evidence,roots)
                        transfer=read(evidence/'TRANSFER.json')
                        self.assertEqual(transfer['compatibility']['kind'],'qa-only-source-transfer')
                        self.assertEqual(transfer['candidate_digest'],tree_digest(self.candidate))
                        value={'status':outcome,'findings':[] if outcome=='PASS' else ['Actual fixture incompatibility or missing evidence'],'evidence':['Fresh independent fixture review']}
                        return {'session_id':'fresh-fixture-review','evidence':str(evidence),'configuration_verified':verified,'message':json.dumps(value)}
                    self.engine.model=model
                    self.engine.step=lambda *args,**kwargs:{'returncode':0}
                    self.engine.schedule_tasks=lambda *args:order.append('clean-task-dispatch')
                    self.engine.finish_trial=lambda *args:None
                    if outcome=='PASS' and verified:self.engine.trial(job)
                    elif outcome=='FAIL':
                        with self.assertRaises(CandidateRejected):self.engine.trial(job)
                    else:
                        with self.assertRaises(RuntimeError) as caught:self.engine.trial(job)
                        self.assertNotIsInstance(caught.exception,CandidateRejected)
                self.assertEqual(order,['candidate-compatibility','clean-task-dispatch'] if outcome=='PASS' and verified else ['candidate-compatibility'])
                data=self.store.snapshot();trial=data['trials'][self.engine.current(job['id'])['trial']]
                self.assertEqual(trial['tasks'],{})
                if outcome=='PASS' and verified:
                    self.assertEqual(trial['candidate']['compatibility_review']['session_id'],'fresh-fixture-review')
                self.store.control(self.current,'experiment','stop')


if __name__=='__main__':unittest.main()
