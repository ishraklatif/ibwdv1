from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from click.testing import CliRunner

from ibwd.cli import main
from ibwd.evaluation import EvaluationInputError, evaluate, replay_read_only, registered_tool_schemas, save_evaluation


SCHEMAS = {
    "ibwd_find_symbol": {
        "type": "object", "required": ["name"],
        "properties": {"name": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
    },
    "ibwd_read": {
        "type": "object", "required": ["symbol_id_or_path", "expected_hash"],
        "properties": {"symbol_id_or_path": {"type": "string"}, "expected_hash": {"type": "string"},
                       "range": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]}},
    },
    "ibwd_callers": {"type": "object", "required": ["symbol"], "properties": {"symbol": {"type": "string"}}},
}


def test_scores_schema_arguments_grounding_dependencies_retrieval_and_efficiency():
    cases = {"schema_version": 1, "cases": [{
        "id": "symbol-read", "messages": ["Find Thing and read its definition"],
        "expected_calls": [
            {"tool": "ibwd_find_symbol", "arguments": {"name": "Thing"}},
            {"tool": "ibwd_read", "arguments": {"symbol_id_or_path": "src/a.py::Thing", "expected_hash": "h1"}},
        ],
        "expected_evidence": ["src/a.py::Thing"],
        "dependencies": [{"from_call": 0, "from_result_path": "[0].symbol_id", "to_call": 1,
                          "to_argument": "symbol_id_or_path"}],
        "expected_latest_arguments": [{"call_index": 1, "argument": "symbol_id_or_path", "value": "src/a.py::Thing"}],
        "scope_paths": ["src"], "robustness_group": "find-thing",
    }]}
    traces = {"schema_version": 1, "runs": [{"case_id": "symbol-read", "run_id": "one", "tokens": 400,
        "latency_ms": 250, "calls": [
            {"tool": "ibwd_find_symbol", "arguments": {"name": "Thing"},
             "result": [{"symbol_id": "src/a.py::Thing", "expected_hash": "h1"}]},
            {"tool": "ibwd_read", "arguments": {"symbol_id_or_path": "src/a.py::Thing", "expected_hash": "h1"},
             "result": {"source": "src/a.py::Thing"}},
        ]}]}

    report = evaluate(cases, traces, SCHEMAS)
    result = report["results"][0]
    assert result["schema"]["valid"]
    assert result["arguments"]["parameter_f1"] == 1
    assert result["grounding"]["grounded"]
    assert result["retrieval"]["recall"] == 1
    assert result["trajectory"]["trajectory_exact"]
    assert result["dependencies"] == {"checks": [True], "passed": 1, "total": 1}
    assert result["state_tracking"] == {"checks": [True], "passed": 1, "total": 1}
    assert result["cost"] == {"tool_calls": 2, "tokens": 400, "latency_ms": 250, "cost_usd": None}
    assert report["model_calls"] == 0


def test_flags_bad_schema_hallucinated_arguments_scope_and_unexpected_calls():
    cases = {"schema_version": 1, "cases": [{"id": "bad", "messages": ["Find Thing"],
        "expected_calls": [{"tool": "ibwd_find_symbol", "arguments": {"name": "Thing"}}],
        "scope_paths": ["src"], "allowed_tools": ["ibwd_find_symbol"]}]}
    traces = {"schema_version": 1, "runs": [{"case_id": "bad", "calls": [
        {"tool": "ibwd_find_symbol", "arguments": {"name": 7, "invented": "secret"}, "result": []},
        {"tool": "ibwd_callers", "arguments": {"symbol": "x", "file": "outside/x.py"}, "result": []},
    ]}]}
    result = evaluate(cases, traces, SCHEMAS)["results"][0]
    assert not result["schema"]["valid"]
    assert result["grounding"]["unsupported_count"] >= 1
    assert result["safety"]["unauthorized_call_indexes"] == [1]
    assert result["safety"]["out_of_scope_call_indexes"] == [1]
    assert result["trajectory"]["precision"] == 0.5
    assert result["trajectory"]["recall"] == 1


def test_abstention_recovery_redundancy_repeatability_and_robustness():
    cases = {"schema_version": 1, "cases": [
        {"id": "unknown-a", "messages": ["unrelated"], "should_abstain": True, "robustness_group": "unknown"},
        {"id": "unknown-b", "messages": ["please do something unrelated"], "should_abstain": True,
         "robustness_group": "unknown"},
        {"id": "clarify", "messages": ["find it"], "should_abstain": True, "clarification_required": True},
        {"id": "retry", "messages": ["find Thing"], "expected_calls": [{"tool": "ibwd_find_symbol", "arguments": {"name": "Thing"}}],
         "recovery": {"trigger": "timeout", "max_retries": 1}},
    ]}
    traces = {"schema_version": 1, "runs": [
        {"case_id": "unknown-a", "run_id": "a1", "calls": []},
        {"case_id": "unknown-a", "run_id": "a2", "calls": []},
        {"case_id": "unknown-b", "run_id": "b1", "calls": [{"tool": "ibwd_find_symbol", "arguments": {"name": "x"}}]},
        {"case_id": "clarify", "calls": [], "clarification": "Which repository should I search?"},
        {"case_id": "retry", "calls": [
            {"tool": "ibwd_find_symbol", "arguments": {"name": "Thing"}, "error": "timeout"},
            {"tool": "ibwd_find_symbol", "arguments": {"name": "Thing"}, "error": "timeout"},
            {"tool": "ibwd_find_symbol", "arguments": {"name": "Thing"}, "result": []},
        ]},
    ]}
    report = evaluate(cases, traces, SCHEMAS)
    assert report["repeatability"]["unknown-a"] == {
        "case_id": "unknown-a", "settings": {}, "runs": 2, "pass_at_1": 1, "pass_at_k": True, "pass_power_k": True, "pass_rate": 1,
    }
    assert report["robustness"]["unknown"]["variants"] == 2
    assert report["results"][2]["abstention"]["correct"] is False
    assert report["results"][3]["abstention"]["correct"] is True
    retry = report["results"][4]
    assert retry["recovery"]["recovered"]
    assert retry["redundant_identical_calls"] == 2
    assert not retry["recovery"]["retry_limit_respected"]


def test_read_only_replay_blocks_side_effect_tools(tmp_path, monkeypatch):
    from ibwd.mcp.server import mcp

    async def fake_call_tool(name, arguments):
        return type("Result", (), {"structured_content": {"ok": True}, "content": []})()

    monkeypatch.setattr(mcp, "call_tool", fake_call_tool)
    result = replay_read_only({"calls": [
        {"tool": "ibwd_find_symbol", "arguments": {"name": "x"}},
        {"tool": "ibwd_artifact_save", "arguments": {}},
    ]}, tmp_path)
    assert result["blocked_calls"] == 1
    assert result["calls"][0]["result"] == {"ok": True}
    assert result["calls"][1]["blocked"] is True


def test_eval_agent_cli_reads_local_trace_files(tmp_path):
    cases_path = tmp_path / "cases.json"
    traces_path = tmp_path / "traces.json"
    cases_path.write_text(json.dumps({"schema_version": 1, "cases": [{"id": "none", "should_abstain": True}]}))
    traces_path.write_text(json.dumps({"schema_version": 1, "runs": [{"case_id": "none", "calls": []}]}))
    result = CliRunner().invoke(main, ["eval-agent", str(cases_path), str(traces_path), '--repo', str(tmp_path)])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["evaluation"] == "offline-recorded-agent-traces"
    assert report["results"][0]["abstention"]["correct"] is True
    assert len(list((tmp_path / '.ibwd/usage/evaluations').glob('*.json'))) == 1
    dashboard = (tmp_path / '.ibwd/usage/dashboard.html').read_text()
    assert 'Agent Evaluations' in dashboard and 'Repository evaluation' in dashboard


def score(case, calls, **metadata):
    case = dict(id='case', **case)
    return evaluate({'schema_version': 1, 'cases': [case]},
                    {'schema_version': 1, 'runs': [dict(case_id='case', calls=calls, **metadata)]}, SCHEMAS)


def find(name='Thing', **extra):
    return dict(tool='ibwd_find_symbol', arguments={'name': name}, **extra)


@pytest.mark.parametrize('field', ['input_schema', 'inputSchema'])
def test_mcp_schema_attribute_compatibility(monkeypatch, field):
    from ibwd.mcp.server import mcp

    async def list_tools():
        return [SimpleNamespace(name='test', **{field: {}})]

    monkeypatch.setattr(mcp, 'list_tools', list_tools)
    assert registered_tool_schemas() == {'test': {}}


def test_real_installed_mcp_schemas_are_discovered():
    assert 'name' in registered_tool_schemas()['ibwd_find_symbol']['properties']


def test_missing_results_and_missing_gold_never_pass():
    report = score({'expected_calls': [find()]}, [find()])
    row = report['results'][0]
    assert row['execution']['unobserved_calls'] == 1
    assert row['execution']['outcome_success'] is None
    assert row['status'] == 'not_evaluated'
    assert report['repeatability']['case']['pass_at_1'] is None
    assert score({}, [])['results'][0]['status'] == 'not_evaluated'
    row = score({'expected_calls': [find()], 'expected_evidence': ['src/a.py']}, [find()])['results'][0]
    assert row['status'] == 'not_evaluated' and row['retrieval']['recall'] is None


def test_missing_and_misaligned_arguments_remain_in_denominator():
    row = score({'expected_calls': [find(), find('Other')]}, [find(result=[])])['results'][0]
    assert row['arguments']['exact_match_rate'] == 0.5
    row = score({'expected_calls': [find()]}, [find('extra', result=[]), find(result=[])])['results'][0]
    assert row['arguments']['parameter_recall'] == 1
    assert row['arguments']['parameter_precision'] == 0.5


def test_argument_aware_alternatives_and_optional_defaults():
    case = {'expected_calls': [find('Wrong')], 'acceptable_call_sequences': [[find('Thing')]]}
    actual = find(result=[])
    actual['arguments']['limit'] = 10
    row = score(case, [actual])['results'][0]
    assert row['arguments']['parameter_f1'] == 1
    assert row['status'] == 'passed'


def test_semantic_matching_is_explicit_and_never_normalizes_identifiers():
    row = score({'expected_calls': [find('THING')]}, [find('thing', result=[])])['results'][0]
    assert row['arguments']['semantic_match_rate'] is None
    assert row['status'] == 'failed'
    with pytest.raises(EvaluationInputError, match='free-text'):
        score({'expected_calls': [dict(find(), semantic_arguments=['name'])]}, [find(result=[])])
    cases = {'schema_version': 1, 'cases': [{'id': 'q', 'expected_calls': [
        {'tool': 'query', 'arguments': {'task': 'Find Widget'}, 'semantic_arguments': ['task']}]}]}
    traces = {'schema_version': 1, 'runs': [{'case_id': 'q', 'calls': [
        {'tool': 'query', 'arguments': {'task': ' find   widget '}, 'result': []}]}]}
    row = evaluate(cases, traces, {'query': {'type': 'object', 'properties': {'task': {'type': 'string'}}}})['results'][0]
    assert row['arguments']['exact_match_rate'] == 0
    assert row['arguments']['semantic_match_rate'] == 1
    assert row['status'] == 'passed'


@pytest.mark.parametrize('arguments', [[], 'bad', {'name': True}, {'name': 'Thing', 'extra': 2}])
def test_invalid_argument_shapes_and_extra_parameters_fail(arguments):
    call = dict(tool='ibwd_find_symbol', arguments=arguments, result=[])
    assert not score({'expected_calls': [find()]}, [call])['results'][0]['schema']['valid']


def test_full_json_schema_constraints_are_checked():
    schema = {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1},
        'range': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'integer'}}}}
    row = evaluate({'schema_version': 1, 'cases': [{'id': 'x'}]},
        {'schema_version': 1, 'runs': [{'case_id': 'x', 'calls': [
            {'tool': 't', 'arguments': {'limit': 0, 'range': [1]}, 'result': []}]}]}, {'t': schema})['results'][0]
    assert len(row['schema']['failures']) == 2


def test_recovery_requires_success_after_error_and_no_trailing_failure():
    case = {'recovery': {'trigger': 'Timeout', 'max_retries': 2}}
    row = score(case, [find(result=[]), find(result=[]), find(error='TIMEOUT')])['results'][0]
    assert not row['recovery']['recovered']
    assert row['status'] == 'failed'
    row = score(case, [find(error='timeout'), find(result=[])])['results'][0]
    assert row['recovery']['recovered']
    assert row['status'] == 'passed'
    assert score(case, [find(result=[])])['results'][0]['status'] == 'not_evaluated'
    assert score(case, [find(error='timeout'), find()])['results'][0]['status'] == 'not_evaluated'


def test_retrieval_uses_exact_values_and_requires_precision_evidence():
    row = score({'expected_evidence': ['src/a.py']}, [find(result={'src/a.py': 'src/a.py.bak'})])['results'][0]
    assert row['retrieval']['recall'] == 0
    assert row['retrieval']['precision'] is None
    row = score({'expected_evidence': ['src/a.py']}, [find(result=[])],
                returned_evidence=['src/a.py', 'src/b.py'])['results'][0]
    assert row['retrieval']['precision'] == 0.5


def test_grounding_does_not_use_future_turns_or_dictionary_keys():
    row = score({'messages': [{'turn': 2, 'content': 'Thing'}]},
        [find('Earlier', result={'Thing': 'other'}), find('Thing', result=[], turn=1)])['results'][0]
    assert any(item['call'] == 1 for item in row['grounding']['unsupported'])
    assert row['status'] == 'not_evaluated'


def test_scope_rejects_dotdot_and_state_values_are_case_sensitive():
    case = {'scope_paths': ['src'], 'expected_latest_arguments': [
        {'call_index': 0, 'argument': 'symbol_id_or_path', 'value': 'src/a.py'}]}
    row = score(case, [{'tool': 'ibwd_read', 'arguments': {
        'symbol_id_or_path': 'src/../../secret.py', 'expected_hash': 'h'}, 'result': []}])['results'][0]
    assert row['safety']['out_of_scope_call_indexes'] == [0]
    row = score({'expected_latest_arguments': [{'call_index': 0, 'argument': 'name', 'value': 'Thing'}]},
                [find('thing', result=[])])['results'][0]
    assert row['state_tracking']['passed'] == 0


def test_parallel_dependency_cannot_consume_unavailable_result():
    case = {'dependencies': [{'from_call': 0, 'to_call': 1, 'from_result_path': 'name', 'to_argument': 'name'}]}
    row = score(case, [find(result={'name': 'Thing'}, parallel_group='g'), find(result=[], parallel_group='g')])['results'][0]
    assert row['dependencies']['passed'] == 0
    with pytest.raises(EvaluationInputError, match='earlier'):
        score({'dependencies': [{'from_call': 1, 'to_call': 0, 'from_result_path': 'name', 'to_argument': 'name'}]}, [])


def test_repeatability_is_empirical_rate_and_separates_settings():
    cases = {'schema_version': 1, 'cases': [{'id': 'x', 'should_abstain': True}]}
    runs = [{'case_id': 'x', 'run_id': 'a', 'calls': []},
            {'case_id': 'x', 'run_id': 'b', 'calls': [find(result=[])]},
            {'case_id': 'x', 'run_id': 'c', 'calls': [], 'settings': {'model': 'other'}}]
    report = evaluate(cases, {'schema_version': 1, 'runs': runs}, SCHEMAS)
    assert len(report['repeatability']) == 2
    assert report['repeatability']['x']['pass_at_1'] == 0.5
    assert report['repeatability']['x']['pass_at_k'] is True
    assert report['repeatability']['x']['pass_power_k'] is False


def test_expected_null_state_requires_observation():
    assert score({'expected_state': None}, [], final_state=None)['results'][0]['status'] == 'passed'
    assert score({'expected_state': None}, [])['results'][0]['status'] == 'not_evaluated'


def test_replay_preserves_mcp_errors_and_blocks_model_helpers(tmp_path, monkeypatch):
    from ibwd.mcp.server import mcp

    async def call_tool(*args):
        return SimpleNamespace(is_error=True, structured_content=None,
                               content=[SimpleNamespace(text='tool failed')])

    monkeypatch.setattr(mcp, 'call_tool', call_tool)
    result = replay_read_only({'calls': [find(), {'tool': 'ibwd_context', 'arguments': {'task': 'x'}}]}, tmp_path)
    assert result['calls'][0]['is_error']
    assert 'tool failed' in result['calls'][0]['error']
    assert result['calls'][1]['blocked']


def test_real_replay_and_text_tuple_response(tmp_path, monkeypatch):
    (tmp_path / 'app.py').write_text('def example():\n    return 1\n')
    result = replay_read_only({'calls': [{'tool': 'ibwd_find_files', 'arguments': {}}]}, tmp_path)
    assert not result['calls'][0]['is_error']
    assert 'app.py' in json.dumps(result['calls'][0]['result'])
    from ibwd.mcp.server import mcp

    async def call_tool(*args):
        return ([SimpleNamespace(text='[{"file":"app.py"}]')], None)

    monkeypatch.setattr(mcp, 'call_tool', call_tool)
    assert replay_read_only({'calls': [find()]}, tmp_path)['calls'][0]['result'] == [{'file': 'app.py'}]


def test_cli_validates_all_input_before_replay(tmp_path, monkeypatch):
    cases = tmp_path / 'cases.json'
    traces = tmp_path / 'traces.json'
    cases.write_text(json.dumps({'schema_version': 1, 'cases': [{'id': 'x'}]}))
    traces.write_text(json.dumps({'schema_version': 1, 'runs': [{'case_id': 'other', 'calls': [find()]}]}))
    monkeypatch.setattr('ibwd.evaluation.replay_read_only', lambda *args: pytest.fail('replay should not run'))
    result = CliRunner().invoke(main, ['eval-agent', str(cases), str(traces), '--replay-read-only', '--repo', str(tmp_path)])
    assert result.exit_code == 1 and 'Unknown case' in result.output


def test_dashboard_filters_sessions_escapes_content_and_skips_corrupt_reports(tmp_path):
    from ibwd.evaluation_dashboard import evaluation_panel
    report = score({'should_abstain': True}, [])
    report['results'][0]['case_id'] = '<script>alert(1)</script>'
    save_evaluation(deepcopy(report), tmp_path, client='codex', session_key='first')
    save_evaluation(deepcopy(report), tmp_path, client='claude', session_key='second')
    folder = tmp_path / '.ibwd/usage'
    (folder / 'evaluations/bad.json').write_text('{')
    page = evaluation_panel(folder, {'client': 'codex', 'session_key': 'first'})
    assert 'codex session first' in page and 'claude session second' not in page
    assert '&lt;script&gt;' in page and '<script>alert' not in page
    assert 'Unreadable or unsupported evaluation reports: 1' in page
    assert all(label in page for label in ['Schema validity', 'Repeated trials', 'Multi-turn state', 'Error recovery'])
    page = evaluation_panel(folder, {'client': 'codex', 'session_key': 'missing'})
    assert 'No evaluations recorded' in page


def test_session_association_and_report_selector(tmp_path):
    from ibwd.evaluation_dashboard import evaluation_panel
    report = score({'should_abstain': True}, [])
    with pytest.raises(EvaluationInputError, match='both'):
        save_evaluation(report, tmp_path, client='codex')
    save_evaluation(deepcopy(report), tmp_path)
    save_evaluation(deepcopy(report), tmp_path)
    page = evaluation_panel(tmp_path / '.ibwd/usage', None)
    assert 'id="evaluation-select"' in page and 'id="evaluation-1" hidden' in page
