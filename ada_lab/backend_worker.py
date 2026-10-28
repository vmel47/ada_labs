"""Application host: HTTP data only, never an authoritative test verdict."""
import base64
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('repair_app', Path.cwd() / 'app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
database = str(Path.cwd() / '.runtime' / 'data.sqlite')
for line in sys.stdin:
    try:
        if len(line) > 3_000_000:
            raise ValueError('Request too large')
        request = json.loads(line)
        status, headers, body = app.handle_request(request['method'], request['path'],
            base64.b64decode(request['body']), request['headers'], database)
        if not isinstance(body, bytes) or len(body) > 2_000_000:
            raise ValueError('Invalid response body')
        print(json.dumps({'status': status, 'headers': headers,
                          'body': base64.b64encode(body).decode()}), flush=True)
    except Exception as error:
        print(json.dumps({'status': 500, 'headers': {'Content-Type': 'application/json'},
            'body': base64.b64encode(json.dumps({'error': str(error)}).encode()).decode()}), flush=True)
