from __future__ import annotations

import base64
import hashlib
import hmac
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import pathlib
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import winreg

from desktop.common import ROOT, read_secret, save_secret, secret_path
from desktop import email_policy

PROVIDERS = {
    'gmail': {'authorize': 'https://accounts.google.com/o/oauth2/v2/auth',
              'token': 'https://oauth2.googleapis.com/token',
              'scope': 'https://www.googleapis.com/auth/gmail.readonly',
              'api': 'https://gmail.googleapis.com/gmail/v1/users/me'},
    'outlook': {'authorize': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
                'token': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
                'scope': 'https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access',
                'api': 'https://graph.microsoft.com/v1.0'},
}
LOCK = threading.RLock()


def json_request(url, form=None, headers=None):
    request = urllib.request.Request(url,
        data=urllib.parse.urlencode(form).encode() if form is not None else None,
        headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise RuntimeError('Email response is too large')
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Email provider returned HTTP {exc.code}. Reconnect the account if needed.') from None


def configure_gmail(filename):
    data = json.loads(pathlib.Path(filename).read_text(encoding='utf-8-sig')).get('installed', {})
    if not str(data.get('client_id', '')).endswith('.apps.googleusercontent.com'):
        raise ValueError('Choose a Google OAuth Desktop app client JSON file')
    save_secret('gmail-client', {key: data[key] for key in ('client_id', 'client_secret') if key in data})


def configure_outlook(client_id):
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', client_id.strip()):
        raise ValueError('Enter the application client ID from your Microsoft app registration')
    save_secret('outlook-client', {'client_id': client_id.strip()})


def connect(provider):
    if not email_policy.enabled():
        raise RuntimeError(email_policy.UNAVAILABLE)
    spec = PROVIDERS[provider]
    client = read_secret(provider + '-client')
    if not client:
        raise RuntimeError('Set up this provider in Email settings first')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    result = {}
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            valid = parsed.path == '/' and hmac.compare_digest(query.get('state', [''])[0], state)
            if valid:
                result.update(code=query.get('code', [None])[0], error=query.get('error', [None])[0])
            message = b'Jarvis received the sign-in response. You can close this tab.' if valid else b'Invalid sign-in response.'
            self.send_response(200 if valid else 400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(message)
    with HTTPServer(('127.0.0.1', 0), Callback) as server:
        host = 'localhost' if provider == 'outlook' else '127.0.0.1'
        redirect = f'http://{host}:{server.server_port}/'
        params = {'client_id': client['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
                  'scope': spec['scope'], 'state': state, 'code_challenge': challenge,
                  'code_challenge_method': 'S256', 'prompt': 'select_account' if provider == 'outlook' else 'consent'}
        if provider == 'gmail':
            params['access_type'] = 'offline'
        os.startfile(spec['authorize'] + '?' + urllib.parse.urlencode(params))
        server.timeout = 1
        deadline = time.monotonic() + 240
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get('code'):
        raise RuntimeError('Sign-in was cancelled or timed out')
    token = json_request(spec['token'], {**client, 'grant_type': 'authorization_code',
        'code': result['code'], 'redirect_uri': redirect, 'code_verifier': verifier})
    token['expires_at'] = time.time() + token.get('expires_in', 3600)
    save_secret(provider, token)
    profile = api(provider, '/profile' if provider == 'gmail' else '/me?$select=mail,userPrincipalName')
    token['address'] = profile.get('emailAddress') or profile.get('mail') or profile.get('userPrincipalName')
    save_secret(provider, token)
    return {'ok': True, 'provider': provider, 'address': token['address']}


def access_token(provider):
    with LOCK:
        token = read_secret(provider)
        if not token:
            raise RuntimeError(f'{provider.title()} is not connected. Open Email settings on the orb.')
        if token.get('expires_at', 0) <= time.time() + 90:
            client = read_secret(provider + '-client')
            if not token.get('refresh_token') or not client:
                raise RuntimeError(f'Reconnect {provider.title()} in Email settings')
            fresh = json_request(PROVIDERS[provider]['token'], {**client, 'grant_type': 'refresh_token',
                                 'refresh_token': token['refresh_token']})
            token.update(fresh)
            token['expires_at'] = time.time() + fresh.get('expires_in', 3600)
            save_secret(provider, token)
        return token['access_token']


def api(provider, path, params=None):
    if not email_policy.enabled():
        raise RuntimeError(email_policy.UNAVAILABLE)
    if not path.startswith('/') or '://' in path:
        raise ValueError('Invalid email API path')
    url = PROVIDERS[provider]['api'] + path
    if params:
        url += '?' + urllib.parse.urlencode(params, doseq=True)
    return json_request(url, headers={'Authorization': 'Bearer ' + access_token(provider),
                                    'Prefer': 'outlook.body-content-type="text"'})


def classic_available():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Office\16.0\Outlook\Profiles') as key:
            return winreg.QueryInfoKey(key)[0] > 0
    except OSError:
        return False


def classic(request):
    if not email_policy.enabled():
        return email_policy.unavailable()
    if not classic_available():
        return {'ok': False, 'error': 'Add your Gmail and Outlook accounts to Outlook classic, or connect them in Jarvis Email settings.', 'accounts': []}
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(ROOT / 'desktop/outlook-read.ps1')],
        input=json.dumps(request), capture_output=True, encoding='utf-8-sig', timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode:
        return {'ok': False, 'error': 'Outlook classic could not read the mailbox. Open Outlook and finish account setup.'}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {'ok': False, 'error': 'Outlook returned an unreadable response'}


def status():
    if not email_policy.enabled():
        return {'enabled': False, 'message': email_policy.UNAVAILABLE,
                'accounts': [{'provider': provider, 'connected': False, 'configured': False, 'address': None} for provider in PROVIDERS],
                'classicProfileAvailable': False}
    accounts = []
    for provider in PROVIDERS:
        token = read_secret(provider)
        accounts.append({'provider': provider, 'connected': bool(token),
                         'configured': secret_path(provider + '-client').exists(),
                         'address': token.get('address') if token else None})
    return {'enabled': True, 'accounts': accounts, 'classicProfileAvailable': classic_available()}


def plain_body(payload):
    chunks = []
    def walk(part):
        if part.get('filename'):
            return
        data = part.get('body', {}).get('data')
        if data and part.get('mimeType') in ('text/plain', 'text/html'):
            value = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', errors='replace')
            if part.get('mimeType') == 'text/html':
                value = html.unescape(re.sub('<[^>]+>', ' ', value))
            chunks.append((part.get('mimeType'), value))
        for child in part.get('parts', []):
            walk(child)
    walk(payload)
    preferred = [v for mime, v in chunks if mime == 'text/plain'] or [v for _, v in chunks]
    return '\n'.join(preferred)[:16000]


class MailTools:
    def __init__(self):
        self.refs = {}
        self.refs_lock = threading.RLock()

    def reference(self, provider, value):
        with self.refs_lock:
            for reference, saved in self.refs.items():
                if saved == (provider, value):
                    return reference
            if len(self.refs) >= 100:
                self.refs.pop(next(iter(self.refs)))
            reference = secrets.token_hex(8)
            self.refs[reference] = (provider, value)
            return reference

    def accounts(self):
        data = status()
        if data['classicProfileAvailable']:
            data['classic'] = classic({'action': 'accounts'})
        return data

    def search(self, provider='all', query='', unread_only=False, limit=5):
        if not email_policy.enabled():
            return email_policy.unavailable()
        if provider not in ('all', 'gmail', 'outlook'):
            raise ValueError('Choose Gmail, Outlook or all')
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError('Email search is too long')
        limit = max(1, min(10, int(limit)))
        connected = [p for p in PROVIDERS if (provider == 'all' or provider == p) and secret_path(p).exists()]
        messages, errors = [], []
        for selected in connected:
            try:
                if selected == 'gmail':
                    q = (query.strip() or 'in:inbox') + (' is:unread' if unread_only else '')
                    rows = api('gmail', '/messages', {'q': q, 'maxResults': limit}).get('messages', [])
                    for row in rows:
                        message = api('gmail', '/messages/' + urllib.parse.quote(row['id'], safe=''),
                                      {'format': 'metadata', 'metadataHeaders': ['From', 'Subject', 'Date']})
                        headers = {h['name'].lower(): h['value'] for h in message.get('payload', {}).get('headers', [])}
                        messages.append({'id': self.reference('gmail', row['id']), 'provider': 'gmail',
                            'from': headers.get('from'), 'subject': headers.get('subject'), 'date': headers.get('date'),
                            'snippet': html.unescape(message.get('snippet', '')), 'unread': 'UNREAD' in message.get('labelIds', [])})
                else:
                    params = {'$top': limit, '$select': 'id,subject,from,receivedDateTime,bodyPreview,isRead'}
                    if query.strip():
                        search = query.replace('\\', '\\\\').replace('"', '\\"')
                        params['$search'] = '"' + search + '"'
                        if unread_only:
                            params['$top'] = 50
                    else:
                        params['$orderby'] = 'receivedDateTime desc'
                        if unread_only:
                            params['$filter'] = 'isRead eq false'
                    route = '/me/messages' if query.strip() else '/me/mailFolders/inbox/messages'
                    rows = api('outlook', route, params).get('value', [])
                    for row in [x for x in rows if not unread_only or not x.get('isRead')][:limit]:
                        messages.append({'id': self.reference('outlook', row['id']), 'provider': 'outlook',
                            'from': row.get('from', {}).get('emailAddress'), 'subject': row.get('subject'),
                            'date': row.get('receivedDateTime'), 'snippet': row.get('bodyPreview', ''), 'unread': not row.get('isRead')})
            except Exception as exc:
                errors.append(str(exc))
        missing = [p for p in PROVIDERS if (provider == 'all' or provider == p) and p not in connected]
        scope = 'Provider mailbox search'
        if missing and classic_available():
            result = classic({'action': 'search', 'providers': missing, 'query': query, 'unreadOnly': unread_only, 'limit': limit})
            for row in result.get('messages', []):
                row['id'] = self.reference('classic', {'entryId': row.pop('entryId'), 'storeId': row.pop('storeId')})
                messages.append(row)
            scope += '; ' + result.get('scope', 'Outlook classic')
            if not result.get('ok'):
                errors.append(result.get('error', 'Outlook could not search'))
        elif missing:
            errors.append('Not connected: ' + ', '.join(missing) + '. Open Email settings on the orb.')
        return {'ok': bool(messages) or not errors, 'messages': messages, 'errors': errors, 'scope': scope}

    def read(self, reference):
        if not email_policy.enabled():
            return email_policy.unavailable()
        with self.refs_lock:
            saved = self.refs.get(reference)
        if saved is None:
            return {'ok': False, 'error': 'Search the inbox again to get a current message reference'}
        provider, value = saved
        if provider == 'classic':
            return classic({'action': 'read', **value})
        if provider == 'gmail':
            message = api('gmail', '/messages/' + urllib.parse.quote(value, safe=''), {'format': 'full'})
            headers = {h['name'].lower(): h['value'] for h in message.get('payload', {}).get('headers', [])}
            return {'ok': True, 'provider': provider, 'from': headers.get('from'), 'subject': headers.get('subject'),
                    'body': plain_body(message.get('payload', {}))}
        message = api('outlook', '/me/messages/' + urllib.parse.quote(value, safe=''),
                      {'$select': 'subject,from,body,receivedDateTime'})
        return {'ok': True, 'provider': provider, 'from': message.get('from'), 'subject': message.get('subject'),
                'body': message.get('body', {}).get('content', '')[:16000]}
