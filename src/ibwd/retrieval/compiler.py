"""Explicit installed TypeScript adapter. No downloads or graph promotion."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from ibwd.retrieval.bounded import encoded_size
from ibwd.retrieval.service import fresh_query


def compiler_evidence(root, file, line, column, project='tsconfig.json', compiler=None, max_bytes=16384):
    root = Path(root).resolve()
    if type(line) is not int or type(column) is not int or min(line, column) < 1:
        raise ValueError('line and column must be positive (one-based UTF-16 positions)')
    if type(max_bytes) is not int or not 256 <= max_bytes <= 65536:
        raise ValueError('max_bytes must be 256..65536')
    paths = [(root / p).resolve() for p in (file, project)]
    if any(not p.is_relative_to(root) for p in paths):
        raise ValueError('file and project must be inside the repository')
    file = paths[0].relative_to(root).as_posix()
    compiler_path = Path(compiler).expanduser().resolve() if compiler else root / 'node_modules/typescript/lib/typescript.js'
    node = shutil.which('node')

    def query(conn, generation):
        result = dict(schema_version=2, index_generation=generation, status='unknown', items=[],
                      compiler='typescript', reason='Installed Node/TypeScript/project unavailable')
        inventory = dict(conn.execute("SELECT file_path,content_hash FROM nodes WHERE node_type='File'"))
        if file not in inventory:
            raise ValueError('File is outside the indexed repository inventory')
        if node and compiler_path.is_file() and all(p.is_file() for p in paths):
            project_hash = hashlib.sha256(paths[1].read_bytes()).hexdigest()
            compiler_hash = hashlib.sha256(compiler_path.read_bytes()).hexdigest()
            adapter = Path(__file__).parents[1] / 'resources/typescript-evidence.cjs'
            with tempfile.TemporaryDirectory(prefix='ibwd-compiler-') as directory:
                out = Path(directory) / 'evidence.json'
                try:
                    completed = subprocess.run([node, '--max-old-space-size=512', str(adapter), str(compiler_path),
                        str(root), str(paths[1]), str(paths[0]), str(line), str(column), str(out)],
                        cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
                    if completed.returncode or not out.is_file():
                        result['reason'] = 'Compiler failed; environment or project is incomplete'
                    elif out.stat().st_size > 2_000_000:
                        result['reason'] = 'Compiler output exceeded resource budget'
                    else:
                        result.update(json.loads(out.read_text()))
                        if result['status'] != 'unknown':
                            result.pop('reason', None)
                        result['files'] = {p: inventory[p] for p in sorted({file} | {i['file'] for i in result['items']}) if p in inventory}
                        result['project_sha256'] = project_hash
                        result['compiler_sha256'] = compiler_hash
                        if (hashlib.sha256(paths[1].read_bytes()).hexdigest() != project_hash
                                or hashlib.sha256(compiler_path.read_bytes()).hexdigest() != compiler_hash):
                            result.update(status='unknown', items=[], reason='Compiler or project changed during query')
                        result['execution'] = 'local installed compiler; no emit; 20s deadline; 512 MiB V8 heap'
                except subprocess.TimeoutExpired:
                    result['reason'] = 'Compiler deadline exceeded; environment evidence is unknown'
        if encoded_size(result) > max_bytes:
            raise ValueError('Compiler evidence exceeds output budget; increase max_bytes')
        return result
    return fresh_query(root, query)
