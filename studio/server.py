import html
import json
import math
import re
import secrets
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .domain import scan_folder, validate, schedule, parse_date
from .engine import Engine
from .google import Google, ApiError
from .store import Store
from .address import UI_HOST, ui_url
from .catalog import CATEGORIES, LANGUAGES, validate_choices
from . import __version__
from .watcher import FolderWatcher, SOURCE_FIELDS
from .removal import plan_removal, remove_drafts

EDITABLE = {'title', 'description', 'tags', 'thumbnail', 'category', 'language', 'privacy',
            'publish_at', 'made_for_kids', 'synthetic', 'playlists'}
BUSY = {'queued', 'uploading', 'finishing'}


class App:
    def __init__(self, data_dir, web_dir):
        self.store = Store(Path(data_dir) / 'studio.db')
        self.google = Google(self.store)
        self.engine = Engine(self.store, self.google)
        self.web_dir = Path(web_dir)
        self.token = secrets.token_urlsafe(32)
        self.mutation = threading.RLock()
        self.tasks = {}
        self.task_lock = threading.Lock()
        self.watcher = FolderWatcher(self)

    def close(self):
        self.watcher.close()
        self.engine.close()

    def snapshot(self):
        jobs = []
        for j in self.store.jobs():
            j['has_session'] = bool(j['session'])
            j.pop('session', None)
            j.pop('fingerprint', None)
            j.pop('watch_source', None)
            jobs.append(j)
        accounts = self.store.accounts()
        return {'jobs': jobs, 'accounts': accounts, 'events': self.store.events(), 'watches': self.store.watches()}

    def background(self, fn):
        tid = secrets.token_hex(12)
        with self.task_lock:
            if len(self.tasks) > 200:
                finished = [k for k, v in self.tasks.items() if v['state'] != 'running']
                for k in finished[:100]:
                    self.tasks.pop(k, None)
            self.tasks[tid] = {'state': 'running'}
        def run():
            try:
                result = {'state': 'done', 'result': fn()}
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ValueError, OSError, ApiError)) else 'Không thực hiện được thao tác. Hãy thử lại.'
                result = {'state': 'error', 'error': message}
            with self.task_lock:
                self.tasks[tid] = result
        threading.Thread(target=run, daemon=True).start()
        return {'task': tid}

    def import_folder(self, body):
        aid = body.get('account', '')
        if not self.store.account(aid):
            raise ValueError('Kết nối và chọn một kênh trước khi nhập video.')
        rows, warnings = scan_folder(body.get('path', ''))
        added = 0
        added_jobs = []
        with self.mutation:
            account = self.store.account(aid)
            if not account:
                raise ValueError('Kênh đã bị ngắt kết nối.')
            same = {a['id'] for a in self.store.accounts() if a['channel_id'] == account['channel_id']}
            known = {j['fingerprint'] for j in self.store.jobs()
                     if j['account'] in same or j.get('channel_id') == account['channel_id']}
            existing = {str(Path(j['path']).resolve()): j for j in self.store.jobs() if j['account'] == aid}
            for row in rows:
                row['import_root'] = str(Path(body['path']).expanduser().resolve())
                if not Path(row['path']).is_file():
                    raise ValueError('Thư mục vừa thay đổi. Hãy thử nhập lại.')
                if body.get('watch', False):
                    row.update(watch_root=str(Path(body['path']).expanduser().resolve()),
                               watch_source={k: row[k] for k in SOURCE_FIELDS})
                previous = existing.get(row['path'])
                if previous and previous['fingerprint'] == row['fingerprint']:
                    # Migrate folder provenance only; preserve paused sessions and metadata.
                    updates = {'import_root': row['import_root']}
                    if body.get('watch', False):
                        updates['watch_root'] = row['watch_root']
                    self.store.update(previous['id'], **updates)
                if row['fingerprint'] not in known:
                    job = self.store.add(row, aid)
                    if job:
                        known.add(row['fingerprint'])
                        added_jobs.append(job)
                        added += 1
            if body.get('watch', False):
                self.apply_automation(aid, added_jobs)
        self.store.event(f'Đã nhập {added} video, bỏ qua {len(rows)-added} video trùng.')
        return {'added': added, 'duplicates': len(rows)-added, 'warnings': warnings}

    def apply_automation(self, aid, jobs):
        """Apply the latest channel defaults to every supplied unstarted draft."""
        if not jobs:
            return []
        rule = self.automation_rule(aid)
        content = rule.get('content', {})
        allowed = {'made_for_kids', 'synthetic', 'category', 'language', 'playlists'}
        content = {key: value for key, value in content.items() if key in allowed}
        planned = {}
        schedule_rule = rule.get('schedule')
        if schedule_rule:
            account = self.store.account(aid)
            related = {a['id'] for a in self.store.accounts()
                       if account and a['channel_id'] == account['channel_id']}
            new_ids = {job['id'] for job in jobs}
            occupied = [job['publish_at'] for job in self.store.jobs()
                        if (job['account'] in related or (account and job.get('channel_id') == account['channel_id']))
                        and job['id'] not in new_ids and job.get('publish_at')]
            schedule_jobs = list(jobs)
            if schedule_jobs:
                times = schedule(len(schedule_jobs), schedule_rule['date'], schedule_rule['slots'],
                                 int(schedule_rule['interval']), int(schedule_rule['offset']), occupied)
                planned = dict(zip((job['id'] for job in schedule_jobs), times))
        updates = []
        for job in jobs:
            changes = dict(content)
            if job['id'] in planned:
                changes.update(publish_at=planned[job['id']], privacy='private')
            if changes:
                updates.append((job['id'], changes))
        return self.store.update_many(updates) if updates else jobs

    def automation_rule(self, aid):
        """Migrate settings created before automatic folder rules were persisted."""
        rule = self.store.automation(aid)
        jobs = [job for job in self.store.jobs() if job['account'] == aid]
        changed = False
        if 'content' not in rule:
            configured = [job for job in jobs if type(job.get('made_for_kids')) is bool
                          and type(job.get('synthetic')) is bool]
            inferred = {}
            for key in ('made_for_kids', 'synthetic', 'category', 'language', 'playlists'):
                values = [job.get(key) for job in configured]
                if values and all(value == values[0] for value in values):
                    inferred[key] = values[0]
            if inferred:
                rule['content'] = inferred
                changed = True
        if 'schedule' not in rule:
            scheduled = sorted(parse_date(job['publish_at']).astimezone()
                               for job in jobs if job.get('publish_at'))
            if len(scheduled) >= 2:
                days = sorted({value.date() for value in scheduled})
                gaps = [(right-left).days for left, right in zip(days, days[1:]) if right > left]
                interval = math.gcd(*gaps) if gaps else 1
                offset = int((scheduled[0].utcoffset().total_seconds() if scheduled[0].utcoffset() else 0) / 60)
                rule['schedule'] = {'date': days[0].isoformat(),
                                    'slots': ', '.join(sorted({value.strftime('%H:%M') for value in scheduled})),
                                    'interval': max(1, interval), 'offset': offset}
                changed = True
        if changed:
            self.store.save_automation(aid, **rule)
        return rule

    def action(self, route, body, base):
        if route == '/api/import':
            def import_and_watch():
                result = self.import_folder(body)
                if body.get('watch', False):
                    with self.mutation:
                        if self.store.account(body['account']):
                            self.store.watch(body['account'], str(Path(body['path']).expanduser().resolve()))
                return result
            return self.background(import_and_watch)
        if route == '/api/pick':
            def pick():
                flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                command = [sys.executable, '--pick-folder'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'studio.picker']
                result = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=180,
                                        creationflags=flags, cwd=str(self.web_dir.parent))
                if result.returncode:
                    raise ValueError('Không mở được cửa sổ chọn thư mục. Hãy dán đường dẫn thư mục.')
                return {'path': result.stdout.strip()}
            return self.background(pick)
        if route == '/api/connect':
            return {'url': self.google.begin(body.get('config', {}), base + '/oauth/callback')}
        if route == '/api/playlists':
            return self.background(lambda: self.google.playlists(body['account']))
        if route == '/api/schedule':
            def plan():
                aid = body['account']
                remote = self.google.scheduled(aid) if body.get('sync', True) else []
                with self.mutation, self.engine.lock:
                    ids = list(dict.fromkeys(body.get('ids', [])))
                    jobs = [self.store.job(jid) for jid in ids]
                    jobs = [j for j in jobs if j['state'] == 'draft' and not j['session'] and not j['video_id']]
                    if not jobs or any(j['account'] != aid for j in jobs):
                        raise ValueError('Chỉ xếp lịch cho video chưa bắt đầu của cùng một kênh.')
                    account = self.store.account(aid)
                    if not account:
                        raise ValueError('Kết nối lại kênh trước khi xếp lịch.')
                    jobs = [j for j in self.store.jobs() if j['account'] == aid and j['state'] == 'draft'
                            and not j['session'] and not j['video_id']]
                    ids = [j['id'] for j in jobs]
                    related = {a['id'] for a in self.store.accounts() if a['channel_id'] == account['channel_id']}
                    occupied = remote + [j['publish_at'] for j in self.store.jobs()
                                         if (j['account'] in related or j.get('channel_id') == account['channel_id'])
                                         and j['id'] not in ids and j['publish_at']]
                    times = schedule(len(jobs), body['date'], body['slots'], int(body['interval']), int(body['offset']), occupied)
                    self.store.update_many([(job['id'], {'publish_at': when, 'privacy': 'private'})
                                            for job, when in zip(jobs, times)])
                    self.store.save_automation(aid, schedule={
                        'date': body['date'], 'slots': body['slots'], 'interval': int(body['interval']),
                        'offset': int(body['offset'])})
                return {'count': len(times), 'times': times}
            return self.background(plan)
        with self.mutation, self.engine.lock:
            if route == '/api/unwatch':
                self.store.watch(body['account'], '')
                return {'ok': True}
            if route == '/api/remove-preview':
                _, plans = plan_removal(self.store, body.get('ids', []))
                return {'moved': plans}
            if route == '/api/remove':
                result = remove_drafts(self.store, body.get('ids', []))
                self.store.event(f'Đã chuyển {result["count"]} bản nháp sang thư mục -remove video.')
                return result
            if route == '/api/bulk':
                jobs = [self.store.job(jid) for jid in dict.fromkeys(body.get('ids', []))]
                jobs = [j for j in jobs if j['state'] == 'draft' and not j['session'] and not j['video_id']]
                updates = {k: body[k] for k in ('made_for_kids', 'synthetic') if k in body}
                if not jobs or len(updates) != 2 or any(type(v) is not bool for v in updates.values()):
                    raise ValueError('Chọn video và xác nhận đầy đủ hai khai báo nội dung.')
                validate_choices(body)
                updates.update({k: body[k] for k in ('category', 'language') if k in body})
                if 'playlists' in body:
                    aid = body.get('account')
                    if not aid or not self.store.account(aid) or any(j['account'] != aid for j in jobs):
                        raise ValueError('Chọn một kênh cụ thể để áp dụng playlist cho bản nháp của kênh đó.')
                    playlists = body['playlists']
                    if not isinstance(playlists, list) or any(not isinstance(p, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in playlists):
                        raise ValueError('Danh sách playlist không hợp lệ.')
                    updates['playlists'] = list(dict.fromkeys(playlists))
                accounts = {job['account'] for job in jobs}
                jobs = [job for job in self.store.jobs() if job['account'] in accounts
                        and job['state'] == 'draft' and not job['session'] and not job['video_id']]
                self.store.update_many([(job['id'], updates) for job in jobs])
                for aid in accounts:
                    rule = self.store.automation(aid)
                    content = dict(rule.get('content', {}))
                    content.update(updates)
                    self.store.save_automation(aid, content=content)
                return {'ok': True, 'count': len(jobs)}
            if route == '/api/edit':
                job = self.store.job(body['id'])
                if job['state'] in BUSY or job['session'] or job['video_id']:
                    raise ValueError('Không sửa nội dung khi video đã bắt đầu upload. Xem video trên YouTube Studio.')
                updates = {k: v for k, v in body.get('changes', {}).items() if k in EDITABLE}
                candidate = dict(job, **updates)
                validate(candidate)
                self.store.update(job['id'], **updates, state='draft', error='')
                return {'ok': True}
            if route == '/api/start':
                self.engine.start(body.get('ids', []))
                return {'ok': True}
            if route == '/api/pause':
                self.engine.pause(body.get('ids', []))
                return {'ok': True}
            if route == '/api/reset-session':
                job = self.store.job(body['id'])
                if job['state'] in BUSY or job['video_id'] or not body.get('confirmed'):
                    raise ValueError('Chỉ tạo lại phiên cho video lỗi sau khi đã kiểm tra YouTube Studio.')
                self.store.update(job['id'], session='', progress=0, uploaded=0, state='draft', error='')
                return {'ok': True}
            if route == '/api/forget':
                aid = body['account']
                if any(j['account'] == aid and j['state'] in BUSY for j in self.store.jobs()):
                    raise ValueError('Tạm dừng upload của kênh trước khi ngắt kết nối.')
                self.store.forget(aid)
                return {'ok': True}
        raise ValueError('Thao tác không tồn tại.')


def make_server(app, port=0):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass  # Never log OAuth authorization codes or tokens.

        def send(self, status, content, mime='application/json; charset=utf-8', extra=None):
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False).encode()
            elif isinstance(content, str):
                content = content.encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def trusted(self, api=False):
            host = self.headers.get('Host', '')
            allowed = {f'{UI_HOST}:{self.server.server_port}'}
            if host not in allowed:
                return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + host:
                return False
            if api and not secrets.compare_digest(self.headers.get('X-Studio-Token', ''), app.token):
                return False
            return True

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if not self.trusted(parsed.path.startswith('/api/')):
                return self.send(403, {'error': 'Yêu cầu không hợp lệ.'})
            if parsed.path == '/health':
                return self.send(200, {'app': 'UpVideoStudio', 'version': __version__, 'ui_url': ui_url(self.server.server_port)})
            if parsed.path == '/api/state':
                return self.send(200, app.snapshot())
            if parsed.path == '/api/task':
                tid = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
                with app.task_lock:
                    task = app.tasks.get(tid, {'state': 'error', 'error': 'Thao tác đã hết hạn.'})
                return self.send(200, task)
            if parsed.path == '/oauth/callback':
                params = urllib.parse.parse_qs(parsed.query)
                try:
                    if params.get('error'):
                        raise ValueError('Bạn đã hủy hoặc chưa cấp quyền đăng nhập. Có thể quay lại và thử lại.')
                    account = app.google.finish(params.get('state', [''])[0], params.get('code', [''])[0])
                    message = 'Đã kết nối ' + account['name'] + '. Bạn có thể đóng tab này và quay lại UpVideo Studio.'
                except Exception as exc:
                    message = str(exc) if isinstance(exc, (ValueError, ApiError)) else 'Kết nối thất bại. Hãy thử lại.'
                return self.send(200, '<!doctype html><html lang="vi"><meta charset="utf-8"><title>Kết nối Google</title><body><h2>' + html.escape(message) + '</h2><a href="' + ui_url(self.server.server_port) + '/">Về UpVideo Studio</a></body></html>', 'text/html; charset=utf-8')
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if parsed.path not in files:
                return self.send(404, {'error': 'Không tìm thấy.'})
            name, mime = files[parsed.path]
            text = (app.web_dir / name).read_text(encoding='utf-8')
            if name == 'index.html':
                text = text.replace('__TOKEN__', app.token)
                text = text.replace('__CATALOG__', html.escape(json.dumps({'categories': CATEGORIES, 'languages': LANGUAGES}), quote=True))
                text = text.replace('__VERSION__', __version__)
            self.send(200, text, mime)

        def do_POST(self):
            if not self.trusted(True):
                return self.send(403, {'error': 'Yêu cầu không hợp lệ. Tải lại trang.'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1_000_000:
                    raise ValueError('Dữ liệu yêu cầu quá lớn hoặc trống.')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Dữ liệu yêu cầu không hợp lệ.')
                result = app.action(self.path, body, 'http://127.0.0.1:' + str(self.server.server_port))
                self.send(200, result)
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ValueError, ApiError)) else 'Không thực hiện được thao tác. Kiểm tra dữ liệu và thử lại.'
                self.send(400, {'error': message})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server
