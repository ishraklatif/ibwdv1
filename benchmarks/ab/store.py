"""Attempt store: one immutable JSON record per attempt plus its raw logs; atomic writes; resume by inspection of what exists."""
from __future__ import annotations

import json
import os
from pathlib import Path

FINAL_INVALID = {"budget_exhausted", "error_result", "usage_missing", "malformed", "no_result", "conflicting_results", "timeout", "nonzero_exit"}
RETRYABLE = {"infra_failure"}


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "attempts").mkdir(parents=True, exist_ok=True)
        (self.root / "raw").mkdir(exist_ok=True)

    def _key(self, session_id: str) -> str:
        return session_id.replace("/", "__")

    def attempts(self, session_id: str) -> list[dict]:
        k = self._key(session_id)
        return [json.loads(p.read_text()) for p in sorted((self.root / "attempts").glob(f"{k}__a*.json"))]

    def next_attempt_number(self, session_id: str) -> int:
        k = self._key(session_id)
        started = {int(p.name.rsplit("__a", 1)[1].split(".")[0]) for p in (self.root / "raw").glob(f"{k}__a*.started")}
        done = {a["attempt"] for a in self.attempts(session_id)}
        return max(started | done | {0}) + 1

    def interrupted(self, session_id: str) -> list[int]:
        """Attempts that started but never produced a record (the process was interrupted; its cost, if any, is unknown)."""
        k = self._key(session_id)
        started = {int(p.name.rsplit("__a", 1)[1].split(".")[0]) for p in (self.root / "raw").glob(f"{k}__a*.started")}
        return sorted(started - {a["attempt"] for a in self.attempts(session_id)})

    def mark_started(self, session_id: str, attempt: int) -> None:
        _atomic(self.root / "raw" / f"{self._key(session_id)}__a{attempt}.started", "")

    def save(self, record: dict, stdout: str, stderr: str) -> None:
        k, a = self._key(record["session_id"]), record["attempt"]
        _atomic(self.root / "raw" / f"{k}__a{a}.stdout.jsonl", stdout)
        _atomic(self.root / "raw" / f"{k}__a{a}.stderr.txt", stderr)
        _atomic(self.root / "attempts" / f"{k}__a{a}.json", json.dumps(record, indent=1, sort_keys=True) + "\n")   # the record is written LAST: it is the checkpoint

    def is_complete(self, session_id: str, max_infra_retries: int) -> bool:
        atts = self.attempts(session_id)
        if any(a["status"] == "ok" or a["status"] in FINAL_INVALID for a in atts):
            return True
        return sum(1 for a in atts if a["status"] in RETRYABLE) > max_infra_retries        # infra failures exhausted: stop retrying, keep them all

    def all_attempts(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.root / "attempts").glob("*.json"))]
