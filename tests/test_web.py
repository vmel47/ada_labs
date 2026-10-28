"""Real local HTTP API tests with a non-inference fixture executor."""
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ada_lab.campaigns import Campaigns
from ada_lab.files import ROOT, write
from ada_lab.web import Application, server


class FixtureEngine:
    def __init__(self,store):
        self.store=store;self.calls=[]
        self.runtime=SimpleNamespace(cached={'status':'available','auth':'chatgpt','runtime':'fixture-runtime',
            'models':[{'id':'fixture-model','efforts':['medium','high']}],'quota':{}},check_profile=lambda p:None)
        self.lab=SimpleNamespace(history=lambda:[],config=lambda:{'ada':str(ROOT.parent/'Ada')})
    def enqueue(self,campaign,kind,payload=None):
        self.calls.append((campaign,kind,payload));return self.store.admit(campaign,kind,payload or {},2,100)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='web-fixture-',dir=ROOT/'.lab');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.store=Campaigns(self.base/'state')
        task=self.base/'task';(task/'starter').mkdir(parents=True);(task/'task.md').write_text('Controlled fixture',encoding='utf-8');write(task/'benchmark.json',{'id':'fixture'})
        self.store.task('fixture',task,'unavailable');self.suite=self.store.suite('Fixture suite',['fixture'])
        self.engine=FixtureEngine(self.store);self.app=Application(self.store,self.engine);self.http=server(self.app,0)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.identity=patch('ada_lab.web.identity',return_value={'commit':'fixture-commit','status':'','source_digest':'fixture'})
        self.identity.start();self.addCleanup(self.identity.stop)
        self.csrf=self.request('GET','/api/bootstrap')[1]['csrf']

    def close(self):self.http.shutdown();self.http.server_close();self.thread.join(timeout=3)

    def request(self,method,path,body=None,headers=None,raw=None):
        connection=HTTPConnection('127.0.0.1',self.http.server_port,timeout=4)
        h={'Content-Type':'application/json','Origin':'http://127.0.0.1:'+str(self.http.server_port),'X-CSRF-Token':getattr(self,'csrf','')}
        h.update(headers or {})
        try:
            connection.request(method,path,body=raw if raw is not None else json.dumps(body).encode('utf-8') if body is not None else None,headers=h)
            response=connection.getresponse();data=response.read();head=dict(response.getheaders())
            return response.status,json.loads(data) if head.get('Content-Type','').startswith('application/json') else data,head
        finally:connection.close()

    def campaign(self,effort='medium',name='Fixture campaign'):
        response=self.request('POST','/api/action/campaign',{'suite':self.suite['revision'],'model':'fixture-model','effort':effort,'name':name})
        self.assertEqual(response[0],200);return response[1]['result']['id']

    def test_real_ui_and_security_headers(self):
        status,body,headers=self.request('GET','/')
        self.assertEqual(status,200);self.assertIn(b'ADA Labs',body);self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')

    def test_persistent_history_remains_accessible_after_twenty_jobs(self):
        campaign=self.campaign()
        with self.store.transaction() as data:
            for i in range(27):
                data['jobs'][str(i)]={'id':str(i),'campaign':campaign,'status':'completed','kind':'research','stages':{}}
        first=self.request('GET','/api/state?campaign='+campaign)[1]
        older=self.request('GET','/api/state?campaign='+campaign+'&history_page=1')[1]
        self.assertTrue(first['history_more']);self.assertFalse(older['history_more'])
        self.assertEqual(len(first['jobs']),20);self.assertEqual(len(older['jobs']),7)
        self.assertEqual({j['id'] for j in first['jobs']+older['jobs']},{str(i) for i in range(27)})

    def test_global_idea_selector_retains_older_reusable_metadata(self):
        with self.store.transaction() as data:
            for i in range(55):
                data['ideas'][str(i)]={'id':str(i),'title':'Retained '+str(i),'tags':[],'priority':None}
        result=self.request('GET','/api/state')[1]
        self.assertEqual(len(result['global_ideas']),55)
        self.assertEqual(result['global_ideas'][0]['id'],'0')

    def test_failed_metadata_refresh_reports_stale_cache(self):
        self.engine.runtime.discover=lambda:(_ for _ in ()).throw(RuntimeError('Metadata offline'))
        self.app.discover()
        result=self.request('GET','/api/state')[1]
        self.assertEqual(result['runtime_refresh_error'],'Metadata offline')
        self.assertEqual(result['runtime']['status'],'available')

    def test_host_origin_and_csrf_required_for_mutation(self):
        payload={'name':'x','tasks':['fixture']}
        for headers in ({'Host':'evil.invalid'},{'Origin':'http://evil.invalid'},{'X-CSRF-Token':''}):
            self.assertEqual(self.request('POST','/api/action/suite',payload,headers)[0],403)
        self.assertEqual(len(self.store.snapshot()['suites']),1)

    def test_campaign_identity_and_explicit_effort_through_http(self):
        first=self.campaign();self.assertEqual(first,self.campaign(name='Different display name'))
        self.assertNotEqual(first,self.campaign('high'))
        self.assertEqual(self.engine.calls,[])

    def test_research_button_route_queues_real_persistent_intent(self):
        campaign=self.campaign();status,data,_=self.request('POST','/api/action/research',{'campaign':campaign})
        self.assertEqual(status,200);self.assertEqual(self.engine.calls[0][1],'research')
        job=data['result']['id'];self.assertEqual(Campaigns(self.store.root).snapshot()['jobs'][job]['status'],'queued')
        self.request('POST','/api/action/control',{'campaign':campaign,'area':'research','action':'pause'})
        self.assertEqual(self.store.snapshot()['campaigns'][campaign]['research_state'],'paused')

    def test_invalid_json_unicode_and_path_traversal_rejected(self):
        before=self.store.snapshot()
        for raw in (b'{',b'{"extra":NaN}',b'{"name":"\\ud800"}',b'[]',b'\xff'):
            self.assertEqual(self.request('POST','/api/action/suite',raw=raw)[0],400)
        self.assertEqual(self.request('POST','/api/action/task',{'id':'x','path':'../../','adapter':'x'})[0],400)
        self.assertEqual(before,self.store.snapshot())

    def test_generated_html_is_downloaded_not_privileged_ui(self):
        path=self.base/'generated.html';path.write_text('<script>fetch("/api/state")</script>',encoding='utf-8')
        url=self.app.file_link(path,'source');status,body,headers=self.request('GET',url)
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(self.request('GET','/api/file/../../config.json')[0],404)


if __name__=='__main__':unittest.main()
