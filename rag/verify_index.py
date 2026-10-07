"""Offline integrity checks and real semantic retrieval probes for deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
import transformers
import tokenizers

from config import CHROMA_PERSIST_DIR
from rag.hybrid import hybrid_search
from rag.embedder import get_embedder
from rag.vector_store import _get_client, current_games_collection_name, index_compatibility, index_manifest


def main(output):
    manifest = index_manifest()
    compatibility = index_compatibility()
    if not compatibility['compatible']:
        raise RuntimeError(f'Incompatible index: {compatibility}')
    collection = _get_client().get_collection(current_games_collection_name())
    rows = collection.get(include=['embeddings', 'metadatas', 'documents'])
    vectors = np.asarray(rows['embeddings'])
    game_ids = {meta['appid'] for meta in rows['metadatas']}
    if len(game_ids) != 1000 or len(rows['ids']) != manifest['vector_count']:
        raise RuntimeError('Index counts do not match manifest')
    if vectors.shape[1] != manifest['embedding_dimension'] or not np.isfinite(vectors).all():
        raise RuntimeError('Invalid embedding dimensions or values')
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4):
        raise RuntimeError('Embeddings are not normalized')
    model = get_embedder()
    if any(len(model.tokenizer.encode(document)) > model.max_seq_length for document in rows['documents']):
        raise RuntimeError('A document exceeds the embedding token limit')
    pointer_path = Path(CHROMA_PERSIST_DIR) / 'current_index.json'
    pointer = json.loads(pointer_path.read_text(encoding='utf-8')) if pointer_path.exists() else {}
    cache = Path(CHROMA_PERSIST_DIR) / pointer.get('cache', 'game_cache.json')
    if hashlib.sha256(cache.read_bytes()).hexdigest() != manifest['cache_sha256']:
        raise RuntimeError('Cache digest mismatch')
    probes = [
        ('single-player underwater survival exploration crafting submarine base building', {'has_singleplayer': True}, '264710'),
        ('cooperative dwarves mining minerals fighting alien bugs in caves', {'has_coop': True}, '548430'),
        ('relaxing farming growing crops fishing relationships village life', {'has_singleplayer': True}, '413150'),
    ]
    report = {'game_count': len(game_ids), 'vector_count': len(rows['ids']),
              'embedding_dimension': vectors.shape[1], 'compatibility': compatibility, 'probes': []}
    for query, where, expected in probes:
        result = hybrid_search(query, top_k=10, where=where)
        ids = [row['appid'] for row in result['results']]
        if len(ids) != len(set(ids)) or result['retrieval']['dense_error'] or not result['retrieval']['reranker_used']:
            raise RuntimeError(f'Retrieval probe failed: {result["retrieval"]}')
        if expected not in ids:
            raise RuntimeError(f'Expected game {expected} missing from probe: {ids}')
        report['probes'].append({'query': query, 'expected_appid': expected,
                                'expected_rank': ids.index(expected)+1,
                                'results': [{'appid': row['appid'], 'name': row['metadata']['name']} for row in result['results']],
                                'retrieval': result['retrieval']})
        print('Probe passed', expected, 'rank', ids.index(expected)+1, flush=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest['runtime_versions'] = {'torch': torch.__version__, 'transformers': transformers.__version__, 'tokenizers': tokenizers.__version__}
    audit_path = Path(CHROMA_PERSIST_DIR) / 'source_audit.json'
    if audit_path.exists():
        manifest['source_audit_sha256'] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    path = Path(CHROMA_PERSIST_DIR) / pointer.get('manifest', 'index_manifest.json')
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    print('VERIFIED', len(game_ids), 'games', len(rows['ids']), 'vectors', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
