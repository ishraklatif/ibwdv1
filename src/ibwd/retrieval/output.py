"""Opt-in saved-log reduction. Unknown formats fail open to the full output."""
from pathlib import Path
import re


def reduce_output(log, format_name, exit_code):
    path = Path(log).resolve()
    if format_name not in {'pytest', 'tsc'}:
        raise ValueError('Supported formats: pytest, tsc.')
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Log exceeds 8 MiB; inspect the original file locally.')
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    failures = None
    if format_name == 'pytest':
        # The final summary can list both failures and collection/setup errors.
        summaries = [line for line in lines if re.search(r'\d+ (?:passed|failed|skipped|errors?)\b.* in ', line)]
        if summaries:
            failures = sum(int(n) for n, _ in re.findall(r'(\d+) (failed|errors?)\b', summaries[-1]))
        start = next((i for i, line in enumerate(lines) if re.match(r'=+ .*?(FAILURES|ERRORS)', line)), None)
        if start is not None:
            prefix = [line for line in lines[:start] if not re.fullmatch(r'[.s xXFE]+\s*\[\s*\d+%\]\s*\n?', line)]
            reduced = ''.join(prefix + lines[start:])  # Retain unknown diagnostics before failures, too.
        elif exit_code == 0 and summaries and failures == 0:
            # Drop only known progress/banner lines; retain warnings and unknown diagnostics.
            reduced = ''.join(line for line in lines if not re.fullmatch(r'[.s xX]+\s*\[\s*\d+%\]\s*\n?', line)
                              and not line.startswith('============================= test session starts'))
        else:
            reduced = text
    else:
        diagnostics = re.findall(r'\berror TS\d+:', text)
        failures = len(diagnostics) if diagnostics else (0 if exit_code == 0 and not text.strip() else None)
        # Preserve multi-line source excerpts and related diagnostics. Only ANSI color is dispensable.
        reduced = re.sub(r'\x1b\[[0-9;]*m', '', text)
    return dict(format=format_name, exit_code=exit_code, failure_count=failures,
                diagnostics=reduced, full_output_path=str(path), original_bytes=len(text.encode()),
                reduced_bytes=len(reduced.encode()), complete_diagnostics=True)
