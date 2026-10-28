"""Bounded unauthenticated retrieval of public primary research sources."""
from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

ALLOWED=('arxiv.org','openai.com','developers.openai.com','learn.chatgpt.com','github.com',
         'raw.githubusercontent.com','anthropic.com','research.google','microsoft.com','aclanthology.org','openreview.net')


def allowed(url):
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):return False
    except ValueError:return False
    return any(parsed.hostname==host or (parsed.hostname or '').endswith('.'+host) for host in ALLOWED)


class Redirects(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,message,headers,newurl):
        if not allowed(newurl):raise ValueError('Research redirect leaves permitted public sources')
        return super().redirect_request(request,fp,code,message,headers,newurl)


def retrieve(source,folder):
    result=dict(source);result['retrieved_at']=datetime.now(timezone.utc).isoformat()
    url=source.get('url','')
    if not allowed(url):
        return {**result,'verified_retrieval':False,'limitation':'Outside current public-source retrieval allowlist; not independently fetched'}
    try:
        with build_opener(Redirects()).open(Request(url,headers={'User-Agent':'ADA-Lab-Research/0.2'}),timeout=12) as response:
            body=response.read(120001)
            if len(body)>120000:body=body[:120000]
            if 'text/' not in response.headers.get('Content-Type','') and 'json' not in response.headers.get('Content-Type',''):
                raise ValueError('Only bounded text source material is retained')
            filename=hashlib.sha256(url.encode()).hexdigest()+'.txt'
            folder.mkdir(parents=True,exist_ok=True);(folder/filename).write_bytes(body)
            excerpt=re.sub('<[^>]+>',' ',body.decode('utf-8',errors='replace'))
            result.update(verified_retrieval=True,http_status=response.status,sha256=hashlib.sha256(body).hexdigest(),
                excerpt=' '.join(excerpt.split())[:1400],evidence_file=filename,
                limitation='Retrieval verifies availability/content, not that the source supports the hypothesis')
    except Exception as error:result.update(verified_retrieval=False,limitation=str(error)[:300])
    return result
