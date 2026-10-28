"""Native isolated workers. No AI session launcher or alternative operating system."""
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import importlib.util

from . import sandbox
from .files import ROOT, write


def browser_executable():
    roots = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')),
             Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')),
             Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))]
    for root in roots:
        for suffix in ['Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe']:
            path = root / suffix
            if path.is_file():
                return path
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        path = Path(playwright.chromium.executable_path)
    if not path.exists():
        raise RuntimeError('Browser missing: run python -m playwright install chromium')
    return path


def playwright_driver():
    spec = importlib.util.find_spec('playwright')
    if not spec:
        raise RuntimeError('Playwright missing: install ADA Lab dependencies with pip install -e .')
    folder = Path(spec.origin).parent / 'driver'
    return folder / ('node.exe' if os.name == 'nt' else 'node'), folder / 'package'


class Worker:
    def __init__(self, workspace, script, evidence, extra=(), executable=None):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.log = Path(evidence).open('ab')
        argv = [executable, ROOT / 'ada_lab' / script] if executable else [sys.executable, '-I', ROOT / 'ada_lab' / script]
        environment = sandbox.clean_env(self.workspace)
        if executable:
            environment['NODE_OPTIONS'] = '--preserve-symlinks --preserve-symlinks-main'
        self.process = subprocess.Popen(sandbox.command(self.workspace,
            argv, [ROOT / 'ada_lab', *extra]),
            cwd=self.workspace, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, start_new_session=os.name != 'nt')
        self.queue = queue.Queue(maxsize=8)
        def reader():
            try:
                while True:
                    line = self.process.stdout.readline(3_000_001)
                    if not line or len(line) > 3_000_000:
                        break
                    self.queue.put(line, timeout=2)
            except (ValueError, OSError, queue.Full):
                pass
            finally:
                try:
                    self.queue.put(None, timeout=1)
                except queue.Full:
                    pass
        threading.Thread(target=reader, daemon=True).start()

    def send(self, data):
        encoded = (json.dumps(data) + '\n').encode()
        if len(encoded) > 3_000_000:
            raise ValueError('Worker frame exceeds limit')
        completed = queue.Queue(maxsize=1)
        def writer():
            try:
                self.process.stdin.write(encoded)
                self.process.stdin.flush()
                completed.put(None)
            except Exception as error:
                completed.put(error)
        threading.Thread(target=writer, daemon=True).start()
        try:
            error = completed.get(timeout=5)
        except queue.Empty as error:
            sandbox.stop(self.process)
            raise TimeoutError('Isolated worker stopped reading requests') from error
        if error:
            raise error

    def receive(self, timeout=10):
        try:
            line = self.queue.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError('Isolated worker did not respond; see worker log') from error
        if line is None:
            raise RuntimeError('Isolated worker exited; see worker log')
        return json.loads(line)

    def stop(self):
        sandbox.stop(self.process)
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.log.close()


class NativeApplication:
    def __init__(self, workspace, evidence):
        self.workspace, self.evidence = Path(workspace), Path(evidence)
        self.worker = None
        self.port = self  # Evaluator endpoint object, not a TCP port.

    def start(self):
        self.worker = Worker(self.workspace, 'backend_worker.py', self.evidence / 'server.log')
        status, _, _ = self.request('GET', '/health', b'', {})
        if status != 200:
            raise RuntimeError('Application health request failed')

    def request(self, method, path, body, headers):
        self.worker.send({'method': method, 'path': path,
                          'body': base64.b64encode(body or b'').decode(), 'headers': headers})
        response = self.worker.receive()
        status, headers = response['status'], response['headers']
        body = base64.b64decode(response['body'], validate=True)
        if type(status) is not int or not 100 <= status <= 599 or len(body) > 2_000_000 or not isinstance(headers, dict):
            raise RuntimeError('Invalid application response')
        return status, headers, body

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.worker = None

    def restart(self):
        self.stop()
        self.start()

    def browser_checks(self):
        browser = browser_executable()
        node, library = playwright_driver()
        workspace = self.evidence.parent / 'browser-workspace'
        workspace.mkdir(exist_ok=True)
        write(workspace / 'browser.json', {'executable': str(browser), 'library': str(library)})
        worker = Worker(workspace, 'browser_checks.js', self.evidence / 'browser.log', [browser.parent, node.parent], executable=node)
        deadline = time.monotonic() + 100
        try:
            while time.monotonic() < deadline:
                message = worker.receive(timeout=min(10, max(.1, deadline - time.monotonic())))
                if message['kind'] == 'result':
                    return message['result']
                if message['kind'] != 'request':
                    raise RuntimeError('Invalid browser worker message')
                req = message['request']
                status, headers, body = self.request(req['method'], req['path'], base64.b64decode(req['body']), req['headers'])
                worker.send({'status': status, 'headers': headers, 'body': base64.b64encode(body).decode()})
            raise TimeoutError('Browser evaluation timed out')
        finally:
            worker.stop()
            screenshot = workspace / 'mobile.png'
            if screenshot.is_file():
                (self.evidence / 'mobile.png').write_bytes(screenshot.read_bytes())
