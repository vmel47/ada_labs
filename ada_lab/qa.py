"""Execute independent Reviewer-selected actions through existing protected workers."""
import base64
import json
from pathlib import Path
import time
import uuid
from .files import copy_tree, write, ROOT
from .native import NativeApplication, Worker, browser_executable, playwright_driver
from . import sandbox


def validate_plan(plan):
    api=plan.get('api_checks',[]);steps=plan.get('browser_steps',[])
    if not isinstance(api,list) or not 1<=len(api)<=30 or not isinstance(steps,list) or not 1<=len(steps)<=64:
        raise ValueError('Reviewer must choose bounded real API and browser actions')
    for case in api:
        if not isinstance(case,dict) or type(case.get('expected_status')) is not int or not 100<=case['expected_status']<=599:
            raise ValueError('Each API action needs an explicit expected HTTP status')
    for step in steps:
        if not isinstance(step,dict) or step.get('op') not in ('fill','click','select','press','delay_next','settle','wait_text','assert_text','assert_count','reload'):
            raise ValueError('Unknown Reviewer browser action')
        if step['op'] not in ('reload','delay_next','settle') and (not isinstance(step.get('selector'),str) or not step['selector']):raise ValueError('Browser selector required')
        if step['op'] in ('fill','select','press','wait_text','assert_text') and not isinstance(step.get('value'),str):raise ValueError('Browser text/key value required')
        if step['op']=='assert_count' and (type(step.get('count'))is not int or not 0<=step['count']<=1000):raise ValueError('Bounded nonnegative element count required')
        if step['op']=='delay_next' and (step.get('target')!='/api/repairs' or type(step.get('ms'))is not int or not 1<=step['ms']<=2000):raise ValueError('Only bounded actual repair-list response delay is supported')


def review_actions(source, plan, output, ada, state):
    sandbox.require_safe(state,ada)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    api=plan.get('api_checks',[]);steps=plan.get('browser_steps',[])
    validate_plan(plan)
    browser=browser_executable();node,library=playwright_driver()
    results=[];images=[]
    for mode in ('api','browser'):
        workspace=output/(mode+'-app');copy_tree(source,workspace)
        evidence=output/(mode+'-evidence');evidence.mkdir()
        app=NativeApplication(workspace,evidence)
        try:
            app.start()
            if mode=='api':
                for case in api:
                    try:
                        method=case.get('method','GET');target=case.get('target','/api/repairs')
                        if method not in ('GET','POST','PUT','PATCH','DELETE') or not isinstance(target,str) or len(target)>2000:raise ValueError('Invalid API action')
                        raw=case['raw'].encode('utf-8') if 'raw' in case else json.dumps(case.get('body')).encode('utf-8') if 'body' in case else b''
                        before=app.request('GET','/api/repairs',b'',{})[2] if case.get('unchanged') else None
                        status,headers,body=app.request(method,target,raw,{'Content-Type':'application/json'})
                        passed=status==case['expected_status']
                        if before is not None:passed=passed and app.request('GET','/api/repairs',b'',{})[2]==before
                        results.append({'case':case,'actual_status':status,'body':body.decode('utf-8',errors='replace')[:1500],'status':'PASS' if passed else 'FAIL'})
                    except Exception as error:results.append({'case':case,'status':'FAIL','detail':str(error)})
            else:
                browser_workspace=output/'browser';browser_workspace.mkdir()
                write(browser_workspace/'browser.json',{'library':str(library),'executable':str(browser),'steps':steps})
                worker=Worker(browser_workspace,'journeys.js',evidence/'browser.log',[browser.parent,node.parent],executable=node)
                try:
                    end=time.monotonic()+115
                    while time.monotonic()<end:
                        message=worker.receive(timeout=min(10,max(.1,end-time.monotonic())))
                        if message['kind']=='result':results.append({'browser':message['result'],'status':message['result']['status']});break
                        req=message['request'];status,headers,body=app.request(req['method'],req['path'],base64.b64decode(req['body']),req['headers'])
                        worker.send({'status':status,'headers':headers,'body':base64.b64encode(body).decode()})
                    else:raise TimeoutError('Reviewer browser actions timed out')
                finally:worker.stop()
                images=[]
                for name in ('desktop.png','mobile.png'):
                    path=browser_workspace/name
                    if path.exists():
                        data=path.read_bytes()
                        if len(data)>5_000_000 or not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Invalid/bounded screenshot evidence')
                        (evidence/name).write_bytes(data);images.append(str(evidence/name))
        except Exception as error:results.append({'mode':mode,'status':'FAIL','detail':str(error)})
        finally:app.stop()
    result={'status':'PASS' if results and all(x['status']=='PASS' for x in results) and len(images)==2 else 'FAIL','results':results,'images':images}
    write(output/'receipt.json',result)
    return result
