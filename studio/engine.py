import json
import mimetypes
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .domain import fingerprint, validate_source, parse_date, utc_now
from .google import UPLOAD, ApiError, header

MAX_CONCURRENT_CHANNELS = 10


class Paused(Exception):
    pass


class Engine:
    def __init__(self, store, google, dispatch=True):
        self.store, self.google = store, google
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.cancel = set()
        self.active = {}  # one worker per channel, even across OAuth clients
        self.pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHANNELS, thread_name_prefix='upload')
        self.thread = threading.Thread(target=self.dispatch, daemon=True)
        if dispatch:
            self.thread.start()

    def start(self, ids):
        with self.lock:
            selected = [self.store.job(jid) for jid in dict.fromkeys(ids)]
            if not selected:
                raise ValueError('Chọn ít nhất một video.')
            blocked = []
            eligible = []
            for job in selected:
                if job['state'] in ('uploading', 'queued', 'finishing', 'done'):
                    continue
                if job.get('asset_errors'):
                    blocked.append({'title': job.get('title', ''), 'account': job['account'],
                                    'path': job['path'], 'errors': job['asset_errors']})
                    continue
                if not self.store.account(job['account']):
                    raise ValueError('Kết nối lại kênh trước khi chạy.')
                if not job['video_id']:
                    validate_source(job, check_time=not bool(job['session']))
                    if not Path(job['path']).is_file():
                        raise ValueError('Không tìm thấy video: ' + job['path'])
                eligible.append(job)
            ready = [j for j in eligible if j['state'] not in ('uploading', 'queued', 'finishing', 'done')]
            self.store.update_many([(j['id'], {'state': 'queued', 'error': ''}) for j in ready])
            for job in ready:
                self.cancel.discard(job['id'])
            return {'queued': len(ready), 'blocked': blocked}

    def pause(self, ids):
        with self.lock:
            for jid in ids:
                job = self.store.job(jid)
                if job['state'] in ('uploading', 'finishing'):
                    self.cancel.add(jid)
                elif job['state'] == 'queued':
                    self.store.update(jid, state='paused')

    def checkpoint(self, jid):
        if self.stop.is_set() or jid in self.cancel:
            raise Paused()

    def dispatch(self):
        while not self.stop.wait(.25):
            with self.lock:
                self.active = {k: f for k, f in self.active.items() if not f.done()}
                # Store order is the user-defined upload order inside each channel.
                queued = self.store.jobs(state='queued')
                if not queued:
                    continue
                accounts = {a['id']: a for a in self.store.accounts()}
                for job in queued:
                    account = accounts.get(job['account'])
                    if not account:
                        continue
                    key = account['channel_id']
                    if key in self.active or len(self.active) >= MAX_CONCURRENT_CHANNELS:
                        continue
                    self.store.update(job['id'], state='uploading')
                    self.active[key] = self.pool.submit(self.run, job['id'])

    def backoff(self, jid, attempt, delay=0):
        delay = min(120, max(delay, 2 ** attempt + random.random()))
        self.store.update(jid, error=f'Kết nối gián đoạn. Thử lại sau {int(delay)} giây…')
        for _ in range(int(delay * 4)+1):
            self.checkpoint(jid)
            self.stop.wait(.25)

    def query_session(self, job):
        return self.google.raw(job['account'], 'PUT', job['session'], b'',
                               {'Content-Length': '0', 'Content-Range': f"bytes */{job['size']}"})

    def upload(self, job):
        jid, aid = job['id'], job['account']
        if job.get('asset_errors'):
            raise ValueError('Không thể tải video vì thiếu hoặc có lỗi ở các file nguồn: ' + '; '.join(job['asset_errors']))
        path = Path(job['path'])
        self.checkpoint(jid)
        if fingerprint(path, lambda: self.checkpoint(jid)) != job['fingerprint']:
            raise ValueError('File video đã thay đổi. Hãy nhập lại file thành một công việc mới.')
        self.checkpoint(jid)
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if not job['session']:
            validate_source(job)
            status = {'privacyStatus': job['privacy'], 'selfDeclaredMadeForKids': job['made_for_kids'],
                      'containsSyntheticMedia': job['synthetic']}
            if job['publish_at']:
                status['publishAt'] = job['publish_at']
            snippet = {'title': job['title'], 'description': job['description'], 'tags': job['tags'],
                       'categoryId': job['category']}
            if job['language']:
                snippet.update(defaultLanguage=job['language'], defaultAudioLanguage=job['language'])
            _, headers, _ = self.google.raw(aid, 'POST', UPLOAD + 'videos?uploadType=resumable&part=snippet,status',
                json.dumps({'snippet': snippet, 'status': status}).encode(),
                {'Content-Type': 'application/json', 'X-Upload-Content-Length': str(job['size']),
                 'X-Upload-Content-Type': mime})
            session = header(headers, 'Location')
            if not session:
                raise ValueError('Google không trả về phiên upload.')
            job = self.store.update(jid, session=session)
        need_query, offset, attempts = True, 0, 0
        with path.open('rb') as stream:
            while True:
                self.checkpoint(jid)
                try:
                    if need_query:
                        code, headers, raw = self.query_session(job)
                        need_query = False
                    else:
                        if job['publish_at'] and parse_date(job['publish_at']) <= utc_now():
                            raise ValueError('Lịch đăng của phiên này đã qua. Dừng để tránh công khai ngoài ý muốn; kiểm tra kênh rồi tạo lại phiên với lịch mới.')
                        stream.seek(offset)
                        chunk = stream.read(4 * 1024 * 1024)
                        if not chunk:
                            raise ValueError('Phiên upload không xác nhận hoàn tất. Hãy thử tiếp tục.')
                        code, headers, raw = self.google.raw(aid, 'PUT', job['session'], chunk,
                            {'Content-Type': mime, 'Content-Length': str(len(chunk)),
                             'Content-Range': f'bytes {offset}-{offset+len(chunk)-1}/{job["size"]}'})
                    if code in (200, 201):
                        video = json.loads(raw)
                        if not video.get('id'):
                            raise ValueError('Google chưa xác nhận video ID; giữ phiên upload để kiểm tra lại.')
                        # Persist immediately, before thumbnails or playlists.
                        self.store.update(jid, video_id=video['id'], progress=100, uploaded=job['size'], state='finishing')
                        return video['id']
                    if code != 308:
                        raise ValueError('Phản hồi upload không hợp lệ.')
                    value = header(headers, 'Range')
                    next_offset = int(value.rsplit('-', 1)[1])+1 if value else 0
                    if not 0 <= next_offset <= job['size']:
                        raise ValueError('Vị trí tiếp tục upload không hợp lệ.')
                    if next_offset == offset:
                        attempts += 1
                        if attempts > 6:
                            raise ValueError('Upload không tiến triển. Phiên đã lưu; hãy thử lại sau.')
                    else:
                        attempts = 0
                    offset = next_offset
                    self.store.update(jid, uploaded=offset, progress=round(100*offset/job['size'], 1), error='')
                    retry_after = header(headers, 'Retry-After', '0')
                    if retry_after.isdigit() and int(retry_after):
                        self.backoff(jid, 0, int(retry_after))
                except ApiError as exc:
                    if exc.status in (404, 410):
                        raise ValueError('Phiên upload đã hết hạn. Kiểm tra YouTube Studio trước khi tạo phiên mới để tránh đăng trùng.') from None
                    if not exc.transient or attempts >= 6:
                        raise
                    attempts += 1
                    need_query = True
                    self.backoff(jid, attempts, exc.retry_after)

    def finish(self, job):
        jid, aid, vid = job['id'], job['account'], job['video_id']
        if job['thumbnail'] and not job['thumbnail_done']:
            self.checkpoint(jid)
            thumb = Path(job['thumbnail'])
            try:
                if (not thumb.is_file() or thumb.suffix.lower() not in ('.jpg', '.jpeg', '.png')
                        or thumb.stat().st_size > 2 * 1024 * 1024):
                    raise ValueError(f'{thumb}: Thumbnail phải là JPG/PNG tồn tại và không quá 2 MB.')
                data = thumb.read_bytes()
            except OSError as exc:
                raise ValueError(f'{thumb}: Không đọc được file thumbnail: {exc}') from None
            try:
                self.google.raw(aid, 'POST', UPLOAD + 'thumbnails/set?videoId=' + vid + '&uploadType=media',
                                data, {'Content-Type': mimetypes.guess_type(thumb.name)[0] or 'image/jpeg'})
            except ApiError as exc:
                raise ValueError(f'{thumb}: YouTube không chấp nhận thumbnail: {exc}') from None
            self.store.update(jid, thumbnail_done=True)
        done = list(job['completed_playlists'])
        for playlist in job['playlists']:
            self.checkpoint(jid)
            if playlist not in done:
                self.google.ensure_playlist(aid, playlist, vid)
                done.append(playlist)
                self.store.update(jid, completed_playlists=done)
        response = self.google.api(aid, 'videos', {'part': 'status,processingDetails', 'id': vid})
        items = response.get('items', [])
        if not items:
            raise ValueError('Không đọc được trạng thái video sau khi upload.')
        status = items[0].get('status', {})
        processing = items[0].get('processingDetails', {}).get('processingStatus', '')
        if status.get('uploadStatus') in ('failed', 'rejected', 'deleted') or processing in ('failed', 'terminated'):
            raise ValueError('YouTube không xử lý được video. Kiểm tra video trong YouTube Studio.')
        if job['publish_at'] and parse_date(job['publish_at']) > utc_now():
            if not status.get('publishAt') or parse_date(status['publishAt']) != parse_date(job['publish_at']):
                raise ValueError('Video đã tải lên nhưng lịch YouTube không khớp lịch yêu cầu. Kiểm tra video trong YouTube Studio.')
        if not job['publish_at'] and status.get('privacyStatus') != job['privacy']:
            raise ValueError('Video đã tải lên nhưng chế độ hiển thị khác yêu cầu. Kiểm tra project API trong YouTube Studio.')
        self.store.update(jid, state='done', error='', processing=processing)
        self.store.event('Đã tải lên: ' + job['title'], account=aid)

    def run(self, jid):
        try:
            job = self.store.job(jid)
            if not job['video_id']:
                self.upload(job)
            self.checkpoint(jid)
            self.store.update(jid, state='finishing', error='')
            self.finish(self.store.job(jid))
        except Paused:
            self.store.update(jid, state='paused', error='Đã lưu tiến trình. Có thể tiếp tục.')
        except Exception as exc:
            job = self.store.job(jid)
            state = 'warning' if job['video_id'] else 'error'
            message = str(exc) if isinstance(exc, (ApiError, ValueError)) else 'Có lỗi xử lý. Kiểm tra file và thử tiếp tục.'
            self.store.update(jid, state=state, error=message)
            self.store.event(job['title'] + ': ' + message, 'error', account=job['account'])
            if isinstance(exc, ApiError) and exc.reason in ('quotaExceeded', 'uploadLimitExceeded'):
                with self.lock:
                    account = self.store.account(job['account'])
                    accounts = {a['id']: a for a in self.store.accounts()}
                    updates = []
                    for pending in self.store.jobs(state='queued'):
                        other = accounts.get(pending['account'])
                        if account and other and (
                            account['client_id'] == other['client_id'] if exc.reason == 'quotaExceeded'
                            else account['channel_id'] == other['channel_id']):
                            updates.append((pending['id'], {'state': 'paused', 'error': message}))
                    self.store.update_many(updates)

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        self.pool.shutdown(wait=True)
