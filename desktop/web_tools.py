from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import urllib.parse

from desktop.common import ROOT

EXE = ROOT / '.runtime/obscura/obscura.exe'
PAGE_SCRIPT = '''(()=>{document.querySelectorAll('script,style,noscript,svg,nav,header,footer').forEach(e=>e.remove());const main=document.querySelector('main')||document.querySelector('article')||document.body;return JSON.stringify({title:document.title,url:location.href,text:(main.innerText||'').slice(0,18000)})})()'''
SEARCH_SCRIPT = '''JSON.stringify(Array.from(document.querySelectorAll('.result')).map(e=>({title:e.querySelector('.result__a')?.textContent||'',url:e.querySelector('.result__a')?.href||'',snippet:e.querySelector('.result__snippet')?.textContent||''})).filter(e=>e.title&&e.url).slice(0,6))'''


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use a public HTTP or HTTPS web address')
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost' or '.' not in host or host.endswith(('.local', '.localhost', '.internal')):
        raise ValueError('Only public web pages can be read')
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError('Only public web pages can be read')
    except ValueError as exc:
        if 'Only public' in str(exc):
            raise
    return url


def evaluate(url, script):
    validate_url(url)
    env = os.environ.copy()
    env.pop('OBSCURA_ALLOW_PRIVATE_NETWORK', None)
    env['RUST_LOG'] = 'error'
    result = subprocess.run([str(EXE), 'fetch', url, '--eval', script, '--timeout', '15', '--wait', '0', '-q'],
        capture_output=True, encoding='utf-8', errors='replace', timeout=22,
        creationflags=subprocess.CREATE_NO_WINDOW, env=env, cwd=EXE.parent)
    if result.returncode:
        raise RuntimeError('Obscura could not load that public page')
    lines = result.stdout.strip().splitlines()
    for line in reversed(lines):
        try:
            data = json.loads(line)
            return json.loads(data) if isinstance(data, str) else data
        except (ValueError, TypeError):
            continue
    raise RuntimeError('The page did not return readable content')


def search_web(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('Search text must be between 1 and 500 characters')
    url = 'https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query})
    results = evaluate(url, SEARCH_SCRIPT)
    if not isinstance(results, list) or not results:
        return {'ok': False, 'error': 'The search provider returned no readable results or blocked automated access.'}
    cleaned = []
    for row in results:
        link = row['url']
        if link.startswith('//'):
            link = 'https:' + link
        redirect = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get('uddg')
        if redirect:
            link = redirect[0]
        try:
            validate_url(link)
        except ValueError:
            continue
        cleaned.append({'title': row['title'][:300], 'url': link, 'snippet': row['snippet'][:900]})
    return {'ok': bool(cleaned), 'results': cleaned, 'source': 'Obscura / DuckDuckGo'}


def read_webpage(url):
    result = evaluate(url, PAGE_SCRIPT)
    if not isinstance(result, dict) or not result.get('text'):
        return {'ok': False, 'error': 'This page has no readable text'}
    validate_url(result.get('url', url))
    return {'ok': True, **result}
