"""Read-only retrieval evaluation against a frozen index and label file."""
import argparse
import hashlib
import json
import math
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from config import (
    CHROMA_PERSIST_DIR,
    EMBEDDING_MODEL,
    EMBEDDING_REVISION,
    RAG_DENSE_CANDIDATES,
    RAG_LEXICAL_CANDIDATES,
    RAG_RERANK_CANDIDATES,
    RAG_RERANK_WEIGHT,
    RERANKER_MODEL,
    RERANKER_REVISION,
)
from rag.hybrid import hybrid_search, reset_retrieval_cache
from rag.translate import translate_to_english
from rag.vector_store import get_games_collection, index_compatibility, index_manifest


# These are the only metadata filters currently present in the frozen game index.
# Other requirements remain semantic query text until the index is rebuilt with
# truthful fields for them.
FILTER_FIELDS = {
    'free': ('is_free', lambda value: bool(value)),
    'single_player': ('has_singleplayer', lambda value: bool(value)),
    'cooperative': ('has_coop', lambda value: bool(value)),
    'online_cooperative': ('has_online_coop', lambda value: bool(value)),
    'language': ('supports_schinese', lambda value: value == 'schinese'),
    'release_after': ('release_year', lambda value: {'$gte': int(str(value)[:4]) + 1}),
}


def _translate_query(query):
    if any('\u4e00' <= char <= '\u9fff' for char in query):
        translated = translate_to_english(query) or query
        return translated, translated != query
    return query, False


def _build_where(case, metadata_fields):
    where = []
    unsupported = []
    for key, value in case.get('required_filters', {}).items():
        spec = FILTER_FIELDS.get(key)
        if spec is None or spec[0] not in metadata_fields:
            unsupported.append(key)
            continue
        field, convert = spec
        try:
            where.append({field: convert(value)})
        except (TypeError, ValueError):
            unsupported.append(key)
    expression = None
    if len(where) == 1:
        expression = where[0]
    elif len(where) > 1:
        expression = {'$and': where}
    return expression, unsupported


def metrics(ids, case):
    relevant = set(case['relevant_appids'])
    acceptable = set(case['acceptable_appids'])
    if not relevant:
        return {'empty_result_correct': not ids}
    result = {}
    for k in (5, 10):
        hits = len(set(ids[:k]) & relevant)
        result[f'recall_at_{k}'] = hits / len(relevant)
        result[f'precision_at_{k}'] = hits / k
    result['mrr_at_10'] = next((1 / i for i, appid in enumerate(ids[:10], 1) if appid in relevant), 0)
    def gain(appid):
        return 3 if appid in relevant else 1 if appid in acceptable else 0
    dcg = sum(gain(appid) / math.log2(i + 1) for i, appid in enumerate(ids[:10], 1))
    ideal = sorted([3] * len(relevant) + [1] * len(acceptable), reverse=True)[:10]
    result['ndcg_at_10'] = dcg / sum(g / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    result['duplicate_rate_at_10'] = 1 - len(set(ids[:10])) / len(ids[:10]) if ids else 0
    result['forbidden_hit_rate_at_10'] = len(set(ids[:10]) & set(case['forbidden_appids'])) / 10
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--query-field', default='query')
    parser.add_argument('--configs', default='', help='Comma-separated configuration names; empty runs all.')
    parser.add_argument(
        '--include-unfilterable', action='store_true',
        help='Run cases with unsupported hard filters as semantic-only cases.',
    )
    args = parser.parse_args()
    compatibility = index_compatibility()
    if not compatibility['compatible']:
        raise RuntimeError(f'Index is incompatible with the running environment: {compatibility}')
    manifest = index_manifest()
    cache_path = Path(CHROMA_PERSIST_DIR) / 'game_cache.json'
    if manifest.get('cache_sha256'):
        if not cache_path.exists():
            raise RuntimeError('BM25 cache is missing; refusing to evaluate.')
        cache_hash = hashlib.sha256(cache_path.read_bytes()).hexdigest()
        if cache_hash != manifest['cache_sha256']:
            raise RuntimeError(
                'BM25 cache does not match the frozen index manifest; refusing to evaluate.'
            )
    dataset = json.loads(args.dataset.read_text(encoding='utf-8'))
    collection = get_games_collection()
    metadatas = collection.get(include=['metadatas'])['metadatas']
    if manifest.get('vector_count') is not None and collection.count() != int(manifest['vector_count']):
        raise RuntimeError('Vector count does not match the frozen index manifest; refusing to evaluate.')
    indexed = {str(m['appid']) for m in metadatas}
    metadata_fields = set().union(*(set(m) for m in metadatas)) if metadatas else set()
    if not metadata_fields:
        raise RuntimeError('Frozen index has no metadata; refusing to evaluate filters.')
    missing = sorted({a for c in dataset['cases'] for key in ('relevant_appids', 'acceptable_appids', 'forbidden_appids') for a in c[key]} - indexed)
    report = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'executed_provisional_labels', 'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        'manifest': manifest, 'compatibility': compatibility,
        'environment': platform.platform(), 'indexed_game_count': len(indexed), 'missing_label_appids': missing,
        'retrieval_contract': {
            'embedding_model': EMBEDDING_MODEL,
            'embedding_revision': EMBEDDING_REVISION,
            'reranker_model': RERANKER_MODEL,
            'reranker_revision': RERANKER_REVISION,
            'dense_candidates': RAG_DENSE_CANDIDATES,
            'lexical_candidates': RAG_LEXICAL_CANDIDATES,
            'rerank_candidates': RAG_RERANK_CANDIDATES,
            'reranker_weight': RAG_RERANK_WEIGHT,
            'top_k': 10,
            'query_preprocessing': 'production translation_to_english for Chinese queries',
            'metadata_fields': sorted(metadata_fields),
        },
        'metric_policy': 'Macro mean on nonempty relevant labels; P@k denominator k; nDCG gains 3/1 (grades 2/1); empty-label cases reported separately.',
        'limitations': ['Gold labels are provisional and contain hard-filter violations.', 'Filter precision/recall and evidence coverage unavailable without independent complete annotations.', 'Filtered mode applies only representable boolean filters; unsupported conditions are listed.', 'Duplicate rate is post-AppID aggregation only.'],
        'configurations': {},
    }
    configs = {
        'Dense': (True, False, False), 'BM25': (False, True, False),
        'Hybrid-RRF': (True, True, False), 'Hybrid-RRF-Rerank': (True, True, True),
        'Filtered-Hybrid': (True, True, True),
    }
    if args.configs:
        selected = {name.strip() for name in args.configs.split(',')}
        configs = {name: value for name, value in configs.items() if name in selected}
    for name, (dense, lexical, rerank) in configs.items():
        reset_retrieval_cache()
        # Warm up with a neutral query; do not include model loading in latency.
        hybrid_search('cooperative puzzle adventure', top_k=10, use_dense=dense, use_lexical=lexical, use_reranker=rerank)
        rows = []
        for case in dataset['cases']:
            expression, unsupported = _build_where(case, metadata_fields)
            if name != 'Filtered-Hybrid':
                expression = None
            if name == 'Filtered-Hybrid' and unsupported and not args.include_unfilterable:
                rows.append({
                    'id': case['id'], 'query': case.get(args.query_field) or case['query'],
                    'status': 'skipped_unsupported_filters',
                    'unsupported_filters': unsupported,
                })
                continue
            started = time.perf_counter()
            query = case.get(args.query_field) or case['query']
            search_query, translated = _translate_query(query)
            output = hybrid_search(search_query, top_k=10, where=expression, use_dense=dense, use_lexical=lexical, use_reranker=rerank)
            retrieval_status = output.get('retrieval', {}).get('status', 'ok')
            ids = [str(row['appid']) for row in output['results']]
            rows.append({'id': case['id'], 'query': search_query, 'query_original': query, 'translated': translated, 'status': 'error' if retrieval_status in {'dense_error', 'unsupported_filter'} else 'executed', 'results': [{'appid': str(r['appid']), 'name': r['metadata'].get('name'), 'score': r['final_score']} for r in output['results']], 'metrics': metrics(ids, case), 'latency_ms': (time.perf_counter() - started) * 1000, 'retrieval': output['retrieval'], 'applied_where': expression, 'unsupported_filters': unsupported})
            print(name, case['id'], 'returned', len(ids), 'dense_error', output['retrieval'].get('dense_error', ''), flush=True)
        executed_rows = [row for row in rows if row.get('status') == 'executed']
        error_rows = [row for row in rows if row.get('status') == 'error']
        skipped_rows = [row for row in rows if row.get('status') == 'skipped_unsupported_filters']
        aggregate = {}
        for key in sorted({k for row in executed_rows for k in row['metrics']}):
            aggregate[key] = statistics.mean(row['metrics'][key] for row in executed_rows if key in row['metrics'])
        latencies = sorted(r['latency_ms'] for r in executed_rows)
        if latencies:
            aggregate.update({'latency_mean_ms': statistics.mean(latencies), 'latency_p50_ms': statistics.median(latencies), 'latency_p95_ms': latencies[math.ceil(.95 * len(latencies)) - 1]})
        aggregate.update({'filter_precision': None, 'filter_recall': None, 'evidence_coverage': None, 'evaluated_cases': len(executed_rows), 'skipped_cases': len(skipped_rows), 'error_cases': len(error_rows), 'coverage': len(executed_rows) / len(rows) if rows else 0.0})
        valid = bool(executed_rows) and not error_rows and len(executed_rows) == len(rows) and not any(r['retrieval'].get('dense_error') or (rerank and r['results'] and not r['retrieval']['reranker_used']) for r in executed_rows)
        report['configurations'][name] = {'valid_execution': valid, 'cache_policy': 'lexical cache reset per configuration; model/client caches may remain warm', 'aggregate': aggregate, 'cases': rows}
        temporary_output = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary_output.replace(args.output)
    print('REPORT', args.output, flush=True)


if __name__ == '__main__':
    main()
