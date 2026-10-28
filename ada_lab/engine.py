"""Sequential, checkpointed research and matched-suite execution controller."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid
import subprocess

from .campaigns import Campaigns, aggregate
from .files import ROOT, copy_tree, digest, identity, inventory, lock, now, read, tree_digest, write
from .lab import Lab
from .runtime import CodexRuntime, structured, ModelOutputError
from . import evaluator, sandbox
from .qa import review_actions, validate_plan
from .adapters import ADAPTERS
from .research_sources import retrieve
from .stateio import write

RUBRIC = ['quality','resources','evidence','generality','simplicity','experimental_value','low_risk']
BROWSER_CAPABILITIES = '''The controller's actual native browser QA supports bounded fill/click/select/press, wait_text/assert_text/assert_count using Playwright locators (including :visible and :has-text), reload, delay_next and settle. delay_next has {op:"delay_next",target:"/api/repairs",ms:1..2000} and delays one actual GET response after the real backend handles it; it does not synthesize a response. Await actual loading/new-result observations, then settle to wait for consumed delayed and in-flight requests before testing stale-response behavior. Unconsumed delays fail QA. press uses {selector,value:"Enter"|"Tab"|other Playwright key}. settle/reload need no selector. At most 64 browser steps and 30 API checks are allowed, with a 110-second browser deadline and six-second assertion bounds. Mandatory external checks already cover save/list failure, loading, empty, reload and mobile. Choose additional actual keyboard access, inert markup and any binding PLAN-specific behavior through the available actions; do not promise unsupported checks or add product requirements. Native tools deny network sockets; direct handler/SQLite own checks plus protected controller browser checks are the available path.'''
RULES = """No nested agents or AI process launches. No packages, new cloud services/accounts, API keys or paid execution. Configured research web search is allowed. No production edits. Your tool commands execute only in native offline isolation. Never request an approval or unsandboxed execution. Read assigned ADA; follow its actual protocol. Frozen TASK/adapter/ADA/PLAN/criteria must not be weakened or changed after design. Save temporary records under .cache/.runtime. Final answer must be one JSON object, no Markdown fences. Review findings lists contain ONLY actionable defects or missing mandatory capabilities/evidence. PASS requires findings=[]; place positive observations, cautionary notes and unproven-benefit limitations in evidence or limitations instead. Never remove a real finding merely to produce PASS. Do not claim independent PASS on your own work. Unknown metrics remain unknown. The controller records actual tokens/events. Do not inspect any reference, other arm, previous app or evaluator source. Independent Reviewer receives only its explicitly provided actual observation evidence."""


class CandidateRejected(RuntimeError):pass


class Engine:
    def __init__(self, store=None, runtime=None, adapters=None):
        self.store=store or Campaigns();self.runtime=runtime or CodexRuntime(self.store.root/'runtime')
        self.lab=Lab();self.halt=threading.Event();self.thread=None
        self.worker='controller-'+uuid.uuid4().hex
        self.adapters=dict(ADAPTERS if adapters is None else adapters)
        self.loaded_digest=self.controller_digest()

    def start(self):
        self.lease=lock(self.store.root/'controller.lock');self.lease.__enter__()
        self.store.recover()
        known=set(self.store.snapshot()['jobs'])
        for record in self.lab.history():
            for session in record['sessions']:
                if session['status']=='active' and any(session['observer'].startswith('worker:'+j+':') for j in known):
                    self.lab.finish(record['id'],session['id'],{'status':'interrupted','recovered_after_restart':True,
                        'source_ref':'Controller lifetime lease reacquired; ambiguous old stage retained, no automatic replay'})
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def stop(self):
        self.halt.set();self.runtime.stop()
        if self.thread:self.thread.join(timeout=15)
        if not self.thread or not self.thread.is_alive():
            if getattr(self,'lease',None):self.lease.__exit__(None,None,None);self.lease=None

    def current(self,job):return self.store.snapshot()['jobs'][job]

    @staticmethod
    def verdict(result):
        value=structured(result['message'])
        if value.get('status') not in ('PASS','FAIL','BLOCKED') or not isinstance(value.get('findings'),list):
            raise ModelOutputError('Independent review requires status PASS/FAIL/BLOCKED and a findings list')
        if value['status']=='PASS' and value['findings']:
            raise ModelOutputError('Contradictory review: PASS requires findings=[]; preserve real actionable defects as FAIL/BLOCKED and put informational observations in evidence/limitations')
        return value

    def cancelled(self,job):
        data=self.store.snapshot();j=data['jobs'][job];c=data['campaigns'][j['campaign']]
        area='research' if j['kind']=='research' else 'experiment'
        return self.halt.is_set() or c[area+'_state'] in ('paused','stopped')

    def cancel_active(self,job):
        data=self.store.snapshot();j=data['jobs'][job];c=data['campaigns'][j['campaign']]
        return self.halt.is_set() or c[('research' if j['kind']=='research' else 'experiment')+'_state']=='stopped'

    def loop(self):
        while not self.halt.wait(.3):
            try:data=self.store.snapshot()
            except Exception as error:self.last_error=str(error);continue
            job=next((j for j in data['jobs'].values() if j['status']=='queued' and
                data['campaigns'][j['campaign']][('research' if j['kind']=='research' else 'experiment')+'_state']=='running'),None)
            if not job:continue
            with self.store.transaction() as state:
                if state['jobs'][job['id']]['status']!='queued':continue
                state['jobs'][job['id']].update(status='running',owner=self.worker)
            try:
                self.preflight(job)
                if job['kind']=='research':self.research(job)
                else:self.trial(job)
                with self.store.transaction() as state:
                    j=state['jobs'][job['id']];j.update(status='completed',stage='complete',finished=now())
                    c=state['campaigns'][job['campaign']]
                    area=('research' if job['kind']=='research' else 'experiment')+'_state'
                    if c[area]=='running':c[area]='running' if job['kind']=='trial' and c.get('auto_queue') and c.get('pending_candidates') else 'idle'
                    self.release(state,j)
                    self.store.event(state,job['campaign'],job['kind']+' complete',job=job['id'])
                if job['kind']=='trial':self.next_queued(job['campaign'])
            except CandidateRejected as error:
                with self.store.transaction() as state:
                    j=state['jobs'][job['id']];j.update(status='rejected',error=str(error));self.release(state,j)
                    if j.get('trial'):
                        t=state['trials'][j['trial']];t.update(status='rejected',rejection=str(error))
                        working=self.store.root/'jobs'/job['id']/'candidate-ada'
                        if working.exists() and not t.get('candidate'):
                            source=tree_digest(working);target=working.parent/'candidate-rejected'
                            if not target.exists():copy_tree(working,target)
                            t['candidate']={'digest':source,'path':str(target),'status':'rejected','intervention':'See retained real candidate implementation/criticism receipts'}
                        state['campaigns'][job['campaign']]['summary']['findings']=(state['campaigns'][job['campaign']]['summary']['findings']+
                            [{'trial':t['id'],'idea':t['idea'],'outcome':'Rejected before full-suite completion','reason':str(error)}])[-8:]
                    self.store.event(state,job['campaign'],'Candidate rejected with retained evidence: '+str(error)[:400],job=job['id'])
                    campaign=state['campaigns'][job['campaign']]
                    if campaign['experiment_state']=='running' and not campaign.get('auto_queue'):campaign['experiment_state']='idle'
                self.next_queued(job['campaign'])
            except Exception as error:
                with self.store.transaction() as state:
                    j=state['jobs'][job['id']];c=state['campaigns'][job['campaign']]
                    if isinstance(error,ModelOutputError) and j['stage'] in j['stages']:
                        j['stages'][j['stage']].update(status='invalid_output',error=str(error))
                        if j.get('format_retries',0)<1 and j.get('reservation',{}).get('calls',0)>0 and not self.cancelled_snapshot(state,j):
                            j.update(status='queued',format_retries=j.get('format_retries',0)+1,error=str(error))
                            self.store.event(state,job['campaign'],'One bounded output-format correction queued; previous usage retained',job=job['id'])
                            continue
                    stopped=c[('research' if job['kind']=='research' else 'experiment')+'_state']=='stopped'
                    j.update(status='stopped' if stopped else 'paused' if self.cancelled_snapshot(state,j) else 'blocked',error=str(error))
                    if stopped:self.release(state,j)
                    c=state['campaigns'][job['campaign']];c['blocker']=str(error)
                    self.store.event(state,job['campaign'],'Work checkpointed: '+str(error)[:400],job=job['id'])

    @staticmethod
    def cancelled_snapshot(data,job):
        c=data['campaigns'][job['campaign']]
        return c[('research' if job['kind']=='research' else 'experiment')+'_state'] in ('paused','stopped')

    @staticmethod
    def release(data,job):
        remaining=job.get('reservation',{})
        c=data['campaigns'][job['campaign']]
        c['usage']['reserved_calls']-=remaining.get('calls',0)
        c['usage']['reserved_tokens']-=remaining.get('tokens',0)
        job['reservation']={'calls':0,'tokens':0}

    def enqueue(self,campaign,kind,payload=None,automatic=False):
        payload=dict(payload or {});data=self.store.snapshot();c=data['campaigns'][campaign]
        if kind=='trial':
            if payload.get('reuse_accepted'):
                prior=self.reusable_candidate(data,c,payload['source_trial'])
                if prior['idea']!=payload.get('idea'):raise ValueError('Reusable source belongs to another hypothesis')
            idea=data['ideas'][payload['idea']]
            if idea['status']!='reviewed':raise ValueError('Independent idea criticism is required before experimentation')
            existing=[data['trials'][t] for t in c['trials'] if data['trials'][t]['idea']==idea['id']]
            if any(t.get('complete') or t.get('status')=='rejected' for t in existing) and not (payload.get('confirmation') or payload.get('reuse_accepted')):
                raise ValueError('This idea was already tested in this campaign. Select explicit confirmation to repeat with fresh contexts.')
            calls=(0 if payload.get('confirmation') or payload.get('reuse_accepted') else 4)+20*len(data['suites'][c['suite']]['tasks'])+1
            if payload.get('reuse_accepted') and prior['suite']!=c['suite']:calls+=1
        else:
            payload.setdefault('iterations',c['settings']['research_iterations']);calls=2*payload['iterations']
        # A conservative stage estimate uses actual observed history, never time-as-tokens.
        history=[s['tokens'] for j in data['jobs'].values() for s in j['stages'].values() if s.get('tokens') is not None]
        estimated=max(80000,max(history,default=80000))*calls
        return self.store.admit(campaign,kind,payload,calls,estimated,automatic)

    def ranked_queue(self,campaign):
        data=self.store.snapshot();c=data['campaigns'][campaign]
        ranked=sorted(c['queue'],key=lambda i:data['ideas'][i].get('manual_priority',data['ideas'][i].get('priority') or 0),reverse=True)
        selected=[i for i in ranked if data['ideas'][i]['status']=='reviewed' and not data['ideas'][i].get('excluded')
            and data['ideas'][i].get('manual_priority',data['ideas'][i].get('priority') or 0)>=c['settings']['threshold']
            and not any(data['trials'][t]['idea']==i and (data['trials'][t]['complete'] or data['trials'][t].get('status')=='rejected') for t in c['trials'])][:c['settings']['shortlist']]
        if not selected:raise ValueError('No untested reviewed ideas meet the current threshold')
        with self.store.transaction() as state:
            x=state['campaigns'][campaign];x.update(auto_queue=True,pending_candidates=selected,experiment_state='running')
        return self.next_queued(campaign)

    def next_queued(self,campaign):
        data=self.store.snapshot();c=data['campaigns'][campaign]
        if not c.get('auto_queue') or c['experiment_state']!='running':return None
        pending=[i for i in c.get('pending_candidates',[]) if not data['ideas'][i].get('excluded') and not any(data['trials'][t]['idea']==i and (data['trials'][t].get('complete') or data['trials'][t].get('status')=='rejected') for t in c['trials'])]
        with self.store.transaction() as state:state['campaigns'][campaign]['pending_candidates']=pending
        if not pending:
            with self.store.transaction() as state:state['campaigns'][campaign]['experiment_state']='idle'
            return None
        try:return self.enqueue(campaign,'trial',{'idea':pending[0]},automatic=True)
        except ValueError as error:
            with self.store.transaction() as state:
                state['campaigns'][campaign].update(experiment_state='paused',blocker=str(error))
            return None

    def resume(self,identifier):
        job=self.current(identifier)
        data=self.store.snapshot();c=data['campaigns'][job['campaign']]
        total=2*job['payload'].get('iterations',1) if job['kind']=='research' else (0 if job['payload'].get('confirmation') or job['payload'].get('reuse_accepted') else 4)+20*len(data['suites'][c['suite']]['tasks'])+1
        if job['payload'].get('reuse_accepted') and data['trials'][job['payload']['source_trial']]['suite']!=c['suite']:total+=1
        remaining=max(1,total-sum(s['status']=='completed' for s in job['stages'].values()))
        self.store.resume_admitted(identifier,remaining,80000*remaining)

    def preflight(self,job):
        if self.loaded_digest!=self.controller_digest():raise RuntimeError('Controller files changed; reopen Lab before authorizing more model work')
        campaign=self.store.snapshot()['campaigns'][job['campaign']]
        campaign={**campaign,**job.get('execution_profiles',{})}
        for name in ('development','reviewer','research_profile'):
            policy=campaign[name].get('settings',{}).get('execution_policy')
            if policy and policy!=self.loaded_digest:raise RuntimeError('Execution policy differs from this campaign; create a campaign with the current explicit profile')
        self.runtime.discover()
        for p in ('development','reviewer','research_profile'):self.runtime.check_profile(campaign[p])
        sandbox.require_safe(self.lab.state,self.lab.config()['ada'])
        if job['kind']=='research':return
        data=self.store.snapshot();seen=set()
        for task in data['suites'][campaign['suite']]['tasks']:
            frozen=self.verify_installed(task);adapter=self.adapters.get(frozen['adapter'])
            if adapter is None:raise RuntimeError('Required task adapter is unavailable: '+frozen['adapter'])
            adapter.verify(self,task,frozen)
            if frozen['adapter'] not in seen:adapter.preflight(self);seen.add(frozen['adapter'])

    def verify_installed(self,task):
        frozen=self.store.verify_task(task['revision'])
        for name,value in frozen['procedures'].items():
            if hashlib.sha256((ROOT/'ada_lab'/name).read_bytes()).hexdigest()!=value:
                raise RuntimeError('Installed procedure differs from frozen task: '+name)
        for name,value in frozen.get('dependencies',{}).items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=value:raise RuntimeError('Installed dependency manifest differs from frozen task')
        return frozen

    def model(self,job,key,profile,workspace,prompt,read_roots=(),images=()):
        if self.loaded_digest!=self.controller_digest():raise RuntimeError('Controller files changed during work; checkpoint retained')
        identifier=job['id'];folder=self.store.root/'jobs'/identifier/key
        checkpoint=self.current(identifier)['stages'].get(key)
        prompt_identity=digest({'prompt':RULES+'\n'+prompt,'profile':profile,'workspace':str(workspace),
            'read_roots':[str(x) for x in read_roots], 'images':[hashlib.sha256(Path(x).read_bytes()).hexdigest() for x in images]})
        if checkpoint and checkpoint['status']=='completed':
            if checkpoint['input_digest']!=prompt_identity:raise RuntimeError('Completed stage inputs changed; refusing replay')
            if checkpoint.get('receipt_digest') and hashlib.sha256(Path(checkpoint['receipt']).read_bytes()).hexdigest()!=checkpoint['receipt_digest']:
                raise RuntimeError('Completed runtime receipt integrity changed')
            result=read(checkpoint['receipt'])
            return result
        if checkpoint and checkpoint['status']!='invalid_output' and not Path(checkpoint['receipt']).exists() and hasattr(self.runtime,'recover_completed'):
            self.runtime.recover_completed(Path(checkpoint['receipt']).parent,profile,workspace,checkpoint.get('actual_prompt_digest'))
        if checkpoint and checkpoint['status']!='invalid_output' and Path(checkpoint['receipt']).exists():
            recovered=read(checkpoint['receipt'])
            if recovered.get('status')=='completed':
                if checkpoint['input_digest']!=prompt_identity or recovered['requested']!=profile:
                    raise RuntimeError('Recovered runtime inputs differ; refusing to rebind output')
                usage=recovered.get('usage');tokens=usage['input_tokens']+usage['output_tokens'] if usage else None
                with self.store.transaction() as data:
                    stage=data['jobs'][identifier]['stages'][key]
                    if stage.get('accounted') is not True:
                        if tokens is None:data['campaigns'][job['campaign']]['usage']['unknown_usage_stages']+=1
                        else:data['campaigns'][job['campaign']]['usage']['known_tokens']+=tokens
                    elif stage.get('accounted_unknown') and tokens is not None:
                        data['campaigns'][job['campaign']]['usage']['unknown_usage_stages']-=1
                        data['campaigns'][job['campaign']]['usage']['known_tokens']+=tokens
                    stage.update(status='completed',tokens=tokens,accounted=True,recovered=True,
                        session_id=recovered['session_id'],configuration_verified=recovered['configuration_verified'],wall_seconds=recovered['wall_seconds'],
                        receipt_digest=hashlib.sha256(Path(stage['receipt']).read_bytes()).hexdigest())
                return recovered
        if self.cancelled(identifier):raise InterruptedError('Paused before admitting the next model stage')
        attempt=folder/uuid.uuid4().hex
        retry_note=('\nPrevious completed response failed controller output validation: '+checkpoint.get('error','')+
                    '. Repair the final JSON response using retained files. Do not repeat completed valid code or checks unnecessarily.') if checkpoint and checkpoint['status']=='invalid_output' else ''
        if checkpoint and checkpoint['status'] in ('failed','interrupted'):
            retry_note+='\nThis is an explicitly authorized continuation. Inspect retained partial files and continue; do not rebuild valid completed work or claim unavailable earlier results.'
        with self.store.transaction() as data:
            j=data['jobs'][identifier];c=data['campaigns'][j['campaign']];r=j.get('reservation',{})
            if r.get('calls',0)<1:raise RuntimeError('Reserved model stages exhausted; retain pending work and expand authorization explicitly')
            budget=c['settings']['token_budget']
            if budget is not None and (c['usage']['unknown_usage_stages'] or c['usage']['known_tokens']+c['usage']['reserved_tokens']>budget):
                raise RuntimeError('Updated actual usage no longer fits the authorized remaining token reserve')
            allocated=r.get('tokens',0)//r['calls'];r['tokens']-=allocated;c['usage']['reserved_tokens']-=allocated
            r['calls']-=1;c['usage']['reserved_calls']-=1;c['usage']['calls']+=1
            if key in j['stages']:j.setdefault('stage_history',[]).append(j['stages'][key])
            j['stages'][key]={'key':key,'status':'running','input_digest':prompt_identity,'started':now(),'receipt':str(attempt/'receipt.json'),'tokens':None,
                'legacy_session':j.get('legacy_pending',{}).get(key),'accounted':False,
                'actual_prompt_digest':hashlib.sha256((RULES+'\n'+prompt+retry_note).encode('utf-8')).hexdigest()}
            j['stage']=key;self.store.event(data,j['campaign'],'Model stage '+key,job=identifier)
        try:
            result=self.runtime.run(profile,workspace,RULES+'\n'+prompt+retry_note,attempt,read_roots,
                timeout=self.store.snapshot()['campaigns'][job['campaign']]['settings']['stage_timeout'],
                cancelled=lambda:self.cancel_active(identifier),images=images)
        except Exception as error:
            with self.store.transaction() as data:
                s=data['jobs'][identifier]['stages'][key];s.update(status='failed',error=str(error),accounted=True,accounted_unknown=True)
                data['campaigns'][job['campaign']]['usage']['unknown_usage_stages']+=1
            raise
        usage=result.get('usage');tokens=(usage['input_tokens']+usage['output_tokens']) if usage else None
        with self.store.transaction() as data:
            j=data['jobs'][identifier];s=j['stages'][key];c=data['campaigns'][job['campaign']]
            s.update(status='completed' if result['status']=='completed' else 'interrupted' if result['status']=='interrupted' else 'failed',
                finished=now(),tokens=tokens,session_id=result.get('session_id'),configuration_verified=result.get('configuration_verified'),wall_seconds=result['wall_seconds'],
                receipt_digest=hashlib.sha256(Path(s['receipt']).read_bytes()).hexdigest())
            s['accounted']=True
            if tokens is None:c['usage']['unknown_usage_stages']+=1
            else:c['usage']['known_tokens']+=tokens
        if result['status']!='completed':raise RuntimeError('Runtime stage '+key+' '+result['status']+': '+str(result['errors'])[:700])
        return result

    def research(self,job):
        completed=self.current(job['id']).get('research_iterations_completed',0)
        for iteration in range(completed,job['payload'].get('iterations',1)):
            if self.cancelled(job['id']):raise InterruptedError('Research paused; reviewed ideas retained')
            self.research_iteration(job,iteration)
            with self.store.transaction() as state:state['jobs'][job['id']]['research_iterations_completed']=iteration+1

    def research_iteration(self,job,iteration):
        data=self.store.snapshot();c=data['campaigns'][job['campaign']]
        c={**c,**job.get('execution_profiles',{})}
        folder=self.store.root/'jobs'/job['id']/('iteration-'+str(iteration));work=folder/'research-workspace';work.mkdir(parents=True,exist_ok=True)
        context={'ada':identity(self.lab.config()['ada']),'history':[{'id':r['id'],'hypothesis':r['hypothesis'],'outcome':r.get('comparison',{}).get('outcome'),'analysis':r.get('analysis',{}).get('hypothesis_result')} for r in self.lab.history()][-8:],
                 'knowledge':[{'id':x['id'],'title':x['title'],'hypothesis':x['hypothesis'],'priority':x['priority'],'tags':x['tags']} for x in data['ideas'].values()][-20:]}
        context_file=work/'CONTEXT.json'
        if not context_file.exists():write(context_file,context)
        source=Path(self.lab.config()['ada'])
        prompt=f"""Research ADA optimization for campaign {job['campaign']}. Read CONTEXT.json and read-only ADA at {source}. Investigate weaknesses, negative past findings and substantially different strategies. Use live web search if available; otherwise say unavailable. Do not claim sources were checked merely from memory. Propose 3–5 concise, distinct hypotheses. Sources must contain real URL/date/evidence-quality; uncertainty explicit. No production writes. For this release favor small independently testable changes; at least one candidate should be a single reusable instruction change with no weakening of quality. Output {{"ideas":[{{"title":str,"hypothesis":str,"mechanism":str,"tags":[str],"sources":[{{"url":str,"date":str,"quality":str}}],"scope":str}}],"research_limitations":[str]}}. No fixed requirement to exhaust research."""
        result=self.model(job,'research-'+str(iteration),c['research_profile'],work,prompt,[source])
        proposals=structured(result['message'])
        if not isinstance(proposals.get('ideas'),list) or not 2<=len(proposals['ideas'])<=8:raise ModelOutputError('Research must retain multiple actual hypotheses')
        for proposal in proposals['ideas']:
            if not isinstance(proposal,dict) or any(not isinstance(proposal.get(k),str) or not proposal[k].strip() for k in ('title','hypothesis','mechanism','scope')) or not isinstance(proposal.get('tags'),list) or not isinstance(proposal.get('sources'),list):
                raise ModelOutputError('Hypotheses need valid structured text, tags and source records')
            if any(not isinstance(s,dict) or not isinstance(s.get('url'),str) for s in proposal['sources']):raise ModelOutputError('Invalid research source record')
        source_cache={}
        for proposal in proposals['ideas']:
            sources=[]
            for citation in proposal.get('sources',[])[:4]:
                url=citation.get('url')
                if url not in source_cache and len(source_cache)<10:source_cache[url]=retrieve(citation,folder/'sources')
                sources.append(source_cache.get(url,{**citation,'verified_retrieval':False,'limitation':'Retrieval limit reached'}))
            proposal['sources']=sources
        ids=[self.store.idea(p,job['campaign'])['id'] for p in proposals['ideas']]
        critic=folder/'critic-workspace';critic.mkdir(exist_ok=True)
        payload={'ideas':[self.store.snapshot()['ideas'][i] for i in ids],'limitations':proposals.get('research_limitations',[]),
                 'prior_findings':context['history'],'existing_knowledge':context['knowledge']}
        if not (critic/'IDEAS.json').exists():write(critic/'IDEAS.json',payload)
        prompt=f"""Independently criticize IDEAS.json. Inspect relevant read-only ADA at {source} if needed. Challenge duplicate/weak mechanisms, unsupported external claims, cost, regression risk, applicability and information value. Priority is research priority, NOT a proven improvement. For every idea output {{"reviews":[{{"id":str,"scores":{{{','.join(k+':integer0to10' for k in RUBRIC)}}},"confidence":"low|medium|high","objections":[str]}}]}}. Sources not independently available must be identified as unverified. Retain useful negative findings."""
        reviewed=self.model(job,'critic-'+str(iteration),c['reviewer'],critic,prompt,[source])
        criticism=structured(reviewed['message'])
        if {x['id'] for x in criticism.get('reviews',[])}!=set(ids) or len(criticism['reviews'])!=len(ids):raise ModelOutputError('Critic omitted/duplicated ideas')
        try:
            for entry in criticism['reviews']:self.store.rank(entry['id'],entry,reviewed['session_id'])
        except (ValueError,KeyError,TypeError) as error:raise ModelOutputError(str(error)) from error
        with self.store.transaction() as state:
            campaign=state['campaigns'][job['campaign']]
            campaign['queue'].sort(key=lambda i:state['ideas'][i].get('manual_priority',state['ideas'][i].get('priority') or -1),reverse=True)
            campaign['summary']['next']='Select a reviewed idea or run the affordable ranked shortlist'
            campaign['research_limitations']=proposals.get('research_limitations',[])

    def step(self,job,key,operation,inputs=None):
        path=self.store.root/'jobs'/job['id']/(key+'.json')
        binding=path.with_suffix('.inputs.json')
        if path.exists():
            if not binding.exists() or read(binding)!=digest(inputs):raise RuntimeError('Checkpoint inputs changed: '+key)
            return read(path)
        if self.cancelled(job['id']):raise InterruptedError('Paused before '+key)
        with self.store.transaction() as data:
            data['jobs'][job['id']]['stage']=key
            self.store.event(data,job['campaign'],key,job=job['id'])
        value=operation();write(binding,digest(inputs));write(path,value);return value

    def candidate_compatibility(self,data,old_suite,new_suite):
        if old_suite==new_suite:return {'kind':'exact-suite','source_suite':old_suite,'target_suite':new_suite,'changes':[]}
        before=data['suites'].get(old_suite);after=data['suites'].get(new_suite)
        if not before or not after or before.get('policy')!=after.get('policy'):
            raise ValueError('Reusable candidate needs the same exact suite and pinned Stable, or a verified QA-only revision')
        def selection(s):return [{k:v for k,v in task.items() if k!='revision'} for task in s['tasks']]
        if selection(before)!=selection(after):raise ValueError('Reusable candidate ordered task selection changed')
        changes=[]
        for a,b in zip(before['tasks'],after['tasks']):
            if a['revision']==b['revision']:continue
            old=self.store.verify_task(a['revision']);new=self.store.verify_task(b['revision'])
            if {k:v for k,v in old.items() if k!='procedures'}!={k:v for k,v in new.items() if k!='procedures'}:
                raise ValueError('Reusable candidate mandatory task, criteria, starter, capabilities or dependencies changed')
            modified={name for name in old['procedures'].keys()|new['procedures'].keys() if old['procedures'].get(name)!=new['procedures'].get(name)}
            if not modified or not modified<={'journeys.js','qa.py'}:raise ValueError('Reusable candidate procedures changed beyond the explicit browser assertion interpreter allowlist')
            for name in sorted(modified):
                import difflib
                old_path=self.store.root/'tasks'/a['revision']/'procedures'/name
                new_path=self.store.root/'tasks'/b['revision']/'procedures'/name
                changes.append({'task':a['id'],'procedure':name,'before':old['procedures'][name],'after':new['procedures'][name],
                    'diff':''.join(difflib.unified_diff(old_path.read_text(encoding='utf-8').splitlines(True),new_path.read_text(encoding='utf-8').splitlines(True),fromfile='before/'+name,tofile='after/'+name))})
        if not changes:raise ValueError('Different suite identities need explicit verified QA changes')
        return {'kind':'qa-only-source-transfer','source_suite':old_suite,'target_suite':new_suite,'changes':changes,
            'scope':'ADA source/provenance only. No previous application, quality outcome, timing, tokens or score is imported. Fresh independent compatibility criticism and clean application development are required.'}

    def reusable_candidate(self,data,c,identifier):
        prior=data['trials'][identifier];saved=prior.get('candidate') or {}
        if prior['stable']!=identity(self.lab.config()['ada']):raise ValueError('Reusable candidate needs the same exact suite and pinned Stable')
        self.candidate_compatibility(data,prior['suite'],c['suite'])
        def comparable(p):return {**p,'settings':{k:v for k,v in p.get('settings',{}).items() if k!='execution_policy'}}
        if any(comparable(prior['profiles'][role])!=comparable(c[role]) for role in ('development','reviewer')):
            raise ValueError('Reusable candidate development/Reviewer profile differs beyond controller revision')
        if saved.get('review_value',{}).get('status')!='PASS' or saved['review_value'].get('findings') or not saved.get('review',{}).get('configuration_verified'):
            raise ValueError('Candidate needs actual accepted independent source/behavior review')
        design=saved.get('design_review',{})
        if design.get('verdict',{}).get('status')!='PASS' or design['verdict'].get('findings') or any(not design.get(key) for key in ('session_id','evidence','plan_digest','design_digest')):
            raise ValueError('Candidate needs complete prospective independent design criticism with no unresolved findings')
        if tree_digest(saved['path'])!=saved['digest']:raise ValueError('Accepted candidate source changed')
        return prior

    def trial(self,job):
        data=self.store.snapshot();c=data['campaigns'][job['campaign']];suite=data['suites'][c['suite']]
        c={**c,**job.get('execution_profiles',{})}
        folder=self.store.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
        trial_id=job.get('trial')
        if not trial_id:
            stable=identity(self.lab.config()['ada'])
            if stable['status']:raise RuntimeError('Production ADA must be clean')
            trial_id='trial-'+uuid.uuid4().hex[:16]
            with self.store.transaction() as state:
                order=['stable','candidate'] if len(state['campaigns'][job['campaign']]['trials'])%2==0 else ['candidate','stable']
                state['trials'][trial_id]={'id':trial_id,'campaign':job['campaign'],'idea':job['payload']['idea'],
                    'suite':c['suite'],'profiles':{'development':c['development'],'reviewer':c['reviewer']},
                    'stable':stable,'idea_snapshot':data['ideas'][job['payload']['idea']],
                    'controller_digest':self.controller_digest(),'created':now(),'order':order,'tasks':{},'complete':False,'candidate':None}
                state['jobs'][job['id']]['trial']=trial_id
                state['campaigns'][job['campaign']]['trials'].append(trial_id)
                state['ideas'][job['payload']['idea']]['trials'].append(trial_id)
        trial=self.store.snapshot()['trials'][trial_id]
        if trial.get('controller_digest')!=self.controller_digest():raise RuntimeError('Controller source changed during trial; preserve this history and start a new trial')
        if identity(self.lab.config()['ada'])!=trial['stable']:raise RuntimeError('Pinned production baseline changed')
        working=folder/'candidate-ada'
        idea=trial['idea_snapshot']
        confirmation=job['payload'].get('confirmation')
        if confirmation or job['payload'].get('reuse_accepted'):
            prior=self.store.snapshot()['trials'][job['payload']['source_trial']]
            if job['payload'].get('reuse_accepted'):self.reusable_candidate(self.store.snapshot(),c,prior['id'])
            elif prior['campaign']!=job['campaign'] or prior['idea']!=trial['idea'] or prior['stable']!=trial['stable'] or not prior['complete']:
                raise RuntimeError('Confirmation must reuse the exact candidate, campaign and anchored Stable')
            saved=prior['candidate'];working=folder/'candidate-frozen'
            if tree_digest(saved['path'])!=saved['digest']:raise RuntimeError('Confirmed source changed')
            if not working.exists():copy_tree(saved['path'],working)
            review=saved['review'];verdict=saved['review_value'];candidate_digest=saved['digest']
            candidate_value={**saved,'path':str(working),('confirmation_of' if confirmation else 'reused_candidate_from'):prior['id']}
            compatibility=self.candidate_compatibility(self.store.snapshot(),prior['suite'],c['suite'])
            candidate_value['source_transfer']=compatibility
            if compatibility['kind']=='qa-only-source-transfer':
                critic=folder/'candidate-compatibility-review';critic.mkdir(parents=True,exist_ok=True)
                evidence=folder/'candidate-compatibility-evidence';evidence.mkdir(parents=True,exist_ok=True)
                transfer={'compatibility':compatibility,'candidate_digest':candidate_digest,'stable':trial['stable'],
                    'prospective_design':saved['design_review'],'independent_acceptance':saved['review_value'],
                    'original_review_session':review['session_id'],'configuration_verified':review['configuration_verified']}
                if not (evidence/'TRANSFER.json').exists():write(evidence/'TRANSFER.json',transfer)
                checked=self.model(job,'candidate-compatibility',c['reviewer'],critic,
                    f'Independently criticize the proposed reuse of unchanged accepted ADA source at {working}. Read actual protected provenance and exact QA procedure diff at {evidence/"TRANSFER.json"}, plus pinned Stable at {self.lab.config()["ada"]}. Verify the exact source difference, prior prospective/final independent evidence, and whether the QA-only assertion change introduces any new requirement for this ADA instruction. Do not reuse any prior app or outcome. This is source compatibility revalidation, not evidence of efficiency or new application success. BLOCKED if actual proof is insufficient; FAIL for a material incompatibility. Return {{"status":"PASS|FAIL|BLOCKED","findings":[str],"evidence":[str]}}.',
                    [working,evidence,Path(self.lab.config()['ada']),Path(saved['path']),Path(review['evidence']),Path(saved['design_review']['evidence'])])
                checked_value=self.verdict(checked)
                if checked_value['status']=='BLOCKED' or not checked.get('configuration_verified'):
                    raise RuntimeError('Accepted-source compatibility evidence or runtime configuration BLOCKED: '+str(checked_value))
                if checked_value['status']!='PASS':raise CandidateRejected('Accepted-source compatibility revalidation did not pass: '+str(checked_value))
                if tree_digest(working)!=candidate_digest:raise RuntimeError('Reused ADA source changed during compatibility review')
                candidate_value['compatibility_review']={'session_id':checked['session_id'],'evidence':checked['evidence'],'verdict':checked_value}
        else:
            if not working.exists():copy_tree(self.lab.config()['ada'],working)
            design_path=working/'.cache/candidate-design.json'
            proposal={'stable':trial['stable'],'idea':{k:idea[k] for k in ('title','hypothesis','mechanism','scope','objections')},
                'constraints':'One small reusable change; preserve independent quality gates, role transitions and all required verification.'}
            if not design_path.exists():write(design_path,proposal)
            elif read(design_path)!=proposal:raise RuntimeError('Prospective candidate design changed')
            implemented='candidate-implementation' in self.current(job['id'])['stages']
            if not implemented and tree_digest(working)!=trial['stable']['source_digest']:raise RuntimeError('Candidate source changed before prospective design criticism')
            self.model(job,'candidate-plan',c['research_profile'],working,
                'PHASE1 only: read .cache/candidate-design.json, assigned AGENTS/README/ACCEPTANCE and relevant source. '
                'Prepare .cache/candidate-plan.md with concrete target/placement and exact proposed instruction text, addressed material objections, falsifiable decision rule, '
                'unchanged quality gates and inexpensive verification cases. Make no implementation/source changes. State limits and recovery for adverse cases explicitly. '
                'Do not claim review or behavioral PASS. Return {"plan":".cache/candidate-plan.md","summary":str}.')
            plan_path=working/'.cache/candidate-plan.md';plan_digest=hashlib.sha256(plan_path.read_bytes()).hexdigest()
            if not implemented and tree_digest(working)!=trial['stable']['source_digest']:raise RuntimeError('Planning stage implemented candidate before design criticism')
            design_critic=folder/'candidate-design-review';design_critic.mkdir(exist_ok=True)
            design_review=self.model(job,'candidate-design',c['reviewer'],design_critic,
                f'Before any implementation, independently challenge the concrete plan with raw file SHA-256 {plan_digest} at {plan_path} and proposal at {design_path}. Read the unchanged baseline at {self.lab.config()["ada"]}. '
                'Assess scope, safety, independent quality gates, general applicability and falsifiability. This is prospective design criticism, not a claim of behavioral success. '
                'Choose inexpensive relevant verification cases for the subsequent independent reviewer. Return {"status":"PASS|FAIL|BLOCKED","evidence":[str],"findings":[str],"verification_cases":[str]}.',
                [working,Path(self.lab.config()['ada'])])
            design_value=self.verdict(design_review)
            if design_value.get('status')=='BLOCKED':raise RuntimeError('Prospective candidate design capability BLOCKED: '+str(design_value))
            if design_value.get('status')!='PASS' or design_value.get('findings'):raise CandidateRejected('Candidate design rejected before implementation: '+str(design_value))
            if not design_value.get('verification_cases'):raise ModelOutputError('Prospective design review must choose relevant verification cases')
            design_evidence={'session_id':design_review['session_id'],'evidence':design_review['evidence'],'design_digest':digest(proposal),'plan_digest':plan_digest,'verdict':design_value}
            write(working/'.cache/candidate-design-review.json',design_evidence)
            result=self.model(job,'candidate-implementation',c['research_profile'],working,
            'Develop ONE small real isolated ADA improvement for this hypothesis: '+json.dumps({k:idea[k] for k in ('title','hypothesis','mechanism','scope','objections')})+
            '. Prospective independent design criticism has already passed; read .cache/candidate-plan.md, .cache/candidate-design.json and .cache/candidate-design-review.json for actual session/evidence and selected cases. '
            'Preserve that plan. Read AGENTS/README/ACCEPTANCE and relevant sources. Preserve all independent quality gates, general applicability and existing role transitions. Do not edit Lab or benchmark. '
            'Implement, do not just plan. Native isolation adds denied empty control folders at the assigned workspace root. For check.py use an exact source-only nested copy under .cache, '
            'excluding ignored artifacts and .agents/.aws/.codex control folders; preserve source identity and every required test. Do not weaken checks to bypass sandbox restrictions. '
            'Native Git/MSYS namespace creation is unavailable. The controller will run the full unchanged trusted evaluate.py only if all executable/config bytes remain identical to pinned production. '
            'Report that native limitation and exercise your relevant change cases; do not repeatedly attempt unsupported Git tests or claim independent acceptance. '
            'Return {"change":str,"intervention":str,"limitations":[str]}.')
            candidate_intervention=structured(result['message'])
            if read(design_path)!=proposal or hashlib.sha256(plan_path.read_bytes()).hexdigest()!=plan_digest:raise RuntimeError('Candidate implementation changed its prospectively reviewed design')
            candidate_digest=tree_digest(working)
            if candidate_digest==trial['stable']['source_digest']:raise RuntimeError('Candidate contains no actual change')
            regression=self.step(job,'candidate-trusted-regression',lambda:self.documentation_regression(working,folder/'trusted-candidate-regression',trial['stable']),candidate_digest)
            if regression['status']!='PASS':raise RuntimeError('Candidate full regression BLOCKED: '+str(regression))
            critic=folder/'candidate-critic';critic.mkdir(exist_ok=True)
            subject=critic/'.cache/subject'
            if not subject.exists():copy_tree(working,subject)
            if tree_digest(subject)!=candidate_digest:raise RuntimeError('Independent candidate test copy differs from frozen source')
            review=self.model(job,'candidate-review',c['reviewer'],critic,
                f'Independently inspect actual read-only candidate {candidate_digest} at {working} and stable at {self.lab.config()["ada"]}. Hypothesis: {idea["hypothesis"]}. '
                f'Prospective independent design receipt: {json.dumps(design_evidence)}. An exact source-only verification copy is at {subject}; run check.py and evaluate.py there yourself '
                f'only where supported. Actual complete regression evidence is at {folder/"trusted-candidate-regression"/"result.json"}. The controller verified EVERY non-Markdown byte equals pinned trusted production code '
                'before running those unchanged trusted verification programs on a clean local copy. No generated executable code ran on the host. Native Git/MSYS namespace creation is unavailable; '
                'do not repeat that known unsupported native Git invocation or weaken its tests. Independently exercise the selected instruction-behavior cases and check.py through actual native tool commands. '
                'The nested copy avoids denied sandbox control folders at the workspace root. '
                'Keep raw results under .cache; do not edit candidate or verification-copy source. Do not substitute Lead claims or artifact hashes for actual independent checks. '
                'Reject weakened requirements, missing independence, unsafe execution, benchmark-specific overfitting or unrelated changes. '
                'Return {"status":"PASS|FAIL|BLOCKED","evidence":[str],"findings":[str]}.',[working,Path(self.lab.config()['ada']),folder/'trusted-candidate-regression'])
            verdict=self.verdict(review)
            if tree_digest(working)!=candidate_digest or tree_digest(subject)!=candidate_digest:raise RuntimeError('Candidate changed during independent review')
            if verdict.get('status')=='BLOCKED':raise RuntimeError('Candidate review capability is BLOCKED: '+str(verdict))
            if verdict.get('status')!='PASS' or verdict.get('findings'):raise CandidateRejected('Candidate independent criticism did not pass: '+str(verdict))
            candidate_value={'digest':candidate_digest,'intervention':candidate_intervention,'design_review':design_evidence,'review':review,'review_value':verdict}
        check=self.step(job,'candidate-validation',lambda:sandbox.run(working,[sys.executable,'check.py'],timeout=60),candidate_digest)
        if check['returncode']:raise CandidateRejected('Candidate native structural feasibility check failed; raw observations retained')
        frozen=folder/'candidate-frozen'
        if not frozen.exists():copy_tree(working,frozen)
        if tree_digest(frozen)!=candidate_digest:raise RuntimeError('Frozen candidate identity changed')
        working=frozen;candidate_value['path']=str(frozen)
        with self.store.transaction() as state:
            state['trials'][trial_id]['candidate']=candidate_value
        self.schedule_tasks(job,trial_id,suite,working,review,verdict,c)
        self.finish_trial(trial_id)

    def documentation_regression(self,candidate,target,baseline):
        """Host only pinned trusted programs; generated executable/config changes stay blocked."""
        stable=Path(self.lab.config()['ada'])
        if identity(stable)!=baseline:raise RuntimeError('Pinned trusted verification source changed')
        before=inventory(stable);after=inventory(candidate)
        changed=[name for name in set(before)|set(after) if before.get(name)!=after.get(name)]
        if any(Path(name).suffix.lower()!='.md' for name in changed):
            return {'status':'BLOCKED','reason':'Full native Git regression unavailable; only Markdown changes with all executable/config bytes pinned may use trusted host verification'}
        target=Path(target);subject=target/'subject';target.mkdir(parents=True,exist_ok=True)
        if not subject.exists():copy_tree(candidate,subject)
        expected=tree_digest(candidate)
        if tree_digest(subject)!=expected:raise RuntimeError('Trusted regression source differs')
        from .runtime import environment
        env=environment();env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=__import__('os').devnull)
        results={}
        for name in ('check.py','evaluate.py'):
            command=[getattr(sys,'_base_executable',sys.executable),name]
            result=subprocess.run(command,cwd=subject,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
            results[name]={'argv':command,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
        if tree_digest(subject)!=expected:raise RuntimeError('Trusted regression changed candidate source')
        value={'status':'PASS' if all(r['returncode']==0 for r in results.values()) else 'FAIL',
            'candidate_digest':expected,'stable_digest':tree_digest(stable),'changed_markdown':sorted(changed),
            'trust':'All non-Markdown files exactly match pinned production; generated executable/config changes are never host-executed',
            'results':results,'at':now()}
        write(target/'result.json',value);return value

    def schedule_tasks(self,job,trial_id,suite,working,review,verdict,c):
        """The same ordered dispatcher serves real adapters and controlled test fixtures."""
        for task in suite['tasks']:
            self.store.verify_task(task['revision'])
            current=self.store.snapshot()['trials'][trial_id]['tasks'].get(task['id'],{})
            if current.get('complete'):
                for arm in ('stable','candidate'):
                    saved=current[arm]
                    if saved.get('artifact') and tree_digest(saved['artifact'])!=saved['artifact_digest']:
                        raise RuntimeError('Completed application artifact changed')
                continue
            identity_task=self.verify_installed(task);adapter=self.adapters.get(identity_task['adapter'])
            if adapter is None:raise RuntimeError('Required task adapter unavailable: '+identity_task['adapter'])
            adapter.verify(self,task,identity_task)
            adapter.execute(self,job,trial_id,task,working,review,verdict,c)

    @staticmethod
    def controller_digest():
        return digest({name:hashlib.sha256((ROOT/'ada_lab'/name).read_bytes()).hexdigest() for name in
            ('engine.py','runtime.py','campaigns.py','adapters.py','qa.py','journeys.js','lab.py','process_job.py','ledger.py','research_sources.py','stateio.py')})

    @staticmethod
    def sync(source,target):
        source=Path(source);target=Path(target).resolve()
        if not target.is_relative_to(ROOT/'.lab'):raise ValueError('Unsafe experimental target')
        wanted=inventory(source);existing=inventory(target)
        for name in set(existing)-set(wanted):(target/name).unlink()
        for name in wanted:
            p=target/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((source/name).read_bytes())

    def execute_task(self,job,trial_id,task,candidate,review,verdict,c):
        trial=self.store.snapshot()['trials'][trial_id];task_id=task['id']
        def prepare():
            existing=self.store.snapshot()['trials'][trial_id]['tasks'].get(task_id,{}).get('experiment')
            if existing:return {'experiment':existing}
            packet=self.lab.prepare(self.store.snapshot()['ideas'][trial['idea']]['hypothesis'],task_id,c['development']['model'],max_sessions=30)
            exp=packet['experiment'];f=self.lab.folder(exp);self.sync(candidate,f/'candidate-working')
            receipt={'observer':'codex:'+review['session_id'],'session_ref':review['session_id'],'source_ref':review['evidence'],
                'status':'PASS','subject_digest':tree_digest(candidate),'evidence':verdict['evidence'],'findings':[]}
            self.lab.freeze_candidate(exp,receipt,'Actually execute the reviewed isolated ADA instructions in clean application development')
            self.lab.run(exp,'stable');self.lab.run(exp,'candidate')
            with self.store.transaction() as state:
                state['trials'][trial_id]['tasks'][task_id]={'complete':False,'experiment':exp,'revision':task['revision']}
            return {'experiment':exp}
        setup=self.step(job,task_id+'-prepare',prepare,{'candidate':tree_digest(candidate),'task':task,'profiles':c['development']});exp=setup['experiment']
        for arm in trial['order']:
            saved=self.store.snapshot()['trials'][trial_id]['tasks'][task_id]
            if saved.get(arm,{}).get('complete'):continue
            result=self.arm(job,task_id,exp,arm,c)
            with self.store.transaction() as state:state['trials'][trial_id]['tasks'][task_id][arm]=result
        results=self.store.snapshot()['trials'][trial_id]['tasks'][task_id]
        comparison=self.lab.compare(exp) if all(results[a]['quality'] for a in ('stable','candidate')) else {'outcome':'Independent quality rejection; see full-suite comparison'}
        comparable=results['stable']['execution_environment']==results['candidate']['execution_environment']
        comparable=comparable and comparison['outcome'] not in ('Invalid experiment','Inconclusive result')
        with self.store.transaction() as state:
            t=state['trials'][trial_id]['tasks'][task_id]
            t.update(complete=True,comparison_valid=comparable,legacy_comparison=comparison['outcome'])
        self.lab.report(exp)

    def work_model(self,job,key,role,arm,exp,profile,workspace,prompt,reads=(),images=()):
        # Timed legacy segments stay bound to actual CLI events and requested profile.
        checkpoint=self.current(job['id'])['stages'].get(key)
        if checkpoint and (checkpoint['status']=='completed' or checkpoint['status']!='invalid_output' and Path(checkpoint['receipt']).exists() and read(checkpoint['receipt']).get('status')=='completed'):
            result=self.model(job,key,profile,workspace,prompt,reads,images)
            old=checkpoint.get('legacy_session')
            if old:
                previous=next(s for s in self.lab.load(exp)['sessions'] if s['id']==old)
                if previous['status']=='active':self.lab.finish(exp,old,{'status':'completed','source_ref':result['evidence'],'recovered_after_restart':True})
            return result
        if self.cancelled(job['id']):raise InterruptedError('Paused before '+key)
        session=self.lab.begin(exp,'worker:'+job['id']+':'+arm+':'+role,role,arm,model=profile['model'],
            source_ref='Controlled subscription CLI stage '+key,timed=True,stage=key)
        with self.store.transaction() as data:data['jobs'][job['id']].setdefault('legacy_pending',{})[key]=session['id']
        try:
            result=self.model(job,key,profile,workspace,prompt,reads,images)
            usage=result.get('usage') or {}
            self.lab.finish(exp,session['id'],{'status':'completed','source_ref':result['evidence'],'runtime_session':result['session_id'],
                **{k:usage[k] for k in ('input_tokens','output_tokens','cached_input_tokens') if k in usage}})
            return result
        except BaseException:
            self.lab.finish(exp,session['id'],{'status':'interrupted' if self.cancelled(job['id']) else 'failed','source_ref':'Retained controller runtime stage '+key})
            raise

    def arm(self,job,task_id,exp,arm,c):
        f=self.lab.folder(exp);workspace=f/(arm+'-workspace');prefix=task_id+'-'+arm
        ada_proof=f/(arm+'-ada-regression')
        proof=self.step(job,prefix+'-assigned-ada-regression',lambda:self.documentation_regression(workspace/'ada',ada_proof,self.lab.load(exp)['stable']),tree_digest(workspace/'ada'))
        if proof['status']!='PASS':raise RuntimeError('Assigned ADA full verification unavailable: '+str(proof))
        proof_instruction=f' Actual complete assigned-ADA regression evidence is at {ada_proof/"result.json"}, bound to source {proof["candidate_digest"]}. The controller verified all executable/config bytes against pinned production before executing unchanged trusted ADA verification programs. Read this actual independent evidence. Native Git/MSYS namespace creation is unavailable, so do not repeat unsupported Git fixtures inside native isolation. This full regression is already complete; all actual application API/browser checks still run natively. '
        spec=read(f/'benchmark'/'benchmark.json')
        criteria={'task':task_id,'version':1,'items':[{'id':x['id'],'description':x['description'],
            'categories':['goal','code'],'capabilities':['native-runtime']+(['real-browser'] if x['id'].startswith('ui_') or x['id']=='responsive' else []),'independent':True} for x in spec['criteria']]}
        criteria_path=workspace/'.cache/criteria-frozen.json'
        if not criteria_path.exists():write(criteria_path,criteria)
        elif read(criteria_path)!=criteria:raise RuntimeError('Frozen controller acceptance criteria changed')
        plan=self.work_model(job,prefix+'-plan','lead',arm,exp,c['development'],workspace,
            'PHASE1 only. Read TASK.md, supplied README/server.py and assigned ada/AGENTS.md, ada/README.md, ada/ACCEPTANCE.md. Prepare PLAN.md and protocol-required design artifacts before any app.py/UI implementation. Existing .cache/criteria-frozen.json maps the unchanged task; preserve it. Controller supplies actual independent ledger evidence after QA. Follow assigned ADA. No missing-implementation failure is a defect.'+proof_instruction+BROWSER_CAPABILITIES+' Return {"plan":"PLAN.md","summary":str}.',[ada_proof])
        plan_digest=digest((workspace/'PLAN.md').read_text(encoding='utf-8'))
        old_design=self.lab.load(exp)['runs'][arm]['reviews'].get('design')
        if old_design and old_design['status']=='PASS' and old_design['subject_digest']!=plan_digest:raise RuntimeError('Previously frozen plan changed')
        critic=self.store.root/'jobs'/job['id']/(prefix+'-reviewer');critic.mkdir(parents=True,exist_ok=True)
        qa_path=self.store.root/'jobs'/job['id']/(task_id+'-qa-plan.json')
        frozen_qa=read(qa_path) if qa_path.exists() else None
        qa_instruction=BROWSER_CAPABILITIES+('Reuse this independently chosen frozen QA plan; critique this arm design separately: '+json.dumps(frozen_qa) if frozen_qa else '''Choose meaningful API/browser checks before implementation covering frozen TASK and the PLAN's binding browser observations. api_checks is a list of {method,target,body or raw,expected_status,unchanged:boolean}. Include useful invalid input rejection, not only health. browser_steps is an ordered list using the declared actions and TASK testids. Include actual keyboard create/edit access, inert stored markup and, if the PLAN promises it, delayed-response ordering using delay_next plus real observations and settle. Do not invent IDs. These exact actions will run both arms. Return them in qa_plan.''')
        dprogress=self.current(job['id']).get('design_progress',{}).get(prefix,0)
        pending_design=self.current(job['id']).get('design_correction_context',{}).get(prefix)
        if pending_design and pending_design['attempt']==dprogress:
            self.work_model(job,prefix+'-design-correction','lead',arm,exp,c['development'],workspace,pending_design['prompt'],[ada_proof])
            dprogress+=1
            with self.store.transaction() as state:state['jobs'][job['id']].setdefault('design_progress',{})[prefix]=dprogress
        for design_attempt in range(dprogress,2):
            plan_digest=digest((workspace/'PLAN.md').read_text(encoding='utf-8'))
            plan_file_sha=hashlib.sha256((workspace/'PLAN.md').read_bytes()).hexdigest()
            design_prompt=f'Independently inspect read-only TASK and PLAN with raw file SHA-256 {plan_file_sha} at {workspace}. app.py must be absent. Challenge completeness and assigned ADA-specific preparation; do not implement app. {proof_instruction} {qa_instruction} Return {{"status":"PASS|FAIL|BLOCKED","evidence":[str],"findings":[str],"qa_plan":{{"api_checks":[],"browser_steps":[]}}}}.'
            prompt_path=critic/('DESIGN_PROMPT-'+str(design_attempt)+'.txt')
            if not prompt_path.exists():prompt_path.write_text(design_prompt,encoding='utf-8')
            else:design_prompt=prompt_path.read_text(encoding='utf-8')
            key=prefix+'-design'+('' if design_attempt==0 else '-1')
            design=self.work_model(job,key,'reviewer',arm,exp,c['reviewer'],critic,design_prompt,[workspace,ada_proof])
            design_value=self.verdict(design)
            status=design_value.get('status')
            if status=='BLOCKED':raise RuntimeError('Independent design capability BLOCKED: '+str(design_value))
            passed=status=='PASS' and not design_value.get('findings')
            receipt={'observer':'codex:'+design['session_id'],'session_ref':design['session_id'],'source_ref':design['evidence'],
                'status':'PASS' if passed else 'FAIL','subject_digest':plan_digest,'evidence':design_value.get('evidence',[]),'findings':design_value.get('findings',[])}
            current_design=self.lab.load(exp)['runs'][arm]['reviews'].get('design')
            if not current_design or current_design['subject_digest']!=plan_digest or current_design['status']!=receipt['status']:self.lab.add_review(exp,arm,'design',receipt)
            if passed:
                if not qa_path.exists():
                    try:validate_plan(design_value.get('qa_plan',{}))
                    except ValueError as error:raise ModelOutputError(str(error)) from error
                    write(qa_path,design_value['qa_plan'])
                break
            if design_attempt==1:raise CandidateRejected('Application design failed independent challenge after bounded revision')
            write(f/(arm+'-rejected-plan.json'),{'text':(workspace/'PLAN.md').read_text(encoding='utf-8'),'review':receipt})
            prompt='Revise only the unaccepted design/PLAN and required preparation artifacts to address independent findings: '+json.dumps(receipt['findings'])+'. Do not implement app/UI yet. Preserve TASK/server/assignedADA/frozen criteria.'+proof_instruction+' Return {"revision":str}.'
            with self.store.transaction() as state:state['jobs'][job['id']].setdefault('design_correction_context',{})[prefix]={'attempt':design_attempt,'prompt':prompt}
            self.work_model(job,prefix+'-design-correction','lead',arm,exp,c['development'],workspace,prompt,[ada_proof])
            with self.store.transaction() as state:state['jobs'][job['id']].setdefault('design_progress',{})[prefix]=design_attempt+1
        preparation_path=f/(arm+'-preparation.json')
        if not preparation_path.exists():
            if (workspace/'app.py').exists():raise RuntimeError('Missing preimplementation preparation evidence')
            prepared={k:v for k,v in inventory(workspace).items() if Path(k).suffix in ('.md','.json') and k!='README.md' and k not in ('TASK.md','server.py') and not k.startswith('ada/')}
            archive=f/(arm+'-preparation-source')
            for name in prepared:
                target=archive/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((workspace/name).read_bytes())
            # Continuation/status and integration records may legitimately evolve.
            # Preserve their actual initial bytes without treating them as frozen requirements.
            write(preparation_path,{'files':{'PLAN.md':prepared['PLAN.md']},'initial_files':prepared,
                'archive':str(archive),'source_ref':design['evidence'],'app_absent':True,'observed_at':now()})
        preparation=read(preparation_path)
        implementation=self.work_model(job,prefix+'-implementation','lead',arm,exp,c['development'],workspace,
            'PHASE2. Independent design passed. Read frozen PLAN/TASK and assigned ADA; implement the entire functional responsive Repair Queue app now using only Python stdlib/SQLite/HTML/CSS/vanillaJS. Preserve PLAN/TASK/server.py/ada and frozen criteria. Execute meaningful own checks through native isolated tool commands; retain actual failed commands and fixes in .cache. Native network sockets, including loopback and asyncio wakeup sockets, are unavailable. The controller supplies its verified request-byte bridge and mandatory actual browser QA in Phase 3. Do not rebuild that bridge or repeatedly probe known denied sockets. Your own handler/SQLite checks can run directly in isolated tool processes; browser acceptance remains pending independent QA. No authoritative evaluator/reference/other arm. Keep import-time no-I/O. Implement all task requirements and selectors, malformed/invalid JSON with nonmutation, Unicode, intersected search/filters, persistent SQLite, UI failure/retry/loading/empty/error/mobile. Follow any assigned ADA extra preparation and execute its actual checks.'+proof_instruction+' Return {"summary":str,"own_checks":[str],"corrections":[str],"limitations":[str]}.',[ada_proof])
        implementation_value=structured(implementation['message'])
        progress=self.current(job['id']).get('arm_progress',{}).get(prefix,0)
        pending=self.current(job['id']).get('correction_context',{}).get(prefix)
        if pending and pending['attempt']==progress:
            if not pending.get('prompt'):raise RuntimeError('Historical correction prompt binding unavailable; refusing evidence rebind')
            recovered=self.work_model(job,prefix+f'-correction-{progress}','lead',arm,exp,c['development'],workspace,pending['prompt'],[ada_proof])
            if recovered.get('requested')!=c['development'] or recovered.get('source_after')!=tree_digest(workspace):
                raise RuntimeError('Recovered correction profile/source differs from actual workspace')
            corrected=self.current(job['id'])['stages'].get(prefix+f'-correction-{progress}')
            if corrected and corrected['status']=='completed':
                self.lab.correction(exp,arm,{'before_digest':pending['before'],'after_digest':tree_digest(workspace),
                    'source_ref':corrected['receipt'],'findings_ref':pending['findings_ref']})
                progress+=1
                with self.store.transaction() as state:state['jobs'][job['id']].setdefault('arm_progress',{})[prefix]=progress
        for attempt in range(progress,3):
            subject=tree_digest(workspace)
            if any(inventory(workspace).get(k)!=value for k,value in preparation['files'].items()):raise RuntimeError('Frozen preparation artifact changed after design')
            if digest((workspace/'PLAN.md').read_text(encoding='utf-8'))!=plan_digest:raise RuntimeError('Frozen plan changed')
            original=f/arm
            if tree_digest(workspace/'ada')!=tree_digest(original):raise RuntimeError('Assigned ADA changed inside app workspace')
            if (workspace/'TASK.md').read_bytes()!=(f/'benchmark'/'task.md').read_bytes() or (workspace/'server.py').read_bytes()!=(f/'benchmark'/'starter'/'server.py').read_bytes():raise RuntimeError('Frozen task/adapter changed')
            evaluation=self.step(job,prefix+f'-evaluate-{attempt}',lambda:evaluator.evaluate(workspace,self.lab.state,self.lab.config()['ada'],task_id),{'source':subject,'evaluator':evaluator.version()})
            structure=self.step(job,prefix+f'-structure-{attempt}',lambda:sandbox.run(workspace,[sys.executable,'ada/check.py'],timeout=60),subject)
            actions=self.step(job,prefix+f'-actions-{attempt}',lambda:review_actions(workspace,read(qa_path),
                self.store.root/'jobs'/job['id']/(prefix+f'-qa-{attempt}'),self.lab.config()['ada'],self.lab.state),{'source':subject,'plan':read(qa_path)})
            quality_evidence={key:evaluation[key] for key in ('quality_pass','artifact_digest','benchmark_revision','criteria_digest','evaluator_version','evidence')}
            quality_evidence['criteria']={key:{k:v for k,v in value.items() if k in ('status','detail')} for key,value in evaluation['criteria'].items()}
            observation={'artifact_digest':subject,'mandatory':quality_evidence,'reviewer_actions':actions,'reviewer_plan':read(qa_path),'ada_structure':structure,'preparation':preparation,'assigned_ada_regression':proof,
                'prospective_design':{'plan_session':plan['session_id'],'review_session':design['session_id'],
                    'frozen_plan_digest':plan_digest,'plan_file_sha256':hashlib.sha256((workspace/'PLAN.md').read_bytes()).hexdigest(),
                    'independent_review':self.lab.load(exp)['runs'][arm]['reviews']['design'],
                    'review_output':design_value,'implementation_session':implementation['session_id']},
                'acceptance_phase':'Phase 3 independent behavior/source review. Phase 4 ledger binding and assess follow this verdict; no final acceptance is claimed yet.'}
            command_events=[]
            for key,stage in self.current(job['id'])['stages'].items():
                if key not in (prefix+'-plan',prefix+'-design-correction',prefix+'-implementation') and not key.startswith(prefix+'-correction-'):continue
                event_path=Path(stage['receipt']).parent/'events.jsonl'
                source_after=read(stage['receipt']).get('source_after') if Path(stage['receipt']).exists() else None
                if event_path.exists():
                    for line in event_path.read_text(encoding='utf-8').splitlines():
                        try:event=json.loads(line)
                        except ValueError:continue
                        item=event.get('item',{})
                        if event.get('type')=='item.completed' and item.get('type')=='command_execution':
                            command_events.append({'stage':key,'started':stage.get('started'),'finished':stage.get('finished'),
                                'session_id':stage.get('session_id'),'stage_source_after':source_after,'command':item.get('command'),
                                'returncode':item.get('exit_code'),'output':item.get('aggregated_output','')})
            evidence_root=f/(arm+'-review-observations');evidence_root.mkdir(exist_ok=True)
            commands_path=evidence_root/f'COMMANDS-{attempt}.json'
            if not commands_path.exists():write(commands_path,command_events)
            observation['lead_tool_observations']=[{**item,'output':item['output'][-2500:]} for item in command_events[-25:]]
            observation['complete_lead_command_evidence']=str(commands_path)
            observation['stage_order']=[{k:stage.get(k) for k in ('key','started','finished','session_id','status')}
                for key,stage in self.current(job['id'])['stages'].items() if key.startswith(prefix+'-')]
            observation_path=evidence_root/f'OBSERVATIONS-{attempt}.json'
            if not observation_path.exists():write(observation_path,observation)
            decision=self.work_model(job,prefix+f'-final-{attempt}','reviewer',arm,exp,c['reviewer'],critic,
                f'Independently review protected actual observations at {observation_path}, attached real desktop/mobile images, then exact current read-only source digest {subject} at {workspace} and relevant tests/docs. Your selected actions actually ran against native app/browser. The prospective_design and stage_order fields supply actual independent preimplementation criticism; initial preparation/status files are historical snapshots, not current controller phase. You are the Phase 3 independent observer. After your actual verdict the controller will bind authentic external goal/capability observations plus your exact-source review into the ADA ledger and run acceptance.assess in Phase 4. The pending application ledger is not yet final acceptance; do not claim it passed or require Phase 4 to precede Phase 3. Flag any genuinely missing required evidence. Verify assigned ADA practices against actual preparation/tool observations and complete_lead_command_evidence; unexercised required practices are BLOCKED, not a claim of compliance. Judge frozen functional and UX requirements: readability, labels, usability, errors/recovery, mobile/desktop. Do not grade by speed or token consumption. Source review alone is insufficient. Do not override failed checks with assertions. Return {{"status":"PASS|FAIL|BLOCKED","evidence":[str],"findings":[{{"id":str,"category":str,"actual":str,"fix":str}}],"visual_observations":[str],"protocol_observations":[str]}}.',
                [workspace,ada_proof,evidence_root,Path(evaluation['evidence']).parent]+([Path(preparation['archive'])] if preparation.get('archive') else [])+
                ([Path(actions['images'][0]).parent] if actions.get('images') else []),actions.get('images',()))
            value=self.verdict(decision)
            if value.get('status')=='PASS' and (not isinstance(value.get('visual_observations'),list) or len(value['visual_observations'])<2):raise ModelOutputError('PASS needs actual desktop and mobile visual observations')
            if value.get('status')=='PASS' and not value.get('protocol_observations'):raise ModelOutputError('PASS needs independent observed protocol execution evidence')
            if tree_digest(workspace)!=subject:raise RuntimeError('Reviewer changed application source')
            status=value.get('status')
            if status=='BLOCKED':raise RuntimeError('Independent QA capability BLOCKED: '+str(value.get('findings')))
            if status=='PASS' and (not evaluation['quality_pass'] or actions['status']!='PASS' or structure['returncode'] or value.get('findings')):status='FAIL'
            final={'observer':'codex:'+decision['session_id'],'session_ref':decision['session_id'],'source_ref':decision['evidence'],
                'status':status,'subject_digest':subject,'evidence':value.get('evidence',[])+value.get('visual_observations',[]),
                'findings':value.get('findings',[]) or ([] if status=='PASS' else [{'id':'runtime-verification','actual':'Mandatory or Reviewer-selected behavior failed','fix':'Resolve actual runtime observations'}])}
            for phase in ('behavior','code'):self.lab.add_review(exp,arm,phase,final)
            if status=='PASS':
                if read(criteria_path)!=criteria:raise RuntimeError('Frozen acceptance criteria changed')
                ledger_payload=critic/f'LEDGER-{attempt}.json'
                write(ledger_payload,{'criteria':criteria,'implementers':[plan['session_id'],implementation['session_id']],
                    'observer':decision['session_id'],'source_ref':decision['evidence'],
                    'capability_evidence':'Actual native mandatory API/browser checks and independent chosen journeys; desktop/mobile images observed',
                    'observations':{k:'Observed '+v['status']+': '+v.get('detail','') for k,v in evaluation['criteria'].items()}})
                ledger=subprocess.run([sys.executable,'-I',str(ROOT/'ada_lab/ledger.py'),str(self.lab.config()['ada']),str(workspace),str(ledger_payload)],
                    capture_output=True,text=True,encoding='utf-8',timeout=30)
                if ledger.returncode:raise RuntimeError('Trusted acceptance bookkeeping failed: '+ledger.stdout[-1000:]+ledger.stderr[-500:])
                write(critic/f'LEDGER-RESULT-{attempt}.json',json.loads(ledger.stdout));break
            if attempt==2:break
            before=subject
            correction_prompt='Independent verification found actual defects: '+json.dumps(final['findings'])+'. Fix them without modifying frozen requirements, tests/adapter/PLAN/assigned ADA. Preserve failed evidence, run meaningful checks.'+proof_instruction+' Return {"correction":str,"checks":[str],"limitations":[str]}.'
            with self.store.transaction() as state:
                state['jobs'][job['id']].setdefault('correction_context',{})[prefix]={'attempt':attempt,'before':before,'findings_ref':decision['evidence'],'prompt':correction_prompt}
            correction=self.work_model(job,prefix+f'-correction-{attempt}','lead',arm,exp,c['development'],workspace,
                correction_prompt,[ada_proof])
            if correction.get('requested')!=c['development'] or correction.get('source_after')!=tree_digest(workspace):
                raise RuntimeError('Actual correction receipt does not bind current profile/source')
            self.lab.correction(exp,arm,{'before_digest':before,'after_digest':tree_digest(workspace),
                'source_ref':correction['evidence'],'findings_ref':decision['evidence']})
            with self.store.transaction() as state:state['jobs'][job['id']].setdefault('arm_progress',{})[prefix]=attempt+1
        if final['status']=='PASS':
            intervention={'source_ref':decision['evidence'],'observation':{'preparation':preparation,
                'actual_lead_tool_commands':command_events,'independent_protocol_observations':value['protocol_observations'],
                'external_runtime_evidence':evaluation['evidence'],'lead_handoff':implementation_value}}
            submitted=self.lab.submit(exp,arm,intervention if arm=='candidate' else None)
            official=self.lab.evaluate_run(exp,arm)
        else:
            negative=f/'artifacts'/(arm+'-negative-'+subject[:16]);negative.parent.mkdir(exist_ok=True)
            if not negative.exists():copy_tree(workspace,negative)
            if tree_digest(negative)!=subject:raise RuntimeError('Rejected source snapshot changed')
            submitted={'artifact':str(negative),'artifact_digest':subject};official=evaluation
            with lock(f/'controller.lock'):
                record=self.lab.load(exp);record['runs'][arm].update(status='rejected',rejected_artifact=submitted,rejected_evaluation=evaluation)
                record['status']='rejected';record['next_action']='Retain measured independent quality failure; no accepted submission or production promotion'
                self.lab.save(record)
        current_job=self.current(job['id'])
        sessions=[s for k,s in current_job['stages'].items() if k.startswith(prefix+'-')]
        sessions.extend(s for s in current_job.get('stage_history',[]) if s.get('key','').startswith(prefix+'-'))
        tokens=[s.get('tokens') for s in sessions]
        verified=bool(sessions) and all(s.get('configuration_verified') for s in sessions)
        resource_receipts=[read(s['receipt']).get('usage') if Path(s['receipt']).exists() else None for s in sessions]
        resources={key:sum(u.get(key,0) for u in resource_receipts) if resource_receipts and all(u is not None for u in resource_receipts) else None
                   for key in ('input_tokens','output_tokens','cached_input_tokens')}
        return {'complete':True,'quality':official['quality_pass'] and final['status']=='PASS','artifact':submitted['artifact'],
            'artifact_digest':submitted['artifact_digest'],'tokens':sum(tokens) if tokens and all(t is not None for t in tokens) else None,
            'configuration_verified':verified,'comparison_limit':'Runtime model/effort context only; no provider-side rerouting attestation',
            'criteria':{k:v['status'] for k,v in official['criteria'].items()},'images':actions['images'],
            'independent_status':final['status'],'findings':final['findings'],
            'protocol_observations':value.get('protocol_observations',[]),'preparation':preparation,
            'design_revisions':self.current(job['id']).get('design_progress',{}).get(prefix,0),
            'correction_attempts':len(self.lab.load(exp)['runs'][arm]['corrections']),
            'development_profile':c['development'],'reviewer_profile':c['reviewer'],'resources':resources,
            'execution_environment':self.lab.observed_environment({'evaluation':official}),
            'observed_tokens_lower_bound':sum(t for t in tokens if t is not None),'unknown_usage_stages':sum(t is None for t in tokens),
            'runtime_sessions':sorted({s.get('session_id') for s in sessions if s.get('session_id')}),
            'tool_failure_events':sum(len(read(s['receipt']).get('failed_tool_commands',[])) for s in sessions if Path(s['receipt']).exists()),
            'self_check_correction_cycles':None,'correction_scope':'Registered independent-finding revisions; self-check failures remain actual tool events, not inferred source-edit cycles',
            'provider_model_calls':None,'monetary_cost':None,'evaluation_model_tokens':0}

    def finish_trial(self,identifier):
        with self.store.transaction() as data:
            t=data['trials'][identifier];c=data['campaigns'][t['campaign']];suite=data['suites'][t['suite']]
            result=aggregate(t['tasks'],[x['id'] for x in suite['tasks']])
            if not result['complete']:raise RuntimeError('Required suite task results are incomplete')
            t.update(complete=True,finished=now(),comparison=result)
            verified=all(task[a].get('configuration_verified') for task in t['tasks'].values() for a in ('stable','candidate'))
            comparable=all(task.get('comparison_valid',True) for task in t['tasks'].values())
            t['configuration_verified']=verified
            if not verified:t['comparison']['outcome']='Quality evaluated; effective model configuration unverified'
            if not comparable:t['comparison']['outcome']='Quality evaluated; execution environments are not comparable'
            summary={'trial':identifier,'idea':t['idea'],'change':t['candidate']['intervention'],'suite':t['suite'],
                'profiles':t['profiles'],'stable':t['stable']['commit'],'candidate':t['candidate']['digest'],
                'per_task':{k:{a:{x:v[a].get(x) for x in ('quality','tokens','correction_attempts')} for a in ('stable','candidate')} for k,v in t['tasks'].items()},
                'outcome':t['comparison']['outcome'],'limitations':result.get('limitations'),'next':'Retain negative findings; confirm promising changes with varied order and fresh contexts'}
            t['summary']=summary;c['summary']['findings']=(c['summary']['findings']+[summary])[-8:]
            c['summary']['next']='Inspect per-task results, continue ranked queue, or research unresolved failures'
            if verified and comparable and result['quality']['candidate']:
                if identifier not in c['alternatives']:c['alternatives'].append(identifier)
                def vector(x):
                    entry=data['trials'][x];return (entry['comparison']['tokens']['candidate'],sum(v['candidate']['correction_attempts'] for v in entry['tasks'].values()))
                frontier=[]
                for value in c['alternatives']:
                    a=vector(value)
                    dominated=any(other!=value and a[0] is not None and (b:=vector(other))[0] is not None
                        and b[0]<=a[0] and b[1]<=a[1] and b!=a for other in c['alternatives'])
                    if not dominated:frontier.append(value)
                c['alternatives']=frontier
                leaders=[x for x in frontier if data['trials'][x]['comparison']['outcome'] in ('Resource improvement observed','Candidate quality improvement observed')]
                c['leader']=min(leaders,key=lambda x:(0 if data['trials'][x]['comparison']['outcome']=='Candidate quality improvement observed' else 1, vector(x)[0] if vector(x)[0] is not None else float('inf'))) if leaders else None
            matches=[data['trials'][x] for x in c['trials'] if data['trials'][x].get('complete') and
                     data['trials'][x].get('candidate',{}).get('digest')==t['candidate']['digest'] and
                     data['trials'][x].get('configuration_verified') and data['trials'][x]['stable']==t['stable'] and
                     (data['trials'][x]['comparison']['outcome']=='Candidate quality improvement observed' or
                      data['trials'][x]['comparison']['outcome']=='Resource improvement observed' and all(
                          item['candidate']['correction_attempts']<=item['stable']['correction_attempts'] for item in data['trials'][x]['tasks'].values()))]
            if len(matches)>=3 and len({tuple(x['order']) for x in matches})>=2:
                c['champion']={'trial':identifier,'candidate':t['candidate']['digest'],'policy':c['policy']['confirmation'],
                    'confirmed_trials':[x['id'] for x in matches],'scope':'This suite/profile only; not production promotion'}
            data['ideas'][t['idea']]['status']='reviewed'  # Global knowledge stays reusable in other profiles.
