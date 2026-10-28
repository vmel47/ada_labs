"""Populate ADA's ledger from controller observations, never from developer claims.

Executed only as trusted bookkeeping. It imports the unchanged production ledger
validator, scans application bytes, and never imports or executes application code.
"""
import json
from pathlib import Path
import sys


def main():
    trusted,root,payload=map(Path,sys.argv[1:4])
    sys.path.insert(0,str(trusted))
    import acceptance
    source=json.loads(payload.read_text(encoding='utf-8'))
    criteria=source['criteria']
    record=acceptance.create(root,criteria,source['implementers'],'.cache/criteria-frozen.json')
    for name in ('native-runtime','real-browser'):
        record['capabilities'][name]={'available':True,'candidate_digest':record['candidate']['digest'],
            'observer':source['observer'],'receipt':{'kind':'observation','status':'PASS','expected':'Protected actual runtime interaction',
            'actual':source['capability_evidence'],'source_ref':source['source_ref']},'checked_at':acceptance.now()}
    for item in criteria['items']:
        for category in item['categories']:
            acceptance.add(record,item['id'],category,source['observer'],{'kind':'observation','status':'PASS',
                'expected':item['description'],'actual':source['observations'][item['id']]+(' Exact source independently reviewed.' if category=='code' else ''),
                'source_ref':source['source_ref']})
    result=acceptance.assess(record,root,criteria)
    record['verdict']=result['verdict'];record['assessed_at']=acceptance.now()
    acceptance.save(root/'.cache/current.json',record)
    print(json.dumps(result))
    return 0 if result['verdict']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
