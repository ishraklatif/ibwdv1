"""Private subprocess protocol. Optional dependencies and local weights only."""
import json
from pathlib import Path
import sys
from contextlib import redirect_stdout
import os


def main():
    from sentence_transformers import SentenceTransformer

    request = json.loads(Path(sys.argv[1]).read_text())
    model = SentenceTransformer(request['model_path'], device='cpu', local_files_only=True,
                                trust_remote_code=False)
    if model.get_sentence_embedding_dimension() != request['dimensions']:
        raise ValueError('Configured dimensions do not match the local model')
    # Bound token work as well as byte work; no implicit dimensional truncation.
    model.max_seq_length = min(model.max_seq_length, 512)
    vectors = model.encode(request['texts'], batch_size=8, show_progress_bar=False,
                           normalize_embeddings=True, convert_to_numpy=True, prompt='')
    Path(sys.argv[2]).write_text(json.dumps(vectors.tolist(), allow_nan=False))


def serve():
    # Third-party startup/progress output must not enter the JSON-line protocol.
    with open(os.devnull, 'w') as quiet, redirect_stdout(quiet):
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(sys.argv[2], device='cpu', local_files_only=True,
                                    trust_remote_code=False)
        model.max_seq_length = min(model.max_seq_length, 512)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if model.get_sentence_embedding_dimension() != request['dimensions']:
                raise ValueError('dimensions')
            with open(os.devnull, 'w') as quiet, redirect_stdout(quiet):
                vectors = model.encode(request['texts'], batch_size=1, show_progress_bar=False,
                                       normalize_embeddings=True, convert_to_numpy=True, prompt='')
            print(json.dumps(vectors.tolist(), allow_nan=False), flush=True)
        except Exception:
            print('{"error":"embedding failed"}', flush=True)


if __name__ == '__main__':
    serve() if sys.argv[1] == '--serve' else main()
