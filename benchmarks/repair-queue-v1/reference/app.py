"""Repair Queue reference handler, exercised in an isolated native process."""
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse

FIELDS = ('customer', 'device', 'issue', 'status', 'priority')
BROKEN = ''
INITIALIZED = set()


def reply(status, value):
    return status, {'Content-Type': 'application/json; charset=utf-8'}, json.dumps(value).encode('utf-8')


def validate_repair(data):
    if not isinstance(data, dict):
        raise ValueError('Expected an object')
    data = dict(data)
    data.setdefault('status', 'new')
    data.setdefault('priority', 'normal')
    for field, limit in [('customer', 80), ('device', 120), ('issue', 500)]:
        value = data.get(field)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            if BROKEN != 'validation':
                raise ValueError('Invalid ' + field)
            value = str(value or '')
        data[field] = value.strip()
    if data['status'] not in ('new', 'in_progress', 'done') or data['priority'] not in ('low', 'normal', 'high'):
        raise ValueError('Invalid status or priority')
    return {field: data[field] for field in FIELDS}


def handle_request(method, target, body, headers, database):
    db = Path(database)
    db.parent.mkdir(parents=True, exist_ok=True)
    if database not in INITIALIZED:
        if BROKEN == 'persistence' and db.exists():
            db.unlink()
        INITIALIZED.add(database)
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS repairs (id INTEGER PRIMARY KEY, customer TEXT, device TEXT, issue TEXT, status TEXT, priority TEXT)')
    url = urlparse(target)
    if method == 'GET' and url.path == '/health':
        return reply(200, {'ok': True})
    if method == 'GET' and url.path == '/':
        return 200, {'Content-Type': 'text/html; charset=utf-8'}, Path(__file__).with_name('index.html').read_bytes()
    if method == 'GET' and url.path == '/api/repairs':
        with sqlite3.connect(db) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute('SELECT * FROM repairs ORDER BY id DESC')]
        query = {k:v[0] for k,v in parse_qs(url.query).items()}
        if BROKEN != 'filter':
            rows = [row for row in rows if
                (not query.get('q') or query['q'].casefold() in ' '.join(row[f] for f in FIELDS[:3]).casefold()) and
                (not query.get('status') or row['status'] == query['status']) and
                (not query.get('priority') or row['priority'] == query['priority'])]
        return reply(200, rows)
    editing = method == 'PUT' and url.path.startswith('/api/repairs/')
    if (method == 'POST' and url.path == '/api/repairs') or editing:
        try:
            data = validate_repair(json.loads(body))
        except (ValueError, TypeError):
            return reply(400, {'error': 'Check customer, device, issue, status and priority'})
        values = tuple(data[f] for f in FIELDS)
        with sqlite3.connect(db) as connection:
            if editing:
                try:
                    record_id = int(url.path.rsplit('/',1)[1])
                except ValueError:
                    return reply(404, {'error': 'Not found'})
                changed = connection.execute('UPDATE repairs SET customer=?,device=?,issue=?,status=?,priority=? WHERE id=?', (*values,record_id))
                if not changed.rowcount:
                    return reply(404, {'error': 'Not found'})
            else:
                record_id = connection.execute('INSERT INTO repairs (customer,device,issue,status,priority) VALUES (?,?,?,?,?)',values).lastrowid
        return reply(200 if editing else 201, {'id':record_id, **data})
    return reply(404, {'error':'Not found'})
