"""Runtime integrity fixtures never call a model provider."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ada_lab.campaigns import profile
from ada_lab.files import ROOT, read, write
from ada_lab.runtime import CodexRuntime, environment
from ada_lab.process_job import ProcessJob


class ProcessFixture:
    def __init__(self,events):
        self.stdin=io.BytesIO();self.stdout=io.BytesIO(('\n'.join(json.dumps(e) for e in events)+'\n').encode());self.returncode=0
    def wait(self,timeout=None):return self.returncode


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='runtime-fixture-',dir=ROOT/'.lab');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.runtime=CodexRuntime(self.base/'runtime')
        self.profile=profile('fixture-model','medium','fixture-runtime')

    def test_secret_environment_and_supervisor_configuration_not_inherited(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fixture-secret','AWS_SECRET_ACCESS_KEY':'fixture-secret',
                'CODEX_THREAD_ID':'supervisor-session','CODEX_MODEL':'supervisor-model','OPENAI_BASE_URL':'https://wrong.invalid'}):
            env=environment()
        for key in ('OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','CODEX_THREAD_ID','CODEX_MODEL','OPENAI_BASE_URL'):self.assertNotIn(key,env)
        self.assertIn('PATH',env)

    def test_explicit_flags_and_no_unsafe_fallback(self):
        with patch('ada_lab.sandbox.executable',return_value='codex'):
            args=self.runtime.args(self.profile,self.base)
        self.assertIn('--ignore-user-config',args);self.assertIn('--ignore-rules',args)
        self.assertEqual(args[args.index('--model')+1],'fixture-model')
        self.assertIn('model_reasoning_effort="medium"',args)
        self.assertIn('forced_login_method="chatgpt"',args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',args)

    def test_supported_quota_denial_is_not_bypassed_by_purchased_credits(self):
        self.runtime.cached={'auth':'chatgpt','models':[{'id':'fixture-model','efforts':['medium']}],
            'quota':{'ordinaryUsageAllowed':True,'rateLimits':{'primary':{'usedPercent':100},'credits':{'hasCredits':True}}}}
        with patch('ada_lab.sandbox.version',return_value='fixture-runtime'):
            with self.assertRaisesRegex(RuntimeError,'exhausted'):self.runtime.check_profile(self.profile)

    def run_fixture(self,observed,failed=False):
        work=self.base/'worker';work.mkdir(exist_ok=True)
        events=[{'type':'thread.started','thread_id':'fixture-session'},
                {'type':'item.completed','item':{'type':'agent_message','text':'fixture output'}},
                {'type':'turn.failed','error':{'message':'fixture failure'}} if failed else
                {'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':10,'output_tokens':5}}]
        proc=ProcessFixture(events)
        with (patch.object(self.runtime,'check_profile'),patch.object(self.runtime,'observed',return_value=observed),
                patch('ada_lab.runtime.subprocess.Popen',return_value=proc),patch('ada_lab.runtime.ProcessJob',return_value=SimpleNamespace(close=lambda:None)),
                patch('ada_lab.sandbox.run',return_value={'returncode':0}),patch('ada_lab.sandbox.executable',return_value='codex')):
            return self.runtime.run(self.profile,work,'fixture only',self.base/'output')

    def test_known_model_or_effort_mismatch_detected(self):
        for key,value in [('model','rerouted-fixture'),('effort','high'),('provider','wrong-provider')]:
            with self.subTest(key=key):
                observed={'model':'fixture-model','effort':'medium','provider':'openai','permissions_verified':True,key:value}
                result=self.run_fixture(observed)
                self.assertEqual(result['status'],'profile_mismatch');self.assertFalse(result['configuration_verified'])

    def test_unverified_permissions_are_blocked_even_when_model_claims_success(self):
        result=self.run_fixture({'model':'fixture-model','effort':'medium','provider':'openai','permissions_verified':False})
        self.assertEqual(result['status'],'permission_profile_unverified')

    def test_actual_event_usage_and_failed_unknown_are_distinct(self):
        observed={'model':'fixture-model','effort':'medium','provider':'openai','permissions_verified':True}
        result=self.run_fixture(observed)
        self.assertEqual(result['usage'],{'input_tokens':20,'cached_input_tokens':10,'output_tokens':5})
        self.assertEqual(result['session_id'],'fixture-session')
        result=self.run_fixture(observed,True)
        self.assertIsNone(result['usage']);self.assertTrue(result['unknown_partial_usage']);self.assertEqual(result['status'],'failed')

    @unittest.skipUnless(os.name=='nt','Windows lifetime binding')
    def test_windows_job_closes_actual_live_trusted_fixture(self):
        marker=self.base/'child-should-not-complete.txt'
        child="import time,sys;from pathlib import Path;time.sleep(10);Path(sys.argv[1]).write_text('completed',encoding='utf-8')"
        parent="import subprocess,sys,time;sys.stdin.readline();p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);print(p.pid,flush=True);time.sleep(30)"
        proc=subprocess.Popen([sys.executable,'-c',parent,child,str(marker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,encoding='utf-8')
        try:
            job=ProcessJob(proc);self.assertIsNone(proc.poll())
            proc.stdin.write('start\n');proc.stdin.flush();pid=int(proc.stdout.readline())
            from ada_lab.sandbox import alive
            self.assertTrue(alive(pid));job.close();proc.wait(timeout=5)
            deadline=time.monotonic()+2
            while alive(pid) and time.monotonic()<deadline:time.sleep(.02)
            self.assertFalse(alive(pid));self.assertFalse(marker.exists())
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            proc.stdin.close();proc.stdout.close()


if __name__=='__main__':unittest.main()
