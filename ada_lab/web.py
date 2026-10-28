"""Loopback-only local interface. Mutations require an origin-bound CSRF nonce."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, parse_qs
import webbrowser
import platform

from .campaigns import Campaigns, profile
from .engine import Engine
from .files import ROOT, digest, identity, inventory, read, safe_id, tree_digest, write
from .lab import Lab
from . import evaluator
from .stateio import write


class Application:
    def __init__(self,store=None,engine=None):
        self.store=store or Campaigns();self.engine=engine or Engine(self.store)
        self.csrf=secrets.token_urlsafe(32);self.maintenance={'status':'idle'};self.blobs={}

    def initialize(self):
        from .bootstrap import runtime_path
        try:runtime_path()
        except RuntimeError as error:self.runtime_error=str(error)
        lab=self.engine.lab
        if not (lab.state/'config.json').exists():lab.initialize()
        self.store.task('repair-queue-v1',ROOT/'benchmarks/repair-queue-v1')
        self.store.suite('Repair Queue v1',['repair-queue-v1'])

    def discover(self):
        try:self.engine.runtime.discover();self.runtime_error=None
        except Exception as error:self.runtime_error=str(error)

    def file_link(self,path,kind):
        p=Path(path).resolve()
        if not p.is_relative_to(ROOT/'.lab') or not p.is_file():return None
        key=hashlib.sha256((str(p)+kind).encode()).hexdigest()[:24]
        self.blobs[key]={'path':p,'kind':kind}
        return '/api/file/'+key

    def state(self,campaign=None,history_page=0):
        data=self.store.snapshot();selected=data['campaigns'].get(campaign)
        trials=[t for t in data['trials'].values() if not campaign or t['campaign']==campaign]
        history_page=max(0,int(history_page));start=history_page*20
        trial_count=len(trials);trials=trials[::-1][start:start+20]
        for t in trials:
            t.pop('idea_snapshot',None)
            if t.get('candidate'):t['candidate'].pop('review',None);t['candidate'].pop('review_value',None)
            for task in t['tasks'].values():
                for arm in ('stable','candidate'):
                    if arm not in task:continue
                    item=task[arm]
                    item['screenshots']=[self.file_link(p,'image') for p in item.get('images',[])]
                    artifact=Path(item['artifact'])
                    item['artifact_integrity']=tree_digest(artifact)==item['artifact_digest'] if artifact.exists() else False
                    item['source_files']=[{'name':name,'url':self.file_link(artifact/name,'source')} for name in ('app.py','index.html','app.js','style.css','server.py','README.md','TASK.md','PLAN.md') if (artifact/name).is_file()]
        all_jobs=[j for j in data['jobs'].values() if not campaign or j['campaign']==campaign][::-1]
        jobs=all_jobs[start:start+20]
        legacy=[{'id':r['id'],'status':r['status'],'hypothesis':r['hypothesis'],'outcome':r.get('comparison',{}).get('outcome'),
                 'report':self.file_link(self.engine.lab.folder(r['id'])/'report.json','source')} for r in self.engine.lab.history()][::-1]
        legacy_count=len(legacy);legacy=legacy[start:start+20]
        return {'campaigns':[{k:v for k,v in c.items() if k not in ('summary',)} for c in data['campaigns'].values()],
            'selected':selected,'tasks':list(data['tasks'].values()),'suites':list(data['suites'].values()),
            'ideas':sorted([v for v in data['ideas'].values() if not campaign or v['id'] in (selected or {}).get('queue',[])],key=lambda x:x.get('manual_priority',x.get('priority') or 0),reverse=True),
            'global_ideas':[{'id':v['id'],'title':v['title'],'tags':v['tags'],'priority':v['priority']} for v in data['ideas'].values()],
            'reusable_candidates':[{'trial':t['id'],'suite':t['suite'],'idea':t['idea'],'digest':t['candidate']['digest']} for t in data['trials'].values()
                if t.get('candidate') and t['candidate'].get('review_value',{}).get('status')=='PASS' and not t['candidate']['review_value'].get('findings')],
            'jobs':jobs,'trials':trials,'legacy':legacy,'events':[e for e in data['events'] if not campaign or e['campaign']==campaign][-30:][::-1],
            'runtime':self.engine.runtime.cached or {'status':'unavailable','error':getattr(self,'runtime_error','Reading supported runtime metadata…')},
            'history_page':history_page,'history_more':max(trial_count,len(all_jobs),legacy_count)>start+20,
            'runtime_refresh_error':getattr(self,'runtime_error',None),
            'maintenance':self.maintenance,'stable':identity(self.engine.lab.config()['ada']),
            'limits_note':'Tokens are actual CLI event measurements. Subscription quota uses different units. Stage reservations and timeouts are admission controls, not a hard token-spend guarantee.'}

    def action(self,name,body):
        if name=='shutdown':
            for c in self.store.snapshot()['campaigns'].values():
                for area in ('research','experiment'):self.store.control(c['id'],area,'pause')
            threading.Thread(target=self.httpd.shutdown,daemon=True).start()
            return {'status':'closing','note':'Work checkpointed; reopen Run.bat to continue explicitly'}
        if name=='runtime':self.discover();return self.engine.runtime.cached or {'error':self.runtime_error}
        if name=='task':
            path=(ROOT/'benchmarks'/body['path']).resolve()
            if not path.is_relative_to(ROOT/'benchmarks'):raise ValueError('Task paths must stay under benchmarks/')
            return self.store.task(body['id'],path,body.get('adapter','unavailable'),body.get('capabilities',[]))
        if name=='suite':return self.store.suite(body['name'],body['tasks'])
        if name=='campaign':
            info=self.engine.runtime.cached or self.engine.runtime.discover()
            execution=getattr(self.engine,'loaded_digest','fixture-controller')
            environment={'platform':platform.platform(),'python':platform.python_version()}
            dev=profile(body['model'],body['effort'],info['runtime'],execution_policy=execution,environment=environment)
            reviewer=profile(body.get('reviewer_model',body['model']),body.get('reviewer_effort',body['effort']),info['runtime'],execution_policy=execution,environment=environment)
            researcher=profile(body.get('research_model',body['model']),body.get('research_effort',body['effort']),info['runtime'],web_search='live',execution_policy=execution,environment=environment)
            for p in (dev,reviewer,researcher):self.engine.runtime.check_profile(p)
            return self.store.campaign(body['suite'],dev,reviewer,researcher,body.get('name'))
        campaign=body.get('campaign')
        if name=='settings':return self.store.settings(campaign,body['values'])
        if name=='research':return self.engine.enqueue(campaign,'research')
        if name=='trial':return self.engine.enqueue(campaign,'trial',{'idea':body['idea']})
        if name=='reuse':
            prior=self.store.snapshot()['trials'][body['trial']]
            return self.engine.enqueue(campaign,'trial',{'idea':prior['idea'],'reuse_accepted':True,'source_trial':prior['id']})
        if name=='confirm':
            prior=self.store.snapshot()['trials'][body['trial']]
            return self.engine.enqueue(campaign,'trial',{'idea':prior['idea'],'confirmation':True,'source_trial':prior['id']})
        if name=='queue':return self.engine.ranked_queue(campaign)
        if name=='control':
            area=body['area'];action=body['action']
            if area not in ('research','experiment') or action not in ('pause','resume','stop'):raise ValueError('Invalid control')
            if action=='resume':
                jobs=[j for j in self.store.snapshot()['jobs'].values() if j['campaign']==campaign and j['kind']==('research' if area=='research' else 'trial')]
                pending=next((j for j in reversed(jobs) if j['status'] in ('paused','interrupted','blocked','failed','stopped')),None)
                if pending:return self.engine.resume(pending['id'])
            self.store.control(campaign,area,action)
            if area=='experiment' and action=='resume':return self.engine.next_queued(campaign)
            return {'requested':action,'scope':area,'note':'Bounded verification finishes at a safe checkpoint'}
        if name=='resume':return self.engine.resume(body['job'])
        if name=='idea':
            with self.store.transaction() as data:
                idea=data['ideas'][body['idea']]
                if body.get('attach'):
                    if idea['id'] not in data['campaigns'][campaign]['queue']:data['campaigns'][campaign]['queue'].append(idea['id'])
                if 'excluded' in body:idea['excluded']=bool(body['excluded'])
                if 'priority' in body:
                    if type(body['priority']) not in (int,float) or not 0<=body['priority']<=10:raise ValueError('Manual priority must be 0–10')
                    idea['manual_priority']=body['priority']
                return idea
        if name=='backup':
            backup=self.store.export();backup['verified']=self.store.verify_backup(backup['path']);backup['download']=self.file_link(backup['path'],'backup');return backup
        if name=='artifact':
            import zipfile
            trial=self.store.snapshot()['trials'][body['trial']]
            if trial['campaign']!=campaign:raise ValueError('Select the artifact campaign first')
            item=trial['tasks'][body['task']][body['arm']];source=Path(item['artifact'])
            if tree_digest(source)!=item['artifact_digest']:raise ValueError('Frozen application source changed; download refused')
            target=self.store.root/'downloads'/(item['artifact_digest']+'.zip');target.parent.mkdir(exist_ok=True)
            if not target.exists():
                listing=inventory(source)
                with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
                    for name in listing:archive.write(source/name,name)
                    archive.writestr('SOURCE_MANIFEST.json',json.dumps(listing,sort_keys=True))
            return {'download':self.file_link(target,'source'),'source_digest':item['artifact_digest']}
        if name=='calibrate':
            if self.maintenance['status']=='running' or any(j['status']=='running' for j in self.store.snapshot()['jobs'].values()):raise ValueError('Pause current work before calibration')
            self.maintenance={'status':'running','stage':'Native calibration (no model usage)'}
            def calibrate():
                try:self.maintenance=evaluator.calibrate(self.engine.lab.state,self.engine.lab.config()['ada'])
                except Exception as error:self.maintenance={'status':'BLOCKED','error':str(error)}
            threading.Thread(target=calibrate,daemon=True).start();return self.maintenance
        raise ValueError('Unknown application action')


def server(app,port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def valid_origin(self,mutation=False):
            expected='http://127.0.0.1:'+str(self.server.server_port)
            if self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):return False
            origin=self.headers.get('Origin')
            return (origin==expected and secrets.compare_digest(self.headers.get('X-CSRF-Token',''),app.csrf)) if mutation else origin in (None,expected)

        def respond(self,status,body,content_type='application/json; charset=utf-8',download=None):
            if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cross-Origin-Resource-Policy','same-origin');self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:self.send_header('Content-Disposition','attachment; filename="'+download.replace('"','')+'"')
            self.end_headers();self.wfile.write(body)

        def do_GET(self):
            if not self.valid_origin():return self.respond(403,{'error':'Local origin required'})
            url=urlsplit(self.path)
            try:
                if url.path=='/api/bootstrap':return self.respond(200,{'csrf':app.csrf})
                if url.path=='/api/state':
                    query=parse_qs(url.query)
                    return self.respond(200,app.state(query.get('campaign',[None])[0],query.get('history_page',[0])[0]))
                if url.path=='/api/health':return self.respond(200,{'application':'ada-lab','status':'ready'})
                if url.path.startswith('/api/file/'):
                    blob=app.blobs.get(url.path.rsplit('/',1)[-1])
                    if not blob:return self.respond(404,{'error':'Evidence not in current view'})
                    p=blob['path']
                    return self.respond(200,p.read_bytes(),'image/png' if blob['kind']=='image' else 'application/octet-stream',None if blob['kind']=='image' else p.name)
                files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
                if url.path not in files:return self.respond(404,{'error':'Not found'})
                p=ROOT/'ada_lab'/'ui'/files[url.path]
                return self.respond(200,p.read_bytes(),mimetypes.guess_type(p.name)[0]+'; charset=utf-8')
            except Exception as error:self.respond(500,{'error':str(error)})

        def do_POST(self):
            if not self.valid_origin(True):
                # Drain a bounded ordinary request before closing, avoiding Windows TCP resets.
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if 0<size<=100000:self.connection.settimeout(1);self.rfile.read(size)
                except (OSError,ValueError):pass
                return self.respond(403,{'error':'Origin and CSRF token required'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100000:raise ValueError('Invalid request size')
                def reject_constant(value):raise ValueError('Nonstandard JSON constant')
                body=json.loads(self.rfile.read(size).decode('utf-8'),parse_constant=reject_constant)
                if not isinstance(body,dict):raise ValueError('Expected a JSON object')
                pending=[(body,0)]
                while pending:
                    value,depth=pending.pop()
                    if depth>20:raise ValueError('Request nesting is too deep')
                    if isinstance(value,str):
                        try:value.encode('utf-8')
                        except UnicodeEncodeError:raise ValueError('Request must contain valid Unicode text')
                    elif isinstance(value,dict):pending.extend((x,depth+1) for pair in value.items() for x in pair)
                    elif isinstance(value,list):pending.extend((x,depth+1) for x in value)
                name=urlsplit(self.path).path.removeprefix('/api/action/')
                result=app.action(name,body)
                self.respond(200,{'ok':True,'result':result})
            except (ValueError,KeyError,TypeError) as error:self.respond(400,{'error':str(error)})
            except Exception as error:self.respond(500,{'error':str(error)})
    httpd=ThreadingHTTPServer(('127.0.0.1',port),Handler);httpd.daemon_threads=True;app.httpd=httpd
    return httpd


def serve(port=8765,open_browser=False):
    app=Application();app.initialize()
    try:httpd=server(app,port)
    except OSError:
        from urllib.request import urlopen
        url='http://127.0.0.1:'+str(port)
        with urlopen(url+'/api/health',timeout=2) as response:
            if json.load(response).get('application')!='ada-lab':raise
        if open_browser:webbrowser.open(url)
        return
    app.engine.start()
    threading.Thread(target=app.discover,daemon=True).start()
    address='http://127.0.0.1:'+str(httpd.server_port)
    write(ROOT/'.lab/server.json',{'url':address,'pid':__import__('os').getpid()})
    if open_browser:webbrowser.open(address)
    try:httpd.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:pass
    finally:app.engine.stop();httpd.server_close()
