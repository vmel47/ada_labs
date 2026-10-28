"""Native platform sandbox helpers and probes. Never launches an AI session."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
import signal

from .files import ROOT, now, write


def executable():
    value = shutil.which('codex')
    if not value:
        raise RuntimeError('The installed Codex sandbox helper is unavailable')
    return value


def version():
    return subprocess.check_output([executable(), '--version'], text=True).strip()


def clean_env(workspace):
    # Keep only runtime basics. Never pass API credentials or user service tokens.
    names = {'systemroot', 'systemdrive', 'windir', 'comspec', 'path', 'pathext', 'userprofile',
             'localappdata', 'appdata', 'programfiles', 'programfiles(x86)',
             'programdata', 'homedrive', 'homepath'}
    env = {k.upper(): v for k, v in os.environ.items() if k.lower() in names}
    for name in ['.codex','.agents','.aws']:
        (Path(workspace)/name).mkdir(exist_ok=True)
    temp = Path(workspace) / '.runtime' / 'tmp'
    temp.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(temp), TMP=str(temp), PYTHONIOENCODING='utf-8',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    return env


def profile_args(read_roots=()):
    filesystem = {':minimal': 'read', Path(sys.base_prefix).as_posix(): 'read', Path(sys.prefix).as_posix(): 'read'}
    filesystem[Path(executable()).resolve().parent.as_posix()] = 'read'
    for root in read_roots:
        filesystem[Path(root).resolve().as_posix()] = 'read'
    # TOML inline table, not shell text. subprocess always receives an argv array.
    table = '{' + ','.join(json.dumps(k) + '=' + json.dumps(v) for k, v in filesystem.items()) + ', ":workspace_roots"={"."="write",".codex"="deny",".agents"="deny",".git"="deny",".aws"="deny"}}'
    return (['-c', 'windows.sandbox="mxc"'] if os.name == 'nt' else []) + [
            '-c', 'permissions.ada_lab.filesystem=' + table,
            '-c', 'permissions.ada_lab.network.enabled=false',
            '-c', 'default_permissions="ada_lab"']


def command(workspace, argv, read_roots=()):
    return [executable(), *profile_args(read_roots), 'sandbox', '--include-managed-config',
            '--permission-profile', 'ada_lab', '-C', str(Path(workspace).resolve()), '--', *map(str, argv)]


def stop(process):
    if process.poll() is None:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=15)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=15)


def run(workspace, argv, timeout=30):
    process = subprocess.Popen(command(workspace, argv), cwd=workspace,
                               env=clean_env(workspace), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=os.name != 'nt')
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        stop(process)
        raise
    return {'argv': list(map(str, argv)), 'returncode': process.returncode,
            'stdout': stdout.decode('utf-8', errors='replace'),
            'stderr': stderr.decode('utf-8', errors='replace')}


def alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel.CloseHandle(handle)
    return bool(ok and code.value == 259)


PROBE = r'''
import ctypes, json, socket, subprocess, sys, time
from pathlib import Path
inputs=json.loads(Path('inputs.json').read_text())
r={}
Path('allowed.txt').write_text('ok')
r['workspace_write']=Path('allowed.txt').read_text()=='ok'
for name,path in inputs['targets'].items():
    try:
        with Path(path).open('r+b') as f: pass
        r[name]=False
    except OSError: r[name]=True
try:
    Path(inputs['private']).read_text(); r['private_read_denied']=False
except OSError: r['private_read_denied']=True
for name,host,port in [('internet_denied','1.1.1.1',443),('host_loopback_denied','127.0.0.1',inputs['port'])]:
    try:
        socket.create_connection((host,port),timeout=2).close(); r[name]=False
    except OSError: r[name]=True
s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); r['can_listen']=True
# Attempt to terminate only an expendable sentinel, never a real user process.
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.OpenProcess.restype=ctypes.c_void_p
k.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint]
k.CloseHandle.argtypes=[ctypes.c_void_p]
h=k.OpenProcess(1,False,inputs['sentinel_pid'])
r['host_process_denied']=not h
if h:
    r['host_process_denied']=not bool(k.TerminateProcess(h,77)); k.CloseHandle(h)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
r['child_pid']=child.pid
print(json.dumps(r),flush=True)
'''


def doctor(state, ada):
    """Fresh probe each time; cached success never authorizes a later run."""
    if os.name != 'nt':
        return posix_doctor(state, ada)
    folder = Path(state) / 'doctor' / uuid.uuid4().hex
    workspace = folder / 'workspace'
    workspace.mkdir(parents=True)
    protected = folder / 'protected'
    protected.mkdir()
    (protected / 'sentinel').write_text('private-canary', encoding='utf-8')
    # Junction is made by the trusted controller and tested from within MXC.
    junction = workspace / 'escape'
    created = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(protected)],
                             capture_output=True, text=True)
    targets = {'evidence_write_denied': str(protected / 'sentinel'),
               'lab_write_denied': str(ROOT / 'pyproject.toml'),
               'ada_write_denied': str(Path(ada) / 'README.md'),
               'junction_write_denied': str(junction / 'sentinel')}
    report = {'started': now(), 'backend': 'windows-mxc', 'status': 'BLOCKED',
              'codex_version': version(),
              'python': sys.version, 'checks': {}}
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            write(workspace / 'inputs.json', {'targets': targets, 'private': str(protected / 'sentinel'),
                  'port': listener.getsockname()[1], 'sentinel_pid': sentinel.pid})
            (workspace / 'probe.py').write_text(PROBE, encoding='utf-8')
            receipt = run(workspace, [sys.executable, 'probe.py'])
        report['receipt'] = receipt
        if receipt['returncode'] != 0:
            raise RuntimeError(receipt['stderr'] or 'Sandbox probe failed')
        checks = json.loads(receipt['stdout'].strip().splitlines()[-1])
        child_pid = checks.pop('child_pid')
        time.sleep(.3)
        checks['child_terminated'] = not alive(child_pid)
        checks['sentinel_survived'] = sentinel.poll() is None
        checks['junction_created'] = created.returncode == 0
        report['checks'] = checks
        report['status'] = 'PASS' if all(v is True for v in checks.values()) else 'BLOCKED'
    except Exception as error:
        report['error'] = str(error)
    finally:
        stop(sentinel)
        report['finished'] = now()
        write(folder / 'result.json', report)
    report['evidence'] = str(folder / 'result.json')
    return report


POSIX_PROBE = r'''
import json,os,socket,subprocess,sys,signal
from pathlib import Path
i=json.loads(Path('inputs.json').read_text());r={}
Path('allowed').write_text('ok');r['workspace_write']=True
for key,path in i['targets'].items():
 try:
  with open(path,'r+b'):pass
  r[key]=False
 except OSError:r[key]=True
try: Path(i['private']).read_bytes();r['private_read_denied']=False
except OSError:r['private_read_denied']=True
for key,host,port in [('internet_denied','1.1.1.1',443),('host_loopback_denied','127.0.0.1',i['port'])]:
 try:socket.create_connection((host,port),timeout=1).close();r[key]=False
 except OSError:r[key]=True
try:os.kill(i['sentinel_pid'],signal.SIGTERM);r['host_process_denied']=False
except OSError:r['host_process_denied']=True
subprocess.Popen([sys.executable,'-c',"import time;from pathlib import Path;time.sleep(2);Path('leaked-child').write_text('bad')"])
print(json.dumps(r),flush=True)
'''


def posix_doctor(state, ada):
    folder = Path(state).resolve() / 'doctor' / uuid.uuid4().hex
    workspace = folder / 'workspace'; workspace.mkdir(parents=True)
    protected = folder / 'protected'; protected.mkdir()
    (protected / 'sentinel').write_text('private', encoding='utf-8')
    (workspace / 'escape').symlink_to(protected, target_is_directory=True)
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
    report = {'started': now(), 'backend': sys.platform + '-native', 'status': 'BLOCKED', 'checks': {}, 'codex_version': version()}
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            write(workspace / 'inputs.json', {'targets': {
                'evidence_write_denied': str(protected / 'sentinel'),
                'lab_write_denied': str(ROOT / 'pyproject.toml'),
                'ada_write_denied': str(Path(ada) / 'README.md'),
                'symlink_write_denied': str(workspace / 'escape' / 'sentinel')},
                'private': str(protected / 'sentinel'), 'port': listener.getsockname()[1], 'sentinel_pid': sentinel.pid})
            (workspace / 'probe.py').write_text(POSIX_PROBE, encoding='utf-8')
            receipt = run(workspace, [sys.executable, 'probe.py'], timeout=20)
        report['receipt'] = receipt
        if receipt['returncode'] != 0:
            raise RuntimeError(receipt['stderr'])
        checks = json.loads(receipt['stdout'].strip().splitlines()[-1])
        time.sleep(3)
        checks['child_terminated'] = not (workspace / 'leaked-child').exists()
        checks['sentinel_survived'] = sentinel.poll() is None
        report['checks'] = checks
        report['status'] = 'PASS' if all(checks.values()) else 'BLOCKED'
    except Exception as error:
        report['error'] = str(error)
    finally:
        stop(sentinel)
    report['finished'] = now(); report['evidence'] = str(folder / 'result.json')
    write(folder / 'result.json', report)
    return report


def require_safe(state, ada):
    report = doctor(state, ada)
    if report['status'] != 'PASS':
        raise RuntimeError('Isolation preflight BLOCKED: ' + report['evidence'])
    return report

