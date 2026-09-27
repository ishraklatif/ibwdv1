"""Private subprocess protocol. Optional dependencies and local weights only."""
import json
from pathlib import Path
import sys


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


if __name__ == '__main__':
    main()
