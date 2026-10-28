"""Controller recovery and rejection regression fixtures.

Model/native outcomes are deterministic substitutes. No inference, network
request or generated application code runs in these tests. All fixture files
remain in temporary ignored workspaces and are removed after each test.
"""
import json,tempfile,unittest,hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ada_lab.campaigns import Campaigns,profile
from ada_lab.engine import Engine,CandidateRejected
from ada_lab.runtime import ModelOutputError
from ada_lab.files import ROOT,write,read,digest,tree_digest

class EngineRecoveryTests(unittest.TestCase):
 def setUp(self):
  (ROOT/'.lab').mkdir(exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(prefix='engine-recovery-fixture-',dir=ROOT/'.lab')
  self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)
  self.store=Campaigns(self.root/'state');self.p=profile('fixture','medium','fixture')
  with self.store.transaction() as d:
   d['suites']['suite']={'revision':'suite','tasks':[],'name':'Fixture suite'}
  self.c=self.store.campaign('suite',self.p,self.p)['id']
 def test_uncertain_started_attempt_enters_campaign_unknown_accounting(self):
  job=self.store.admit(self.c,'research',{},2,200)
  with self.store.transaction() as d:
   j=d['jobs'][job['id']];j['status']='running';j['reservation']={'calls':1,'tokens':100}
   j['stages']['probe']={'key':'probe','status':'running','receipt':str(self.root/'missing-receipt.json'),'tokens':None,'accounted':False,'input_digest':'old'}
   u=d['campaigns'][self.c]['usage'];u.update(calls=1,reserved_calls=1,reserved_tokens=100)
  self.store.recover();self.store.resume_admitted(job['id'],1,100)
  class Runtime:
   def run(_,profile,workspace,prompt,output,*args,**kw):
    r={'status':'completed','usage':{'input_tokens':10,'output_tokens':2},'session_id':'fixture-session','configuration_verified':True,'wall_seconds':1,'errors':[],'requested':profile,'message':'{}','evidence':str(output)}
    write(Path(output)/'receipt.json',r);return r
  engine=Engine(self.store,Runtime());engine.model(engine.current(job['id']),'probe',self.p,self.root/'workspace','fixture prompt')
  data=self.store.snapshot();u=data['campaigns'][self.c]['usage']
  write(self.root/'observation.json',{'case':'uncertain_attempt_accounting','usage':u,'history':data['jobs'][job['id']]['stage_history']})
  self.assertEqual(u['unknown_usage_stages'],1,'Missing-receipt previous model attempt must not disappear from campaign budget uncertainty')
 def test_correction_completed_receipt_recovers_before_old_evaluation(self):
  job=self.store.admit(self.c,'research',{},3,300);prefix='repair-queue-v1-stable';f=self.root/'experiment';w=f/'stable-workspace'
  (w/'ada').mkdir(parents=True);(f/'stable').mkdir();(f/'benchmark/starter').mkdir(parents=True)
  (w/'PLAN.md').write_text('frozen plan',encoding='utf-8');(w/'TASK.md').write_text('task',encoding='utf-8');(w/'server.py').write_text('adapter',encoding='utf-8');(w/'app.py').write_text('already corrected application',encoding='utf-8')
  (f/'benchmark/task.md').write_text('task',encoding='utf-8');(f/'benchmark/starter/server.py').write_text('adapter',encoding='utf-8');write(f/'benchmark/benchmark.json',{'criteria':[]})
  write(f/'stable-preparation.json',{'files':{'PLAN.md':hashlib.sha256((w/'PLAN.md').read_bytes()).hexdigest()},'app_absent':True,'source_ref':'fixture-preimplementation'})
  write(self.store.root/'jobs'/job['id']/'repair-queue-v1-qa-plan.json',{'api_checks':[{'expected_status':200}],'browser_steps':[{'op':'reload'}]})
  receipt=self.root/'correction-receipt.json';write(receipt,{'status':'completed','requested':self.p,'source_after':tree_digest(w),'usage':{'input_tokens':1,'output_tokens':1}})
  with self.store.transaction() as d:
   j=d['jobs'][job['id']];j['correction_context']={prefix:{'attempt':0,'before':'original-old-digest','findings_ref':'real-prior-finding','prompt':'fixture exact correction prompt'}}
   j['stages'][prefix+'-correction-0']={'status':'running','receipt':str(receipt)}
  engine=Engine(self.store,object());calls=[]
  engine.lab=SimpleNamespace(folder=lambda _:f,load=lambda _: {'runs':{'stable':{'reviews':{'design':{'status':'PASS','subject_digest':digest('frozen plan')}}}}},correction=lambda *a,**k:calls.append('recover-correction'))
  def model(job,key,*args,**kw):
   self.assertGreater(len(args),6)
   self.assertIn(f/'stable-ada-regression',args[6])
   if '-correction-' in key:
    with self.store.transaction() as d:d['jobs'][job['id']]['stages'][key]['status']='completed'
    return read(receipt)
   if key.endswith('-design'):return {'session_id':'design','evidence':'fixture','message':json.dumps({'status':'PASS','findings':[],'evidence':['observed'],'qa_plan':{'api_checks':[],'browser_steps':[]}})}
   return {'session_id':'fixture','evidence':'fixture','message':'{}'}
  engine.work_model=model
  def step(job,key,*args,**kw):
   if key.endswith('-assigned-ada-regression'):return {'status':'PASS','candidate_digest':'fixture'}
   calls.append(key);raise RuntimeError('STOP_BEFORE_ANY_RUNTIME_EXECUTION')
  engine.step=step
  try:engine.arm(engine.current(job['id']),'repair-queue-v1','fixture-exp','stable',{'development':self.p,'reviewer':self.p})
  except RuntimeError as e:self.assertEqual(str(e),'STOP_BEFORE_ANY_RUNTIME_EXECUTION')
  write(self.root/'observation.json',{'case':'correction_receipt_recovery_order','calls':calls})
  self.assertEqual(calls[0],'recover-correction','Completed correction receipt must reconcile before old attempt evidence is reused')
 def test_checkpoint_binding_rejects_changed_source(self):
  job=self.store.admit(self.c,'research',{},2,200);e=Engine(self.store,object());n=[]
  e.step(job,'fixed',lambda:n.append(1) or {'ok':True},{'artifact':'old'})
  with self.assertRaisesRegex(RuntimeError,'inputs changed'):e.step(job,'fixed',lambda:n.append(2),{'artifact':'new'})
  self.assertEqual(n,[1])
 def test_stop_releases_queued_reservation_and_keeps_receipts(self):
  job=self.store.admit(self.c,'research',{},2,200);self.store.control(self.c,'research','stop');d=self.store.snapshot()
  self.assertEqual(d['campaigns'][self.c]['usage']['reserved_calls'],0);self.assertEqual(d['jobs'][job['id']]['status'],'stopped')

 def test_preparation_archives_initial_status_but_only_freezes_plan(self):
  """Run actual arm preparation while its fixture developer updates live records."""
  for change_plan in (False,True):
   with self.subTest(change_plan=change_plan):
    job=self.store.admit(self.c,'trial',{},23,2300)
    f=self.root/('plan-change' if change_plan else 'status-change');w=f/'stable-workspace'
    (w/'ada').mkdir(parents=True);(f/'stable').mkdir();(f/'benchmark/starter').mkdir(parents=True)
    for name,value in [('PLAN.md','frozen plan'),('STATUS.md','Design complete; implementation pending'),('TASK.md','task'),('server.py','adapter')]:
     (w/name).write_text(value,encoding='utf-8')
    write(w/'ada-project.json',{'status':'STATUS.md','phase':'design'})
    write(w/'.cache/current.json',{'verdict':'BLOCKED','phase':'design'})
    (f/'benchmark/task.md').write_text('task',encoding='utf-8');(f/'benchmark/starter/server.py').write_text('adapter',encoding='utf-8')
    write(f/'benchmark/benchmark.json',{'criteria':[]})
    reviews={};engine=Engine(self.store,object())
    engine.lab=SimpleNamespace(folder=lambda _:f,load=lambda _:{'runs':{'stable':{'reviews':reviews}}},
     add_review=lambda exp,arm,phase,receipt:reviews.update({phase:receipt}))

    def model(current,key,*args,**kwargs):
     if key.endswith('-design'):
      message={'status':'PASS','evidence':['Fixture independent design'],'findings':[],
       'qa_plan':{'api_checks':[{'expected_status':200}],'browser_steps':[{'op':'reload'}]}}
     else:message={}
     if key.endswith('-implementation'):
      self.assertFalse((w/'app.py').exists())
      (w/'STATUS.md').write_text('Implementation complete; independent verification pending',encoding='utf-8')
      write(w/'ada-project.json',{'status':'STATUS.md','phase':'verification'})
      write(w/'.cache/current.json',{'verdict':'BLOCKED','phase':'independent verification'})
      (w/'app.py').write_text('Fixture source text; never executed',encoding='utf-8')
      if change_plan:(w/'PLAN.md').write_text('silently weakened plan',encoding='utf-8')
     return {'session_id':'fixture-'+key,'evidence':'Fixture actual controller invocation','message':json.dumps(message)}

    engine.work_model=model
    reached=[]
    def stop_before_native(current,key,*args,**kwargs):
     if key.endswith('-assigned-ada-regression'):return {'status':'PASS','candidate_digest':'fixture'}
     reached.append(key);raise RuntimeError('STOP_BEFORE_ANY_RUNTIME_EXECUTION')
    engine.step=stop_before_native
    expected='Frozen preparation artifact changed' if change_plan else 'STOP_BEFORE_ANY_RUNTIME_EXECUTION'
    with self.assertRaisesRegex(RuntimeError,expected):
     engine.arm(engine.current(job['id']),'repair-queue-v1','fixture-exp','stable',{'development':self.p,'reviewer':self.p})
    preparation=read(f/'stable-preparation.json');archive=Path(preparation['archive'])
    self.assertEqual(set(preparation['files']),{'PLAN.md'})
    self.assertEqual(set(preparation['initial_files']),{'PLAN.md','STATUS.md','ada-project.json'})
    self.assertTrue(preparation['app_absent'])
    self.assertEqual((archive/'STATUS.md').read_text(encoding='utf-8'),'Design complete; implementation pending')
    self.assertEqual(read(archive/'ada-project.json'),{'status':'STATUS.md','phase':'design'})
    self.assertFalse((archive/'.cache/current.json').exists())
    self.assertEqual(reached,[] if change_plan else ['repair-queue-v1-stable-evaluate-0'])
    self.store.control(self.c,'experiment','stop')

 def test_real_arm_third_quality_failure_returns_negative_result(self):
  self.negative_arm_fixture('FAIL')

 def test_reviewer_pass_cannot_override_failed_mandatory_checks(self):
  self.negative_arm_fixture('PASS')

 def negative_arm_fixture(self,review_status):
  from ada_lab.lab import Lab
  from ada_lab.files import inventory
  job=self.store.admit(self.c,'trial',{},23,2300);prefix='repair-queue-v1-candidate';f=self.root/'experiment';w=f/'candidate-workspace'
  (w/'ada').mkdir(parents=True);(f/'candidate').mkdir();(f/'benchmark/starter').mkdir(parents=True)
  for name,text in [('PLAN.md','frozen plan'),('TASK.md','task'),('server.py','adapter'),('app.py','fixture text, never executed')]:
   (w/name).write_text(text,encoding='utf-8')
  (f/'benchmark/task.md').write_text('task',encoding='utf-8');(f/'benchmark/starter/server.py').write_text('adapter',encoding='utf-8')
  write(f/'benchmark/benchmark.json',{'criteria':[{'id':'validation','description':'Actual failure fixture'}]})
  write(f/'candidate-preparation.json',{'files':{'PLAN.md':hashlib.sha256((w/'PLAN.md').read_bytes()).hexdigest()},'app_absent':True,'source_ref':'fixture'})
  write(self.store.root/'jobs'/job['id']/'repair-queue-v1-qa-plan.json',{'api_checks':[{'expected_status':200}],'browser_steps':[{'op':'reload'}]})
  record={'status':'working','runs':{'candidate':{'reviews':{'design':{'status':'PASS','subject_digest':digest('frozen plan'),
   'session_ref':'fixture-'+prefix+'-design','source_ref':'Fixture prospective independent review','evidence':['Actual fixture design receipt'],'findings':[]}},'corrections':[]}}}
  calls=[];engine=Engine(self.store,object())
  def review(exp,arm,phase,receipt):record['runs'][arm]['reviews'][phase]=receipt
  def correction(exp,arm,receipt):record['runs'][arm]['corrections'].append(receipt)
  engine.lab=SimpleNamespace(folder=lambda _:f,load=lambda _:record,save=lambda r:None,add_review=review,correction=correction,
   config=lambda:{'ada':'TRUSTED_FIXTURE_ONLY'},state=self.root,observed_environment=Lab.observed_environment,
   submit=lambda *a,**k:self.fail('Rejected source must never pass Lab.submit'),evaluate_run=lambda *a,**k:self.fail('Rejected source must never be accepted'))
  def model(job,key,*args,**kw):
   self.assertGreater(len(args),6)
   self.assertIn(f/'candidate-ada-regression',args[6])
   if key.endswith('-plan') or key.endswith('-implementation') or '-correction-' in key:
    self.assertIn(str(f/'candidate-ada-regression/result.json'),args[5])
   if '-final-' in key:
    attempt=int(key.rsplit('-',1)[1]);evidence_root=f/'candidate-review-observations'
    self.assertIn(evidence_root,args[6])
    self.assertFalse(evidence_root.is_relative_to(args[4]))
    observed=read(evidence_root/('OBSERVATIONS-'+str(attempt)+'.json'))
    prospective=observed['prospective_design']
    self.assertEqual(prospective['plan_session'],'fixture-'+prefix+'-plan')
    self.assertEqual(prospective['review_session'],'fixture-'+prefix+'-design')
    self.assertEqual(prospective['independent_review']['session_ref'],prospective['review_session'])
    self.assertEqual(prospective['implementation_session'],'fixture-'+prefix+'-implementation')
    self.assertEqual(prospective['review_output']['status'],'PASS')
    self.assertEqual(prospective['plan_file_sha256'],hashlib.sha256((w/'PLAN.md').read_bytes()).hexdigest())
    self.assertIn('Phase 4',observed['acceptance_phase'])
    self.assertIn('Phase 3 independent observer',args[5])
    self.assertIn('do not claim it passed',args[5])
    stages=[stage['key'] for stage in observed['stage_order']]
    self.assertLess(stages.index(prefix+'-plan'),stages.index(prefix+'-design'))
    self.assertLess(stages.index(prefix+'-design'),stages.index(prefix+'-implementation'))
    commands=read(observed['complete_lead_command_evidence'])
    self.assertIn(prefix+'-plan',[item['stage'] for item in commands])
    self.assertIn(prefix+'-implementation',[item['stage'] for item in commands])
    self.assertTrue(all(len(item['output'])>2500 for item in commands))
    self.assertTrue(all(len(item['output'])<=2500 for item in observed['lead_tool_observations']))
   calls.append(key)
   if '-correction-' in key:(w/'app.py').write_text((w/'app.py').read_text(encoding='utf-8')+' correction',encoding='utf-8')
   if '-final-' in key:message={'status':review_status,'evidence':['Fixture independent observation'],
    'findings':[{'id':'actual-failure','actual':'Fixture runtime failure','fix':'Repair actual failing behavior'}] if review_status=='FAIL' else [],
    'visual_observations':['Fixture desktop','Fixture mobile'],'protocol_observations':['Fixture preparation observed']}
   elif key.endswith('-design'):message={'status':'PASS','evidence':['Fixture design'],'findings':[]}
   else:message={}
   receipt=self.root/'receipts'/key/'receipt.json';result={'message':json.dumps(message),'session_id':'fixture-'+key,'evidence':str(receipt.parent),'usage':{'input_tokens':10,'output_tokens':2},'source_after':tree_digest(w),'requested':self.p};write(receipt,result)
   (receipt.parent/'events.jsonl').write_text(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'Fixture command '+key,'exit_code':0,'aggregated_output':'Preserved full fixture output '*140}})+'\n',encoding='utf-8')
   with self.store.transaction() as d:d['jobs'][job['id']]['stages'][key]={'key':key,'status':'completed','tokens':12,'configuration_verified':True,'receipt':str(receipt),'session_id':'fixture-'+key,'started':'fixture dispatch','finished':'fixture completion'}
   return result
  engine.work_model=model
  def step(job,key,operation,inputs=None):
   if key.endswith('-assigned-ada-regression'):return {'status':'PASS','candidate_digest':'fixture'}
   if '-evaluate-' in key:return {'quality_pass':False,'artifact_digest':tree_digest(w),'benchmark_revision':'fixture','criteria_digest':'fixture','evaluator_version':'fixture','evidence':str(self.root/'native-fixture/result.json'),'criteria':{'validation':{'status':'FAIL','detail':'Fixture actual negative observation'}}}
   if '-structure-' in key:return {'returncode':0}
   if '-actions-' in key:return {'status':'FAIL','images':[],'results':[{'status':'FAIL','fixture':True}]}
   self.fail('Unexpected fixture stage '+key)
  engine.step=step
  result=engine.arm(engine.current(job['id']),'repair-queue-v1','fixture-exp','candidate',{'development':self.p,'reviewer':self.p})
  self.assertTrue(result['complete']);self.assertFalse(result['quality']);self.assertEqual(result['independent_status'],'FAIL')
  self.assertEqual(result['correction_attempts'],2);self.assertEqual(record['runs']['candidate']['status'],'rejected')
  self.assertEqual(len([k for k in calls if '-final-' in k]),3);self.assertEqual(tree_digest(result['artifact']),result['artifact_digest'])
  write(self.root/'negative-arm-result.json',result)

 def test_rejected_candidate_queue_advances_without_redevelopment(self):
  def idea(n):
   value=self.store.idea({'title':n,'hypothesis':n,'mechanism':n,'tags':['fixture'],'sources':[],'scope':'fixture'},self.c)
   with self.store.transaction() as d:d['ideas'][value['id']].update(status='reviewed',priority=9)
   return value['id']
  first,second=idea('first rejection'),idea('second pending')
  with self.store.transaction() as d:
   c=d['campaigns'][self.c];c.update(auto_queue=True,pending_candidates=[first,second],experiment_state='running',trials=['rejected-trial'])
   d['trials']['rejected-trial']={'id':'rejected-trial','idea':first,'status':'rejected','complete':False}
  result=Engine(self.store,object()).next_queued(self.c)
  self.assertEqual(result['payload']['idea'],second,'A terminal rejected idea must not consume the queue repeatedly')

 def test_correction_receipt_rejects_foreign_source_binding(self):
  job=self.store.admit(self.c,'research',{},3,300);prefix='repair-queue-v1-stable';f=self.root/'experiment';w=f/'stable-workspace'
  (w/'ada').mkdir(parents=True);(f/'stable').mkdir();(f/'benchmark/starter').mkdir(parents=True)
  (w/'PLAN.md').write_text('frozen plan',encoding='utf-8');(w/'TASK.md').write_text('task',encoding='utf-8');(w/'server.py').write_text('adapter',encoding='utf-8');(w/'app.py').write_text('already corrected application',encoding='utf-8')
  (f/'benchmark/task.md').write_text('task',encoding='utf-8');(f/'benchmark/starter/server.py').write_text('adapter',encoding='utf-8');write(f/'benchmark/benchmark.json',{'criteria':[]})
  write(f/'stable-preparation.json',{'files':{'PLAN.md':hashlib.sha256((w/'PLAN.md').read_bytes()).hexdigest()},'app_absent':True,'source_ref':'fixture-preimplementation'})
  write(self.store.root/'jobs'/job['id']/'repair-queue-v1-qa-plan.json',{'api_checks':[{'expected_status':200}],'browser_steps':[{'op':'reload'}]})
  receipt=self.root/'correction-receipt.json';write(receipt,{'status':'completed','requested':self.p,'source_after':'different-application-digest','usage':{'input_tokens':1,'output_tokens':1}})
  with self.store.transaction() as d:
   j=d['jobs'][job['id']];j['correction_context']={prefix:{'attempt':0,'before':'original-old-digest','findings_ref':'real-prior-finding','prompt':'fixture exact correction prompt'}}
   j['stages'][prefix+'-correction-0']={'status':'running','receipt':str(receipt)}
  engine=Engine(self.store,object());calls=[]
  engine.lab=SimpleNamespace(folder=lambda _:f,load=lambda _: {'runs':{'stable':{'reviews':{'design':{'status':'PASS','subject_digest':digest('frozen plan')}}}}},correction=lambda *a,**k:calls.append('recover-correction'))
  def model(job,key,*args,**kw):
   self.assertGreater(len(args),6)
   self.assertIn(f/'stable-ada-regression',args[6])
   if '-correction-' in key:
    with self.store.transaction() as d:d['jobs'][job['id']]['stages'][key]['status']='completed'
    return read(receipt)
   if key.endswith('-design'):return {'session_id':'design','evidence':'fixture','message':json.dumps({'status':'PASS','findings':[],'evidence':['observed'],'qa_plan':{'api_checks':[],'browser_steps':[]}})}
   return {'session_id':'fixture','evidence':'fixture','message':'{}'}
  engine.work_model=model
  def step(job,key,*args,**kw):
   if key.endswith('-assigned-ada-regression'):return {'status':'PASS','candidate_digest':'fixture'}
   calls.append(key);raise RuntimeError('STOP_BEFORE_ANY_RUNTIME_EXECUTION')
  engine.step=step
  with self.assertRaisesRegex(RuntimeError,'source|request|receipt|binding'):
   engine.arm(engine.current(job['id']),'repair-queue-v1','fixture-exp','stable',{'development':self.p,'reviewer':self.p})
  self.assertNotIn('recover-correction',calls,'Foreign source receipt must not be rebound to current application')

 def test_correction_receipt_rejects_missing_prospective_prompt(self):
  job=self.store.admit(self.c,'research',{},3,300);prefix='repair-queue-v1-stable';f=self.root/'experiment';w=f/'stable-workspace'
  (w/'ada').mkdir(parents=True);(f/'stable').mkdir();(f/'benchmark/starter').mkdir(parents=True)
  (w/'PLAN.md').write_text('frozen plan',encoding='utf-8');(w/'TASK.md').write_text('task',encoding='utf-8');(w/'server.py').write_text('adapter',encoding='utf-8');(w/'app.py').write_text('already corrected application',encoding='utf-8')
  (f/'benchmark/task.md').write_text('task',encoding='utf-8');(f/'benchmark/starter/server.py').write_text('adapter',encoding='utf-8');write(f/'benchmark/benchmark.json',{'criteria':[]})
  write(f/'stable-preparation.json',{'files':{'PLAN.md':hashlib.sha256((w/'PLAN.md').read_bytes()).hexdigest()},'app_absent':True,'source_ref':'fixture-preimplementation'})
  write(self.store.root/'jobs'/job['id']/'repair-queue-v1-qa-plan.json',{'api_checks':[{'expected_status':200}],'browser_steps':[{'op':'reload'}]})
  receipt=self.root/'correction-receipt.json';write(receipt,{'status':'completed','requested':self.p,'source_after':'different-application-digest','usage':{'input_tokens':1,'output_tokens':1}})
  with self.store.transaction() as d:
   j=d['jobs'][job['id']];j['correction_context']={prefix:{'attempt':0,'before':'original-old-digest','findings_ref':'real-prior-finding'}}
   j['stages'][prefix+'-correction-0']={'status':'running','receipt':str(receipt)}
  engine=Engine(self.store,object());calls=[]
  engine.lab=SimpleNamespace(folder=lambda _:f,load=lambda _: {'runs':{'stable':{'reviews':{'design':{'status':'PASS','subject_digest':digest('frozen plan')}}}}},correction=lambda *a,**k:calls.append('recover-correction'))
  def model(job,key,*args,**kw):
   self.assertGreater(len(args),6)
   self.assertIn(f/'stable-ada-regression',args[6])
   if '-correction-' in key:
    with self.store.transaction() as d:d['jobs'][job['id']]['stages'][key]['status']='completed'
    return read(receipt)
   if key.endswith('-design'):return {'session_id':'design','evidence':'fixture','message':json.dumps({'status':'PASS','findings':[],'evidence':['observed'],'qa_plan':{'api_checks':[],'browser_steps':[]}})}
   return {'session_id':'fixture','evidence':'fixture','message':'{}'}
  engine.work_model=model
  def step(job,key,*args,**kw):
   if key.endswith('-assigned-ada-regression'):return {'status':'PASS','candidate_digest':'fixture'}
   calls.append(key);raise RuntimeError('STOP_BEFORE_ANY_RUNTIME_EXECUTION')
  engine.step=step
  with self.assertRaisesRegex(RuntimeError,'source|request|receipt|binding'):
   engine.arm(engine.current(job['id']),'repair-queue-v1','fixture-exp','stable',{'development':self.p,'reviewer':self.p})
  self.assertNotIn('recover-correction',calls,'Foreign source receipt must not be rebound to current application')


 def test_correction_receipt_rejects_missing_requested_profile(self):
  job=self.store.admit(self.c,'research',{},3,300);prefix='repair-queue-v1-stable';f=self.root/'experiment';w=f/'stable-workspace'
  (w/'ada').mkdir(parents=True);(f/'stable').mkdir();(f/'benchmark/starter').mkdir(parents=True)
  (w/'PLAN.md').write_text('frozen plan',encoding='utf-8');(w/'TASK.md').write_text('task',encoding='utf-8');(w/'server.py').write_text('adapter',encoding='utf-8');(w/'app.py').write_text('already corrected application',encoding='utf-8')
  (f/'benchmark/task.md').write_text('task',encoding='utf-8');(f/'benchmark/starter/server.py').write_text('adapter',encoding='utf-8');write(f/'benchmark/benchmark.json',{'criteria':[]})
  write(f/'stable-preparation.json',{'files':{'PLAN.md':hashlib.sha256((w/'PLAN.md').read_bytes()).hexdigest()},'app_absent':True,'source_ref':'fixture-preimplementation'})
  write(self.store.root/'jobs'/job['id']/'repair-queue-v1-qa-plan.json',{'api_checks':[{'expected_status':200}],'browser_steps':[{'op':'reload'}]})
  receipt=self.root/'correction-receipt.json';write(receipt,{'status':'completed','source_after':tree_digest(w),'usage':{'input_tokens':1,'output_tokens':1}})
  with self.store.transaction() as d:
   j=d['jobs'][job['id']];j['correction_context']={prefix:{'attempt':0,'before':'original-old-digest','findings_ref':'real-prior-finding','prompt':'fixture exact correction prompt'}}
   j['stages'][prefix+'-correction-0']={'status':'running','receipt':str(receipt)}
  engine=Engine(self.store,object());calls=[]
  engine.lab=SimpleNamespace(folder=lambda _:f,load=lambda _: {'runs':{'stable':{'reviews':{'design':{'status':'PASS','subject_digest':digest('frozen plan')}}}}},correction=lambda *a,**k:calls.append('recover-correction'))
  def model(job,key,*args,**kw):
   self.assertGreater(len(args),6)
   self.assertIn(f/'stable-ada-regression',args[6])
   if '-correction-' in key:
    with self.store.transaction() as d:d['jobs'][job['id']]['stages'][key]['status']='completed'
    return read(receipt)
   if key.endswith('-design'):return {'session_id':'design','evidence':'fixture','message':json.dumps({'status':'PASS','findings':[],'evidence':['observed'],'qa_plan':{'api_checks':[],'browser_steps':[]}})}
   return {'session_id':'fixture','evidence':'fixture','message':'{}'}
  engine.work_model=model
  def step(job,key,*args,**kw):
   if key.endswith('-assigned-ada-regression'):return {'status':'PASS','candidate_digest':'fixture'}
   calls.append(key);raise RuntimeError('STOP_BEFORE_ANY_RUNTIME_EXECUTION')
  engine.step=step
  with self.assertRaisesRegex(RuntimeError,'source|request|receipt|binding'):
   engine.arm(engine.current(job['id']),'repair-queue-v1','fixture-exp','stable',{'development':self.p,'reviewer':self.p})
  self.assertNotIn('recover-correction',calls,'Foreign source receipt must not be rebound to current application')


 def test_application_design_prompt_uses_raw_bytes_with_legacy_receipt_binding(self):
  job=self.store.admit(self.c,'trial',{},25,2500);f=self.root/'application-plan';w=f/'stable-workspace'
  (w/'ada').mkdir(parents=True);(f/'stable').mkdir();(f/'benchmark/starter').mkdir(parents=True)
  raw='# Frozen plan\r\nUTF-8 fixture: café, π and "warning".\r\n'.encode('utf-8')
  (w/'PLAN.md').write_bytes(raw)
  for path,text in [(w/'TASK.md','task'),(w/'server.py','adapter'),(f/'benchmark/task.md','task'),(f/'benchmark/starter/server.py','adapter')]:
   path.write_text(text,encoding='utf-8')
  write(f/'benchmark/benchmark.json',{'criteria':[]})
  reviews={};prompts=[];engine=Engine(self.store,object())
  engine.lab=SimpleNamespace(folder=lambda _:f,load=lambda _:{'runs':{'stable':{'reviews':reviews}}},
   add_review=lambda exp,arm,phase,receipt:reviews.update({phase:receipt}))
  def model(current,key,role,arm,exp,selected,workspace,prompt,*args,**kwargs):
   message={}
   if key.endswith('-design'):
    prompts.append(prompt)
    self.assertIn('raw file SHA-256 '+hashlib.sha256(raw).hexdigest(),prompt)
    self.assertNotIn(digest((w/'PLAN.md').read_text(encoding='utf-8')),prompt)
    message={'status':'PASS','evidence':['Fixture design observer'],'findings':[],
     'qa_plan':{'api_checks':[{'expected_status':200}],'browser_steps':[{'op':'reload'}]}}
   if key.endswith('-implementation'):(w/'app.py').write_text('Fixture text; never executed',encoding='utf-8')
   return {'session_id':'fixture-'+key,'evidence':'Fixture actual observation','message':json.dumps(message)}
  engine.work_model=model
  def before_native(*args,**kwargs):
   if args[1].endswith('-assigned-ada-regression'):return {'status':'PASS','candidate_digest':'fixture'}
   raise RuntimeError('STOP_BEFORE_ANY_RUNTIME_EXECUTION')
  engine.step=before_native
  with self.assertRaisesRegex(RuntimeError,'STOP_BEFORE_ANY_RUNTIME_EXECUTION'):
   engine.arm(engine.current(job['id']),'repair-queue-v1','fixture','stable',{'development':self.p,'reviewer':self.p})
  self.assertEqual(len(prompts),1)
  self.assertEqual(reviews['design']['subject_digest'],digest((w/'PLAN.md').read_text(encoding='utf-8')))
  self.assertNotEqual(reviews['design']['subject_digest'],hashlib.sha256(raw).hexdigest())

class CandidateProtocolTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='candidate-protocol-fixture-',dir=ROOT/'.lab')
  self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.store=Campaigns(self.root/'state');self.p=profile('fixture','medium','fixture')
  with self.store.transaction() as data:data['suites']['suite']={'revision':'suite','tasks':[],'name':'Fixture'}
  self.c=self.store.campaign('suite',self.p,self.p)['id']
  self.idea=self.store.idea({'title':'Fixture change','hypothesis':'One reusable change','mechanism':'Bounded instruction change',
   'scope':'README only','objections':['Verify independently'],'tags':['fixture'],'sources':[]},self.c)
  self.stable=self.root/'stable';self.stable.mkdir()
  for name in ('README.md','check.py','evaluate.py'):(self.stable/name).write_text('Fixture source '+name,encoding='utf-8')

 def run_candidate(self,mode='valid'):
  job=self.store.admit(self.c,'trial',{'idea':self.idea['id']},25,2500)
  engine=Engine(self.store,object());engine.lab=SimpleNamespace(config=lambda:{'ada':str(self.stable)})
  calls=[];self.candidate_calls=calls
  identity={'commit':'fixture','status':'','source_digest':tree_digest(self.stable)}

  def model(current,key,selected,workspace,prompt,read_roots=(),images=()):
   calls.append(key);working=self.store.root/'jobs'/job['id']/'candidate-ada'
   if key=='candidate-plan':
    self.assertEqual(tree_digest(working),identity['source_digest'])
    plan_text=(
     '# Concrete plan\nTarget: README.md after verification instructions.\n'
     'Exact text: Retain full failed-command diagnostics in ignored evidence.\n'
     'Addressed objection: warnings and failure output remain available.\n'
     'Decision: reject quality regression; resource savings require actual measurements.\n'
     'Cases: successful, failed and warning-producing output; no quality gate changes.\n')
    if mode=='unicode-plan':plan_text+='UTF-8 fixture: café, π and quoted "warning" output.\n'
    self.candidate_plan_bytes=plan_text.replace('\n','\r\n').encode('utf-8')
    (working/'.cache/candidate-plan.md').write_bytes(self.candidate_plan_bytes)
    if mode=='planning-implements':(working/'README.md').write_text('Premature implementation',encoding='utf-8')
    message={'plan':'.cache/candidate-plan.md','summary':'Concrete independently reviewable fixture proposal'}
   elif key=='candidate-design':
    self.assertEqual(tree_digest(working),identity['source_digest'])
    self.assertEqual(read(working/'.cache/candidate-design.json')['idea']['hypothesis'],self.idea['hypothesis'])
    planned=(working/'.cache/candidate-plan.md').read_text(encoding='utf-8')
    self.assertIn('Exact text:',planned)
    self.assertIn('raw file SHA-256 '+hashlib.sha256((working/'.cache/candidate-plan.md').read_bytes()).hexdigest(),prompt)
    self.assertNotIn(digest(planned),prompt)
    self.assertEqual(read_roots,[working,self.stable])
    message={'status':'FAIL' if mode=='design-fail' else 'PASS','evidence':['Fixture prospective observation'],
     'findings':['Fixture design objection'] if mode=='design-fail' else ['No material design objection'] if mode=='contradictory-design' else [],
     'verification_cases':['Verify retained failure diagnostics']}
   elif key=='candidate-implementation':
    evidence=read(working/'.cache/candidate-design-review.json')
    self.assertEqual(evidence['session_id'],'fixture-candidate-design')
    self.assertEqual(evidence['design_digest'],digest(read(working/'.cache/candidate-design.json')))
    self.assertEqual(evidence['plan_digest'],hashlib.sha256((working/'.cache/candidate-plan.md').read_bytes()).hexdigest())
    self.assertEqual(evidence['verdict']['status'],'PASS')
    (working/'README.md').write_text('Actual fixture candidate instruction change',encoding='utf-8')
    write(working/'.cache/private-lead-record.json',{'fixture':True})
    for name in ('.agents','.aws','.codex'):(working/name).mkdir(exist_ok=True)
    if mode=='proposal-tamper':write(working/'.cache/candidate-design.json',{'different':'proposal'})
    if mode=='plan-tamper':(working/'.cache/candidate-plan.md').write_text('Changed after independent criticism',encoding='utf-8')
    message={'change':'One fixture instruction','intervention':'Retain actual diagnostics','limitations':['Fixture only']}
   elif key=='candidate-review':
    subject=workspace/'.cache/subject'
    self.assertEqual(tree_digest(subject),tree_digest(working))
    self.assertEqual((subject/'README.md').read_bytes(),(working/'README.md').read_bytes())
    self.assertFalse((subject/'.cache/private-lead-record.json').exists())
    for name in ('.agents','.aws','.codex'):self.assertFalse((subject/name).exists())
    self.assertIn('fixture-candidate-design',prompt)
    self.assertIn('run check.py and evaluate.py there yourself',prompt)
    self.assertNotEqual(workspace,working)
    if mode=='clone-tamper':(subject/'README.md').write_text('Changed review subject',encoding='utf-8')
    message={'status':'PASS','evidence':['Fixture independent source and verification observation'],'findings':[]}
   else:self.fail('Unexpected model stage '+key)
   return {'session_id':'fixture-'+key,'evidence':'Fixture actual stage '+key,'message':json.dumps(message)}

  engine.model=model
  def step(current,key,operation,inputs=None):
   if key=='candidate-trusted-regression':return {'status':'PASS','fixture':'No host verification program executed'}
   self.assertEqual(key,'candidate-validation')
   return {'returncode':0}
  engine.step=step
  engine.schedule_tasks=lambda *args:None
  engine.finish_trial=lambda *args:None
  with patch('ada_lab.engine.identity',return_value=identity):engine.trial(job)
  return calls,self.store.snapshot()['trials'][engine.current(job['id'])['trial']]

 def test_prospective_design_precedes_implementation_and_exact_independent_clone(self):
  calls,trial=self.run_candidate()
  self.assertEqual(calls,['candidate-plan','candidate-design','candidate-implementation','candidate-review'])
  self.assertEqual(trial['candidate']['design_review']['session_id'],'fixture-candidate-design')
  self.assertEqual(tree_digest(trial['candidate']['path']),trial['candidate']['digest'])

 def test_rejected_design_never_dispatches_implementation(self):
  with self.assertRaisesRegex(CandidateRejected,'design rejected before implementation'):self.run_candidate('design-fail')
  self.assertEqual(self.candidate_calls,['candidate-plan','candidate-design'])
  self.assertEqual(list((self.store.root/'jobs').glob('*/candidate-critic')),[])

 def test_changed_prospective_proposal_blocks_final_review(self):
  with self.assertRaisesRegex(RuntimeError,'changed its prospectively reviewed design'):self.run_candidate('proposal-tamper')
  self.assertEqual(self.candidate_calls,['candidate-plan','candidate-design','candidate-implementation'])
  self.assertEqual(list((self.store.root/'jobs').glob('*/candidate-critic')),[])

 def test_reviewer_cannot_change_verification_clone_identity(self):
  with self.assertRaisesRegex(RuntimeError,'Candidate changed during independent review'):self.run_candidate('clone-tamper')

 def test_planning_cannot_implement_source_before_criticism(self):
  with self.assertRaisesRegex(RuntimeError,'Planning stage implemented candidate before design criticism'):
   self.run_candidate('planning-implements')
  self.assertEqual(self.candidate_calls,['candidate-plan'])

 def test_changed_prospective_plan_blocks_final_review(self):
  with self.assertRaisesRegex(RuntimeError,'changed its prospectively reviewed design'):self.run_candidate('plan-tamper')
  self.assertEqual(self.candidate_calls,['candidate-plan','candidate-design','candidate-implementation'])
  self.assertEqual(list((self.store.root/'jobs').glob('*/candidate-critic')),[])

 def test_candidate_plan_prompt_hashes_exact_utf8_crlf_bytes(self):
  calls,trial=self.run_candidate('unicode-plan')
  raw=self.candidate_plan_bytes
  self.assertIn(b'\r\n',raw);self.assertIn('café'.encode('utf-8'),raw)
  expected=hashlib.sha256(raw).hexdigest()
  self.assertEqual(trial['candidate']['design_review']['plan_digest'],expected)
  self.assertNotEqual(expected,hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest())
  self.assertNotEqual(expected,digest(raw.decode('utf-8').replace('\r\n','\n')))

 def test_contradictory_design_pass_uses_format_error_not_candidate_rejection(self):
  with self.assertRaisesRegex(ModelOutputError,'Contradictory review'):self.run_candidate('contradictory-design')
  self.assertEqual(self.candidate_calls,['candidate-plan','candidate-design'])

class ReviewVerdictTests(unittest.TestCase):
 def test_contradictory_pass_preserves_findings_as_format_error(self):
  for finding in ('No material design objection',{'id':'defect','actual':'Mandatory evidence absent'}):
   with self.subTest(finding=finding):
    value={'status':'PASS','evidence':['Actual observation'],'findings':[finding]}
    with self.assertRaisesRegex(ModelOutputError,'PASS requires findings='):
     Engine.verdict({'message':json.dumps(value)})
    self.assertEqual(value['findings'],[finding])

 def test_valid_pass_preserves_evidence_and_limitations(self):
  value={'status':'PASS','findings':[],'evidence':['Actual runtime check and exact source inspected'],
   'limitations':['Resource improvement remains unproven'],'verification_cases':['Warnings and failures']}
  self.assertEqual(Engine.verdict({'message':json.dumps(value)}),value)

 def test_actual_fail_and_blocked_findings_remain_negative(self):
  for status in ('FAIL','BLOCKED'):
   with self.subTest(status=status):
    value={'status':status,'findings':[{'id':'mandatory','actual':'Observed defect or unavailable required capability'}],
     'evidence':['Actual observation reference']}
    observed=Engine.verdict({'message':json.dumps(value)})
    self.assertEqual(observed,value);self.assertNotEqual(observed['status'],'PASS')

 def test_invalid_status_or_missing_findings_is_format_error(self):
  for value in ({'status':'approved','findings':[]},{'status':'PASS'},{'status':'FAIL','findings':'actual defect'}):
   with self.subTest(value=value):
    with self.assertRaisesRegex(ModelOutputError,'Independent review requires'):
     Engine.verdict({'message':json.dumps(value)})

class DocumentationBoundaryTests(unittest.TestCase):
 """Never execute candidate programs: host subprocesses below are strict mocks."""
 def setUp(self):
  from ada_lab.files import copy_tree
  self.temp=tempfile.TemporaryDirectory(prefix='documentation-boundary-fixture-',dir=ROOT/'.lab')
  self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.stable=self.root/'stable';self.stable.mkdir()
  for name,text in [('README.md','Trusted baseline documentation'),('check.py','Trusted unchanged check fixture'),
                    ('evaluate.py','Trusted unchanged evaluation fixture'),('ada-project.json','{"trusted":true}')]:
   (self.stable/name).write_text(text,encoding='utf-8')
  self.candidate=self.root/'candidate';copy_tree(self.stable,self.candidate)
  self.baseline={'commit':'fixture','status':'','source_digest':tree_digest(self.stable)}
  self.engine=Engine(Campaigns(self.root/'state'),object())
  self.engine.lab=SimpleNamespace(config=lambda:{'ada':str(self.stable)})

 def test_changed_added_deleted_non_markdown_prevents_host_program_dispatch(self):
  from ada_lab.files import copy_tree
  changes=[('changed-check','check.py','Modified executable'),('changed-evaluation','evaluate.py','Modified executable'),
   ('changed-config','ada-project.json','{"trusted":false}'),('added-script','new.py','Generated executable'),
   ('misleading-suffix','instructions.md.py','Generated executable'),('added-config','.gitconfig','Generated configuration'),
   ('deleted-program','evaluate.py',None)]
  for index,(label,name,text) in enumerate(changes):
   with self.subTest(label=label):
    candidate=self.root/('candidate-'+str(index));copy_tree(self.stable,candidate)
    if text is None:(candidate/name).unlink()
    else:(candidate/name).write_text(text,encoding='utf-8')
    with (patch('ada_lab.engine.identity',return_value=self.baseline),patch('ada_lab.engine.subprocess.run') as execute):
     result=self.engine.documentation_regression(candidate,self.root/('result-'+str(index)),self.baseline)
    self.assertEqual(result['status'],'BLOCKED');execute.assert_not_called()
    self.assertFalse((self.root/('result-'+str(index))/'subject').exists())

 def test_changed_pinned_baseline_prevents_host_program_dispatch(self):
  observed={**self.baseline,'source_digest':'Changed trusted production source'}
  with (patch('ada_lab.engine.identity',return_value=observed),patch('ada_lab.engine.subprocess.run') as execute):
   with self.assertRaisesRegex(RuntimeError,'Pinned trusted verification source changed'):
    self.engine.documentation_regression(self.candidate,self.root/'result',self.baseline)
  execute.assert_not_called();self.assertFalse((self.root/'result').exists())

 def test_documentation_only_dispatch_uses_exact_trusted_code_and_clean_copy(self):
  from ada_lab.files import inventory
  (self.candidate/'README.md').write_text('Actual candidate Markdown change',encoding='utf-8')
  write(self.candidate/'.cache/generated.py',{'ignored':'Must never be copied or executed'})
  target=self.root/'result'
  result_fixture=SimpleNamespace(returncode=0,stdout='Deterministic verification fixture',stderr='')
  with (patch('ada_lab.engine.identity',return_value=self.baseline),
        patch('ada_lab.engine.subprocess.run',return_value=result_fixture) as execute):
   result=self.engine.documentation_regression(self.candidate,target,self.baseline)
  self.assertEqual(result['status'],'PASS');self.assertEqual(result['changed_markdown'],['README.md'])
  self.assertEqual(execute.call_count,2)
  self.assertEqual([call.args[0][1] for call in execute.call_args_list],['check.py','evaluate.py'])
  for call in execute.call_args_list:
   self.assertEqual(call.kwargs['cwd'],target/'subject')
   self.assertEqual(call.kwargs['env']['GIT_CONFIG_NOSYSTEM'],'1')
   self.assertIn('GIT_CONFIG_GLOBAL',call.kwargs['env'])
  trusted={name:value for name,value in inventory(self.stable).items() if Path(name).suffix.lower()!='.md'}
  copied={name:value for name,value in inventory(target/'subject').items() if Path(name).suffix.lower()!='.md'}
  self.assertEqual(copied,trusted);self.assertFalse((target/'subject/.cache/generated.py').exists())
  self.assertEqual(tree_digest(target/'subject'),tree_digest(self.candidate))

 def test_existing_verification_copy_mismatch_prevents_host_program_dispatch(self):
  from ada_lab.files import copy_tree
  target=self.root/'result';target.mkdir();copy_tree(self.candidate,target/'subject')
  (target/'subject/check.py').write_text('Untrusted preexisting executable',encoding='utf-8')
  with (patch('ada_lab.engine.identity',return_value=self.baseline),patch('ada_lab.engine.subprocess.run') as execute):
   with self.assertRaisesRegex(RuntimeError,'Trusted regression source differs'):
    self.engine.documentation_regression(self.candidate,target,self.baseline)
  execute.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
