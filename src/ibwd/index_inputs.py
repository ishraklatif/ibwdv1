"""Fingerprint alias configuration, including ignored local extends dependencies."""
import hashlib
import json
from pathlib import Path

from ibwd.graph.modules import TS_CONFIG_NAMES, _strip_jsonc


def config_digest(root: Path, scanned) -> str:
    visited = {}
    def read(path, depth=0):
        path = path.resolve()
        if path in visited or depth > 10:
            return
        if not path.is_file():
            visited[path] = None
            return
        data = path.read_bytes()
        visited[path] = hashlib.sha256(data).hexdigest()
        try:
            config = json.loads(_strip_jsonc(data.decode(errors='replace')))
        except ValueError:
            return
        parent = config.get('extends') if isinstance(config, dict) else None
        if isinstance(parent, str) and parent.startswith('.'):
            read(path.parent / (parent if parent.endswith('.json') else parent + '.json'), depth + 1)
    directories = set()
    for file in scanned:
        if file.kind not in ('source', 'test'):
            continue
        directory = (root / file.path).parent
        while directory.is_relative_to(root):
            directories.add(directory)
            if directory == root:
                break
            directory = directory.parent
    for directory in sorted(directories):
        for name in TS_CONFIG_NAMES:
            read(directory / name)
    return hashlib.sha256(json.dumps(sorted((str(p), h) for p, h in visited.items())).encode()).hexdigest()
