"""Find the installed native CLI without depending on the supervising app's PATH."""
import os
from pathlib import Path
import shutil


def runtime_path():
    found=shutil.which('codex')
    if found:return found
    if os.name=='nt':
        local=Path(os.environ.get('LOCALAPPDATA',''))
        candidates=list((local/'OpenAI'/'Codex'/'bin').glob('*/codex.exe'))
        for path in sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True):
            os.environ['PATH']=str(path.parent)+os.pathsep+os.environ.get('PATH','')
            return str(path)
    raise RuntimeError('Installed Codex CLI not found. Install/sign in to Codex, then reopen Run.bat. No API key is required.')
