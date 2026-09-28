"""Offline scoring of versioned agent traces; no agent or model is invoked."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import time
from typing import Any
import uuid

from jsonschema import Draft202012Validator

from ibwd.local_io import atomic_write

READ_ONLY_TOOLS = frozenset({
    'ibwd_find_files', 'ibwd_find_symbol', 'ibwd_list_symbols', 'ibwd_callers',
    'ibwd_dependents', 'ibwd_trace_path', 'ibwd_context', 'ibwd_read', 'ibwd_impact',
})
METHODS = {
    'schema': 'Schema validity', 'arguments': 'Exact arguments',
    'semantic': 'Free-text equivalence', 'grounding': 'Grounding hints',
    'execution': 'Execution and state', 'retrieval': 'Retrieval relevance',
    'trajectory': 'Call trajectory', 'abstention': 'Abstention and clarification',
    'recovery': 'Error recovery', 'robustness': 'Robustness variants',
    'safety': 'Tool and path scope', 'cost': 'Cost and latency',
    'repeatability': 'Repeated trials', 'state_tracking': 'Multi-turn state',
}


class EvaluationInputError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _equal(left, right):
    return _canonical(left) == _canonical(right)


def _strings(value):
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _strings(child)]
    return [value] if isinstance(value, str) else []


def _name(call):
    return call.get('tool', call.get('name'))


def _args(call):
    return call.get('arguments', call.get('args', {}))


def _mapping(call):
    value = _args(call)
    return value if isinstance(value, dict) else {}


def _result(call):
    return call.get('result', call.get('output'))


def _call_status(call):
    result = _result(call)
    if (call.get('error') or call.get('blocked') or call.get('is_error') or call.get('isError')
            or isinstance(result, dict) and (result.get('isError') or result.get('is_error'))):
        return 'failed'
    return 'passed' if 'result' in call or 'output' in call else 'not_evaluated'


def _get_path(value, path):
    if not isinstance(path, str) or not re.fullmatch(r'(?:\[\d+\]|[A-Za-z_][\w-]*)(?:\[\d+\]|\.[A-Za-z_][\w-]*)*', path):
        raise ValueError('Expected a field path such as items[0].symbol_id')
    for part in re.findall(r'[^.\[\]]+', path):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def registered_tool_schemas():
    from ibwd.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    schemas = {}
    for tool in tools:
        schema = getattr(tool, 'input_schema', None)
        if schema is None:
            schema = getattr(tool, 'inputSchema', None)
        if not isinstance(schema, dict):
            raise EvaluationInputError(f'MCP tool {tool.name} has no supported input schema')
        schemas[tool.name] = schema
    return schemas


def _schema_errors(value, schema):
    # Tool arguments have a fixed signature even when SDK schemas omit this keyword.
    schema = deepcopy(schema)
    if schema.get('type') == 'object' and 'properties' in schema:
        schema.setdefault('additionalProperties', False)
    return [f"{'.'.join(map(str, error.absolute_path)) or 'arguments'}: {error.message}"
            for error in Draft202012Validator(schema).iter_errors(value)]


_TEXTS = {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True}
_INDEX = {'type': 'integer', 'minimum': 0}
_MESSAGES = {'type': 'array', 'items': {'anyOf': [
    {'type': 'string'}, {'type': 'object', 'required': ['content', 'turn'],
                       'properties': {'content': {'type': 'string'}, 'turn': _INDEX}},
]}}
_CALL = {'type': 'object', 'anyOf': [{'required': ['tool']}, {'required': ['name']}],
         'properties': {'tool': {'type': 'string', 'minLength': 1}, 'name': {'type': 'string', 'minLength': 1},
                        'turn': _INDEX, 'is_error': {'type': 'boolean'}, 'isError': {'type': 'boolean'},
                        'blocked': {'type': 'boolean'}, 'semantic_arguments': _TEXTS,
                        'latency_ms': {'type': 'number', 'minimum': 0}}}
_CALLS = {'type': 'array', 'items': _CALL, 'maxItems': 500}
_CASE = {'type': 'object', 'required': ['id'], 'properties': {
    'id': {'type': 'string', 'minLength': 1}, 'messages': _MESSAGES, 'expected_calls': _CALLS,
    'acceptable_call_sequences': {'type': 'array', 'items': _CALLS},
    'expected_evidence': _TEXTS, 'allowed_tools': _TEXTS, 'forbidden_tools': _TEXTS,
    'scope_paths': _TEXTS, 'should_abstain': {'type': 'boolean'},
    'clarification_required': {'type': 'boolean'}, 'requires_confirmation': {'type': 'boolean'},
    'robustness_group': {'type': 'string'},
    'dependencies': {'type': 'array', 'items': {'type': 'object',
        'required': ['from_call', 'to_call', 'from_result_path', 'to_argument'], 'properties': {
            'from_call': _INDEX, 'to_call': _INDEX, 'from_result_path': {'type': 'string', 'minLength': 1},
            'to_argument': {'type': 'string', 'minLength': 1}}}},
    'expected_latest_arguments': {'type': 'array', 'items': {'type': 'object',
        'required': ['call_index', 'argument', 'value'], 'properties': {
            'call_index': _INDEX, 'argument': {'type': 'string', 'minLength': 1}}}},
    'recovery': {'type': 'object', 'required': ['trigger'], 'properties': {
        'trigger': {'type': 'string', 'minLength': 1}, 'max_retries': _INDEX, 'allowed_tools': _TEXTS}},
}}
_RUN = {'type': 'object', 'required': ['case_id', 'calls'], 'properties': {
    'case_id': {'type': 'string', 'minLength': 1}, 'run_id': {'type': 'string', 'minLength': 1},
    'calls': _CALLS, 'messages': _MESSAGES, 'returned_evidence': _TEXTS,
    'clarification': {'type': 'string'}, 'confirmed': {'type': 'boolean'},
    'latency_ms': {'type': ['number', 'null'], 'minimum': 0},
    'cost_usd': {'type': ['number', 'null'], 'minimum': 0}, 'settings': {'type': 'object'},
    'tokens': {'anyOf': [{'type': 'null'}, {'type': 'integer', 'minimum': 0},
        {'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0}}]},
}}


def validate_inputs(cases_data, traces_data):
    for label, data, key, item in [('cases', cases_data, 'cases', _CASE), ('traces', traces_data, 'runs', _RUN)]:
        schema = {'type': 'object', 'required': ['schema_version', key], 'properties': {
            'schema_version': {'const': 1}, key: {'type': 'array', 'items': item, 'minItems': 1}}}
        error = next(Draft202012Validator(schema).iter_errors(data), None)
        if error:
            raise EvaluationInputError(f"{label} {'.'.join(map(str, error.absolute_path))}: {error.message}")
        try:
            _canonical(data)
        except (TypeError, ValueError) as exc:
            raise EvaluationInputError('Inputs must contain finite JSON values') from exc
    ids = [case['id'] for case in cases_data['cases']]
    if len(set(ids)) != len(ids):
        raise EvaluationInputError('Case IDs must be unique')
    for case in cases_data['cases']:
        if case.get('clarification_required') and not case.get('should_abstain'):
            raise EvaluationInputError('clarification_required requires should_abstain=true')
        for sequence in [case.get('expected_calls', []), *case.get('acceptable_call_sequences', [])]:
            for call in sequence:
                if not isinstance(_args(call), dict):
                    raise EvaluationInputError('Expected arguments must be objects')
                for field in call.get('semantic_arguments', []):
                    if field not in {'task', 'query', 'text', 'description'} or not isinstance(_mapping(call).get(field), str):
                        raise EvaluationInputError('semantic_arguments may name only free-text task/query/text/description fields')
        for dep in case.get('dependencies', []):
            if dep['from_call'] >= dep['to_call']:
                raise EvaluationInputError('Dependencies must reference an earlier producer call')
    seen = set()
    for index, run in enumerate(traces_data['runs']):
        if run['case_id'] not in ids:
            raise EvaluationInputError(f"Unknown case: {run['case_id']}")
        identity = (run['case_id'], run.get('run_id', str(index + 1)), _canonical(run.get('settings', {})))
        if identity in seen:
            raise EvaluationInputError('Duplicate run identity')
        seen.add(identity)


def _argument_match(expected, actual, field, semantic=False):
    if field not in actual:
        return False
    left, right = expected[field], actual[field]
    if semantic and isinstance(left, str) and isinstance(right, str):
        return ' '.join(left.split()).casefold() == ' '.join(right.split()).casefold()
    return _equal(left, right)


def _alignment(expected, actual):
    # Ordered alignment maximizes matched calls, then matching argument values.
    rows = [[(0, 0)] * (len(actual) + 1) for _ in range(len(expected) + 1)]
    for i, gold in enumerate(expected, 1):
        for j, call in enumerate(actual, 1):
            options = [rows[i - 1][j], rows[i][j - 1]]
            if _name(gold) == _name(call):
                count, args = rows[i - 1][j - 1]
                matches = sum(_argument_match(_mapping(gold), _mapping(call), field,
                                              field in gold.get('semantic_arguments', [])) for field in _mapping(gold))
                options.append((count + 1, args + matches))
            rows[i][j] = max(options)
    pairs = []
    i, j = len(expected), len(actual)
    while i and j:
        if rows[i][j] == rows[i - 1][j]:
            i -= 1
        elif rows[i][j] == rows[i][j - 1]:
            j -= 1
        else:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
    return list(reversed(pairs)), rows[-1][-1]


def _path_values(call):
    for field, value in _mapping(call).items():
        if field in {'file', 'repo', 'path', 'symbol_id_or_path', 'targets', 'source', 'target', 'symbol', 'name'}:
            for text in _strings(value):
                path = text.split('::', 1)[0].replace('\\', '/')
                if (field in {'file', 'repo', 'path'} or '/' in path or '::' in text
                        or re.search(r'\.(py|js|jsx|ts|tsx|md|json|toml|yaml|yml)$', path)):
                    yield path


def _in_scope(path, roots):
    path = posixpath.normpath(path)
    if path == '..' or path.startswith('../'):
        return False
    return any(path == root or path.startswith(root.rstrip('/') + '/') or
               root == '.' and not path.startswith('/') for root in map(posixpath.normpath, roots))


def _visible_messages(messages, turn):
    return [message if isinstance(message, str) else message['content']
            for message in messages if isinstance(message, str) or message['turn'] <= turn]


def evaluate_run(case, run, schemas):
    calls = run['calls']
    states = [_call_status(call) for call in calls]
    sequences = [case['expected_calls']] if 'expected_calls' in case else []
    sequences += case.get('acceptable_call_sequences', [])
    if case.get('should_abstain'):
        sequences = [[]]
    has_gold = bool(sequences)
    expected = max(sequences, key=lambda seq: (*_alignment(seq, calls)[1], -len(seq))) if sequences else []
    pairs, _ = _alignment(expected, calls)
    failures, ungrounded = [], []
    for index, call in enumerate(calls):
        schema = schemas.get(_name(call))
        errors = _schema_errors(_args(call), schema) if schema is not None else ['Unknown tool']
        failures.extend({'call': index, 'error': error} for error in errors)
        visible = _visible_messages(case.get('messages', []) + run.get('messages', []), call.get('turn', 0))
        visible += [text for earlier in calls[:index] if _call_status(earlier) == 'passed'
                    and earlier.get('turn', 0) <= call.get('turn', 0)
                    and not (call.get('parallel_group') is not None and call.get('parallel_group') == earlier.get('parallel_group'))
                    for text in _strings(_result(earlier))]
        for field, value in _mapping(call).items():
            prop = (schema or {}).get('properties', {}).get(field, {})
            if 'default' in prop and _equal(value, prop['default']):
                continue
            for text in _strings(value):
                if text and not any(re.search(r'(?<!\w)' + re.escape(text) + r'(?!\w)', source) for source in visible):
                    ungrounded.append({'call': index, 'argument': field, 'value': text})

    predicted = sum(len(_mapping(call)) for call in calls)
    total = sum(len(_mapping(call)) for call in expected)
    exact = accepted = semantic_matches = semantic_total = 0
    for gold_index, actual_index in pairs:
        gold, actual = expected[gold_index], calls[actual_index]
        for field in _mapping(gold):
            is_semantic = field in gold.get('semantic_arguments', [])
            exact += _argument_match(_mapping(gold), _mapping(actual), field)
            accepted += _argument_match(_mapping(gold), _mapping(actual), field, is_semantic)
            if is_semantic:
                semantic_matches += _argument_match(_mapping(gold), _mapping(actual), field, True)
    semantic_total = sum(len(call.get('semantic_arguments', [])) for call in expected)
    # Supplied optional defaults do not penalize argument precision.
    for gold_index, actual_index in pairs:
        gold, actual = expected[gold_index], calls[actual_index]
        props = schemas.get(_name(actual), {}).get('properties', {})
        predicted -= sum(field not in _mapping(gold) and 'default' in props.get(field, {})
                         and _equal(value, props[field]['default']) for field, value in _mapping(actual).items())
    precision = exact / predicted if predicted else float(total == 0)
    recall = exact / total if total else float(predicted == 0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    relevant = set(case.get('expected_evidence', []))
    returned = set(run.get('returned_evidence', [])) if 'returned_evidence' in run else {
        text for call in calls if _call_status(call) == 'passed' for text in _strings(_result(call))}
    found = relevant & returned
    retrieval_recall = (len(found) / len(relevant) if relevant else float(not returned)) if 'expected_evidence' in case else None
    if 'not_evaluated' in states and relevant - found and 'returned_evidence' not in run:
        retrieval_recall = None
    retrieval_precision = (len(found) / len(returned) if returned else float(not relevant)) if (
        'returned_evidence' in run and 'expected_evidence' in case) else None

    allowed = set(case.get('allowed_tools', schemas))
    unauthorized = [i for i, call in enumerate(calls) if _name(call) not in allowed or _name(call) in case.get('forbidden_tools', [])]
    out_of_scope = [i for i, call in enumerate(calls) if case.get('scope_paths')
                    and any(not _in_scope(path, case['scope_paths']) for path in _path_values(call))]
    confirmation = not case.get('requires_confirmation') or not calls or run.get('confirmed') is True
    dependencies = []
    for dep in case.get('dependencies', []):
        try:
            producer, consumer = calls[dep['from_call']], calls[dep['to_call']]
            concurrent = producer.get('parallel_group') is not None and producer.get('parallel_group') == consumer.get('parallel_group')
            dependencies.append(_call_status(producer) == 'passed' and not concurrent
                and producer.get('turn', 0) <= consumer.get('turn', 0)
                and _equal(_get_path(_result(producer), dep['from_result_path']), _mapping(consumer)[dep['to_argument']]))
        except (IndexError, KeyError, TypeError, ValueError):
            dependencies.append(False)
    carry = []
    for check in case.get('expected_latest_arguments', []):
        try:
            carry.append(_equal(_mapping(calls[check['call_index']])[check['argument']], check['value']))
        except (IndexError, KeyError, TypeError):
            carry.append(False)

    recovery = None
    if case.get('recovery'):
        rule = case['recovery']
        triggers = [i for i, call in enumerate(calls) if states[i] == 'failed'
                    and rule['trigger'].casefold() in str(call.get('error', _result(call))).casefold()]
        retries, recovered, tolerated_errors = 0, bool(triggers), set()
        pending = False
        for index in triggers:
            names = rule.get('allowed_tools', [_name(calls[index])])
            later = [i for i in range(index + 1, len(calls)) if _name(calls[i]) in names]
            success = next((i for i in later if states[i] == 'passed'), None)
            attempts = [i for i in later if success is None or i <= success]
            retries = max(retries, len(attempts))
            recovered &= success is not None
            if success is None and any(states[i] == 'not_evaluated' for i in later):
                pending = True
            if success is not None:
                tolerated_errors.add(index)
        recovered &= all(state != 'failed' or i in tolerated_errors for i, state in enumerate(states))
        if not triggers or (pending and all(state != 'failed' or i in triggers for i, state in enumerate(states))):
            recovered = None
        recovery = {'trigger_seen': bool(triggers), 'recovered': recovered, 'retry_count': retries,
                    'retry_limit_respected': retries <= rule.get('max_retries', 3)}

    outcome = (False if 'failed' in states else None if 'not_evaluated' in states else True)
    if recovery and recovery['recovered']:
        outcome = None if 'not_evaluated' in states else True
    elif recovery and recovery['recovered'] is None and recovery['trigger_seen']:
        outcome = None
    state_match = _equal(run['final_state'], case['expected_state']) if (
        'expected_state' in case and 'final_state' in run) else None
    abstention = not calls and (not case.get('clarification_required') or bool(run.get('clarification', '').strip()))
    trajectory = {'expected_calls': len(expected) if has_gold else None, 'actual_calls': len(calls),
        'ordered_matches': len(pairs) if has_gold else None,
        'precision': (len(pairs) / len(calls) if calls else float(not expected)) if has_gold else None,
        'recall': (len(pairs) / len(expected) if expected else float(not calls)) if has_gold else None,
        'trajectory_exact': [_name(c) for c in expected] == [_name(c) for c in calls] if has_gold else None,
        'steps_over_minimum': max(0, len(calls) - len(expected)) if has_gold else None,
        'steps_efficiency': min(1.0, len(expected) / len(calls)) if has_gold and calls else None}
    checks = [not failures, not unauthorized and not out_of_scope and confirmation, all(dependencies), all(carry)]
    if has_gold:
        checks.extend([trajectory['trajectory_exact'], accepted == total and predicted == total])
    if case.get('should_abstain'):
        checks.append(abstention)
    if 'expected_evidence' in case and retrieval_recall is not None:
        checks.append(retrieval_recall == 1)
    if 'expected_state' in case and state_match is not None:
        checks.append(state_match)
    if recovery:
        checks.append(recovery['retry_limit_respected'])
        if recovery['recovered'] is not None:
            checks.append(recovery['recovered'])
    if outcome is False:
        checks.append(False)
    expectations = has_gold or any(key in case for key in ('expected_state', 'expected_evidence', 'dependencies', 'expected_latest_arguments', 'recovery'))
    incomplete = (outcome is None or 'expected_state' in case and state_match is None or not expectations
                  or recovery is not None and recovery['recovered'] is None)
    status = 'failed' if not all(checks) else 'not_evaluated' if incomplete else 'passed'
    keys = [(_name(call), _canonical(_args(call))) for call in calls]
    return {
        'case_id': case['id'], 'run_id': run.get('run_id', '1'), 'status': status,
        'settings': run.get('settings', {}), 'schema': {'valid': not failures, 'failures': failures},
        'arguments': {'exact_match_rate': exact / total if has_gold and total else None,
            'semantic_match_rate': semantic_matches / semantic_total if semantic_total else None,
            'parameter_precision': precision if has_gold else None, 'parameter_recall': recall if has_gold else None,
            'parameter_f1': f1 if has_gold else None, 'predicted_parameters': predicted,
            'expected_parameters': total if has_gold else None, 'compared': total, 'accepted': accepted},
        'grounding': {'unsupported_count': len(ungrounded), 'unsupported': ungrounded,
            'grounded': not ungrounded if calls else None, 'assessment': 'lexical hints; not a pass/fail criterion'},
        'execution': {'successful_calls': states.count('passed'), 'failed_calls': states.count('failed'),
            'unobserved_calls': states.count('not_evaluated'), 'outcome_success': outcome, 'expected_state_match': state_match},
        'retrieval': {'expected': len(relevant) if 'expected_evidence' in case else None, 'found': len(found),
            'missing': sorted(relevant - found), 'recall': retrieval_recall, 'precision': retrieval_precision},
        'trajectory': trajectory, 'redundant_identical_calls': len(keys) - len(set(keys)),
        'abstention': {'expected': bool(case.get('should_abstain')), 'correct': abstention if case.get('should_abstain') else None,
            'clarification_required': bool(case.get('clarification_required')), 'clarification_present': bool(run.get('clarification', '').strip())},
        'safety': {'unauthorized_call_indexes': unauthorized, 'out_of_scope_call_indexes': out_of_scope,
            'confirmation_required': bool(case.get('requires_confirmation')), 'confirmation_present': run.get('confirmed') is True,
            'safe': not unauthorized and not out_of_scope and confirmation,
            'scope_assessed': bool(case.get('scope_paths'))},
        'dependencies': {'checks': dependencies, 'passed': sum(dependencies), 'total': len(dependencies)},
        'state_tracking': {'checks': carry, 'passed': sum(carry), 'total': len(carry)},
        'recovery': recovery, 'cost': {'tool_calls': len(calls), 'tokens': run.get('tokens'),
            'latency_ms': run.get('latency_ms'), 'cost_usd': run.get('cost_usd')},
        'robustness_group': case.get('robustness_group'),
    }


def evaluate(cases_data, traces_data, schemas=None):
    validate_inputs(cases_data, traces_data)
    schemas = registered_tool_schemas() if schemas is None else schemas
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    cases = {case['id']: case for case in cases_data['cases']}
    results = [evaluate_run(cases[run['case_id']], dict(run, run_id=run.get('run_id', str(i + 1))), schemas)
               for i, run in enumerate(traces_data['runs'])]
    grouped = defaultdict(list)
    for item in results:
        grouped[(item['case_id'], _canonical(item['settings']))].append(item)
    repeatability = {}
    for (case_id, settings), group in grouped.items():
        complete = all(item['status'] != 'not_evaluated' for item in group)
        passed = sum(item['status'] == 'passed' for item in group)
        key = case_id if settings == '{}' else case_id + ':' + hashlib.sha256(settings.encode()).hexdigest()[:12]
        repeatability[key] = {'case_id': case_id, 'settings': json.loads(settings), 'runs': len(group),
            'pass_at_1': passed / len(group) if complete else None,
            'pass_at_k': bool(passed) if complete else None, 'pass_power_k': passed == len(group) if complete else None,
            'pass_rate': passed / len(group) if complete else None}
    robust = defaultdict(list)
    for item in results:
        if item['robustness_group']:
            robust[(item['robustness_group'], _canonical(item['settings']))].append(item)
    robustness = {}
    for (name, settings), group in robust.items():
        key = name if settings == '{}' else name + ':' + hashlib.sha256(settings.encode()).hexdigest()[:12]
        robustness[key] = {'variants': len({item['case_id'] for item in group}), 'runs': len(group),
            'settings': json.loads(settings), 'schema_valid_rate': sum(item['schema']['valid'] for item in group) / len(group),
            'pass_rate': sum(item['status'] == 'passed' for item in group) / len(group)
                         if all(item['status'] != 'not_evaluated' for item in group) else None}
    return {'schema_version': 2, 'evaluation': 'offline-recorded-agent-traces', 'model_calls': 0,
        'cases': len(cases), 'runs': len(results), 'results': results,
        'unrun_cases': sorted(set(cases) - {item['case_id'] for item in results}),
        'summary': {status: sum(item['status'] == status for item in results) for status in ('passed', 'failed', 'not_evaluated')},
        'repeatability': repeatability, 'robustness': robustness,
        'schema_fingerprint': hashlib.sha256(_canonical(schemas).encode()).hexdigest(),
        'limitations': ['Grounding is a lexical hint, not proof of hallucination or a scored correctness label.',
            'Retrieval precision requires explicit returned_evidence; recall uses exact evidence identities.',
            'Safety assesses supplied tool/path/confirmation rules, not injection resistance or all runtime effects.',
            'Repeated trials and robustness compare supplied traces within identical recorded settings.',
            'Missing outcomes and missing gold expectations remain not evaluated. No live agent was run.']}


def replay_read_only(run, repo):
    """Replay retrieval only. Freshness may update .ibwd; no model helpers are allowed."""
    from ibwd.mcp.server import mcp

    replayed = []
    previous = Path.cwd()
    repo = repo.resolve()
    schemas = registered_tool_schemas()
    try:
        os.chdir(repo)
        for call in run['calls']:
            name, args = _name(call), _args(call)
            reason = 'Tool is not allowlisted for replay' if name not in READ_ONLY_TOOLS else None
            if reason is None and _schema_errors(args, schemas[name]):
                reason = 'Invalid tool arguments'
            if reason is None and name == 'ibwd_context' and (args.get('semantic') is not False or args.get('helper') is not False):
                reason = 'Context replay requires semantic=false and helper=false'
            if reason is None and any(not (repo / path).resolve().is_relative_to(repo) for path in _path_values(call)):
                reason = 'Replay path leaves the selected repository'
            if reason:
                replayed.append({'tool': name, 'blocked': True, 'error': reason, 'is_error': True})
                continue
            started = time.perf_counter()
            try:
                response = asyncio.run(mcp.call_tool(name, args))
                error = bool(getattr(response, 'isError', False) or getattr(response, 'is_error', False))
                structured = getattr(response, 'structured_content', None)
                if structured is None:
                    structured = getattr(response, 'structuredContent', None)
                content = getattr(response, 'content', None)
                if isinstance(response, tuple):
                    content = response[0]
                    if len(response) > 1:
                        structured = response[1]
                if structured is None and content:
                    text = next((getattr(item, 'text', None) for item in content if getattr(item, 'text', None)), None)
                    try:
                        structured = json.loads(text) if text else None
                    except ValueError:
                        structured = text
                if isinstance(structured, dict) and set(structured) == {'result'}:
                    structured = structured['result']
                item = {'tool': name, 'result': structured, 'is_error': error}
                if error:
                    item['error'] = str(structured)
            except Exception as exc:
                item = {'tool': name, 'error': f'{type(exc).__name__}: {exc}', 'is_error': True}
            item['latency_ms'] = (time.perf_counter() - started) * 1000
            replayed.append(item)
    finally:
        os.chdir(previous)
    return {'repo': str(repo), 'calls': replayed, 'blocked_calls': sum(bool(item.get('blocked')) for item in replayed)}


def save_evaluation(report, repo, *, client=None, session_key=None, cases_data=None, traces_data=None):
    if bool(client) != bool(session_key):
        raise EvaluationInputError('Supply both --client and --session-key to associate an evaluation with a session')
    if client and client not in {'codex', 'claude'}:
        raise EvaluationInputError('Unsupported session client')
    if session_key and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_key):
        raise EvaluationInputError('Invalid session key')
    report.update(report_id=uuid.uuid4().hex, created_at=datetime.now(timezone.utc).isoformat(),
                  repo=str(repo.resolve()), client=client, session_key=session_key)
    report['input_fingerprints'] = {name: hashlib.sha256(_canonical(data).encode()).hexdigest()
                                    for name, data in [('cases', cases_data), ('traces', traces_data)] if data is not None}
    path = repo / '.ibwd' / 'usage' / 'evaluations' / (report['report_id'] + '.json')
    report['report_path'] = str(path.resolve())
    atomic_write(path, json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    return path
