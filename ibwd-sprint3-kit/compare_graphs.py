#!/usr/bin/env python3
"""Compare normalized oracle/IBWD graphs; per relation and resolution tier.
Symbol IDs MUST be canonicalized identically before comparison (file, qualname, kind).
python compare_graphs.py oracle.json ibwd.json > comparison.json
"""
import json,sys,collections
from pathlib import Path
o,b=[json.loads(Path(p).read_text()) for p in sys.argv[1:3]]
def key(e):return (e['source'],e['target'],e['relation'],e['file'],e['line'])
O={key(e) for e in o['edges']};B={key(e) for e in b['edges']}
def scores(expected,actual):
 tp=len(expected&actual);return {'tp':tp,'fp':len(actual-expected),'fn':len(expected-actual),'precision':tp/len(actual) if actual else None,'recall':tp/len(expected) if expected else None}
relations=sorted({e['relation'] for e in o['edges']+b['edges']});tiers={}
for tier in sorted({str(e.get('tier','unknown')) for e in b['edges']}):
 actual={key(e) for e in b['edges'] if str(e.get('tier','unknown'))==tier}
 tiers[tier]={'predictions':len(actual),'precision':len(actual&O)/len(actual) if actual else None,'note':'Recall by prediction tier is not identifiable for missing edges; prelabel oracle strata separately.'}
uses={'CALLS','REFERENCES'};oracle_used={e['target'] for e in o['edges'] if e['relation'] in uses};ibwd_used={e['target'] for e in b['edges'] if e['relation'] in uses}
print(json.dumps({'overall':scores(O,B),'by_relation':{r:scores({x for x in O if x[2]==r},{x for x in B if x[2]==r}) for r in relations},'by_tier':tiers,'missing':sorted(O-B),'spurious':sorted(B-O),'false_no_static_use':sorted(oracle_used-ibwd_used),'oracle_complete':o.get('complete',False)},indent=2))
