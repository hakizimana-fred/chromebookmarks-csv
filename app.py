#!/usr/bin/env python3
"""Export and explore Chromium-browser bookmarks (Chrome, Chromium, Brave, Edge, Vivaldi).

    python app.py                  # export CSV + JSON + interactive HTML, open the viewer
    python app.py --list           # show detected browsers/profiles
    python app.py --check          # also probe every URL for dead links
    python app.py -b brave -f csv  # one browser, one format
"""
import argparse
import csv
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

FIELDS = ['Title', 'URL', 'Folder', 'Domain', 'Added', 'Browser', 'Profile', 'Duplicate', 'Status']
HISTORY_FIELDS = ['Title', 'URL', 'Folder', 'Domain', 'Last visit', 'Visits', 'Browser', 'Profile']
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
TEMPLATE = Path(__file__).with_name('template.html')

# Browser name -> user-data dir, relative to the per-OS base directory
BROWSER_DIRS = {
    'Linux': (Path.home() / '.config', {
        'chrome': 'google-chrome',
        'chromium': 'chromium',
        'brave': 'BraveSoftware/Brave-Browser',
        'edge': 'microsoft-edge',
        'vivaldi': 'vivaldi',
    }),
    'Darwin': (Path.home() / 'Library/Application Support', {
        'chrome': 'Google/Chrome',
        'chromium': 'Chromium',
        'brave': 'BraveSoftware/Brave-Browser',
        'edge': 'Microsoft Edge',
        'vivaldi': 'Vivaldi',
    }),
    'Windows': (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')), {
        'chrome': r'Google\Chrome\User Data',
        'chromium': r'Chromium\User Data',
        'brave': r'BraveSoftware\Brave-Browser\User Data',
        'edge': r'Microsoft\Edge\User Data',
        'vivaldi': r'Vivaldi\User Data',
    }),
}


def find_profiles(only_browser=None, only_profile=None, filename='Bookmarks'):
    """Yield (browser, profile_name, data_file) for every profile found on disk."""
    system = platform.system()
    if system not in BROWSER_DIRS:
        sys.exit(f'System not supported: {system}')
    base, browsers = BROWSER_DIRS[system]
    for browser, subdir in browsers.items():
        if only_browser and browser != only_browser:
            continue
        root = base / subdir
        names = {}
        try:  # friendly profile names ("Work", "Personal") live in Local State
            state = json.loads((root / 'Local State').read_text(encoding='utf-8'))
            names = {k: v.get('name', k) for k, v in state['profile']['info_cache'].items()}
        except (OSError, ValueError, KeyError):
            pass
        for entry in sorted(root.glob(f'*/{filename}')) if root.is_dir() else []:
            dir_name = entry.parent.name
            profile = names.get(dir_name, dir_name)
            if only_profile and only_profile.lower() not in (profile.lower(), dir_name.lower()):
                continue
            yield browser, profile, entry


def chrome_time(value):
    """Chrome stores microseconds since 1601-01-01; return an ISO date or ''."""
    try:
        micros = int(value)
        return (CHROME_EPOCH + timedelta(microseconds=micros)).strftime('%Y-%m-%d') if micros else ''
    except (ValueError, TypeError, OverflowError):
        return ''


def walk(node, path, out):
    """Recursively collect url nodes, remembering the folder path they live in."""
    if node.get('type') == 'url':
        out.append((node.get('name', ''), node.get('url', ''), ' / '.join(path), node.get('date_added')))
    for child in node.get('children', []):
        sub = path + [child['name']] if child.get('type') == 'folder' else path
        walk(child, sub, out)


def normalize(url):
    """Key used to spot duplicates: ignore scheme, 'www.', fragment, trailing slash."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix('www.')
    query = f'?{parts.query}' if parts.query else ''
    return f'{host}{parts.path.rstrip("/")}{query}'


def load_bookmarks(only_browser=None, only_profile=None):
    rows = []
    for browser, profile, file in find_profiles(only_browser, only_profile):
        try:
            roots = json.loads(file.read_text(encoding='utf-8'))['roots']
        except (OSError, ValueError, KeyError) as error:
            print(f'  skipped {browser}/{profile}: {error}', file=sys.stderr)
            continue
        found = []
        for root in roots.values():
            if isinstance(root, dict):
                walk(root, [root.get('name', '')], found)
        for title, url, folder, added in found:
            if not url.startswith(('http://', 'https://', 'ftp://', 'file://')):
                continue  # skip javascript: bookmarklets, chrome:// pages etc.
            rows.append({
                'Title': title or url, 'URL': url, 'Folder': folder,
                'Domain': urlsplit(url).netloc.lower().removeprefix('www.'),
                'Added': chrome_time(added), 'Browser': browser, 'Profile': profile,
                'Duplicate': False, 'Status': '',
            })
    seen = {}
    for row in rows:
        seen.setdefault(normalize(row['URL']), []).append(row)
    for group in seen.values():
        if len(group) > 1:
            for row in group:
                row['Duplicate'] = True
    return rows


def load_history(only_browser=None, only_profile=None, days=None):
    """Read visited pages. Chrome locks its History DB while running, so query a copy."""
    rows = []
    cutoff = None
    if days:
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=days) - CHROME_EPOCH).total_seconds() * 1_000_000)
    for browser, profile, file in find_profiles(only_browser, only_profile, 'History'):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'History'
            try:
                shutil.copy2(file, copy)
                for suffix in ('-wal', '-journal'):  # uncommitted recent visits
                    if Path(f'{file}{suffix}').exists():
                        shutil.copy2(f'{file}{suffix}', f'{copy}{suffix}')
                db = sqlite3.connect(copy)
                query = 'SELECT url, title, visit_count, last_visit_time FROM urls WHERE hidden = 0 AND last_visit_time > ?'
                found = db.execute(query, (cutoff or 0,)).fetchall()
                db.close()
            except (OSError, sqlite3.Error) as error:
                print(f'  skipped history of {browser}/{profile}: {error}', file=sys.stderr)
                continue
        for url, title, visits, last in found:
            if not url.startswith(('http://', 'https://')):
                continue
            when = (CHROME_EPOCH + timedelta(microseconds=last)).astimezone()
            rows.append({
                'Title': title or url, 'URL': url, 'Folder': f'{when:%Y} / {when:%Y-%m}',
                'Domain': urlsplit(url).netloc.lower().removeprefix('www.'),
                'Last visit': f'{when:%Y-%m-%d %H:%M}', 'Visits': visits,
                'Browser': browser, 'Profile': profile,
            })
    return rows


def check_links(rows, workers=32, timeout=8):
    """Fill row['Status'] with an HTTP code, or 'dead' when the host can't be reached."""
    headers = {'User-Agent': 'Mozilla/5.0 (bookmark-checker)'}
    unique = sorted({r['URL'] for r in rows if r['URL'].startswith('http')})

    def probe(url):
        for method in ('HEAD', 'GET'):  # some servers reject HEAD
            try:
                with urlopen(Request(url, method=method, headers=headers), timeout=timeout) as resp:
                    return url, str(resp.status)
            except HTTPError as error:
                if method == 'GET' or error.code not in (403, 405, 501):
                    return url, str(error.code)
            except Exception:
                if method == 'GET':
                    return url, 'dead'
        return url, 'dead'

    done = 0
    with ThreadPoolExecutor(workers) as pool:
        results = {}
        for url, status in pool.map(probe, unique):
            results[url] = status
            done += 1
            print(f'\r  checking links {done}/{len(unique)}', end='', file=sys.stderr, flush=True)
    print(file=sys.stderr)
    for row in rows:
        row['Status'] = results.get(row['URL'], '')


def write_csv(rows, path, fields=FIELDS):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:  # BOM so Excel reads UTF-8
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows, path):
    Path(path).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')


def write_netscape(rows, path):
    """Standard bookmarks.html that every browser can import."""
    lines = ['<!DOCTYPE NETSCAPE-Bookmark-file-1>', '<TITLE>Bookmarks</TITLE>', '<H1>Bookmarks</H1>', '<DL><p>']
    for row in rows:
        lines.append(f'  <DT><A HREF="{escape(row["URL"])}">{escape(row["Title"])}</A>')
    lines.append('</DL><p>')
    Path(path).write_text('\n'.join(lines), encoding='utf-8')


def write_viewer(datasets, path):
    if not TEMPLATE.exists():
        sys.exit(f'Missing {TEMPLATE.name} next to app.py')
    data = json.dumps(datasets, ensure_ascii=False).replace('</', '<\\/')  # keep </script> out of the data
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    page = TEMPLATE.read_text(encoding='utf-8').replace('/*__DATA__*/{}', data).replace('__GENERATED__', generated)
    Path(path).write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='Export and explore your Chromium bookmarks and history.')
    parser.add_argument('-w', '--what', choices=['bookmarks', 'history', 'all'], default='all',
                        help='what to read (default: all)')
    parser.add_argument('-b', '--browser', choices=list(BROWSER_DIRS['Linux'][1]), help='only this browser')
    parser.add_argument('-p', '--profile', help='only this profile (name or folder, e.g. "Default")')
    parser.add_argument('-f', '--format', choices=['csv', 'json', 'html', 'netscape', 'all'], default='all',
                        help="html = interactive explorer, netscape = importable bookmarks file (default: all)")
    parser.add_argument('-o', '--out', default='.', help='output directory (default: current)')
    parser.add_argument('--days', type=int, help='only history from the last N days')
    parser.add_argument('--check', action='store_true', help='probe every bookmark URL and record dead links')
    parser.add_argument('--no-open', action='store_true', help="don't open the explorer in a browser")
    parser.add_argument('--list', action='store_true', help='list detected browser profiles and exit')
    args = parser.parse_args()

    if args.list:
        for browser, profile, file in find_profiles():
            print(f'{browser:9} {profile:20} {file}')
        return

    bookmarks = load_bookmarks(args.browser, args.profile) if args.what in ('bookmarks', 'all') else []
    history = load_history(args.browser, args.profile, args.days) if args.what in ('history', 'all') else []
    if not bookmarks and not history:
        sys.exit('Nothing found. Try --list to see which profiles were detected.')
    if args.check:
        check_links(bookmarks)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = {'csv', 'json', 'html', 'netscape'} if args.format == 'all' else {args.format}
    outputs = []  # (format, writer, filename)
    for name, rows, fields in (('bookmarks', bookmarks, FIELDS), ('history', history, HISTORY_FIELDS)):
        if not rows:
            continue
        if 'csv' in wanted:
            outputs.append((write_csv, rows, f'{name}.csv', fields))
        if 'json' in wanted:
            outputs.append((write_json, rows, f'{name}.json'))
    if 'netscape' in wanted and bookmarks:
        outputs.append((write_netscape, bookmarks, 'bookmarks_import.html'))
    if 'html' in wanted:
        outputs.append((write_viewer, {'bookmarks': bookmarks, 'history': history}, 'explorer.html'))
    for func, data, name, *extra in outputs:
        func(data, out / name, *extra)
        print(f'  wrote {out / name}')

    if bookmarks:
        dupes = sum(r['Duplicate'] for r in bookmarks)
        print(f'{len(bookmarks)} bookmarks, {len({r["Folder"] for r in bookmarks})} folders, {dupes} duplicates')
    if history:
        print(f'{len(history)} history entries across {len({r["Domain"] for r in history})} sites')
    if 'html' in wanted and not args.no_open:
        webbrowser.open((out / 'explorer.html').resolve().as_uri())


if __name__ == '__main__':
    main()
