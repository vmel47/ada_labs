"""Real native browser checks of trusted delayed-DOM fixtures, with no AI calls."""
import base64
from pathlib import Path
import tempfile
import time
import unittest

from ada_lab.files import ROOT, read, write
from ada_lab.native import Worker, NativeApplication, browser_executable, playwright_driver
from ada_lab import sandbox


PAGE=b'''<!doctype html><html><body>
<div id="gone">Initially present</div><div id="text">Pending</div>
<div id="persistent">Must remain present</div>
<div id="visible" style="display:none">Visible after asynchronous update</div>
<script>
setTimeout(()=>{const n=document.createElement('div');n.id='one';n.textContent='Created';document.body.append(n)},200);
setTimeout(()=>document.querySelector('#visible').style.display='block',350);
setTimeout(()=>document.querySelector('#gone').remove(),600);
setTimeout(()=>document.querySelector('#text').textContent='Delayed ready text',1000);
</script></body></html>'''

RACE_PAGE=b'''<!doctype html><html><body>
<label>Query<input id="query"></label><div id="state">idle</div><div id="result"></div>
<form id="keyboard-form"><label>Name<input id="keyboard"></label><button id="save">Save</button></form><div id="keyboard-result"></div>
<script>
document.querySelector('#keyboard-form').addEventListener('submit',event=>{event.preventDefault();document.querySelector('#keyboard-result').textContent=document.querySelector('#keyboard').value});
document.querySelector('#query').addEventListener('input',async event=>{
 const q=event.target.value;document.querySelector('#state').textContent='loading '+q;
 const response=await fetch('/api/repairs?q='+encodeURIComponent(q));const value=await response.json();
 document.querySelector('#result').textContent=value.q;
});
</script></body></html>'''

ABORT_PAGE=RACE_PAGE.replace(
    b"document.querySelector('#query').addEventListener('input',async event=>{",
    b"let pendingController;document.querySelector('#query').addEventListener('input',async event=>{if(pendingController)pendingController.abort();pendingController=new AbortController();"
).replace(
    b"const response=await fetch('/api/repairs?q='+encodeURIComponent(q));const value=await response.json();",
    b"try{const response=await fetch('/api/repairs?q='+encodeURIComponent(q),{signal:pendingController.signal});const value=await response.json();"
).replace(
    b"document.querySelector('#result').textContent=value.q;",
    b"document.querySelector('#result').textContent=value.q;}catch(error){if(error.name!=='AbortError')throw error}"
)


def cleanup_native_fixture(temporary):
    """Retry only transient Windows sharing/access denials, then fail honestly."""
    deadline=time.monotonic()+5
    while True:
        try:
            temporary.cleanup()
            return
        except OSError as error:
            transient=isinstance(error,PermissionError) or getattr(error,'winerror',None)==32
            if not transient or time.monotonic()>=deadline:raise
            time.sleep(.05)


class NativeJourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT/'.lab/config.json').exists():
            raise unittest.SkipTest('Initialize the local native runtime before these integration fixtures; application QA remains BLOCKED until available')
        config=read(ROOT/'.lab/config.json')
        sandbox.require_safe(ROOT/'.lab',config['ada'])
        cls.browser=browser_executable();cls.node,cls.library=playwright_driver()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='native-journey-fixture-',dir=ROOT/'.lab')
        self.addCleanup(cleanup_native_fixture,self.temp);self.root=Path(self.temp.name)

    def journey(self,steps,native_backend=False,backend_page=RACE_PAGE):
        workspace=self.root/'browser'
        write(workspace/'browser.json',{'library':str(self.library),'executable':str(self.browser),'steps':steps})
        app=None;self.actual_backend_responses=[]
        if native_backend:
            backend=self.root/'backend';backend.mkdir();evidence=self.root/'backend-evidence';evidence.mkdir()
            source='import json\nfrom urllib.parse import urlsplit,parse_qs\nPAGE='+repr(backend_page)+'\n'
            source+='def handle_request(method,target,body,headers,database):\n'
            source+=' p=urlsplit(target)\n if p.path=="/":return 200,{"Content-Type":"text/html; charset=utf-8"},PAGE\n'
            source+=' if p.path=="/health":return 200,{"Content-Type":"application/json"},b"{}"\n'
            source+=' if p.path=="/api/repairs":return 200,{"Content-Type":"application/json"},json.dumps({"q":parse_qs(p.query).get("q",[""])[0]}).encode("utf-8")\n'
            source+=' return 404,{"Content-Type":"application/json"},b"{}"\n'
            (backend/'app.py').write_text(source,encoding='utf-8')
            app=NativeApplication(backend,evidence);app.start();self.addCleanup(app.stop)
        worker=Worker(workspace,'journeys.js',self.root/'browser.log',[self.browser.parent,self.node.parent],executable=self.node)
        started=time.monotonic()
        try:
            deadline=started+25
            while time.monotonic()<deadline:
                item=worker.receive(timeout=min(10,max(.1,deadline-time.monotonic())))
                if item['kind']=='result':return item['result']
                self.assertEqual(item['kind'],'request')
                self.assertEqual(item['request']['method'],'GET')
                if app:
                    request=item['request']
                    status,headers,body=app.request(request['method'],request['path'],base64.b64decode(request['body']),request['headers'])
                    self.actual_backend_responses.append({'path':request['path'],'status':status,'body':body})
                else:
                    self.assertEqual(item['request']['path'],'/')
                    status,headers,body=200,{'Content-Type':'text/html; charset=utf-8'},PAGE
                worker.send({'status':status,'headers':headers,'body':base64.b64encode(body).decode('ascii')})
            self.fail('Trusted journey fixture exceeded its bounded native deadline')
        finally:
            worker.stop()
            if worker.process.stdout:worker.process.stdout.close()
            if worker.process.stderr:worker.process.stderr.close()
            if app:
                process=app.worker.process if app.worker else None
                app.stop()
                if process and process.stdout:process.stdout.close()
                if process and process.stderr:process.stderr.close()

    def test_assertions_wait_for_delayed_count_one_count_zero_and_text(self):
        steps=[{'op':'assert_count','selector':'#one','count':1},
               {'op':'assert_count','selector':'#gone','count':0},
               {'op':'assert_text','selector':'#text','value':'Delayed ready text'}]
        result=self.journey(steps)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual([item['op'] for item in result['observations']],['assert_count','assert_count','assert_text'])
        self.assertFalse(result['overflow'])

    def test_count_timeout_remains_failure_and_reports_exact_step(self):
        step={'op':'assert_count','selector':'#persistent','count':0}
        result=self.journey([step])
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['observations'][-1]['step'],step)
        self.assertIn('Timeout',result['observations'][-1]['detail'])
        self.assertIn('6000',result['observations'][-1]['detail'])

    def test_text_timeout_remains_failure_and_reports_exact_step(self):
        step={'op':'assert_text','selector':'#text','value':'This text never appears'}
        result=self.journey([step])
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['observations'][-1]['step'],step)
        self.assertIn('Timeout',result['observations'][-1]['detail'])
        self.assertIn('6000',result['observations'][-1]['detail'])

    def test_text_does_not_accept_one_match_among_multiple_selected_nodes(self):
        step={'op':'assert_text','selector':'#one, #text','value':'Delayed ready text'}
        result=self.journey([step])
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['observations'][-1]['step'],step)
        self.assertIn('Timeout',result['observations'][-1]['detail'])

    def test_playwright_visible_and_has_text_selector_contract(self):
        steps=[{'op':'assert_count','selector':'#visible:visible','count':1},
               {'op':'wait_text','selector':'#text:has-text("Delayed ready text")','value':'Delayed ready text'},
               {'op':'assert_text','selector':'#text:has-text("Delayed ready text")','value':'Delayed ready text'},
               {'op':'assert_count','selector':'#text:has-text("Delayed ready text")','count':1}]
        result=self.journey(steps)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(len(result['observations']),len(steps))

    def test_keyboard_press_submits_real_form(self):
        steps=[{'op':'fill','selector':'#keyboard','value':'Keyboard fixture'},
               {'op':'press','selector':'#save','value':'Enter'},
               {'op':'assert_text','selector':'#keyboard-result','value':'Keyboard fixture'}]
        self.assertEqual(self.journey(steps,native_backend=True)['status'],'PASS')

    def test_delayed_actual_backend_response_arrives_after_newer_response(self):
        steps=[{'op':'delay_next','target':'/api/repairs','ms':1000},
               {'op':'fill','selector':'#query','value':'old'},
               {'op':'wait_text','selector':'#state','value':'loading old'},
               {'op':'fill','selector':'#query','value':'new'},
               {'op':'assert_text','selector':'#result','value':'new'},
               {'op':'settle'},
               {'op':'assert_text','selector':'#result','value':'old'}]
        result=self.journey(steps,native_backend=True)
        self.assertEqual(result['status'],'PASS',result)
        delayed=result['delayed_responses'];self.assertEqual(len(delayed),1)
        self.assertEqual(delayed[0]['path'],'/api/repairs?q=old')
        self.assertTrue(delayed[0]['consumed']);self.assertTrue(delayed[0]['completed'])
        self.assertEqual(delayed[0]['actual_backend_status'],200)
        requests=[item for item in self.actual_backend_responses if item['path'].startswith('/api/repairs')]
        self.assertEqual([item['path'] for item in requests],['/api/repairs?q=old','/api/repairs?q=new'])
        self.assertEqual([item['body'] for item in requests],[b'{"q": "old"}',b'{"q": "new"}'])

    def test_unused_delay_prevents_premature_zero_inflight_settle(self):
        step={'op':'settle'}
        result=self.journey([{'op':'delay_next','target':'/api/repairs','ms':100},step])
        self.assertEqual(result['status'],'FAIL')
        failures=[item for item in result['observations'] if item['status']=='FAIL']
        self.assertEqual(failures[0]['step'],step)
        self.assertIn('6000',failures[0]['detail'])
        self.assertTrue(any('not consumed and completed' in item.get('detail','') for item in failures))

    def test_aborted_old_response_is_observed_without_bridge_failure(self):
        steps=[{'op':'delay_next','target':'/api/repairs','ms':1000},
               {'op':'fill','selector':'#query','value':'old'},
               {'op':'wait_text','selector':'#state','value':'loading old'},
               {'op':'fill','selector':'#query','value':'new'},
               {'op':'assert_text','selector':'#result','value':'new'},
               {'op':'settle'},{'op':'assert_text','selector':'#result','value':'new'}]
        result=self.journey(steps,native_backend=True,backend_page=ABORT_PAGE)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(len(result['delayed_responses']),1)
        delayed=result['delayed_responses'][0]
        self.assertEqual(delayed['path'],'/api/repairs?q=old')
        self.assertTrue(delayed['completed']);self.assertEqual(delayed['actual_backend_status'],200)
        self.assertTrue(delayed.get('aborted_by_application'),delayed)


if __name__=='__main__':unittest.main()
