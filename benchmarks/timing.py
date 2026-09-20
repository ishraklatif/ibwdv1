"""Full scan vs. touch-one-file-and-rescan timing (Sprint 4 DoD: one-file rescan >= 3-4x faster).

Usage:
  uv run python benchmarks/timing.py                    # synthetic repos of 100 / 400 / 1200 files
  uv run python benchmarks/timing.py --sizes 200 2000   # custom synthetic sizes
  uv run python benchmarks/timing.py --repo /path/to/repo   # a real repo (copied to a temp dir first)

Each measurement is the MEDIAN of --runs repetitions (default 5); best-of-N claims are withdrawn. Real repos are copied so
the scan's .ibwd/ directory and the touched file never land in your checkout.
"""

from __future__ import annotations

import argparse
import random
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from ibwd.scan import run_scan

TARGET_RATIO = 3.0
RECORDS: list[dict] = []


def make_synthetic_repo(root: Path, n_files: int, seed: int = 1) -> None:
    """Deterministic Python repo: packages of modules with cross-module imports and calls."""
    rng = random.Random(seed)
    for i in range(n_files):
        pkg = root / f"pkg{i % 20}"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").touch()
        lines: list[str] = []
        for j in rng.sample(range(n_files), min(3, n_files)):
            if j != i:
                lines.append(f"from pkg{j % 20}.mod{j} import fn{j}_0")
        for k in range(8):
            callee = rng.randrange(n_files)
            call = f"    return fn{callee}_0()" if callee != i and k == 0 else "    return 1"
            lines.append(f"def fn{i}_{k}():\n{call}")
        lines.append(f"class C{i}:\n    def m(self):\n        return fn{i}_0()")
        lines.append(f"def entry{i}():\n    return fn{i}_0() + fn{i}_1()")
        (pkg / f"mod{i}.py").write_text("\n\n".join(lines) + "\n")


def timed(fn, runs: int = 1) -> float:
    """Median wall time of `runs` repetitions."""
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def measure(root: Path, touch: Path, label: str, runs: int) -> float:
    full = timed(lambda: (shutil.rmtree(root / ".ibwd", ignore_errors=True), run_scan(root)), runs)
    idle = timed(lambda: run_scan(root), runs)

    def touch_and_rescan():
        touch.write_text(touch.read_text() + "\n# touched\n")
        run_scan(root)

    one = timed(touch_and_rescan, runs)
    ratio = full / one
    verdict = "PASS" if ratio >= TARGET_RATIO else "FAIL"
    print(f"{label:22} median of {runs}: full {full:6.2f}s | idle rescan {idle:5.2f}s | one-file rescan {one:5.2f}s | {ratio:5.2f}x  {verdict} (>= {TARGET_RATIO}x)")
    RECORDS.append({"repo": label, "repetitions": runs, "statistic": "median", "full_scan_s": round(full, 4), "idle_rescan_s": round(idle, 4),
                    "one_file_rescan_s": round(one, 4), "ratio": round(ratio, 2), "passes_3x": ratio >= TARGET_RATIO})
    return ratio


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="*", default=[100, 400, 1200])
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json-out", type=Path, help="append the result records to this JSON file")
    args = ap.parse_args()

    ratios: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:
        if args.repo:
            root = Path(tmp) / "repo"
            shutil.copytree(args.repo, root, symlinks=True, ignore=shutil.ignore_patterns(".git", ".ibwd", "node_modules", ".venv"))
            candidates = sorted(p for p in root.rglob("*") if p.suffix in (".py", ".ts", ".tsx", ".js") and p.is_file())
            if not candidates:
                print("no source files found")
                return 2
            ratios.append(measure(root, candidates[len(candidates) // 2], args.repo.name, args.runs))
        else:
            for n in args.sizes:
                root = Path(tmp) / f"synth{n}"
                make_synthetic_repo(root, n)
                ratios.append(measure(root, root / "pkg3" / "mod3.py", f"synthetic {n} files", args.runs))
    if args.json_out:
        existing = json.loads(args.json_out.read_text()) if args.json_out.exists() else []
        args.json_out.write_text(json.dumps(existing + RECORDS, indent=1) + "\n")
    return 0 if all(r >= TARGET_RATIO for r in ratios) else 1


if __name__ == "__main__":
    sys.exit(main())
