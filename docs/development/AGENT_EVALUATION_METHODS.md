# Agent evaluation methods for IBWD

This guide describes ways to evaluate how coding agents use IBWD. These are
evaluation methods, not claims that the current usage report or test suite
already measures them. Start with deterministic, offline tests and a small set
of representative tasks; expand only when the results answer a real question.

## 1. Schema validation

Validate each proposed tool call against the tool's input schema before
execution. Check argument names, types, required fields, allowed values, and
constraints such as valid line ranges. This catches malformed calls
independently of whether their intent is correct.

For example, an `ibwd_read` case can require a path, current expected hash, and
a valid inclusive line range. Record schema pass rate as valid calls divided by
all proposed calls. Also report which constraint failed; a single aggregate
score is hard to act on.

## 2. Exact argument matching

For each test request, define the expected tool and argument values. Compare
arguments individually, rather than treating the whole call as one opaque
string. Exact matching is appropriate for stable values such as a known
repository-relative path, symbol ID, limit, or expected hash.

Report exact-call accuracy and per-argument accuracy. Keep this strict for
identifiers: changing a hash or symbol ID can change the meaning of a call.
Do not require one exact call sequence when several valid strategies exist.

## 3. Semantic argument matching

Some free-text arguments can be equivalent despite formatting differences.
Normalize predictable variation first, such as whitespace, path separators,
case where the field is case-insensitive, or equivalent date and unit formats.
Compare identifiers and case-sensitive paths exactly.

If normalization cannot resolve a free-text field, an equivalence judge may be
used for that field alone, with a documented rubric and human-reviewed sample.
Do not use a semantic judge to excuse invented paths, IDs, or other values that
should have come from evidence.

## 4. Grounding checks

Trace every argument to either the user's request or a prior tool result.
Flag values with no such source, including invented paths, symbol names,
hashes, filters, and line ranges. A simple test fixture can provide the allowed
facts and expected provenance for each argument.

Report the fraction of arguments grounded and the count of unsupported values.
Treat unsupported repository identifiers as a correctness issue even if the
tool happens to return something plausible.

## 5. Execution verification

Execute valid calls in a disposable fixture repository and record whether they
complete. Then check the returned structure and outcome; a successful exit
alone does not show that the right evidence was returned.

Fixtures should make expected results explicit: a file query returns the
expected paths, a symbol query returns the expected identity, and a read
returns the intended source span for its hash. Record execution success
separately from outcome correctness so transport errors and wrong results are
not conflated.

## 6. Retrieval relevance evaluation

Create queries with a reviewed set of relevant files, symbols, edges, or source
spans. Compare returned evidence with this reference set. Precision measures
how much of the returned evidence is relevant; recall measures how much of the
expected evidence was found. Also track ranking quality when the order of
results matters, and truncation so missing items are not mistaken for
irrelevance.

Use separate query sets for lexical retrieval and optional semantic fusion.
Keep expected evidence versioned with the fixture because source changes can
make old labels stale. This evaluates IBWD retrieval quality, not whether the
agent selected the right tool.

## 7. Multi-step trajectory scoring

Represent a task as acceptable actions and dependencies rather than always
requiring one exact sequence. Check whether required calls occurred, whether
dependent calls waited for their inputs, and whether output fields were passed
correctly to later calls. Allow independent calls in either order when that is
valid.

Useful measures include required-call precision and recall, dependency
correctness, unnecessary-call rate, and steps compared with a known minimal
workflow. Use strict trajectory exact match only for cases where order and
absence of extra calls are genuinely part of the requirement.

## 8. Abstention tests

Include requests that are unrelated to repository navigation, requests for
which no available IBWD tool fits, and requests missing a required value. The
correct behavior may be no tool call or a clarifying question.

Measure false-call rate on irrelevant requests and unsupported-argument rate
when information is missing. Score a clear clarification as correct when the
missing detail is necessary; do not reward guessing merely because a guessed
call happens to run.

## 9. Error recovery tests

Inject deterministic failures such as timeout, empty result, stale read hash,
invalid arguments, and malformed tool output. Check that the agent retries
only when useful, changes its approach when appropriate, respects a retry or
step limit, and reports unresolved failure honestly.

Record recovery rate, repeated-identical-call rate, retries per task, and
fabricated-success rate. Keep expected error cases distinct from unexpected
failures so ordinary no-match results are not mislabeled as agent mistakes.

## 10. Robustness tests

Run equivalent tasks with paraphrased requests, reordered tool descriptions,
renamed tools where the protocol permits, and distractor tools. Add controlled
noisy cases such as truncated or irrelevant results. Compare accuracy and call
efficiency across variants.

Test instructions embedded in tool output as untrusted data. The expected
behavior is to use the output as evidence only, not to follow instructions
that conflict with the user's request or system policy. Keep each perturbation
isolated so a failure has an identifiable cause.

## 11. Safety checks

Verify that calls stay within the permitted tool set and repository scope.
Include cases involving out-of-scope paths, sensitive files, and operations
with side effects. Check that destructive or write actions require any
confirmation mandated by the product policy, and that reads do not silently
become writes.

Track scope violations, unauthorized calls, unconfirmed destructive actions,
and sensitive-data exposure as separate high-severity outcomes, not as small
deductions hidden inside an average score.

## 12. Cost and latency tracking

For each task, record tool-call count, elapsed time, and token or cost data
when those counters are available and attributable. Report missing or
estimated measurements explicitly. Compare cost per successful task as well
as cost per attempt; repeated failures can make a cheap attempt expensive
overall.

Compare agent runs with and without IBWD on the same task set before making
token-savings claims. Keep model, prompt, context, and run conditions fixed as
far as possible, and distinguish measured results from session-level usage
telemetry.

## 13. Repeatability testing

Run the same task cases several times under representative production
settings. Report single-run success (`pass@1`) and the proportion of tasks
that succeed on every run (`pass^k` for k runs). The first measures capability
on one attempt; the second exposes inconsistency.

Publish the number of runs and relevant model/settings metadata. Do not infer
reliability from one successful example or compare results collected under
substantially different conditions.

## 14. Multi-turn state tracking

Use conversations where the user provides repository details over multiple
turns, changes a constraint, or corrects a path or symbol. Check that the
agent carries forward valid identifiers and uses the latest correction rather
than stale information.

Include cases where a prior tool result supplies a value needed later, such as
an exact symbol ID or source hash. Score entity carry-over, correction
handling, dependency correctness, and required confirmations for risky actions
separately.

## A practical starting suite

Begin with a small, offline fixture set covering common workflows:

- Find a file, then list its symbols.
- Find a symbol, inspect callers, then read a cited span using its current hash.
- Ask an underspecified question and verify the agent requests the missing
  repository or symbol detail instead of inventing it.
- Ask an unrelated question and verify the agent does not call an IBWD tool.
- Return a stale hash or truncated result and verify recovery is bounded and
  reported accurately.
- Attempt an out-of-scope path and verify scope enforcement.

For each case, store the user turns, fixture state, acceptable calls and
arguments, expected evidence, and scoring rules. Prefer deterministic checks
for schemas, identifiers, execution, scope, and state. Use human review for
ambiguous semantic judgments. Report failures by category; a single overall
score can hide whether the issue is agent tool choice, argument grounding,
retrieval quality, or test infrastructure.

These tests can run locally without paid model jobs by validating recorded
tool-call traces and executing calls against deterministic fixtures. Live-agent
benchmarking is a separate step and should be clearly labeled as such.
