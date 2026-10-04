"""Low-memory, resumable Steam-only collection; no embedding dependencies."""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class Results(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tags = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get('data-ds-appid', '')
        if tag == 'a' and value.isdigit():
            self.ids.append(int(value))
            self.tags[value] = json.loads(attrs.get('data-ds-tagids', '[]'))


last_request = 0.0


def fetch(url):
    global last_request
    for attempt in range(3):
        time.sleep(max(0, 2.1 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        try:
            with urlopen(Request(url, headers={'User-Agent': 'SteamGameCatalog/1.0'}), timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code in (403, 429):
                raise RuntimeError(f'Collection paused: HTTP {error.code}; Retry-After={error.headers.get("Retry-After")}') from error
            if error.code < 500:
                return None
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(5 * (2 ** attempt))
    return None


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--tags-only', action='store_true')
    parser.add_argument('--extend', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    candidates_path = args.output / 'candidates.json'
    if args.extend:
        candidates = json.loads(candidates_path.read_text(encoding='utf-8'))
        sources = {source['url']: source for values in candidates.values() for source in values}
        buckets = []
        for url, original in sources.items():
            url = url.replace('start=0&', 'start=100&')
            if original['sort'] == '_ASC':
                url += '&filter=topsellers'
            data = fetch(url)
            if not data or not data.get('success'):
                raise RuntimeError('Candidate extension failed')
            parsed = Results()
            parsed.feed(data['results_html'])
            source = {**original, 'url': url, 'page': 2}
            buckets.append([(appid, source) for appid in parsed.ids])
        for rank in range(max(map(len, buckets))):
            for bucket in buckets:
                if rank < len(bucket):
                    appid, source = bucket[rank]
                    candidates.setdefault(str(appid), []).append(source)
        write(candidates_path, candidates)
        print('EXTENDED', len(candidates), flush=True)
        return
    if args.tags_only:
        candidates = json.loads(candidates_path.read_text(encoding='utf-8'))
        names = {r['tagid']: r['name'] for r in fetch('https://store.steampowered.com/tagdata/populartags/english')}
        urls = dict.fromkeys(source['url'] for sources in candidates.values() for source in sources)
        tags = {}
        for url in urls:
            data = fetch(url)
            if not data or not data.get('success'):
                raise RuntimeError('Tag source failed')
            parsed = Results()
            parsed.feed(data['results_html'])
            for appid, ids in parsed.tags.items():
                tags[appid] = [names[tag] for tag in ids if tag in names]
        write(args.output / 'tag-metadata.json', {'sources': list(urls), 'tags': tags})
        print('TAGS COMPLETE', len(tags), flush=True)
        return
    if not candidates_path.exists():
        buckets = []
        # Steam genre IDs and store category IDs; round robin avoids one-chart bias.
        groups = [('all', {}), *[(f'genre-{g}', {'genre': g}) for g in (1,2,3,4,9,23,25,28)],
                  *[(f'mode-{c}', {'category3': c}) for c in (2,1,9,38)],
                  *[(f'tag-{t}', {'tags': t}) for t in (19,1664,3834,492,599,4182,1628)]]
        for label, filters in groups:
            for sort in ('Reviews_DESC', '_ASC'):
                query = {'start': 0, 'count': 100, 'sort_by': sort, 'category1': 998,
                         'infinite': 1, 'l': 'english', 'cc': 'us', **filters}
                url = 'https://store.steampowered.com/search/results/?' + urlencode(query)
                data = fetch(url)
                if not data or not data.get('success'):
                    raise RuntimeError(f'Search failed: {label}')
                results = Results()
                results.feed(data['results_html'])
                buckets.append([(appid, {'bucket': label, 'sort': sort, 'url': url}) for appid in results.ids])
                print('candidates', label, sort, len(results.ids), flush=True)
        candidates = {}
        for rank in range(max(map(len, buckets))):
            for bucket in buckets:
                if rank < len(bucket):
                    appid, source = bucket[rank]
                    candidates.setdefault(str(appid), []).append(source)
        write(candidates_path, candidates)
    candidates = json.loads(candidates_path.read_text(encoding='utf-8'))
    existing = set()
    total = 0
    for path in args.output.glob('batch-*.json'):
        records = json.loads(path.read_text(encoding='utf-8'))
        existing.update(str(r['appid']) for r in records)
        total += len(records)
    pending = []
    batch_number = len(list(args.output.glob('batch-*.json')))
    for value, sources in candidates.items():
        if total >= args.count:
            break
        if value in existing:
            continue
        url = f'https://store.steampowered.com/api/appdetails?appids={value}&l=english&cc=us'
        data = fetch(url)
        item = (data or {}).get(value, {})
        detail = item.get('data', {})
        if not item.get('success') or detail.get('type') != 'game' or detail.get('release_date', {}).get('coming_soon'):
            continue
        if not detail.get('short_description') or not (detail.get('about_the_game') or detail.get('detailed_description')):
            continue
        fields = ('type','name','steam_appid','short_description','about_the_game','genres','categories',
                  'developers','release_date','is_free','metacritic','supported_languages','header_image')
        normalized = {k: detail[k] for k in fields if k in detail}
        if not normalized.get('about_the_game'):
            normalized['about_the_game'] = detail.get('detailed_description', '')
        pending.append({'appid': int(value), 'detail': normalized,
                        'provenance': {'provider': 'Steam official', 'detail_url': url,
                                       'collected_at': datetime.now(timezone.utc).isoformat(), 'selection': sources}})
        total += 1
        if len(pending) == 100 or total == args.count:
            batch_number += 1
            path = args.output / f'batch-{batch_number:03}.json'
            write(path, pending)
            print('saved', path.name, 'total', total, 'sha256', hashlib.sha256(path.read_bytes()).hexdigest(), flush=True)
            pending = []
    if pending:
        write(args.output / f'batch-{batch_number+1:03}.json', pending)
    if total != args.count:
        raise RuntimeError(f'Only {total} eligible games; target {args.count}')
    print('COMPLETE', total, flush=True)


if __name__ == '__main__':
    main()
