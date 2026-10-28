"""Immutable task/suite identities and durable research state (no model execution)."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import shutil
import uuid
import time

from .files import ROOT, copy_tree, digest, inventory, lock, now, read, safe_id, tree_digest, write
from . import evaluator
from .stateio import write, rename

POLICY = {'version':3, 'quality':'all mandatory criteria and independent reviews, every task',
          'priority':['quality','observed development tokens','autonomy','time diagnostic'],
          'confirmation':{'pairs':3,'orders':2,'fresh_contexts':True,
                          'basis':'repeated quality gain OR complete token advantage without correction regression'}}
CAPABILITIES = {'repair_queue_v1':['python','sqlite','native-isolation','real-browser']}


def profile(model, effort, runtime, **settings):
    if not isinstance(model,str) or not model.strip() or effort not in ('low','medium','high','xhigh','max'):
        raise ValueError('Select an explicit model and supported reasoning effort')
    if not isinstance(runtime,str) or not runtime.strip():
        raise ValueError('Runtime version must be explicit')
    return {'provider':'codex-chatgpt','model':model.strip(),'effort':effort,'runtime':runtime,
            'settings':{'permissions':'lab-worker-v1','web_search':'disabled', **settings}}


class Campaigns:
    def __init__(self, state=None):
        self.root = Path(state or ROOT/'.lab'/'app').resolve()
        if not self.root.is_relative_to(ROOT/'.lab'):
            raise ValueError('Campaign state must remain under ignored .lab')
        self.root.mkdir(parents=True,exist_ok=True)

    @contextmanager
    def transaction(self,write_back=True):
        deadline=time.monotonic()+10
        while True:
            guard=lock(self.root/'state.lock')
            try:guard.__enter__();break
            except RuntimeError:
                if time.monotonic()>=deadline:raise
                time.sleep(.02)
        try:
            data = read(self.root/'state.json') if (self.root/'state.json').exists() else {
                'schema':1,'tasks':{},'suites':{},'campaigns':{},'ideas':{},'trials':{},'jobs':{},'events':[]}
            yield data
            if write_back:write(self.root/'state.json',data)
        finally:guard.__exit__(None,None,None)

    def snapshot(self):
        with self.transaction(False) as data:
            return __import__('copy').deepcopy(data)

    @staticmethod
    def event(data, campaign, message, **details):
        data['events'].append({'at':now(),'campaign':campaign,'message':message,**details})
        # Full stage evidence remains archived; UI events are intentionally bounded.
        data['events'] = data['events'][-300:]

    def task(self, identifier, path, adapter='repair_queue_v1', capabilities=None):
        safe_id(identifier)
        path = Path(path).resolve()
        manifest = inventory(path)
        if 'task.md' not in manifest or 'benchmark.json' not in manifest:
            raise ValueError('A task needs task.md, benchmark.json and a starter directory')
        if not (path/'starter').is_dir():
            raise ValueError('Task starter is missing')
        procedures = {name:hashlib.sha256((ROOT/'ada_lab'/name).read_bytes()).hexdigest()
                      for name in ('evaluator.py','sandbox.py','files.py','native.py','backend_worker.py','browser_checks.js','qa.py','journeys.js','adapters.py','ledger.py')}
        identity = {'task':identifier,'files':manifest,'adapter':adapter,'procedures':procedures,
                    'dependencies':{'pyproject.toml':hashlib.sha256((ROOT/'pyproject.toml').read_bytes()).hexdigest()},
                    'adapter_version':1,'capabilities':capabilities or CAPABILITIES.get(adapter,[])}
        revision = digest(identity)
        target = self.root/'tasks'/revision
        with self.transaction() as data:
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                staging=target.parent/('.prepare-'+uuid.uuid4().hex);staging.mkdir()
                copy_tree(path,staging/'source')
                (staging/'procedures').mkdir()
                (staging/'dependencies').mkdir();shutil.copyfile(ROOT/'pyproject.toml',staging/'dependencies/pyproject.toml')
                for name in procedures:
                    shutil.copyfile(ROOT/'ada_lab'/name,staging/'procedures'/name)
                write(staging/'identity.json',identity)
                rename(staging,target)
            self.verify_task(revision)
            data['tasks'][identifier] = {'id':identifier,'revision':revision,'adapter':adapter,
                'capabilities':identity['capabilities'],'supported':adapter in CAPABILITIES}
        return {'id':identifier,'revision':revision}

    def verify_task(self, revision):
        target = self.root/'tasks'/safe_id(revision)
        identity = read(target/'identity.json')
        if digest(identity)!=revision or inventory(target/'source')!=identity['files']:
            raise ValueError('Frozen task identity changed')
        for name,value in identity['procedures'].items():
            if hashlib.sha256((target/'procedures'/name).read_bytes()).hexdigest()!=value:
                raise ValueError('Frozen evaluator procedure changed')
        for name,value in identity.get('dependencies',{}).items():
            if hashlib.sha256((target/'dependencies'/name).read_bytes()).hexdigest()!=value:raise ValueError('Frozen dependency manifest changed')
        return identity

    def suite(self, name, task_ids, policy=None):
        if not isinstance(name,str) or not name.strip() or not isinstance(task_ids,list) or not task_ids:
            raise ValueError('A named suite needs one or more ordered tasks')
        if len(set(task_ids))!=len(task_ids):
            raise ValueError('Select distinct task identifiers')
        with self.transaction() as data:
            tasks = [{'id':name,'revision':data['tasks'][name]['revision'],'required':True} for name in task_ids]
            value = {'tasks':tasks,'policy':policy or {'quality':'all required tasks','aggregation':'sum comparable resources; retain per-task failures'}}
            revision = digest(value)
            data['suites'].setdefault(revision,{'revision':revision,'name':name.strip(),**value})
            return data['suites'][revision]

    def campaign(self, suite, development, reviewer, research=None, name=None):
        value = {'suite':suite,'development':development,'reviewer':reviewer,'policy':POLICY}
        identifier = 'campaign-'+digest(value)[:24]
        with self.transaction() as data:
            if suite not in data['suites']:
                raise ValueError('Unknown immutable suite revision')
            if identifier not in data['campaigns']:
                data['campaigns'][identifier] = {'id':identifier,'fingerprint':digest(value),**value,
                    'name':name or data['suites'][suite]['name']+' / '+development['model']+' / '+development['effort'],
                    'research_profile':research or reviewer,'created':now(),'research_state':'idle','experiment_state':'idle',
                    'queue':[],'trials':[],'leader':None,'champion':None,'alternatives':[],
                    'settings':{'shortlist':3,'threshold':8.5,'reserve_fraction':0.2,'max_calls':40,'token_budget':None,'stage_timeout':900,'research_iterations':1},
                    'usage':{'calls':0,'known_tokens':0,'unknown_usage_stages':0,'reserved_calls':0,'reserved_tokens':0},
                    'summary':{'findings':[],'open_questions':[],'next':'Run focused research or select a retained idea'},'blocker':None}
                self.event(data,identifier,'Campaign created; no model work launched')
            return data['campaigns'][identifier]

    def idea(self, payload, campaign=None):
        required = ('title','hypothesis','mechanism','tags','sources','scope')
        if any(not payload.get(k) for k in required if k!='sources'):
            raise ValueError('Hypothesis needs a title, mechanism, applicability and implementation scope')
        key = digest({'mechanism':' '.join(payload['mechanism'].casefold().split()),
                      'hypothesis':' '.join(payload['hypothesis'].casefold().split())})
        identifier = 'idea-'+key[:20]
        with self.transaction() as data:
            if identifier not in data['ideas']:
                data['ideas'][identifier] = {'id':identifier,**payload,'created':now(),'status':'proposed',
                    'objections':[],'priority':None,'confidence':'unreviewed','related':[],'trials':[],'reviews':[]}
            if campaign and identifier not in data['campaigns'][campaign]['queue']:
                data['campaigns'][campaign]['queue'].append(identifier)
            return data['ideas'][identifier]

    def rank(self, identifier, criticism, source):
        names = ('quality','resources','evidence','generality','simplicity','experimental_value','low_risk')
        scores = criticism.get('scores',{})
        if set(scores)!=set(names) or any(type(scores[k]) is not int or not 0<=scores[k]<=10 for k in names):
            raise ValueError('Priority needs seven integer 0–10 rubric values')
        if not criticism.get('objections') or criticism.get('confidence') not in ('low','medium','high'):
            raise ValueError('Independent criticism and confidence are required')
        with self.transaction() as data:
            idea = data['ideas'][identifier]
            idea.update(objections=criticism['objections'],confidence=criticism['confidence'],scores=scores,
                        priority=round(sum(scores.values())/len(names),1),status='reviewed')
            idea['reviews'].append({'at':now(),'source':source,**criticism})
            return idea

    def settings(self, identifier, values):
        allowed = {'shortlist','threshold','reserve_fraction','max_calls','token_budget','stage_timeout','research_iterations'}
        if set(values)-allowed:
            raise ValueError('Unknown campaign setting')
        with self.transaction() as data:
            settings = {**data['campaigns'][identifier]['settings'],**values}
            if type(settings['shortlist']) is not int or not 1<=settings['shortlist']<=20 or not 0<=settings['threshold']<=10:
                raise ValueError('Invalid shortlist settings')
            if not 0<=settings['reserve_fraction']<=0.5 or type(settings['max_calls']) is not int or settings['max_calls']<1:
                raise ValueError('Invalid admission settings')
            if settings['token_budget'] is not None and (type(settings['token_budget']) is not int or settings['token_budget']<1):
                raise ValueError('Token budget must be a positive integer or unknown')
            if not 30<=settings['stage_timeout']<=3600:
                raise ValueError('Stage timeout must be 30–3600 seconds')
            if type(settings['research_iterations']) is not int or not 1<=settings['research_iterations']<=10:
                raise ValueError('Research iterations must be 1–10')
            data['campaigns'][identifier]['settings'] = settings
            return settings

    def reserve(self, campaign, calls, tokens=None):
        with self.transaction() as data:
            c = data['campaigns'][campaign]; u=c['usage']; s=c['settings']
            if u['calls']+u['reserved_calls']+calls>s['max_calls']:
                raise ValueError('Insufficient authorized model-stage allowance; queue retained')
            if s['token_budget'] is not None:
                if tokens is None or u['unknown_usage_stages']:
                    raise ValueError('Token admission is uncertain; no enforceable complete estimate')
                if u['known_tokens']+u['reserved_tokens']+tokens*(1+s['reserve_fraction'])>s['token_budget']:
                    raise ValueError('Insufficient configured token budget including reserve')
            u['reserved_calls']+=calls; u['reserved_tokens']+=tokens or 0

    def control(self, campaign, area, action):
        if area not in ('research','experiment') or action not in ('pause','resume','stop'):
            raise ValueError('Invalid control')
        with self.transaction() as data:
            c=data['campaigns'][campaign]
            c[area+'_state']={'pause':'paused','resume':'running','stop':'stopped'}[action]
            if area=='experiment' and action=='stop':c['auto_queue']=False
            if action=='stop':
                for job in data['jobs'].values():
                    if job['campaign']==campaign and job['kind']==('research' if area=='research' else 'trial') and job['status'] in ('queued','paused','blocked','interrupted'):
                        r=job.get('reservation',{});c['usage']['reserved_calls']-=r.get('calls',0);c['usage']['reserved_tokens']-=r.get('tokens',0)
                        job['reservation']={'calls':0,'tokens':0};job['status']='stopped'
            self.event(data,campaign,area+' '+action+' requested')

    def enqueue(self, campaign, kind, payload=None):
        if kind not in ('research','trial'):
            raise ValueError('Unknown work kind')
        with self.transaction() as data:
            c=data['campaigns'][campaign]
            if any(j['campaign']==campaign and j['kind']==kind and j['status'] in ('queued','running') for j in data['jobs'].values()):
                raise ValueError('This work is already queued or running')
            identifier='job-'+uuid.uuid4().hex[:16]
            data['jobs'][identifier]={'id':identifier,'campaign':campaign,'kind':kind,'payload':payload or {},
                'status':'queued','created':now(),'stages':{},'stage':None,'error':None}
            c[('research' if kind=='research' else 'experiment')+'_state']='running'
            c['blocker']=None
            self.event(data,campaign,kind+' queued',job=identifier)
            return data['jobs'][identifier]

    def admit(self,campaign,kind,payload,calls,tokens,automatic=False):
        """Reservation, intent and visible queue entry commit atomically."""
        with self.transaction() as data:
            c=data['campaigns'][campaign];u=c['usage'];s=c['settings']
            if automatic and c['experiment_state']!='running':raise ValueError('Queue paused before admission')
            if any(j['campaign']==campaign and j['kind']==kind and j['status'] in ('queued','running') for j in data['jobs'].values()):
                raise ValueError('This work is already queued or running')
            if u['calls']+u['reserved_calls']+calls>s['max_calls']:
                raise ValueError('Insufficient authorized model stages including correction reserve')
            if s['token_budget'] is not None:
                if tokens is None or u['unknown_usage_stages']:raise ValueError('Unknown consumption prevents reliable token admission')
                if u['known_tokens']+u['reserved_tokens']+tokens*(1+s['reserve_fraction'])>s['token_budget']:raise ValueError('Insufficient token budget including reserve')
            identifier='job-'+uuid.uuid4().hex[:16]
            job={'id':identifier,'campaign':campaign,'kind':kind,'payload':payload,'status':'queued','created':now(),
                 'stages':{},'stage':None,'error':None,'reservation':{'calls':calls,'tokens':tokens or 0},
                 'execution_profiles':{key:c[key] for key in ('development','reviewer','research_profile')}}
            u['reserved_calls']+=calls;u['reserved_tokens']+=tokens or 0
            data['jobs'][identifier]=job;c[('research' if kind=='research' else 'experiment')+'_state']='running';c['blocker']=None
            self.event(data,campaign,kind+' admitted',job=identifier)
            return job

    def recover(self):
        with self.transaction() as data:
            for campaign in data['campaigns'].values():
                for area in ('research_state','experiment_state'):
                    if campaign[area]=='running':campaign[area]='paused'
            for job in data['jobs'].values():
                if job['status']=='running':
                    for stage in job['stages'].values():
                        if stage.get('status')=='running' and not stage.get('accounted'):
                            path=stage.get('receipt');receipt=read(path) if path and Path(path).exists() else {}
                            usage=receipt.get('usage')
                            if usage:
                                stage['tokens']=usage['input_tokens']+usage['output_tokens']
                                data['campaigns'][job['campaign']]['usage']['known_tokens']+=stage['tokens']
                            else:
                                data['campaigns'][job['campaign']]['usage']['unknown_usage_stages']+=1
                                stage['accounted_unknown']=True
                            stage['accounted']=True
                    job.update(status='interrupted',error='Controller restarted during work. Reconcile saved receipt before explicit resume; no automatic model replay.')
                    c=data['campaigns'][job['campaign']]
                    c[('research' if job['kind']=='research' else 'experiment')+'_state']='paused'
                    c['blocker']=job['error']

    def retry(self, identifier):
        with self.transaction() as data:
            job=data['jobs'][identifier]
            if job['status'] not in ('interrupted','blocked','failed','paused','stopped'):
                raise ValueError('Only interrupted/blocked work can resume')
            # A recorded runtime start without a final receipt needs a deliberate new attempt.
            for stage in job['stages'].values():
                if stage['status']=='running':
                    stage['status']='interrupted'
            job.update(status='queued',error=None)
            data['campaigns'][job['campaign']][('research' if job['kind']=='research' else 'experiment')+'_state']='running'
            self.event(data,job['campaign'],'Explicit resume authorized; completed receipts will be reused',job=identifier)

    def resume_admitted(self,identifier,calls,tokens):
        with self.transaction() as data:
            job=data['jobs'][identifier];c=data['campaigns'][job['campaign']];u=c['usage'];s=c['settings']
            if job['status'] not in ('interrupted','blocked','failed','paused','stopped'):
                raise ValueError('Only checkpointed work can resume')
            if any(j['id']!=identifier and j['campaign']==job['campaign'] and j['kind']==job['kind'] and j['status'] in ('queued','running') for j in data['jobs'].values()):
                raise ValueError('Another job already owns this campaign area')
            if not job.get('reservation',{}).get('calls'):
                if u['calls']+u['reserved_calls']+calls>s['max_calls']:raise ValueError('Insufficient remaining stage allowance')
                if s['token_budget'] is not None and (u['unknown_usage_stages'] or u['known_tokens']+u['reserved_tokens']+tokens*(1+s['reserve_fraction'])>s['token_budget']):
                    raise ValueError('Resume does not fit reliable token allowance/reserve')
                u['reserved_calls']+=calls;u['reserved_tokens']+=tokens;job['reservation']={'calls':calls,'tokens':tokens}
            for stage in job['stages'].values():
                if stage['status']=='running':stage['status']='interrupted'
            job.update(status='queued',error=None)
            c[('research' if job['kind']=='research' else 'experiment')+'_state']='running';c['blocker']=None
            self.event(data,job['campaign'],'Explicit checkpoint resume admitted',job=identifier)

    def export(self):
        """Local backup, not public publication; every byte has an integrity manifest."""
        import zipfile
        identifier='backup-'+uuid.uuid4().hex[:16]
        target=ROOT/'.lab'/'backups'/(identifier+'.zip');target.parent.mkdir(exist_ok=True)
        import os
        with self.transaction(False) as data:
            if any(j['status']=='running' for j in data['jobs'].values()):raise ValueError('Pause active work before taking a consistent evidence backup')
            listing={};paths={}
            sources={'campaign-state':self.root,'experiments':ROOT/'.lab/experiments','evaluations':ROOT/'.lab/evaluations'}
            for prefix,source in sources.items():
                if not source.exists():continue
                for base,dirs,files in os.walk(source,followlinks=False):
                    dirs[:]=[x for x in dirs if x not in ('__pycache__','.venv','node_modules','tmp')]
                    for name in dirs+files:
                        p=Path(base)/name
                        if p.is_symlink() or hasattr(p,'is_junction') and p.is_junction():raise ValueError('Backup refuses linked inputs')
                    for name in files:
                        if name.endswith(('.lock','.tmp')):continue
                        if name.casefold() in ('auth.json','credentials','credentials.json'):raise ValueError('Backup refuses credential-named files')
                        p=Path(base)/name;key=prefix+'/'+p.relative_to(source).as_posix()
                        paths[key]=p;listing[key]=hashlib.sha256(p.read_bytes()).hexdigest()
            with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
                for name in listing:archive.write(paths[name],name)
                archive.writestr('BACKUP_MANIFEST.json',__import__('json').dumps(listing,sort_keys=True))
        return {'id':identifier,'path':str(target),'files':len(listing),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}

    @staticmethod
    def verify_backup(path):
        import zipfile,json
        with zipfile.ZipFile(path) as archive:
            manifest=json.loads(archive.read('BACKUP_MANIFEST.json'))
            return (len(archive.namelist())==len(manifest)+1 and set(archive.namelist())==set(manifest)|{'BACKUP_MANIFEST.json'}
                    and all(not name.startswith('/') and '..' not in name.split('/') and hashlib.sha256(archive.read(name)).hexdigest()==value for name,value in manifest.items()))


def aggregate(tasks, expected):
    """All ordered required tasks participate; no omission or quality averaging."""
    missing=[t for t in expected if t not in tasks or not tasks[t].get('complete') or any(
        not isinstance(tasks[t].get(a),dict) or type(tasks[t][a].get('quality')) is not bool for a in ('stable','candidate'))]
    result={'complete':not missing,'missing':missing,'tasks':tasks,'quality':{},'tokens':{},'corrections':{},'task_outcomes':{},'outcome':'Incomplete'}
    if missing:return result
    for arm in ('stable','candidate'):
        result['quality'][arm]=all(tasks[t][arm].get('quality') is True for t in expected)
        counts=[tasks[t][arm].get('tokens') for t in expected]
        result['tokens'][arm]=sum(counts) if all(x is not None for x in counts) else None
        corrections=[tasks[t][arm].get('correction_attempts') for t in expected]
        result['corrections'][arm]=sum(corrections) if all(x is not None for x in corrections) else None
    for task in expected:
        a,b=tasks[task]['stable'],tasks[task]['candidate']
        outcome=('Candidate quality failure' if not b.get('quality') else 'Candidate quality improvement observed' if not a.get('quality') else
                 'Resource comparison limited' if a.get('tokens') is None or b.get('tokens') is None else
                 'Resource improvement observed' if b['tokens']<a['tokens'] else 'Resource regression observed' if b['tokens']>a['tokens'] else 'No token difference')
        result['task_outcomes'][task]={'outcome':outcome,'token_delta':b['tokens']-a['tokens'] if a.get('tokens') is not None and b.get('tokens') is not None else None,
            'correction_delta':b['correction_attempts']-a['correction_attempts'] if a.get('correction_attempts') is not None and b.get('correction_attempts') is not None else None}
    if not result['quality']['candidate']:result['outcome']='Candidate quality failure'
    elif not result['quality']['stable']:result['outcome']='Candidate quality improvement observed'
    elif all(v is not None for v in result['tokens'].values()):
        a,b=result['tokens']['stable'],result['tokens']['candidate']
        result['outcome']='Resource improvement observed' if b<a else 'Resource regression observed' if b>a else 'No demonstrated advantage'
    else:result['outcome']='Quality equivalent; resource comparison limited'
    if all(x is not None for x in result['corrections'].values()):
        result['autonomy_tradeoff']=result['corrections']['candidate']>result['corrections']['stable']
    result['limitations']='One suite trial is provisional. Unknown usage is not zero. Correction counts cover registered independent revisions, not inferred per-edit self-check cycles. Time is diagnostic, not a substitute for tokens.'
    return result
