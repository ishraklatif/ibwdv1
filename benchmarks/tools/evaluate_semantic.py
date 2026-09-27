"""Explicit local-model screening. Never downloads models or contacts providers.

Run only when a user authorizes model/resource evaluation. A frozen source/doc
snapshot excludes this runner, labels and result reports from the retrieval corpus.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time


def memory():
    factor = 1 if sys.platform == 'darwin' else 1024
    return dict(parent_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * factor,
                worker_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * factor)


def operation(args):
    from ibwd.retrieval.context import context
    from ibwd.retrieval.semantic import build
    from ibwd.retrieval.bounded import encoded_size
    root = Path(args.repo).resolve()
    started = time.perf_counter()
    if args.operation == 'build':
        result = build(root, args.model, 768, timeout=600, query_timeout=30)
        return dict(result=result, wall_seconds=time.perf_counter() - started, **memory())
    case = json.loads(Path(args.cases).read_text())['cases'][args.case]
    measurements = []
    for repeat in range(2):
        started = time.perf_counter()
        packet = context(root, case['query'], scopes=['source', 'doc'], semantic=args.operation == 'semantic',
                         budget_tokens=8000, max_bytes=32768)
        top = packet['items'][:5]
        hits = set(case['relevant']) & {i['file'] for i in top}
        measurements.append(dict(repeat=repeat, wall_seconds=time.perf_counter() - started,
            recall_at_5=len(hits) / len(case['relevant']),
            exact_first=(top[0].get('symbol_id') == case['symbol']) if top and 'symbol' in case else None,
            top5=[dict(file=i['file'], line=i['line'], end_line=i['end_line'], reason=i['reason']) for i in top],
            semantic=packet.get('semantic'), packet_budget_bytes=encoded_size(packet),
            packet_json_bytes=len(json.dumps(packet, ensure_ascii=False, separators=(',', ':')).encode()),
            returned_items=len(packet['items']), **memory()))
    return dict(id=case['id'], category=case['category'], measurements=measurements)


def child(args, operation_name, case=None):
    command = [sys.executable, str(Path(__file__).resolve()), '--operation', operation_name,
               '--repo', args.repo, '--cases', args.cases, '--model', args.model]
    if case is not None:
        command += ['--case', str(case)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=750)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return json.loads(result.stdout)


def evaluate(args):
    cases_path = Path(args.cases)
    cases = json.loads(cases_path.read_text())['cases']
    report = dict(cases_sha256=hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                  platform=platform.platform(), python=sys.version,
                  versions={name: importlib.metadata.version(name) for name in ('sentence-transformers', 'transformers', 'torch')},
                  metric='Relevant-file recall in first five emitted evidence items; 32000-byte estimated MCP budget',
                  memory_method='getrusage peak RSS, parent and worker separately; not simultaneous process-tree peak',
                  latency_method='Each case/mode gets a fresh parent; two repetitions. Workers reload weights every query. Repeated latency is OS-cache warm, not resident-model warm.',
                  cases=[])
    manifest = Path(args.repo).parent / 'corpus-manifest.json'
    if manifest.is_file():
        report['corpus_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        report['corpus_files'] = len(json.loads(manifest.read_text()))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(report, indent=2) + '\n')
    for label in ('initial_build', 'noop_build'):
        report[label] = child(args, 'build')
        save()
        print(label, report[label]['wall_seconds'], flush=True)
    for number, case in enumerate(cases):
        modes = {}
        # Alternate order to reduce systematic OS-cache ordering bias.
        for mode in (('baseline', 'semantic') if number % 2 == 0 else ('semantic', 'baseline')):
            modes[mode] = child(args, mode, number)
        report['cases'].append(dict(id=case['id'], category=case['category'], **modes))
        save()
        print(case['id'], {m: round(modes[m]['measurements'][0]['recall_at_5'], 3) for m in modes}, flush=True)
    report['summary'] = {}
    for category in ('exact', 'synonym', 'behavior', 'documentation', 'vague', 'all'):
        selected = [c for c in report['cases'] if category == 'all' or c['category'] == category
                    or category == 'vague' and c['category'] in ('synonym', 'behavior')]
        report['summary'][category] = {mode: statistics.mean(c[mode]['measurements'][0]['recall_at_5'] for c in selected)
                                        for mode in ('baseline', 'semantic')}
    report['semantic_fallbacks'] = sum(m['semantic']['status'] != 'ready' for c in report['cases'] for m in c['semantic']['measurements'])
    report['exact_preserved'] = all(c['semantic']['measurements'][0]['exact_first'] for c in report['cases'] if c['category'] == 'exact')
    gain = report['summary']['vague']['semantic'] - report['summary']['vague']['baseline']
    report['screening_quality_gate_passed'] = gain >= 0.1 and report['exact_preserved'] and report['semantic_fallbacks'] == 0
    report['resources'] = {}
    for mode in ('baseline', 'semantic'):
        values = [m for c in report['cases'] for m in c[mode]['measurements']]
        times = sorted(m['wall_seconds'] for m in values)
        report['resources'][mode] = dict(
            median_seconds=statistics.median(times), p95_seconds=times[math.ceil(.95 * len(times)) - 1],
            first_request_median_seconds=statistics.median(c[mode]['measurements'][0]['wall_seconds'] for c in report['cases']),
            repeated_request_median_seconds=statistics.median(c[mode]['measurements'][1]['wall_seconds'] for c in report['cases']),
            max_parent_peak_rss_bytes=max(m['parent_peak_rss_bytes'] for m in values),
            max_worker_peak_rss_bytes=max(m['worker_peak_rss_bytes'] for m in values),
            median_packet_budget_bytes=statistics.median(m['packet_budget_bytes'] for m in values),
            max_packet_budget_bytes=max(m['packet_budget_bytes'] for m in values))
    save()
    print(json.dumps(report['summary'], indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output')
    parser.add_argument('--operation', choices=['build', 'baseline', 'semantic'])
    parser.add_argument('--case', type=int)
    args = parser.parse_args()
    if args.operation:
        print(json.dumps(operation(args)))
    else:
        if not args.output:
            parser.error('--output is required for evaluation')
        evaluate(args)
