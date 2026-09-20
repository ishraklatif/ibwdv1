Sprint 3 Benchmark Protocol — Call Graph & Dependencies

Objective: Validate that ibwd_callers, ibwd_dependents, and ibwd_find_symbol reduce token cost by ≥3× vs. a Grep-only baseline on call-graph and dependency questions, across three real repositories, without sacrificing correctness.

Deliverable: benchmarks/sprint_3_results.csv populated with 54 rows (3 repos × 3 question types × 2 conditions × 3 repeats), plus benchmarks/sprint_3_analysis.md with the go/no-go verdict.

0. Preconditions

Verify all of the following before starting. If any fails, stop and report.

□ IBWD is installed and ibwd scan runs without error on the IBWD repo itself.
□ claude mcp list shows ibwd connected.
□ The three test repos are cloned as sibling directories:
text
~/ibwd-test-corpus/
├── jonga/
├── typescript-call-graph/
└── msoc/
□ Each repo is checked out to a pinned commit and the SHA is recorded (see §1).
□ Python 3.11+, uv, and claude CLI are on PATH.
1. Pin each repo to a known SHA

Run this for each repo and write the SHAs into benchmarks/sprint_3_repos.lock:

bash
cd ~/ibwd-test-corpus/jonga
git rev-parse HEAD > /tmp/jonga.sha
git log -1 --format='%H %ci %s' >> /tmp/jonga.sha

cd ~/ibwd-test-corpus/typescript-call-graph
git rev-parse HEAD > /tmp/tscg.sha
git log -1 --format='%H %ci %s' >> /tmp/tscg.sha

cd ~/ibwd-test-corpus/msoc
git rev-parse HEAD > /tmp/msoc.sha
git log -1 --format='%H %ci %s' >> /tmp/msoc.sha

cat /tmp/jonga.sha /tmp/tscg.sha /tmp/msoc.sha > benchmarks/sprint_3_repos.lock
Why: Without a pinned SHA, a git pull mid-benchmark invalidates your ground truth and your IBWD graph simultaneously. Record the SHA in every CSV row so results are reproducible.

2. Establish ground truth per repo

For each repo, do this in order. Do not proceed to §3 until all three ground-truth files exist and you have eyeballed them.

2.1 jonga — independent ground truth via its own call graph

Jonga generates call graphs from Python source. Use it on itself.

bash
cd ~/ibwd-test-corpus/jonga
# Inspect README for the exact invocation, but it's typically:
python -m jonga --output /tmp/jonga-self-graph.dot jonga/
# Or, if the package uses a different entry point:
python jonga/__main__.py jonga/ > /tmp/jonga-self-graph.dot
Save the output to benchmarks/ground_truth/jonga_callgraph.txt (or .dot if that's what it emits — convert with grep if needed).

Then, independently, extract inheritance:

bash
cd ~/ibwd-test-corpus/jonga
grep -rn "class .*(.*):" jonga/ > benchmarks/ground_truth/jonga_inherits.txt
Now pick 3 test functions from jonga's source with these properties:

Q1 — Direct callers: a function that is called from ≥3 distinct files. Verify by scanning the dot output or grepping for the function name. Good candidates: helper functions in jonga/jonga.py that the CLI calls.
Q2 — Dependencies: a function that calls ≥3 other functions across module boundaries. Prefer one near the top of the call stack (an orchestrator).
Q3 — Chain trace: two functions A and B where A transitively calls B through exactly 2 intermediate functions. This is the hardest to find — trace by hand if needed.
Write the answers into benchmarks/ground_truth/jonga.yaml:

yaml
repo: jonga
sha: <paste from sprint_3_repos.lock>
q1:
  question: "What calls <function_name>?"
  answer_callers:
    - {file: jonga/jonga.py, line: 123}
    - {file: jonga/utils.py, line: 45}
    - {file: jonga/cli.py, line: 88}
q2:
  question: "What does <function_name> depend on / call?"
  answer_dependencies:
    - {file: jonga/parser.py, line: 12, name: parse_module}
    - ...
q3:
  question: "Trace the call chain from <A> to <B>."
  answer_chain: [A, <intermediate1>, <intermediate2>, B]
  verified_by: "hand-traced by reading the 4 files at the listed lines"
2.2 typescript-call-graph — independent ground truth via its own tool

Same pattern, different language.

bash
cd ~/ibwd-test-corpus/typescript-call-graph
# Its README documents running it on a directory. Adapt:
npx ts-node src/index.ts . > /tmp/tscg-self-graph.txt
# Or its compiled entry point, depending on build setup.
Save to benchmarks/ground_truth/tscg_callgraph.txt.

If the tool won't run cleanly (it's unmaintained — this is a real possibility), fall back to manual ground truth: pick 3 functions, grep for their names across .ts files, and read each hit to confirm it's a real call site (not a definition, comment, or string). Note verified_by: manual-grep-and-read in the YAML.

Write benchmarks/ground_truth/tscg.yaml in the same shape as jonga's.

2.3 msoc — manual ground truth from codeScanner/

MSoC is a student project; its own tooling may not run cleanly. Skip trying to run it and go straight to manual ground truth, scoped to codeScanner/ only (ignore the frontend).

bash
cd ~/ibwd-test-corpus/msoc
# Confirm the scanner directory exists and has JS/TS source
ls codeScanner/
Pick 3 functions inside codeScanner/ using the same Q1/Q2/Q3 criteria. Verify by grep + reading. Write benchmarks/ground_truth/msoc.yaml.

If codeScanner/ contains fewer than 10 functions, note that in the YAML and pick the best 3 available — don't force artificial questions.

2.4 Sanity gate

Before moving on, print all three YAML files and confirm:

Every answer cites a real file:line you have opened and read.
Q3's chain has exactly the hop count you claim (don't accidentally count a false hop).
No answer was copied from an LLM's guess — you verified it.
3. Build the task file

Generate benchmarks/tasks.yaml from the three ground-truth YAMLs:

yaml
- id: jonga_q1
  repo: jonga
  repo_sha: <sha>
  question: "What calls <function_name>?"
  task_type: structural
  expected:
    callers: [...]
  ground_truth_source: ground_truth/jonga.yaml#q1

- id: jonga_q2
  ... (same pattern)
- id: jonga_q3
  ...
- id: tscg_q1
  ...
- id: msoc_q1
  ...
# 9 tasks total
4. Run the benchmark

Write benchmarks/run_sprint_3.py that does the following. Do not run tasks interactively by hand — the whole point is reproducibility.

4.1 Setup per (repo, condition)

python
def setup_repo(repo_path):
    """Clear any existing IBWD graph so results are independent of prior runs."""
    subprocess.run(["rm", "-rf", f"{repo_path}/.ibwd"], check=True)
    subprocess.run(["ibwd", "scan"], cwd=repo_path, check=True)
4.2 Invoke Claude Code headlessly

Claude Code supports non-interactive invocation. Use -p (print mode) with --allowedTools and capture tokens from the output. Example:

python
def run_claude(repo_path, question, allowed_tools, condition, repeat_idx):
    cmd = [
        "claude",
        "-p", question,
        "--allowedTools", allowed_tools,
        "--output-format", "json",   # verify this flag name against `claude --help`
    ]
    result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=600)
    data = json.loads(result.stdout)
    return {
        "repo": os.path.basename(repo_path),
        "question": question,
        "condition": condition,
        "repeat": repeat_idx,
        "tokens_in": data.get("usage", {}).get("input_tokens"),
        "tokens_out": data.get("usage", {}).get("output_tokens"),
        "tool_calls": data.get("num_tool_calls"),
        "answer_text": data.get("result", ""),
        "exit_code": result.returncode,
    }
Verify the exact CLI flags first by running claude --help and adjusting. If --output-format json isn't available in your version, fall back to parsing the --verbose output or use claude -p ... | tee and extract tokens from the printed cost report.

4.3 Conditions

Baseline: allowed_tools = "Read,Glob,Grep"
IBWD: allowed_tools = "Read,Glob,Grep,mcp__ibwd__ibwd_callers,mcp__ibwd__ibwd_dependents,mcp__ibwd__ibwd_find_symbol"
4.4 Repeats

Run each (task, condition) 3 times to smooth out LLM nondeterminism. Use the median for the headline number, but log all 3.

4.5 Correctness grading

After each run, grade the answer against the ground truth in the task's expected field. Two options:

Option A (preferred): deterministic grader. Write a small function that checks the answer text for the expected file:line pairs. For ibwd_callers, require all expected callers to appear (recall) and flag any extra callers not in the ground truth (precision).

python
def grade_callers(answer_text, expected_callers):
    hits = 0
    for c in expected_callers:
        pattern = re.escape(c["file"]) + r".*?" + str(c["line"])
        if re.search(pattern, answer_text):
            hits += 1
    recall = hits / len(expected_callers)
    return {"recall": recall, "correct": recall >= 0.9}
Option B: rubric. If the answer is prose and hard to parse, hand-grade with a 0/1 correct flag. Do this only if Option A repeatedly produces false negatives.

4.6 Output

Write every row to benchmarks/sprint_3_results.csv with these columns:

text
repo,repo_sha,task_id,condition,repeat,tokens_in,tokens_out,tool_calls,correct,notes
5. Analysis

Write benchmarks/sprint_3_analysis.md computing:

5.1 Headline (aggregate across all 9 tasks)

For each condition, sum tokens_in + tokens_out across repeats and take the median per (task, condition). Then compute:

text
median_baseline_tokens = median over 9 tasks of (median of 3 repeats)
median_ibwd_tokens     = same, IBWD condition
token_ratio            = median_baseline_tokens / median_ibwd_tokens
Go/no-go threshold: token_ratio >= 3.0 AND ibwd_correct_rate >= baseline_correct_rate.

5.2 Per-repo breakdown

Same computation, split by repo. Expected outcome:

jonga: strongest win (Python, clean structure, independent ground truth).
tscg: medium win (TypeScript path, ground truth may be manual).
msoc: weakest win (student project, possibly fewer functions, manual ground truth).
If any repo shows token_ratio < 1.0 (IBWD is worse), that's a red flag — report it loudly.

5.3 Per-question-type breakdown

Q1 (direct callers): expected 2–4×.
Q2 (dependencies): expected 2–4×.
Q3 (chain trace): expected 4–8× — this is where grep-tracing is most expensive.
If Q3's ratio is not the highest, your recursive CTE traversal has a bug. Investigate retrieval/traversal.py and the depth-2 test from Sprint 3's definition of done.

5.4 Failure-mode inventory

For each incorrect IBWD answer, categorize:

Missing edge — a caller in ground truth isn't in IBWD's output. Likely cause: cascade tier too strict, or the caller uses dynamic dispatch (getattr, importlib) tree-sitter can't see.
Spurious edge — IBWD reports a caller not in ground truth. Likely cause: tier-5 fuzzy matching too permissive.
Truncated output — the answer is right but cut off by depth or result limits.
Count each category. If missing edges ≥ 2, Sprint 3 is not done — debug before Sprint 4.

6. Verdict

Append to benchmarks/sprint_3_analysis.md:

markdown
## Verdict

- Headline token ratio: X.X×  (threshold: ≥3.0×)
- Correctness: IBWD X%, baseline Y%
- **Decision:** [PROCEED to Sprint 4 | STOP and fix <specific issue>]
- Top failure mode: <category> (<count> occurrences)
- Recommended fix before Sprint 4: <one concrete change>
Be honest. A 2.8× result with clean correctness is a better signal than a 3.2× result with 3 missing edges. The plan's explicit instruction is: if Sprint 3 doesn't hit ≥3×, stop and debug the resolution cascade before Sprint 4.

7. Cleanup

After the CSV and analysis are written:

bash
# Keep the corpus around for Sprint 4 (impact analysis reuses the same repos)
# but clear per-repo .ibwd state so Sprint 4 starts fresh
rm -rf ~/ibwd-test-corpus/*/.ibwd
Do not delete ~/ibwd-test-corpus/ — Sprint 4's ibwd_impact demo reuses these exact repos.

Appendix — Things that will probably go wrong

jonga won't run on itself due to pygraphviz import errors. Workaround: install graphviz system package (brew install graphviz / apt install graphviz), or fall back to manual ground truth and note it.
claude --output-format json doesn't exist in your CLI version. Check claude --help; the flag may be --print-json or you may need to parse the standard output.
Token counts in claude -p mode are per-session, not per-turn. If the model makes multiple tool-call round trips, the JSON output should still aggregate them — verify against claude --help docs.
ibwd_callers returns too many results and Claude reads them all anyway. If this happens, add a limit param to the MCP tool before running the benchmark — otherwise you're measuring a strawman.
MSoC's codeScanner/ has <10 functions. Pick whatever's there, note the small-N caveat in the analysis, and don't over-interpret msoc's numbers.
Nondeterminism between repeats exceeds 30%. If repeat 1 and repeat 3 differ wildly, the question is too open-ended — tighten it to a specific function name and re-run.