"""UTF-8, content identities, and atomic local state. No database required."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.cache', '.runtime', '__pycache__', '.venv', 'node_modules'}


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def inventory(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Source directory is missing: ' + str(root))
    result = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(base) / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError(f'Links are forbidden in artifacts: {path}')
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(files):
            path = Path(base) / name
            if path.stat().st_size > 10_000_000:
                raise ValueError(f'Oversized source file: {path}')
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def tree_digest(root):
    return digest(inventory(root))


def copy_tree(source, target):
    source, target = Path(source), Path(target)
    listing = inventory(source)
    target.mkdir(parents=True, exist_ok=False)
    for name in listing:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    return tree_digest(target)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, encoding='utf-8').strip()


def identity(root):
    return {'commit': git(root, 'rev-parse', 'HEAD'),
            'status': git(root, 'status', '--porcelain'), 'source_digest': tree_digest(root)}


def safe_id(value):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}', value):
        raise ValueError('Invalid record identifier')
    return value


@contextlib.contextmanager
def lock(path):
    """OS-held byte lock: crash release without stale PID guessing."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0)
        if path.stat().st_size == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError('Another controller holds this record') from error
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
