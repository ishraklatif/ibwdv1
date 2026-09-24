"""No-model, temporary-fixture performance profile; prints JSON, never alters a repo."""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time


def worker(root, mode):
    from ibwd.mcp.server import ibwd_find_symbol
    os.chdir(root)
    if mode != 'cold':
        ibwd_find_symbol('function_50', response_version=2)
    if mode == 'refresh':
        (root / 'file50.py').write_text('def function_50():\n    return 51\n')
    start = time.perf_counter()
    ibwd_find_symbol('function_50', response_version=2)
    elapsed = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == 'darwin' else 1024)
    return {'seconds': elapsed, 'peak_rss_mib': peak}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--mode', default='cold')
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.mode)))
        return
    samples = {mode: [] for mode in ('cold', 'warm', 'refresh', 'simultaneous')}
    for _ in range(5):
        with tempfile.TemporaryDirectory(prefix='ibwd-3b-') as temp:
            root = Path(temp)
            for n in range(100):
                (root / f'file{n}.py').write_text(f'def function_{n}():\n    return {n}\n')
            command = [sys.executable, str(Path(__file__).resolve()), '--worker', str(root)]
            for mode in ('cold', 'warm', 'refresh'):
                started = time.perf_counter()
                result = subprocess.run(command + ['--mode', mode], capture_output=True, text=True, check=True)
                row = json.loads(result.stdout)
                if mode == 'cold':
                    row['query_seconds'] = row['seconds']
                    row['seconds'] = time.perf_counter() - started
                samples[mode].append(row)
            start = time.perf_counter()
            processes = [subprocess.Popen(command + ['--mode', 'warm'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
            values = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                if process.returncode:
                    raise RuntimeError(stderr)
                values.append(json.loads(stdout))
            samples['simultaneous'].append({'seconds': time.perf_counter()-start,
                                             'peak_rss_mib': max(v['peak_rss_mib'] for v in values)})
    limits = {'cold': 10, 'warm': 2, 'refresh': 10, 'simultaneous': 15}
    summary = {}
    for mode, rows in samples.items():
        seconds = sorted(r['seconds'] for r in rows)
        peak = max(r['peak_rss_mib'] for r in rows)
        summary[mode] = {'median_seconds': statistics.median(seconds), 'p95_seconds': seconds[-1],
                         'peak_rss_mib': peak, 'passed': seconds[-1] <= limits[mode] and peak <= 512}
    print(json.dumps({'profile': '100-file-small-fixture-v1', 'system': platform.system(), 'architecture': platform.machine(),
                      'python': platform.python_version(), 'repetitions': 5, 'summary': summary, 'samples': samples}, indent=2))


if __name__ == '__main__':
    main()
