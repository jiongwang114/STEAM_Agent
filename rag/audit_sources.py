"""Validate downloaded official batches before allowing local indexing."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from rag.ingest import _strip_html, build_chunk


def audit(directory: Path, count: int = 1000):
    records = []
    batch_hashes = {}
    for path in sorted(directory.glob('batch-*.json')):
        batch_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        records.extend(json.loads(path.read_text(encoding='utf-8')))
    raw_count = len(records)
    if len({r['appid'] for r in records}) != raw_count:
        raise ValueError('Duplicate games in source batches')
    excluded = []
    eligible = []
    supplement_path = directory / 'publisher-supplements.json'
    supplements = json.loads(supplement_path.read_text(encoding='utf-8')) if supplement_path.exists() else {}
    for record in records:
        detail = record['detail']
        supplement = supplements.get(str(record['appid']))
        if supplement:
            if urlparse(supplement['url']).netloc not in ('www.cyberpunk.net', 'www.thewitcher.com'):
                raise ValueError('Unverified publisher domain')
            filename = {1091500: 'cyberpunk-official.html', 292030: 'witcher-official.html'}[record['appid']]
            html_path = directory / filename
            if hashlib.sha256(html_path.read_bytes()).hexdigest() != supplement['html_sha256']:
                raise ValueError('Publisher HTML digest mismatch')
            readable_html = _strip_html(html_path.read_text(encoding='utf-8'))
            if any(' '.join(paragraph.split()) not in readable_html for paragraph in supplement['text'].splitlines()):
                raise ValueError('Publisher paragraph is not present in downloaded HTML')
            detail['about_the_game'] = supplement['text']
            record['provenance']['semantic_supplement'] = supplement
        about = _strip_html(detail.get('about_the_game', ''))
        short = _strip_html(detail.get('short_description', ''))
        if not about or not short or len(about + short) < 200:
            excluded.append({'appid': record['appid'], 'name': detail['name'], 'reason': 'insufficient readable semantic description'})
        else:
            eligible.append(record)
    if len(eligible) < count:
        raise ValueError(f'Expected {count} eligible games, got {len(eligible)}')
    records = eligible[:count]
    tags_path = directory / 'tag-metadata.json'
    tags = json.loads(tags_path.read_text(encoding='utf-8')) if tags_path.exists() else {'tags': {}}
    if not tags_path.exists() or any(urlparse(url).netloc != 'store.steampowered.com' for url in tags.get('sources', [])):
        raise ValueError('Missing or invalid official tag sources')
    translations_path = directory / 'translations.json'
    translations = json.loads(translations_path.read_text(encoding='utf-8')) if translations_path.exists() else {}
    genres, modes, years = Counter(), Counter(), Counter()
    lengths = []
    for record in records:
        detail, provenance = record['detail'], record['provenance']
        url = urlparse(provenance['detail_url'])
        if url.scheme != 'https' or url.netloc != 'store.steampowered.com' or url.path != '/api/appdetails':
            raise ValueError('Unexpected detail source')
        if parse_qs(url.query)['appids'] != [str(record['appid'])]:
            raise ValueError('Source AppID mismatch')
        if detail['steam_appid'] != record['appid'] or detail['type'] != 'game':
            raise ValueError('Invalid game identity')
        if not provenance.get('collected_at') or not provenance.get('selection'):
            raise ValueError('Missing provenance')
        for selection in provenance['selection']:
            if urlparse(selection['url']).netloc != 'store.steampowered.com':
                raise ValueError('Unexpected selection source')
        text = _strip_html(detail.get('about_the_game', ''))
        if not text or not _strip_html(detail.get('short_description', '')):
            raise ValueError('Missing semantic description')
        if len(text + _strip_html(detail['short_description'])) < 200:
            raise ValueError(f'Insufficient semantic text: {record["appid"]}')
        record['user_tags'] = tags['tags'].get(str(record['appid']), [])
        provenance['tag_catalog_url'] = 'https://store.steampowered.com/tagdata/populartags/english'
        translation = translations.get(str(record['appid']))
        if translation:
            original = {'short_description': _strip_html(detail['short_description']), 'about_the_game': text}
            digest = hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != translation['source_sha256']:
                raise ValueError('Translation source mismatch')
            detail['short_description_en'] = translation['text']['short_description']
            detail['about_the_game_en'] = translation['text']['about_the_game']
            provenance['translation'] = {'model': translation['model'], 'source_sha256': digest, 'derived': True}
        else:
            combined = text + _strip_html(detail['short_description'])
            if sum(c.isalpha() and ord(c) < 128 for c in combined) < sum(c.isalpha() and ord(c) > 127 for c in combined):
                raise ValueError(f'Localized description requires translation: {record["appid"]}')
        genres.update(g['description'] for g in detail.get('genres', []))
        modes.update(c['description'] for c in detail.get('categories', []))
        _, meta, document = build_chunk(record['appid'], detail, record['user_tags'])
        years.update([meta['release_year']])
        lengths.append(len(document))
    report = {
        'downloaded_records': raw_count, 'excluded_semantic_records': excluded,
        'unused_valid_reserve_records': len(eligible) - count,
        'game_count': len(records), 'unique_appids': len({r['appid'] for r in records}),
        'all_detail_sources': 'https://store.steampowered.com/api/appdetails',
        'semantic_supplement_sources': [s['url'] for s in supplements.values()],
        'batch_sha256': batch_hashes, 'genres': dict(genres), 'play_modes': dict(modes),
        'release_years': dict(sorted(years.items())),
        'pre_2015_games': sum(v for k,v in years.items() if 0 < k < 2015),
        'document_characters': {'min': min(lengths), 'max': max(lengths), 'mean': round(sum(lengths)/len(lengths))},
        'games_with_user_tags': sum(bool(r['user_tags']) for r in records),
        'translated_games': len(translations),
        'tag_metadata_sha256': hashlib.sha256(tags_path.read_bytes()).hexdigest() if tags_path.exists() else '',
    }
    for genre in ('Action','Adventure','RPG','Strategy','Simulation','Casual','Sports','Racing','Indie'):
        if genres[genre] < 10:
            raise ValueError(f'Insufficient genre coverage: {genre}={genres[genre]}')
    for mode in ('Single-player','Multi-player','Co-op'):
        if modes[mode] < 10:
            raise ValueError(f'Insufficient play mode coverage: {mode}={modes[mode]}')
    if report['pre_2015_games'] < 20:
        raise ValueError('Insufficient older game coverage')
    if report['games_with_user_tags'] < count * 0.9:
        raise ValueError('Insufficient user tag coverage')
    (directory / 'audited_cache.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'audit_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    audit(args.directory)
