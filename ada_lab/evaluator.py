"""Authoritative tests run in the controller, never inside the application."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import uuid

from . import sandbox
from .native import NativeApplication
from .files import ROOT, copy_tree, digest, now, read, tree_digest, write


def version():
    return digest({name: hashlib.sha256((ROOT / 'ada_lab' / name).read_bytes()).hexdigest()
                   for name in ['evaluator.py', 'sandbox.py', 'files.py', 'native.py', 'backend_worker.py', 'browser_checks.js']})


def benchmark(name):
    registry = read(ROOT / 'benchmarks' / 'registry.json')
    path = (ROOT / 'benchmarks' / registry[name]).resolve()
    if not path.is_relative_to(ROOT / 'benchmarks'):
        raise ValueError('Invalid benchmark registry path')
    spec = read(path / 'benchmark.json')
    if spec['evaluator'] != 'repair_queue_v1':
        raise ValueError('No installed evaluator for this benchmark')
    return path, spec


def http(port, method='GET', path='/api/repairs', data=None, raw=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    status, headers, response = port.request(method, path, body or b'', {'Content-Type': 'application/json'})
    return status, json.loads(response)



def require(condition, description):
    if not condition:
        raise AssertionError(description)


def evaluate(artifact, state, ada, name='repair-queue-v1'):
    preflight = sandbox.require_safe(state, ada)
    source = Path(artifact).resolve()
    path, spec = benchmark(name)
    folder = Path(state).resolve() / 'evaluations' / uuid.uuid4().hex
    evidence = folder / 'evidence'
    evidence.mkdir(parents=True)
    workspace = folder / 'workspace'
    frozen = copy_tree(source, workspace)
    result = {'started': now(), 'artifact_digest': frozen, 'evaluator_version': version(),
              'benchmark_revision': tree_digest(path), 'criteria_digest': digest(spec['criteria']),
              'criteria': {}, 'preflight': preflight['evidence'], 'evidence': str(evidence),
              'accounting': {'category': 'evaluation', 'model_sessions': 0, 'input_tokens': 0,
                             'output_tokens': 0, 'monetary_cost': None}}
    clock = time.monotonic()
    server = NativeApplication(workspace, evidence)
    base = {'customer': 'Mira', 'device': 'Camera', 'issue': 'Cracked lens', 'status': 'new', 'priority': 'high'}
    expected = []

    def check(key, action):
        started = time.monotonic()
        try:
            action()
            receipt = {'status': 'PASS', 'detail': 'Observed required behavior'}
        except Exception as error:
            receipt = {'status': 'FAIL', 'detail': f'{type(error).__name__}: {error}'}
        receipt['wall_seconds'] = time.monotonic() - started
        result['criteria'][key] = receipt
        write(evidence / 'progress.json', result)

    def crud():
        status, record = http(server.port, 'POST', data=base)
        require(status == 201 and type(record.get('id')) is int, 'Create must return 201 and integer id')
        changed = {**base, 'customer': ' Mira Edited ', 'status': 'in_progress'}
        status, edited = http(server.port, 'PUT', f"/api/repairs/{record['id']}", changed)
        require(status == 200 and edited == {**changed, 'customer': 'Mira Edited', 'id': record['id']}, 'Edit/trim/id semantics')
        require(http(server.port, 'PUT', '/api/repairs/99999999', base)[0] == 404, 'Missing repair must be 404')
        require(http(server.port, path='/missing')[0] == 404, 'Unknown route must be 404')
        defaults = {k: base[k] for k in ['customer', 'device', 'issue']}
        status, defaulted = http(server.port, 'POST', data=defaults)
        require(status == 201 and defaulted['status'] == 'new' and defaulted['priority'] == 'normal', 'Defaults')
        records = http(server.port)[1]
        require([r['id'] for r in records] == sorted([r['id'] for r in records], reverse=True), 'Newest first')
        require(edited in records and len(records) == 2, 'Edit must persist without duplicating')

    def validation():
        before = http(server.port)[1]
        for field, bad_values in [('customer', ['', '  ', None, 12, 'x'*81]),
                                  ('device', ['', None, 'x'*121]), ('issue', ['', None, 'x'*501]),
                                  ('status', ['unknown', None, []]), ('priority', ['urgent', None])]:
            for value in bad_values:
                status, body = http(server.port, 'POST', data={**base, field: value})
                require(status == 400 and isinstance(body.get('error'), str) and body['error'], f'Reject {field}={value!r}')
        for raw in [b'{broken', b'[]', b'null', b'{}']:
            require(http(server.port, 'POST', raw=raw)[0] == 400, 'Malformed/missing fields rejected')
        if before:
            require(http(server.port, 'PUT', f"/api/repairs/{before[0]['id']}", {**base, 'issue': ''})[0] == 400, 'Invalid edit rejected')
        require(http(server.port)[1] == before, 'Invalid writes must not mutate records')

    def filters():
        for record in [{**base, 'customer': 'Other', 'status': 'done'},
                       {**base, 'customer': 'MIRA THIRD', 'priority': 'low'},
                       {**base, 'customer': 'Spare', 'device': 'Printer', 'issue': 'Mira gear', 'status': 'done'}]:
            require(http(server.port, 'POST', data=record)[0] == 201, 'Seed filter data')
        all_rows = http(server.port)[1]
        for query in [{'q':'mIrA'}, {'status':'done'}, {'priority':'low'},
                      {'q':'mira','status':'new','priority':'low'},
                      {'q':'mira','status':'done','priority':'high'}, {'q':'no-match-925'}]:
            wanted = [r for r in all_rows if (not query.get('q') or query['q'].casefold() in ' '.join(r[k] for k in ['customer','device','issue']).casefold())
                      and (not query.get('status') or r['status']==query['status'])
                      and (not query.get('priority') or r['priority']==query['priority'])]
            require(http(server.port, path='/api/repairs?'+urlencode(query))[1] == wanted, f'Filter intersection: {query}')

    def persistence():
        before = http(server.port)[1]
        require(bool(before), 'Persistence requires existing data')
        server.restart()
        require(http(server.port)[1] == before, 'Restart lost records or edits')

    try:
        server.start()
        check('api_crud', crud)
        check('validation', validation)
        check('search_filter', filters)
        check('persistence', persistence)
        browser_result = server.browser_checks()
        result['criteria'].update(browser_result.get('criteria', {}))
        result['browser_version'] = browser_result.get('browser_version')
        if browser_result.get('error'):
            result['error'] = browser_result['error']
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        server.stop()
    for item in spec['criteria']:
        result['criteria'].setdefault(item['id'], {'status':'BLOCKED', 'detail':result.get('error','Prerequisite missing')})
    result['artifact_unchanged'] = tree_digest(source) == frozen and tree_digest(workspace) == frozen
    result['quality_pass'] = result['artifact_unchanged'] and all(r['status']=='PASS' for r in result['criteria'].values())
    result['finished'] = now()
    result['accounting']['wall_seconds'] = time.monotonic() - clock
    write(evidence / 'result.json', result)
    return result


def calibrate(state, ada):
    path, _ = benchmark('repair-queue-v1')
    results = {'reference': evaluate(path/'reference', state, ada)}
    for defect, criterion in [('persistence','persistence'),('validation','validation'),('filter','search_filter')]:
        target = Path(state).resolve()/'calibration'/uuid.uuid4().hex
        copy_tree(path/'reference', target)
        app = target/'app.py'
        app.write_text(app.read_text(encoding='utf-8').replace("BROKEN = ''", f'BROKEN = {defect!r}'), encoding='utf-8')
        results[defect] = evaluate(target, state, ada)
    passed = results['reference']['quality_pass'] and all(results[defect]['criteria'][criterion]['status']=='FAIL'
                    for defect, criterion in [('persistence','persistence'),('validation','validation'),('filter','search_filter')])
    receipt = {'status':'PASS' if passed else 'FAIL', 'evaluator_version':version(),
               'benchmark_revision':tree_digest(path), 'results':results, 'finished':now()}
    write(Path(state)/'calibration.json', receipt)
    return receipt
