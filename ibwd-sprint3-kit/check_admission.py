#!/usr/bin/env python3
"""Fail-closed admission using measured audit + reviewed semantic evidence.
python check_admission.py audit.json review.json
Unmeasured or unreviewed conditions stay UNKNOWN, not PASS.
"""
import json,sys
from pathlib import Path
a=json.loads(Path(sys.argv[1]).read_text());r=json.loads(Path(sys.argv[2]).read_text());s=a.get('semantic_structure') or {};checks={}
def put(name,value):checks[name]='UNKNOWN' if value is None else 'PASS' if value else 'FAIL'
put('pinned_sha_matches_review',r.get('repo_sha')==a['repo_sha'] if r.get('repo_sha') else None)
put('100_to_600_production_files',a['size_pass']);put('full_history_200_nonmerge',a['history_pass']);put('clean_checkout',a['clean']);put('active_last_180_days',a['active_180_days'])
for name in ['public_github','permissive_license','production_classification_reviewed','no_committed_dependencies_or_generated_code','conventional_test_names','test_scope_separable','oracle_available','oracle_complete_for_scope','all_task_evidence_opened','widely_used_function_verified','no_static_use_candidate_reviewed','hierarchy_or_role_verified']:
 put(name,r.get(name))
complete=r.get('oracle_complete_for_scope') is True and r.get('all_task_evidence_opened') is True
for key in ['three_q1','three_q2','three_hop_chain']:put(key,s.get(key) if complete else None)
put('mostly_static_calls',r.get('static_resolved_fraction',0)>=0.90 if r.get('static_resolved_fraction') is not None else None)
if r.get('negative_control') is True:checks['mostly_static_calls']='DIAGNOSTIC_EXCEPTION'
put('timing_benchmark_recorded',r.get('timing_benchmark_recorded'))
# Optional additional source/content checks must be attached to review; none are guessed.
verdict='PASS' if all(v in {'PASS','DIAGNOSTIC_EXCEPTION'} for v in checks.values()) else 'STOP'
print(json.dumps({'repo':a['repo'],'repo_sha':a['repo_sha'],'checks':checks,'admission':verdict},indent=2));sys.exit(0 if verdict=='PASS' else 2)
