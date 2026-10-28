"""Explicit subscription-only Codex CLI execution; no credentials are copied."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
import shutil
from datetime import datetime, timezone, timedelta

from . import sandbox
from .files import ROOT, now, read, tree_digest, write
from .process_job import ProcessJob
from .stateio import write


def environment():
    # Auth remains in the installed CLI's supported store, outside worker access.
    allowed={'SYSTEMROOT','SYSTEMDRIVE','WINDIR','COMSPEC','PATH','PATHEXT','USERPROFILE','LOCALAPPDATA','APPDATA',
             'PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','HOMEDRIVE','HOMEPATH','TEMP','TMP','HOME',
             'LANG','LC_ALL','TERM','COLORTERM','CODEX_HOME'}
    env={name:value for name,value in os.environ.items() if name.upper() in allowed}
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    return env


class CodexRuntime:
    def __init__(self, state=None):
        self.state=Path(state or ROOT/'.lab'/'app'/'runtime')
        self.state.mkdir(parents=True,exist_ok=True)
        self.active=None
        self.cached=None

    def discover(self):
        """Supported read-only app-server calls, never a model inference call."""
        version=sandbox.version()
        proc=subprocess.Popen([sandbox.executable(),'-c','forced_login_method="chatgpt"','app-server','--stdio'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',env=environment())
        messages=queue.Queue()
        def reader():
            for line in proc.stdout:
                try:messages.put(json.loads(line))
                except ValueError:pass
        threading.Thread(target=reader,daemon=True).start()
        def call(identifier,method,params):
            proc.stdin.write(json.dumps({'id':identifier,'method':method,'params':params})+'\n');proc.stdin.flush()
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                item=messages.get(timeout=max(.1,deadline-time.monotonic()))
                if item.get('id')==identifier:
                    if 'error' in item:raise RuntimeError(str(item['error']))
                    return item['result']
            raise TimeoutError('Codex metadata unavailable')
        try:
            hello=call(1,'initialize',{'clientInfo':{'name':'ada_lab','version':'0.2.0'},'capabilities':{'experimentalApi':True}})
            proc.stdin.write('{"method":"initialized"}\n');proc.stdin.flush()
            account=call(2,'account/read',{'refreshToken':False})
            if (account.get('account') or {}).get('type')!='chatgpt':
                raise RuntimeError('ChatGPT subscription login required; API execution is prohibited')
            models=call(3,'model/list',{'limit':100})
            try:
                quota=call(4,'account/rateLimits/read',{})
                # Do not expose account IDs, email, reset credits or credentials to UI/workers.
                quota={k:quota.get(k) for k in ('ordinaryUsageAllowed','rateLimits','rateLimitsByLimitId')}
            except Exception as error:quota={'status':'unknown','reason':str(error)}
            result={'status':'available','at':now(),'runtime':version,'auth':'chatgpt',
                'models':[{'id':m['model'],'efforts':[e['reasoningEffort'] for e in m.get('supportedReasoningEfforts',[]) if e['reasoningEffort']!='ultra']} for m in models['data']],
                'quota':quota,'hard_token_limit':False,'note':'Subscription windows and model tokens are different units. No paid API fallback.'}
            self.codex_home=Path(hello.get('codexHome') or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
            self.cached=result;write(self.state/'capabilities.json',result)
            return result
        finally:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            proc.stdin.close();proc.stdout.close()

    def check_profile(self, profile):
        info=self.cached or self.discover()
        if profile['provider']!='codex-chatgpt' or info['auth']!='chatgpt' or profile['runtime']!=sandbox.version():
            raise RuntimeError('Requested provider/runtime differs from actual subscription runtime')
        found=next((m for m in info['models'] if m['id']==profile['model']),None)
        if not found or profile['effort'] not in found['efforts']:
            raise RuntimeError('Requested model/reasoning effort is not advertised by this runtime')
        if info['quota'].get('ordinaryUsageAllowed') is False:
            raise RuntimeError('Subscription allowance currently denies ordinary usage')
        limits=info['quota'].get('rateLimits') or {}
        if any((limits.get(k) or {}).get('usedPercent',0)>=100 for k in ('primary','secondary')):
            raise RuntimeError('Subscription window exhausted; no purchased-credit/API fallback authorized')
        return info

    def args(self, profile, workspace, read_roots=()):
        # Tool filesystem roots are explicit; neither canonical repo nor evidence is writable.
        shells=[Path(value).resolve().parent for name in ('pwsh','powershell') if (value:=shutil.which(name))]
        bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell'
        if (bundled/'pwsh.exe').exists():shells.append(bundled)
        return [sandbox.executable(),*sandbox.profile_args([*read_roots,*shells]),
            '-c','forced_login_method="chatgpt"','-c','model_provider="openai"',
            '-c','approval_policy="never"','-c','model_reasoning_effort='+json.dumps(profile['effort']),
            '-c','web_search='+json.dumps(profile.get('settings',{}).get('web_search','disabled')),
            '-c','project_doc_max_bytes=0','-c','features.plugins=false','-c','features.apps=false',
            '-c','features.multi_agent=false','-c','features.shell_snapshot=false',
            'exec','--ignore-user-config','--ignore-rules','--model',profile['model'],
            '--json','--skip-git-repo-check','--color','never','-C',str(workspace),'-']

    def observed(self, identifier, workspace):
        """Read only this newly-created session's trusted runtime context, not other chats."""
        home=getattr(self,'codex_home',Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex'))
        day=datetime.now(timezone.utc)
        days=[day,day-timedelta(days=1),day+timedelta(days=1)]
        roots=[home/'sessions'/str(d.year)/f'{d.month:02d}'/f'{d.day:02d}' for d in days]
        for root in roots:
            for path in root.glob('*'+identifier+'*.jsonl'):
                contexts=[];meta=None
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:item=json.loads(line)
                    except ValueError:continue
                    if item.get('type')=='session_meta':meta=item.get('payload',{})
                    if item.get('type')=='turn_context':contexts.append(item.get('payload',{}))
                if not meta or meta.get('id')!=identifier or Path(meta.get('cwd','')).resolve()!=Path(workspace).resolve():continue
                if contexts:
                    ctx=contexts[-1]
                    permissions=ctx.get('permission_profile',{})
                    entries=permissions.get('file_system',{}).get('entries',[])
                    writes=[e.get('path',{}).get('path') for e in entries if e.get('access')=='write']
                    safe=(permissions.get('type')=='managed' and permissions.get('file_system',{}).get('type')=='restricted'
                          and len(writes)==1 and Path(writes[0]).resolve()==Path(workspace).resolve()
                          and ctx.get('approval_policy')=='never' and ctx.get('sandbox_policy',{}).get('network_access') is False)
                    return {'model':ctx.get('model'),'effort':ctx.get('effort'),
                        'provider':meta.get('model_provider'),'source':'CLI session_meta and turn_context',
                        'provider_rerouting':'not independently attested','permissions_verified':safe}
        return {'model':None,'effort':None,'provider':None,'source':'Runtime context unavailable','provider_rerouting':'unknown'}

    def run(self, profile, workspace, prompt, output, read_roots=(), timeout=900, cancelled=None, images=()):
        workspace=Path(workspace).resolve();output=Path(output).resolve()
        if not workspace.is_relative_to(ROOT/'.lab') or not output.is_relative_to(ROOT/'.lab'):
            raise ValueError('Worker/evidence must remain in ignored local state')
        self.check_profile(profile)
        workspace.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
        # Probe the same native permission table before allowing any generated execution.
        probe=sandbox.run(workspace,[sys.executable,'-c','import pathlib; p=pathlib.Path(".runtime-probe"); p.write_text("ok",encoding="utf-8"); p.unlink()'],timeout=30)
        if probe['returncode']!=0:raise RuntimeError('Native worker permission preflight failed: '+probe['stderr'][-500:])
        argv=self.args(profile,workspace,read_roots)
        for image in images:
            at=argv.index('-C');argv[at:at]=['--image',str(image)]
        import hashlib
        write(output/'request.json',{'requested':profile,'workspace':str(workspace),
            'prompt_digest':hashlib.sha256(prompt.encode('utf-8')).hexdigest(),'argv':argv})
        started=time.monotonic();events=[];identifier=None;usage=None;messages=[]
        errors=[];status='failed'
        with (output/'stderr.txt').open('wb') as err,(output/'events.jsonl').open('w',encoding='utf-8') as log:
            proc=subprocess.Popen(argv,cwd=workspace,env=environment(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=err,start_new_session=os.name!='nt')
            try:process_job=ProcessJob(proc)
            except BaseException:
                sandbox.stop(proc);raise
            self.active=proc
            try:proc.stdin.write(prompt.encode('utf-8'));proc.stdin.close()
            except BaseException:
                sandbox.stop(proc);process_job.close();self.active=None;proc.stdin.close();proc.stdout.close();raise
            incoming=queue.Queue()
            def reader():
                for line in proc.stdout:incoming.put(line)
                incoming.put(None)
            reader_thread=threading.Thread(target=reader,daemon=True);reader_thread.start()
            try:
                while True:
                    if cancelled and cancelled():raise InterruptedError('Paused/stopped by owner')
                    if time.monotonic()-started>timeout:raise TimeoutError('Model stage timeout')
                    try:line=incoming.get(timeout=.25)
                    except queue.Empty:continue
                    if line is None:break
                    text=line.decode('utf-8',errors='replace');log.write(text);log.flush()
                    try:event=json.loads(text)
                    except ValueError:continue
                    events.append(event)
                    if event.get('type')=='thread.started':identifier=event.get('thread_id')
                    if event.get('type')=='turn.completed':
                        candidate_usage=event.get('usage')
                        usage=candidate_usage if isinstance(candidate_usage,dict) and all(type(candidate_usage.get(k)) is int and candidate_usage[k]>=0 for k in ('input_tokens','output_tokens')) else None
                        if usage and ('cached_input_tokens' in usage and (type(usage['cached_input_tokens']) is not int or not 0<=usage['cached_input_tokens']<=usage['input_tokens'])):usage=None
                        status='completed'
                    if event.get('type') in ('error','turn.failed'):errors.append(event)
                    item=event.get('item',{})
                    if event.get('type')=='item.completed' and item.get('type')=='agent_message':messages.append(item.get('text',''))
                returncode=proc.wait(timeout=10)
                if returncode or errors:status='failed'
            except BaseException as error:
                sandbox.stop(proc);returncode=proc.returncode;status='interrupted' if isinstance(error,InterruptedError) else 'failed'
                errors.append({'message':str(error)})
            finally:
                self.active=None;process_job.close();reader_thread.join(timeout=2);proc.stdout.close()
        observed=self.observed(identifier,workspace) if identifier else {}
        verified=observed.get('model')==profile['model'] and observed.get('effort')==profile['effort'] and observed.get('provider')=='openai' and observed.get('permissions_verified') is True
        mismatch=any(observed.get(k) is not None and observed[k]!=expected for k,expected in
                     [('model',profile['model']),('effort',profile['effort']),('provider','openai')])
        if mismatch:status='profile_mismatch'
        if observed.get('permissions_verified') is not True:status='permission_profile_unverified'
        try:source_after=tree_digest(workspace)
        except (OSError,ValueError):source_after=None
        receipt={'status':status,'at':now(),'requested':profile,'observed':observed,'configuration_verified':verified,
            'configuration_scope':'Runtime context verified; provider-side rerouting not independently attested',
            'session_id':identifier,'returncode':returncode,'wall_seconds':time.monotonic()-started,
            'usage':usage,'errors':errors,'message':messages[-1] if messages else '', 'message_count':len(messages),'evidence':str(output),
            'unknown_partial_usage':usage is None,'source_after':source_after,
            'failed_tool_commands':[e['item'] for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution' and e['item'].get('exit_code') not in (None,0)]}
        write(output/'receipt.json',receipt)
        return receipt

    def stop(self):
        if self.active:sandbox.stop(self.active)

    def recover_completed(self,output,profile,workspace,prompt_digest):
        """Recover a fully journaled final turn; never infer success from partial code."""
        output=Path(output);events=output/'events.jsonl';request=output/'request.json'
        if not events.exists() or not request.exists() or not prompt_digest:return None
        declared=read(request)
        if declared.get('requested')!=profile or declared.get('workspace')!=str(workspace) or declared.get('prompt_digest')!=prompt_digest:return None
        identifier=None;usage=None;message=None;complete=False;failed=False
        for line in events.read_text(encoding='utf-8').splitlines():
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')=='thread.started':identifier=event.get('thread_id')
            if event.get('type')=='turn.completed':complete=True;usage=event.get('usage')
            if event.get('type') in ('turn.failed','error'):failed=True
            if event.get('type')=='item.completed' and event.get('item',{}).get('type')=='agent_message':message=event['item'].get('text')
        if not complete or failed or not identifier or message is None:return None
        observed=self.observed(identifier,workspace)
        if not (observed.get('model')==profile['model'] and observed.get('effort')==profile['effort'] and observed.get('provider')=='openai' and observed.get('permissions_verified')):return None
        if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in ('input_tokens','output_tokens')):usage=None
        result={'status':'completed','at':now(),'requested':profile,'observed':observed,'configuration_verified':True,
            'session_id':identifier,'usage':usage,'message':message,'returncode':0,'wall_seconds':None,'errors':[],
            'evidence':str(output),'recovered_from_complete_events':True,'unknown_partial_usage':usage is None,
            'configuration_scope':'Original trusted final events and exact request digest; original controller stage duration unavailable'}
        write(output/'receipt.json',result);return result


class ModelOutputError(ValueError):pass


def structured(message):
    """Accept a final JSON object; no executable evaluator code from model output."""
    value=message.strip()
    if value.startswith('```'):
        value='\n'.join(value.splitlines()[1:-1])
    try:
        def reject(value):raise ValueError('Nonstandard JSON constant')
        result=json.loads(value,parse_constant=reject)
        if not isinstance(result,dict):raise ValueError('Expected a structured object')
        pending=[(result,0)]
        while pending:
            item,depth=pending.pop()
            if depth>20:raise ValueError('Output nesting too deep')
            if isinstance(item,str):item.encode('utf-8')
            elif isinstance(item,dict):pending.extend((x,depth+1) for pair in item.items() for x in pair)
            elif isinstance(item,list):pending.extend((x,depth+1) for x in item)
    except (ValueError,UnicodeError) as error:raise ModelOutputError('Invalid final structured output: '+str(error)) from error
    return result
