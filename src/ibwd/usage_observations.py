"""Content-free facts and explicitly qualified automatic session assessments."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePath


def objects(value):
    """Decode complete JSON blocks, never execution source or arbitrary substrings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for block in value:
            if isinstance(block, dict) and block.get('type') in {'text', 'input_text'}:
                yield from objects(block.get('text'))


def receipt(value):
    if not isinstance(value, dict):
        return None
    meta = value.get('_meta', value.get('meta', {}))
    data = value.get('ibwd_observation') or (meta.get('ibwd') if isinstance(meta, dict) else None)
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        return None
    oid = data.get('observation_id')
    return data if isinstance(oid, str) and re.fullmatch(r'[0-9a-f]{32}', oid) else None


def response_facts(value):
    """Read a known IBWD response without retaining source text or paths."""
    data = value.get('structuredContent', value.get('structured_content', value))
    if isinstance(data, dict) and 'result' in data:
        data = data['result']
    if isinstance(data, dict) and 'content' in data:
        data = next((v for v in objects(data['content']) if 'schema_version' in v), {})
    if not isinstance(data, dict) or data.get('schema_version') != 2:
        return None
    if not isinstance(data.get('items'), list) or not isinstance(data.get('index_generation'), str):
        return None
    files, semantic = data.get('files'), data.get('semantic')
    return dict(items=len(data['items']), files=len(files) if isinstance(files, dict) else 0,
                truncated=data.get('truncated') is True,
                semantic=semantic.get('status', 'not recorded') if isinstance(semantic, dict) else 'not recorded')


def command_kind(name, arguments):
    if name in {'apply_patch', 'functions.apply_patch', 'Edit', 'Write', 'MultiEdit'}:
        return 'edit'
    if name in {'exec_command', 'functions.exec_command', 'Bash'}:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        command = arguments.get('cmd', arguments.get('command', '')) if isinstance(arguments, dict) else ''
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
            lexer.whitespace_split = True
            parts = list(lexer)
        except (ValueError, TypeError):
            parts = []
        if not parts:
            return 'command'
        # `&&`/`;` chains (e.g. `cd services/x && pytest`) still guarantee the exit
        # code reflects the final segment; pipes/redirects/`&`/`||` do not, so those
        # still fall back to 'command'.
        segments, current = [], []
        for token in parts:
            if token in (';', '&&'):
                segments.append(current)
                current = []
            elif set(token) <= set(';&|<>'):
                return 'command'
            else:
                current.append(token)
        segments.append(current)
        last = segments[-1]
        if not last:
            return 'command'
        program, rest = PurePath(last[0]).name, last[1:]
        if program == 'npx' and rest:
            program, rest = PurePath(rest[0]).name, rest[1:]
        check = (program == 'pytest' or
                 program in {'jest', 'vitest'} or
                 program.startswith('python') and rest[:2] in (['-m', 'pytest'], ['-m', 'unittest']) or
                 program in {'go', 'cargo', 'npm', 'yarn', 'pnpm'} and rest[:1] == ['test'] or
                 program in {'npm', 'yarn', 'pnpm'} and rest[:2] == ['run', 'test'])
        if check:
            return 'check'
        return 'command'
    return 'tool'


def exit_status(output):
    for value in objects(output):
        if type(value.get('exit_code')) is int and ('output' in value or 'chunk_id' in value):
            return value['exit_code']
    if isinstance(output, str):
        header = output.split('Output:', 1)[0][:512]
        found = re.search(r'(?m)^(?:Process exited with code |Exit code: )(-?\d+)', header)
        if found:
            return int(found[1])
    return None


def assessments(report):
    activity = report.get('activity', {})
    work = activity.get('operations', {})
    config = report.get('observations', {}).get('configured', {}).get('value')
    calls = report.get('observed_ibwd_calls', 0)
    checks = work.get('checks_passed', 0) + work.get('checks_failed', 0)
    facts = report.get('response_evidence', {})
    source = 'Automatic; observed transcript events, not an independent task grade.'
    status = 'Turn in progress' if activity.get('turn_active') else 'Reply completed; outcome unverified'
    if activity.get('turn_active') is None:
        status = 'Recorded snapshot; outcome unverified'
    if activity.get('session_ended'):
        status = 'Session ended; outcome unverified'
    kinds = activity.get('task_kinds', [])
    category = 'mixed' if len(kinds) > 1 else (kinds[0] if kinds else 'unclassified activity')
    validation = (f"{work.get('checks_passed', 0)} passed, {work.get('checks_failed', 0)} failed"
                  if checks else 'No identifiable check results')
    if work.get('checks_pending'):
        validation += f"; {work['checks_pending']} pending"
    if activity.get('edits_after_check'):
        validation += '; later edits not checked'
    retrieved = facts.get('responses', 0)
    result = {
        'outcome': dict(value=status, source=source),
        'task_kind': dict(value=category, source='Inferred from tool activity and request keywords.'),
        'condition': dict(value='enabled (calls observed)' if calls else
                          ('configured at capture' if config is True else
                           'disabled at capture' if config is False else 'No configuration evidence'),
                          source='Recorded calls and current project configuration; not a whole-session treatment label.'),
        'rework': dict(value=f"{work.get('failed', 0)} nonzero tool exits observed",
                       source='Nonzero exits include checks or searches; they do not by themselves establish rework.'),
        'retrieval_usefulness': dict(value=f"{facts.get('items', 0)} items in {retrieved} responses; relevance ungraded"
                                   if retrieved else f'{calls} calls observed; response detail unavailable' if calls
                                   else 'No retrieval observed',
                                   source='Returned evidence counts do not establish task usefulness.'),
        'validation': dict(value=validation, source='Recognized check commands with captured exit codes; not a task-success verdict.'),
    }
    for name in ('outcome', 'task_kind', 'condition', 'rework', 'retrieval_usefulness'):
        if report.get(name, 'unknown') != 'unknown':
            result[name] = dict(value=report[name], source='Explicit user assessment for this snapshot.')
    return result


def assessment_value(report, field):
    return assessments(report).get(field, {}).get('value', 'Not recorded')
