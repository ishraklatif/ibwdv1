"""Frozen experiment configuration: one JSON file, hashed, printed by preflight and recorded in every attempt."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "benchmarks" / "experiment" / "experiment.json"

PROMPT_TEMPLATE = (
    "You are answering a question about the source code of the repository in the current working directory. "
    "Use only the tools you have been given.\n\n"
    "Question:\n{question}\n\n"
    "Reply with ONLY one JSON object, no prose and no code fence, in exactly this shape:\n{schema}\n"
    'Use "task_id": "{task_id}". Include every item that belongs in the answer and nothing else.'
)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = json.loads(Path(path).read_text())
    cfg["_config_sha256"] = sha256_bytes(Path(path).read_bytes())
    return cfg


def load_tasks(cfg: dict, root: Path = ROOT) -> list[dict]:
    """The frozen task specs, in index order, verified against the recorded hashes."""
    index = json.loads((root / cfg["tasks_index"]).read_text())["tasks"]
    tasks = []
    for row in index:
        p = root / row["file"]
        if sha256_bytes(p.read_bytes()) != row["sha256"]:
            raise ValueError(f"task spec {row['task_id']} differs from its recorded hash")
        tasks.append(json.loads(p.read_text()))
    return tasks


def build_prompt(spec: dict) -> str:
    """The prompt for a task. It contains the question and the answer SHAPE only, never an expected answer, and is identical in both conditions."""
    return PROMPT_TEMPLATE.format(question=spec["question"], schema=json.dumps(spec["answer_schema"]), task_id=spec["task_id"])
