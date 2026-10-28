"""Portable one-command entry point: python start.py init (or another Lab command)."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    if sys.version_info < (3, 11):
        raise SystemExit('ADA Lab requires Python 3.11 or newer.')
    root = Path(__file__).resolve().parent
    environment = root / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
    identity = hashlib.sha256((root / 'pyproject.toml').read_bytes()).hexdigest()
    marker = root / '.lab' / 'installation.json'
    installed = json.loads(marker.read_text()) if marker.exists() else {}
    if installed.get('project_digest') != identity:
        subprocess.run([str(python), '-m', 'pip', 'install', '-e', str(root)], check=True)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'project_digest': identity}), encoding='utf-8')
    result = subprocess.run([str(python), '-m', 'ada_lab', *sys.argv[1:]], cwd=root)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
