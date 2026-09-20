#!/usr/bin/env python3
"""Read-only corpus preflight. Unknown evidence never becomes a pass.
Usage: python verify_repo.py REPO --roots sphinx --out audit.json [--edges oracle.json]
The edge file uses {symbols:[{id,file,line,name}], edges:[{source,target,relation,file,line}], complete:bool}.
"""
import argparse, collections, datetime as dt, hashlib, json, pathlib, re, shutil, subprocess, tempfile
EXT={'.py','.js','.jsx','.ts','.tsx'}
TEST={'tests','test','t','__tests__','__mocks__','e2e','tests_typing','fixtures','testdata'}
EXCLUDED={'docs','doc','examples','example','node_modules','vendor','vendored','dist','build','generated','__generated__','.git'}
def run(root,*args):
 return subprocess.check_output(args,cwd=root,text=True,stderr=subprocess.PIPE).strip()
def classify(f,roots):
 p=pathlib.PurePosixPath(f); parts=set(p.parts); n=p.name
 if p.suffix not in EXT:return 'other'
 if parts&TEST or re.search(r'(^test_|_test\.py$|\.(test|spec|stories)\.[^.]+$)',n) or n=='conftest.py':return 'test'
 if parts&EXCLUDED or n.endswith(('.d.ts','.min.js')):return 'excluded'
 if not any(f==r or f.startswith(r.rstrip('/')+'/') for r in roots):return 'outside_roots'
 return 'production'
def structural(data,production,root):
 syms={s['id']:s for s in data['symbols'] if s['file'] in production}
 edges=[]; invalid=[]
 for e in data['edges']:
  if e['relation']!='CALLS' or e['source'] not in syms or e['target'] not in syms:continue
  try:
   lines=(root/e['file']).read_text().splitlines()
   if e['file'] not in production or not 1<=e['line']<=len(lines):raise ValueError('invalid callsite')
  except Exception as ex:invalid.append(str(ex));continue
  edges.append(e)
 incoming=collections.defaultdict(set);outgoing=collections.defaultdict(set);adj=collections.defaultdict(set)
 for e in edges:
  a,b=syms[e['source']],syms[e['target']]
  incoming[b['id']].add(a['file']);adj[a['id']].add(b['id'])
  if a['file']!=b['file']:outgoing[a['id']].add(b['id'])
 q1=[syms[s] for s,v in incoming.items() if len(v)>=3]
 q2=[syms[s] for s,v in outgoing.items() if len(v)>=3]
 chains=[]
 for a in sorted(adj):
  for b in sorted(adj[a]):
   for c in sorted(adj[b]):
    for d in sorted(adj[c]):
     if len({a,b,c,d})==4 and len({syms[x]['file'] for x in [a,b,c,d]})>=2:
      chains.append([a,b,c,d]);break
    if chains and chains[-1][0]==a:break
   if chains and chains[-1][0]==a:break
  if len(chains)>=20:break
 return {'complete_oracle':data.get('complete',False),'invalid_evidence':invalid,'q1_candidates':q1,'q2_candidates':q2,'q3_chains':chains,'three_q1':len(q1)>=3,'three_q2':len(q2)>=3,'three_hop_chain':bool(chains),'note':'Q3 has exactly three edges; does not assert unique or shortest path. Evidence must be independently reviewed.'}
def audit(root,roots,edge_path=None):
 root=root.resolve(); tracked=run(root,'git','ls-files','-z').split('\0');tracked=[f for f in tracked if f]
 groups=collections.defaultdict(list)
 for f in tracked:groups[classify(f,roots)].append(f)
 modes=run(root,'git','ls-files','-s').splitlines()
 symlinks=[x.split('\t',1)[1] for x in modes if x.startswith('120000 ')]
 suspects=[]
 for f in tracked:
  p=pathlib.PurePosixPath(f)
  if set(p.parts)&{'node_modules','vendor','vendored','dist','build','generated','__generated__'} or p.name.endswith('.min.js'):suspects.append(f)
  if p.suffix=='.js' and str(p.with_suffix('.ts')) in tracked:suspects.append(f)
 generated=[]; longlines=[]
 for f in groups['production']:
  p=root/f
  if p.is_symlink():continue
  try:t=p.read_text(encoding='utf-8')
  except UnicodeError:continue
  if re.search(r'(?im)^.{0,15}(?:@generated|auto[- ]generated|generated (?:file|by)|do not edit)',t[:3000]):generated.append(f)
  if any(len(x)>1000 for x in t.splitlines()):longlines.append(f)
 licenses=[f for f in tracked if '/' not in f and re.match(r'(?i)(license|copying)',f)]
 sha=run(root,'git','rev-parse','HEAD');date=run(root,'git','show','-s','--format=%cI','HEAD')
 age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(date)).days
 out={'repo':root.name,'url':run(root,'git','remote','get-url','origin'),'repo_sha':sha,'roots':roots,'commit_count':int(run(root,'git','rev-list','--count','HEAD')),'nonmerge_commits':int(run(root,'git','rev-list','--count','--no-merges','HEAD')),'shallow':run(root,'git','rev-parse','--is-shallow-repository')=='true','clean':not run(root,'git','status','--porcelain'),'last_commit':date,'age_days':age,'active_180_days':0<=age<=180,'counts':{k:len(v) for k,v in groups.items()},'production_files':groups['production'],'test_files':groups['test'],'symlinks':symlinks,'artifact_suspects':sorted(set(suspects)),'generated_header_suspects':generated,'long_line_review':longlines,'license_files':{f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in licenses},'cloc':None,'semantic_structure':None,'checks_requiring_review':['permissive license/SPDX from license text','artifact-suspect adjudication','manual file:line evidence','oracle completeness and version','mostly static resolvable calls','true-negative candidate (no safety proof)','test command and runtime coverage']}
 cloc=shutil.which('cloc')
 if cloc:
  with tempfile.NamedTemporaryFile(mode='w',suffix='.txt') as listing:
   listing.write('\n'.join(str(root/f) for f in groups['production'] if f not in symlinks));listing.flush()
   try:raw=run(root,cloc,'--json','--quiet','--skip-uniqueness','--list-file='+listing.name); out['cloc'],end=json.JSONDecoder().raw_decode(raw); out['cloc_trailing_output']=raw[end:].strip()
   except Exception as e:out['cloc_error']=str(e)
 out['size_pass']=100<=len(groups['production'])<=600
 out['history_pass']=out['nonmerge_commits']>=200 and not out['shallow']
 if edge_path:out['semantic_structure']=structural(json.loads(pathlib.Path(edge_path).read_text()),set(groups['production']),root)
 out['admission']='UNVERIFIED' # no claim of automatic semantic or licence proof
 return out
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('repo',type=pathlib.Path);p.add_argument('--roots',nargs='+',required=True);p.add_argument('--edges');p.add_argument('--out',required=True);a=p.parse_args()
 result=audit(a.repo,a.roots,a.edges);pathlib.Path(a.out).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['repo','repo_sha','counts','commit_count','size_pass','admission']}))
