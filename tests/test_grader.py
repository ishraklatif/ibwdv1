"""Deterministic grader: deliberately wrong answers fail for the intended reasons; harmless formatting differences pass."""
import copy
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
from grade_sprint3_answers import grade, load_spec, norm_path  # noqa: E402

Q1 = load_spec(ROOT / "benchmarks/experiment/tasks/scrapy-q1.json")
Q2 = load_spec(ROOT / "benchmarks/experiment/tasks/scrapy-q2.json")
Q3 = load_spec(ROOT / "benchmarks/experiment/tasks/scrapy-q3.json")


def answer(spec, items, key="callers", tid=None):
    return json.dumps({"task_id": tid or spec["task_id"], "answer": {key: items}})


def q1_items(spec=Q1):
    return [dict(i) for i in spec["expected"]["callers"]]


def test_exact_expected_set_passes():
    r = grade(Q1, answer(Q1, q1_items()))
    assert r["correct"] and not r["missing_items"] and not r["extra_items"] and not r["invalid_items"] and r["parse_error"] is None


def test_order_whitespace_fence_prose_and_path_style_do_not_matter():
    items = q1_items()
    random.Random(1).shuffle(items)
    items[0]["file"] = "./" + items[0]["file"].replace("/", "\\")           # windows separators and a leading ./
    items[1]["symbol"] = "  " + items[1]["symbol"] + " "
    text = "Here is my answer.\n```json\n" + answer(Q1, items) + "\n```\nHope that helps."
    assert grade(Q1, text)["correct"]
    assert grade(Q1, answer(Q1, items + items[:1]))["correct"]               # a duplicate is collapsed, not penalised


def test_one_missing_item_fails():
    r = grade(Q1, answer(Q1, q1_items()[:-1]))
    assert not r["correct"] and len(r["missing_items"]) == 1 and not r["extra_items"]


def test_one_extra_item_fails():
    extra = {"file": Q2["expected"]["callees"][0]["file"], "symbol": Q2["expected"]["callees"][0]["symbol"], "relation": "CALLS"}
    r = grade(Q1, answer(Q1, q1_items() + [extra]))
    assert not r["correct"] and len(r["extra_items"]) == 1 and not r["missing_items"]


def test_correct_name_wrong_file_fails_without_loose_matching():
    items = q1_items()
    good = items[0]
    items[0] = dict(good, file=Q2["expected"]["callees"][0]["file"])           # right symbol name, another (indexed) file
    r = grade(Q1, answer(Q1, items))
    assert not r["correct"] and r["missing_items"] and (r["extra_items"] or r["invalid_items"])


def test_wrong_relation_fails_and_references_are_not_calls():
    items = q1_items()
    items[0]["relation"] = "REFERENCES"
    r = grade(Q1, answer(Q1, items))
    assert not r["correct"] and r["invalid_items"][0]["reason"].startswith("relation 'REFERENCES'")
    q2 = [dict(i) for i in Q2["expected"]["callees"]]
    dist = Q2["distractors_references_only"][0]                                # a function the source only PASSES, never calls
    r = grade(Q2, answer(Q2, q2 + [dict(dist, relation="CALLS")], "callees"))
    assert not r["correct"] and len(r["extra_items"]) == 1


def test_unknown_symbol_and_bad_paths_are_invalid():
    items = q1_items() + [{"file": "scrapy/nope.py", "symbol": "ghost", "relation": "CALLS"}, {"file": "/etc/passwd", "symbol": "x", "relation": "CALLS"},
                          {"file": "../x.py", "symbol": "y", "relation": "CALLS"}]
    r = grade(Q1, answer(Q1, items))
    assert not r["correct"] and len(r["invalid_items"]) == 3
    assert norm_path("a\\b//c.py") == "a/b/c.py" and norm_path("/abs") is None and norm_path("a/../b") is None


def test_q3_exact_path_passes_and_invalid_hop_fails():
    path = Q3["expected"]["accepted_paths"][0]
    good = json.dumps({"task_id": Q3["task_id"], "answer": {"path": path, "relation": "CALLS"}})
    assert grade(Q3, good)["correct"]
    bad = copy.deepcopy(path)
    bad[1], bad[2] = bad[2], bad[1]                                              # both are real symbols but the hop order is not a call chain
    r = grade(Q3, json.dumps({"task_id": Q3["task_id"], "answer": {"path": bad, "relation": "CALLS"}}))
    assert not r["correct"] and r["invalid_items"]
    short = json.dumps({"task_id": Q3["task_id"], "answer": {"path": [path[0], path[-1]], "relation": "CALLS"}})
    assert not grade(Q3, short)["correct"]


def test_q3_equal_cost_alternate_path_passes_under_the_chosen_policy(tmp_path):
    ids = ["a.py::A", "b.py::B", "c.py::C", "d.py::D", "e.py::E"]
    (tmp_path / "s.json").write_text(json.dumps({"indexed_symbol_ids": ids, "call_edges": [[ids[0], ids[1]], [ids[1], ids[3]], [ids[0], ids[2]], [ids[2], ids[3]]]}))
    item = lambda i: {"file": i.split("::")[0], "symbol": i.split("::")[1]}
    spec = {"task_id": "t", "stratum": "Q3", "valid_symbols_file": "s.json",
            "expected": {"accepted_paths": [[item(ids[0]), item(ids[1]), item(ids[3])], [item(ids[0]), item(ids[2]), item(ids[3])]]}}
    for via in (ids[1], ids[2]):
        ans = {"task_id": "t", "answer": {"path": [item(ids[0]), item(via), item(ids[3])], "relation": "CALLS"}}
        assert grade(spec, ans, root=tmp_path)["correct"]
    ans = {"task_id": "t", "answer": {"path": [item(ids[0]), item(ids[4]), item(ids[3])], "relation": "CALLS"}}
    assert not grade(spec, ans, root=tmp_path)["correct"]


def test_malformed_or_incomplete_json_fails_with_a_parse_error():
    for text in ("", "not json at all", '{"task_id": "scrapy-q1", "answer": {"callers": [', '{"answer": {}}', json.dumps({"task_id": "other", "answer": {"callers": []}}),
                 json.dumps({"task_id": Q1["task_id"], "answer": {"wrong_key": []}})):
        r = grade(Q1, text)
        assert not r["correct"] and r["parse_error"], text
