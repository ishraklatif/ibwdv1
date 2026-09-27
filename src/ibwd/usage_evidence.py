"""Content-free retrieval evidence and deterministic ordinary-work recommendations."""
from collections import Counter
import statistics


def embedding_evidence(requests: list[dict]) -> dict:
    """Only context requests select embeddings; missing instrumentation stays unknown."""
    contexts = [e for e in requests if e.get('tool') == 'ibwd_context']
    modes, identities, elapsed = Counter(), set(), []
    attempted = 0
    attempts = [data for event in contexts
                for data in event.get('embedding_attempts', [event.get('embedding', {})])]
    for data in attempts:
        mode = data.get('mode', 'unknown')
        if mode not in {'disabled', 'used', 'fallback'}:
            mode = 'unknown'
        modes[mode] += 1
        identities.add((mode, data.get('model_digest') or 'unknown',
                        data.get('preprocessing_version') or 'unknown'))
        attempted += data.get('inference_attempted') is True
        duration = data.get('elapsed_ms')
        if mode != 'disabled' and type(duration) in (int, float) and duration >= 0:
            elapsed.append(duration)
    return {'context_requests': len(contexts), 'context_attempts': len(attempts), 'modes': dict(sorted(modes.items())),
            'profiles': [dict(zip(('mode', 'model_digest', 'preprocessing_version'), values))
                         for values in sorted(identities)],
            'inference_attempts': attempted, 'timed_optional_attempts': len(elapsed),
            'optional_elapsed_ms': round(sum(elapsed), 3),
            'median_optional_elapsed_ms': statistics.median(elapsed) if elapsed else None,
            'scope': 'Observed context attempts only, including freshness retries. Digest identifies indexed weights/configuration, '
                     'not a product name; on fallback it may identify an unusable index. '
                     'Elapsed time includes validation, model startup and ranking. Memory is not measured.'}


def embedding_profile(report: dict) -> tuple[str, ...]:
    evidence = report.get('server_evidence', {})
    embedding = evidence.get('embedding', {})
    if not embedding:
        return ('unknown',)
    profiles = tuple(sorted(':'.join(p.get(k, 'unknown') for k in
                            ('mode', 'model_digest', 'preprocessing_version'))
                            for p in embedding.get('profiles', [])))
    # A partial ledger must not classify a whole session as a known treatment.
    observed = report.get('observed_ibwd_calls')
    complete = (type(observed) is int and observed > 0 and observed == evidence.get('linked_requests'))
    if not complete:
        return ('incomplete_attribution', *profiles)
    return profiles or ('no_observed_context',)


def retrieval_evidence(events: list[dict]) -> dict:
    requests = {}
    for event in events:
        oid = event.get('observation_id')
        if oid and (oid not in requests or event.get('phase') == 'completed'):
            requests[oid] = event
    retrieval = [e for e in requests.values() if e.get('tool') != 'ibwd_scan']
    completed = [e for e in retrieval if e.get('phase') == 'completed']
    successful = [e for e in completed if e.get('status') == 'success']
    return {
        'linked_requests': len(requests),
        'retrieval_requests': len(retrieval),
        'completed_retrievals': len(completed),
        'errors': sum(e.get('status') == 'error' for e in completed),
        'freshness_validated': sum(e.get('freshness') == 'validated' for e in successful),
        'freshness_unknown': len(retrieval) - sum(e.get('freshness') == 'validated' for e in successful),
        'refreshes': sum(e.get('refreshed') is True for e in successful),
        'semantic_fallbacks': sum(e.get('semantic_status') == 'fallback' for e in completed),
        'truncated_responses': sum(e.get('truncated') is True for e in completed),
        'returned_items': sum(e['result_count'] for e in successful if type(e.get('result_count')) is int),
        'item_count_known_requests': sum(type(e.get('result_count')) is int for e in successful),
        'evidence_file_references': sum(e['evidence_files'] for e in successful if type(e.get('evidence_files')) is int),
        'response_bytes': sum(e['response_bytes'] for e in completed if type(e.get('response_bytes')) is int),
        'response_bytes_known_requests': sum(type(e.get('response_bytes')) is int for e in completed),
        'duration_ms': round(sum(e['duration_ms'] for e in completed if isinstance(e.get('duration_ms'), (int, float))), 3),
        'duration_known_requests': sum(isinstance(e.get('duration_ms'), (int, float)) for e in completed),
        'tools': dict(sorted(Counter(e.get('tool', 'unknown') for e in retrieval).items())),
        'embedding': embedding_evidence(retrieval),
        'join': 'Exact response _meta.ibwd.observation_id only; never timestamps or connection identity.',
        'scope': 'Retained linked requests only. Counts are lower bounds; file references are not unique files. '
                 'Freshness validates indexed inputs at retrieval, not completeness or current freshness. '
                 'Payload bytes are not model tokens; tool duration excludes client/model time. '
                 'Semantic fallback counts do not measure subsequent shell fallback.',
    }


def attention(report: dict) -> list[str]:
    evidence = report.get('server_evidence', {})
    suggestions = []
    if not evidence.get('linked_requests'):
        suggestions.append('Check response-ID capture and the retained server ledger; retrieval freshness is unknown.')
    if evidence.get('errors'):
        suggestions.append('Inspect recorded retrieval errors before tuning retrieval efficiency.')
    if evidence.get('semantic_fallbacks'):
        suggestions.append('Review optional semantic configuration; use deterministic retrieval while its index is unavailable.')
    if evidence.get('truncated_responses'):
        suggestions.append('Narrow broad queries or follow pagination before increasing response budgets.')
    if not suggestions:
        suggestions.append('No specific optimization is justified by the retained retrieval evidence.')
    return suggestions


def evidence_lines(report: dict) -> list[str]:
    evidence = report.get('server_evidence', {})
    lines = ['## Retrieval evidence', '']
    if evidence:
        for name in ('linked_requests', 'retrieval_requests', 'completed_retrievals', 'errors',
                     'freshness_validated', 'freshness_unknown', 'refreshes', 'semantic_fallbacks',
                     'truncated_responses', 'returned_items', 'item_count_known_requests',
                     'evidence_file_references', 'response_bytes', 'response_bytes_known_requests',
                     'duration_ms', 'duration_known_requests'):
            lines.append(f"- {name}: {evidence.get(name, 'unknown')}")
        lines += ['', evidence.get('scope', 'Legacy evidence; freshness and response details are unknown.')]
        embedding = evidence.get('embedding')
        if embedding:
            lines += ['', '## Embedding observations', '',
                      f"Modes: {embedding['modes']}",
                      f"Model profiles: {embedding['profiles']}",
                      f"Inference attempts: {embedding['inference_attempts']}",
                      f"Timed optional attempts: {embedding['timed_optional_attempts']}",
                      f"Median optional elapsed ms: {embedding['median_optional_elapsed_ms']}",
                      embedding['scope']]
    else:
        lines.append('No linked server evidence; freshness and response details are unknown.')
    lines += ['', '## Local attention', '']
    lines.extend(f'- {item}' for item in attention(report))
    return lines
