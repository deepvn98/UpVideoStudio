from __future__ import annotations

import base64
import hashlib
import http.client
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .vault import seal, unseal

API = 'https://www.googleapis.com/youtube/v3/'
UPLOAD = 'https://www.googleapis.com/upload/youtube/v3/'
TOKEN = 'https://oauth2.googleapis.com/token'
# Playlist management needs the youtube scope as well as video upload.
SCOPES = 'https://www.googleapis.com/auth/youtube'


class ApiError(Exception):
    def __init__(self, status, reason, retry_after=0):
        self.status, self.reason, self.retry_after = status, reason, retry_after
        hints = {'quotaExceeded': 'Hết hạn mức API. Kiểm tra quota trong Google Cloud rồi thử lại.',
                 'uploadLimitExceeded': 'Kênh đã chạm giới hạn tải video. Hãy thử lại sau.',
                 'invalid_client': ('Google từ chối OAuth Client ID/secret đã lưu cho kênh này. '
                                    'Vào Kênh của tôi → Kết nối kênh và kết nối lại đúng kênh bằng file OAuth Desktop Client JSON còn hiệu lực. '
                                    'Kết nối lại cùng OAuth client và cùng kênh sẽ giữ hàng đợi. Nếu vẫn lỗi, hãy tải lại JSON từ OAuth client còn hoạt động trong Google Cloud.'),
                 'deleted_client': ('OAuth client đã bị xóa trong Google Cloud. Tạo OAuth Client Desktop mới và kết nối lại đúng kênh bằng JSON mới. '
                                    'UpVideo Studio sẽ chuyển dữ liệu cục bộ sang kết nối mới sau khi xác nhận đúng kênh.'),
                 'invalid_grant': 'Phiên đăng nhập hết hiệu lực. Hãy kết nối lại đúng kênh.',
                 'insufficientPermissions': 'Thiếu quyền Google. Hãy kết nối lại kênh.',
                 'forbidden': 'Google từ chối thao tác. Kiểm tra quyền của kênh.',
                 'network': 'Kết nối bị gián đoạn. Phiên upload đã được giữ lại.'}
        super().__init__(hints.get(reason, f'Google API {status}: {reason}'))

    @property
    def transient(self):
        return self.status in (0, 429, 500, 502, 503, 504)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method, url, data=None, headers=None):
    if urllib.parse.urlparse(url).scheme != 'https':
        raise ValueError('Google API phải sử dụng HTTPS.')
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(req, timeout=45)
        with response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        content = exc.read()
        if exc.code == 308:
            return 308, dict(exc.headers), content
        reason = 'requestFailed'
        try:
            err = json.loads(content).get('error', {})
            reason = err if isinstance(err, str) else (err.get('errors') or [{}])[0].get('reason', err.get('status', reason))
        except (ValueError, TypeError, AttributeError):
            pass
        retry = exc.headers.get('Retry-After', '0')
        raise ApiError(exc.code, reason, int(retry) if retry.isdigit() else 0) from None
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise ApiError(0, 'network') from None


def form(url, values):
    _, _, raw = request('POST', url, urllib.parse.urlencode(values).encode(),
                        {'Content-Type': 'application/x-www-form-urlencoded'})
    return json.loads(raw)


def header(headers, name, default=''):
    return next((v for k, v in headers.items() if k.lower() == name.lower()), default)


class Google:
    AUTH_REASONS = {'invalid_client', 'deleted_client', 'invalid_grant', 'insufficientPermissions'}

    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.pending = {}

    def begin(self, config, redirect, expected_channel_id='', replace_account_id=''):
        client = config.get('installed', {})
        if not client.get('client_id', '').endswith('.apps.googleusercontent.com') or not client.get('client_secret'):
            raise ValueError('Chọn file OAuth Client JSON loại Desktop app từ Google Cloud.')
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if time.time()-v['created'] < 600}
            self.pending[state] = dict(client=client, verifier=verifier, redirect=redirect,
                                       expected_channel_id=expected_channel_id,
                                       replace_account_id=replace_account_id, created=time.time())
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
            'client_id': client['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
            'scope': SCOPES, 'access_type': 'offline', 'prompt': 'consent select_account',
            'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})

    def begin_saved_reconnect(self, aid, redirect):
        account = self.store.account(aid)
        if not account:
            raise ValueError('Không tìm thấy kênh cần kết nối lại. Hãy tải lại danh sách kênh.')
        token = unseal(self.store.secret(aid))
        client_id = token.get('client_id', '')
        client_secret = token.get('client_secret', '')
        if client_id != account.get('client_id') or not client_secret:
            raise ValueError('Không tìm thấy đầy đủ OAuth client đã lưu. Hãy kết nối lại bằng file JSON của cùng OAuth client.')
        config = {'installed': {'client_id': client_id, 'client_secret': client_secret}}
        return self.begin(config, redirect, account['channel_id'])

    def begin_account_replacement(self, aid, config, redirect):
        account = self.store.account(aid)
        if not account:
            raise ValueError('Không tìm thấy kênh cần chuyển OAuth client.')
        return self.begin(config, redirect, account['channel_id'], replace_account_id=aid)

    def mark_flow_error(self, state, reason, message):
        with self.lock:
            flow = self.pending.pop(state, None)
        if not flow or reason not in self.AUTH_REASONS or not flow.get('expected_channel_id'):
            return
        aid = flow.get('replace_account_id') or hashlib.sha256(
            (flow['client']['client_id'] + ':' + flow['expected_channel_id']).encode()).hexdigest()[:32]
        self.store.set_auth_status(aid, 'reauth_required', message, reason)

    def finish(self, state, code):
        with self.lock:
            flow = self.pending.pop(state, None)
        if not flow or time.time()-flow['created'] > 600:
            raise ValueError('Yêu cầu đăng nhập đã hết hạn. Hãy kết nối lại.')
        client = flow['client']
        try:
            token = form(TOKEN, {'code': code, 'client_id': client['client_id'], 'client_secret': client['client_secret'],
                                 'redirect_uri': flow['redirect'], 'grant_type': 'authorization_code',
                                 'code_verifier': flow['verifier']})
        except ApiError as exc:
            if exc.reason in self.AUTH_REASONS and flow.get('expected_channel_id'):
                aid = flow.get('replace_account_id') or hashlib.sha256(
                    (client['client_id'] + ':' + flow['expected_channel_id']).encode()).hexdigest()[:32]
                self.store.set_auth_status(aid, 'reauth_required', str(exc), exc.reason)
            raise
        if not token.get('refresh_token'):
            raise ValueError('Google chưa cấp quyền duy trì đăng nhập. Hãy kết nối lại và chấp nhận quyền.')
        _, _, raw = request('GET', API + 'channels?part=snippet,contentDetails&mine=true&maxResults=50',
                             headers={'Authorization': 'Bearer ' + token['access_token']})
        channels = json.loads(raw).get('items', [])
        if len(channels) != 1:
            raise ValueError('Hãy đăng nhập và chọn đúng một kênh YouTube trong màn hình Google.')
        channel = channels[0]
        if flow.get('expected_channel_id') and channel['id'] != flow['expected_channel_id']:
            raise ValueError('Bạn đã chọn kênh YouTube khác. Hãy thử Kết nối lại và chọn đúng kênh được yêu cầu.')
        aid = hashlib.sha256((client['client_id'] + ':' + channel['id']).encode()).hexdigest()[:32]
        account = dict(id=aid, channel_id=channel['id'], name=channel['snippet']['title'],
                       uploads=channel['contentDetails']['relatedPlaylists']['uploads'],
                       client_id=client['client_id'], connected=datetime.now(timezone.utc).isoformat(),
                       auth_status='connected', auth_error='', auth_error_code='')
        token.update(client_id=client['client_id'], client_secret=client['client_secret'],
                     expires_at=time.time()+token.get('expires_in', 3600))
        if flow.get('replace_account_id'):
            self.store.reconnect_account(flow['replace_account_id'], account, seal(token))
        else:
            self.store.save_account(account, seal(token))
        self.store.event('Đã kết nối kênh ' + account['name'], account=aid)
        return account

    def access_token(self, aid, force=False):
        with self.lock:
            token = unseal(self.store.secret(aid))
            if force or token.get('expires_at', 0) < time.time()+90:
                try:
                    renewed = form(TOKEN, {'client_id': token['client_id'], 'client_secret': token['client_secret'],
                                           'refresh_token': token['refresh_token'], 'grant_type': 'refresh_token'})
                except ApiError as exc:
                    if exc.reason in self.AUTH_REASONS:
                        self.store.set_auth_status(aid, 'reauth_required', str(exc), exc.reason)
                    raise
                token.update(renewed)
                token['expires_at'] = time.time()+renewed.get('expires_in', 3600)
                self.store.renew_secret(aid, seal(token))
                self.store.set_auth_status(aid, 'connected')
            return token['access_token']

    def raw(self, aid, method, url, data=None, headers=None):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('www.googleapis.com', 'youtube.googleapis.com'):
            raise ValueError('Địa chỉ phiên upload không hợp lệ.')
        auth = dict(headers or {}, Authorization='Bearer ' + self.access_token(aid))
        try:
            return request(method, url, data, auth)
        except ApiError as exc:
            if exc.status != 401:
                if exc.reason in self.AUTH_REASONS:
                    self.store.set_auth_status(aid, 'reauth_required', str(exc), exc.reason)
                raise
            try:
                auth['Authorization'] = 'Bearer ' + self.access_token(aid, force=True)
                response = request(method, url, data, auth)
            except ApiError as retry_error:
                if retry_error.status == 401 or retry_error.reason in self.AUTH_REASONS:
                    self.store.set_auth_status(aid, 'reauth_required', str(retry_error), retry_error.reason)
                raise
            return response

    def api(self, aid, resource, params=None, body=None, method='GET'):
        url = API + resource + '?' + urllib.parse.urlencode(params or {})
        _, _, raw = self.raw(aid, method, url, json.dumps(body).encode() if body is not None else None,
                              {'Content-Type': 'application/json'})
        return json.loads(raw) if raw else {}

    def pages(self, aid, resource, params):
        params = dict(params, maxResults=50)
        while True:
            data = self.api(aid, resource, params)
            yield from data.get('items', [])
            if not data.get('nextPageToken'):
                break
            params['pageToken'] = data['nextPageToken']

    def playlists(self, aid):
        return [{'id': p['id'], 'title': p['snippet']['title']}
                for p in self.pages(aid, 'playlists', {'part': 'snippet', 'mine': 'true'})]

    def scheduled(self, aid):
        account = self.store.account(aid)
        if not account:
            raise ValueError('Kênh chưa được kết nối.')
        ids = [p['contentDetails']['videoId'] for p in self.pages(aid, 'playlistItems',
                {'part': 'contentDetails', 'playlistId': account['uploads']})]
        result = []
        for start in range(0, len(ids), 50):
            data = self.api(aid, 'videos', {'part': 'status', 'id': ','.join(ids[start:start+50])})
            result.extend(p['status']['publishAt'] for p in data.get('items', []) if p.get('status', {}).get('publishAt'))
        return result

    def ensure_playlist(self, aid, playlist, video):
        existing = self.api(aid, 'playlistItems', {'part': 'id', 'playlistId': playlist, 'videoId': video, 'maxResults': 1})
        if not existing.get('items'):
            self.api(aid, 'playlistItems', {'part': 'snippet'}, {'snippet': {
                'playlistId': playlist, 'resourceId': {'kind': 'youtube#video', 'videoId': video}}}, 'POST')
