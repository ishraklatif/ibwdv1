"""A/B harness without any model call: schedule, mock execution, failure handling, resume, summarizer arithmetic."""
import copy
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
from ab.config import build_prompt, load_config, load_tasks  # noqa: E402
from ab.mock import MockExecutor  # noqa: E402
from ab.run import run_schedule  # noqa: E402
from ab.schedule import build_schedule  # noqa: E402
from ab.store import Store  # noqa: E402
from ab.transcript import parse_transcript  # noqa: E402
from summarize_sprint3_ab import summarize, weights  # noqa: E402

CFG = load_config()
CFG["experiment_id"] = "test-exp"
TASKS = load_tasks(CFG)
BY_ID = {t["task_id"]: t for t in TASKS}
SCHED = build_schedule("test-exp", TASKS, 3, CFG["seed"])
quiet = lambda *_: None


def run(tmp_path, scenario_of=lambda s: "success", **kw):
    store = Store(tmp_path / "run")
    ex = MockExecutor(BY_ID, scenario_of, kw.pop("hook", None))
    stats = run_schedule(SCHED, BY_ID, ex, store, CFG, on_event=quiet, **kw)
    return store, ex, stats


def test_schedule_has_the_frozen_shape_and_is_deterministic_and_balanced():
    assert len(SCHED) == 120 and len({s["session_id"] for s in SCHED}) == 120
    assert Counter(s["scope"] for s in SCHED) == {"headline": 96, "celery_negative_control": 24}
    assert Counter((s["condition"], s["repeat"]) for s in SCHED) == {(c, r): 20 for c in ("baseline", "ibwd") for r in (1, 2, 3)}
    assert SCHED == build_schedule("test-exp", TASKS, 3, CFG["seed"]) and SCHED != build_schedule("test-exp", TASKS, 3, "another-seed")
    assert SCHED[0]["session_id"].startswith("test-exp/") and SCHED[0]["session_id"].count("/") == 4
    firsts = Counter(SCHED[i]["condition"] for i in range(0, 120, 2))
    assert firsts == {"baseline": 30, "ibwd": 30}                     # each condition leads exactly half of the 60 blocks
    assert {s["position"] for s in SCHED} == set(range(1, 121))


def test_prompt_never_contains_an_expected_answer_and_is_identical_across_conditions():
    for t in TASKS:
        p = build_prompt(t)
        assert t["task_id"] in p and "accepted_paths" not in p
        for key in ("callers", "callees"):
            for item in t["expected"].get(key, []):
                assert f'"symbol": "{item["symbol"]}"' not in p


def test_complete_mock_schedule_produces_120_valid_records_and_a_verdict(tmp_path):
    store, ex, stats = run(tmp_path)
    assert stats == {"completed_now": 120, "skipped_already_complete": 0, "attempts_made": 120} and len(ex.calls) == 120
    recs = store.all_attempts()
    assert len(recs) == 120 and all(r["status"] == "ok" and r["grade"]["correct"] and r["tokens"] > 0 for r in recs)
    assert all(r["usage"].keys() == {"input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"} for r in recs)
    assert (tmp_path / "run" / "raw").is_dir() and len(list((tmp_path / "run" / "raw").glob("*.stdout.jsonl"))) == 120
    s = summarize(CFG, TASKS, recs)
    assert s["complete"] and s["verdict"] == "GO" and s["headline"]["ratio"] > 3 and s["celery_control"]["ratio"] > 3
    assert s["headline"]["baseline"]["correctness"] == 1.0 and len(s["headline"]["tasks"]) == 16 and len(s["celery_control"]["tasks"]) == 4


@pytest.mark.parametrize("scenario,status", [("malformed", "malformed"), ("no_usage", "usage_missing"), ("nonzero_exit", "nonzero_exit"), ("timeout", "timeout"),
                                             ("budget", "budget_exhausted"), ("no_result", "no_result")])
def test_failed_attempts_are_retained_are_not_zero_tokens_and_are_never_rerun(tmp_path, scenario, status):
    target = SCHED[5]["session_id"]
    store, ex, _ = run(tmp_path, lambda s: scenario if s["session_id"] == target else "success")
    att = store.attempts(target)
    assert [a["status"] for a in att] == [status] and att[0]["tokens"] is None and att[0]["valid_measurement"] is False and att[0]["counts_as_failed_answer"]
    assert ex.calls.count(target) == 1                                # invalid measurements are retained, never silently rerun
    s = summarize(CFG, TASKS, store.all_attempts())
    assert not s["complete"] and s["verdict"] == "INCOMPLETE" and any(target in p for p in s["problems"])
    if scenario == "budget":
        assert att[0]["cost_usd"] == 1.02                              # its cost is still counted
        assert s["headline"]["baseline"]["cost_usd_all_attempts"] + s["headline"]["ibwd"]["cost_usd_all_attempts"] + s["celery_control"]["baseline"]["cost_usd_all_attempts"] + s["celery_control"]["ibwd"]["cost_usd_all_attempts"] > 6.0


def test_infrastructure_failures_are_retried_up_to_the_policy_and_all_attempts_are_kept(tmp_path):
    target = SCHED[3]["session_id"]
    seen = {"n": 0}

    def scenario(s):
        if s["session_id"] != target:
            return "success"
        seen["n"] += 1
        return "infra_failure" if seen["n"] <= 2 else "success"       # two crashes, then it works: 3 attempts = 1 + max_infra_retries

    store, ex, _ = run(tmp_path, scenario)
    assert [a["status"] for a in store.attempts(target)] == ["infra_failure", "infra_failure", "ok"]
    seen["n"] = -10                                                    # always crashes: stops after the policy limit, every attempt retained
    store2 = Store(tmp_path / "run2")
    run_schedule(SCHED[3:4], BY_ID, MockExecutor(BY_ID, lambda s: "infra_failure"), store2, CFG, on_event=quiet)
    assert [a["status"] for a in store2.attempts(target)] == ["infra_failure"] * 3
    assert not summarize(CFG, TASKS, store2.all_attempts())["complete"]


def test_duplicate_transcript_events_are_counted_once(tmp_path):
    s = SCHED[0]
    from ab.mock import transcript
    plain = parse_transcript(transcript(s, BY_ID[s["task_id"]], "success"))
    dup = parse_transcript(transcript(s, BY_ID[s["task_id"]], "duplicate_events"))
    assert dup["tokens"] == plain["tokens"] and dup["tool_calls"] == plain["tool_calls"] and dup["status"] == "ok"
    conflicting = transcript(s, BY_ID[s["task_id"]], "success") + json.dumps({"type": "result", "subtype": "success", "uuid": "z", "is_error": False, "result": "x",
                                                                                "usage": {"input_tokens": 1, "cache_creation_input_tokens": 1, "cache_read_input_tokens": 1, "output_tokens": 1}}) + "\n"
    assert parse_transcript(conflicting)["status"] == "conflicting_results"


def test_usage_comes_only_from_the_final_result_and_all_four_fields_are_required():
    base = {"type": "result", "subtype": "success", "is_error": False, "result": "{}"}
    full = dict(base, usage={"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4})
    assert parse_transcript(json.dumps(full))["tokens"] == 10
    assistant = {"type": "assistant", "message": {"content": [], "usage": {"input_tokens": 999, "output_tokens": 999}}}
    assert parse_transcript(json.dumps(assistant) + "\n" + json.dumps(full))["tokens"] == 10          # per-request usage is never added
    partial = dict(base, usage={"input_tokens": 1, "output_tokens": 4})
    r = parse_transcript(json.dumps(partial))
    assert r["tokens"] is None and r["status"] == "usage_missing"


def test_interrupted_execution_then_resume_does_not_duplicate_completed_attempts(tmp_path):
    def boom(session, n):
        if n == 13:
            raise KeyboardInterrupt
    store = Store(tmp_path / "run")
    with pytest.raises(KeyboardInterrupt):
        run_schedule(SCHED, BY_ID, MockExecutor(BY_ID, hook=boom), store, CFG, on_event=quiet)
    assert len(store.all_attempts()) == 12
    stuck = SCHED[12]["session_id"]
    assert store.interrupted(stuck) == [1] and store.attempts(stuck) == []            # started, never checkpointed: its cost is unknown, and it is reported
    ex2 = MockExecutor(BY_ID)
    stats = run_schedule(SCHED, BY_ID, ex2, store, CFG, on_event=quiet)
    assert stats == {"completed_now": 108, "skipped_already_complete": 12, "attempts_made": 108}
    recs = store.all_attempts()
    assert len(recs) == 120 and len({r["session_id"] for r in recs}) == 120
    assert [r["attempt"] for r in recs if r["session_id"] == stuck] == [2]
    assert stuck in ex2.calls and all(s["session_id"] not in ex2.calls for s in SCHED[:12])


def test_an_invalid_measurement_cannot_silently_improve_the_ratio(tmp_path):
    store, _, _ = run(tmp_path)
    good = summarize(CFG, TASKS, store.all_attempts())
    recs = copy.deepcopy(store.all_attempts())
    victim = next(r for r in recs if r["condition"] == "baseline" and r["scope"] == "headline")
    victim.update(status="usage_missing", valid_measurement=False, tokens=None, usage=None, grade=None)
    s = summarize(CFG, TASKS, recs)
    assert s["verdict"] == "INCOMPLETE" and not s["complete"] and s["provisional_ratio_note"]
    assert s["headline"]["baseline"]["correctness"] < good["headline"]["baseline"]["correctness"]        # counted as a failed answer, not dropped
    recs2 = copy.deepcopy(store.all_attempts())
    r2 = next(r for r in recs2 if r["condition"] == "ibwd" and r["scope"] == "headline")
    r2.update(status="timeout", valid_measurement=False, tokens=None, usage=None, grade=None)
    assert summarize(CFG, TASKS, recs2)["verdict"] == "INCOMPLETE"


def test_summarizer_reproduces_a_hand_calculated_fixture():
    tasks = [{"task_id": tid, "repository": repo, "language": lang, "stratum": st, "condition_scope": "headline"}
             for tid, repo, lang, st in (("a1", "A", "python", "Q1"), ("a2", "A", "python", "Q2"), ("b1", "B", "typescript", "Q1"), ("b2", "B", "typescript", "Q2"))]
    cfg = {"experiment_id": "fx", "repeats": 3, "seed": "s"}
    assert weights(tasks) == {t["task_id"]: 0.25 for t in tasks}                    # 1 / (2 languages x 1 repo x 2 strata x 1 task)
    med_base, med_ibwd = {"a1": 100, "a2": 200, "b1": 300, "b2": 400}, {"a1": 10, "a2": 40, "b1": 30, "b2": 50}
    sched = build_schedule("fx", tasks, 3, "s")
    recs = []
    for s in sched:
        m = (med_base if s["condition"] == "baseline" else med_ibwd)[s["task_id"]]
        tokens = m + {1: -5, 2: 0, 3: 7}[s["repeat"]]                              # the median of {m-5, m, m+7} is m
        recs.append({**{k: s[k] for k in ("session_id", "repo", "task_id", "stratum", "condition", "repeat", "scope")}, "attempt": 1, "status": "ok", "valid_measurement": True,
                     "tokens": tokens, "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": tokens - 1, "output_tokens": 1},
                     "cost_usd": 0.1, "n_tool_calls": 2, "elapsed_s": 10.0, "grade": {"correct": True}})
    out = summarize(cfg, [{**t, "condition_scope": "headline"} for t in tasks], recs)
    assert out["complete"]
    # R = (100 + 200 + 300 + 400) / (10 + 40 + 30 + 50) with equal weights 0.25: 1000 / 130
    assert out["headline"]["ratio"] == pytest.approx(1000 / 130)
    assert out["headline"]["output_token_ratio"] == pytest.approx(1.0)                # every output count is 1 -> equal medians
    assert out["headline"]["by_stratum"]["Q1"] == pytest.approx((100 + 300) / (10 + 30)) and out["headline"]["by_repo"]["A"] == pytest.approx(300 / 50)
    assert [round(r["paired_ratio"], 6) for r in out["headline"]["tasks"]] == [10.0, 5.0, 10.0, 8.0]      # secondary; the headline is NOT their average
    assert out["headline"]["ratio"] != pytest.approx(sum([10.0, 5.0, 10.0, 8.0]) / 4)
    assert out["verdict"] == "GO" and out["headline"]["baseline"]["cost_usd_all_attempts"] == pytest.approx(1.2)


@pytest.mark.skipif(not Path("/usr/bin/sandbox-exec").exists(), reason="macOS sandbox-exec required")
def test_sandbox_profile_denies_sentinels_outside_the_workspace_and_allows_the_workspace(tmp_path):
    import subprocess
    from ab.execute import sandbox_profile
    secret, own, other = tmp_path / "secret", tmp_path / "ws" / "own", tmp_path / "ws" / "other"
    for d in (secret, own, other):
        d.mkdir(parents=True)
    for f in (secret / "s.txt", own / "o.txt", other / "x.txt"):
        f.write_text("VALUE-" + f.parent.name)
    profile = sandbox_profile(deny=[str(secret), str(tmp_path / "ws")], allow_rw=[str(own)], allow_ro=[])
    read = lambda p: subprocess.run(["sandbox-exec", "-p", profile, "/bin/cat", str(p)], capture_output=True, text=True)
    assert read(own / "o.txt").stdout == "VALUE-own"                          # its own workspace works
    for denied in (secret / "s.txt", other / "x.txt"):                       # another workspace and a sensitive tree do not
        r = read(denied)
        assert r.returncode != 0 and "VALUE" not in r.stdout


def test_total_budget_ceiling_is_enforced_across_attempts_and_retries(tmp_path):
    four = SCHED[:4]
    # every session costs 0.05 and succeeds: 4 x $1 worst case fits a $4 ceiling exactly
    store = Store(tmp_path / "a")
    r = run_schedule(four, BY_ID, MockExecutor(BY_ID), store, CFG, on_event=quiet, max_total_usd=4.0)
    assert r["attempts_made"] == 4 and "stopped_by_total_budget" not in r
    # crashes have unknown cost, so each reserves its full $1 cap: session 2 crashes three times (retries exhausted), then session 3 cannot start
    # because 0.05 + 3.00 committed + a $1 cap would exceed $4 - the run stops, keeps every attempt, and nothing is skipped or replaced
    store2 = Store(tmp_path / "b")
    r2 = run_schedule(four, BY_ID, MockExecutor(BY_ID, lambda s: "infra_failure" if s["session_id"] == four[1]["session_id"] else "success"), store2, CFG,
                      on_event=quiet, max_total_usd=4.0)
    assert r2["stopped_by_total_budget"] and len(store2.all_attempts()) == 4
    assert [a["status"] for a in store2.attempts(four[1]["session_id"])] == ["infra_failure"] * 3 and store2.attempts(four[2]["session_id"]) == []
    # unknown-cost attempts (timeouts) reserve their full cap
    store3 = Store(tmp_path / "c")
    run_schedule(four[:1], BY_ID, MockExecutor(BY_ID, lambda s: "timeout"), store3, CFG, on_event=quiet, max_total_usd=4.0)
    assert store3.committed_spend(1.0)["committed_usd"] == 1.0
    # an interrupted attempt (started, never recorded) also reserves its cap
    store4 = Store(tmp_path / "d")
    store4.mark_started(four[0]["session_id"], 1)
    assert store4.committed_spend(1.0)["committed_usd"] == 1.0
