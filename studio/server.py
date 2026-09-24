import html
import json
import math
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .domain import scan_folder, scan_channel_variants, validate_source, schedule, parse_date, fingerprint, VIDEO_EXTS
from .engine import Engine
from .google import Google, ApiError
from .store import Store
from .address import UI_HOST, ui_url
from .catalog import CATEGORIES, LANGUAGES, validate_choices
from . import __version__
from .removal import BUSY_STATES, plan_removal, remove_jobs

EDITABLE = {'title', 'description', 'tags', 'thumbnail', 'category', 'language', 'privacy',
            'publish_at', 'made_for_kids', 'synthetic', 'playlists'}
BUSY = {'queued', 'uploading', 'finishing'}
SOURCE_FIELDS = ('title', 'description', 'tags', 'thumbnail', 'content_source', 'size', 'fingerprint')


class App:
    def __init__(self, data_dir, web_dir):
        self.store = Store(Path(data_dir) / 'studio.db')
        self.google = Google(self.store)
        self.engine = Engine(self.store, self.google)
        self.web_dir = Path(web_dir)
        self.token = secrets.token_urlsafe(32)
        self.mutation = threading.RLock()
        self.picker_lock = threading.Lock()
        self.tasks = {}
        self.task_lock = threading.Lock()

    def close(self):
        self.engine.close()

    def snapshot(self):
        jobs = []
        for j in self.store.jobs():
            j['has_session'] = bool(j['session'])
            j.pop('session', None)
            j.pop('fingerprint', None)
            j.pop('watch_source', None)
            j.pop('content_source', None)
            jobs.append(j)
        accounts = self.store.accounts()
        automation = {account['id']: self.automation_rule(account['id']) for account in accounts}
        return {'jobs': jobs, 'accounts': accounts, 'events': self.store.events(),
                'watches': self.store.watches(), 'automation': automation}

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

    def pick_folder(self):
        if not self.picker_lock.acquire(blocking=False):
            raise ValueError('Hộp thoại chọn thư mục đang mở. Hãy hoàn tất hoặc đóng hộp thoại đó trước.')
        try:
            from .picker import folder_windows
            return {'path': folder_windows()}
        finally:
            self.picker_lock.release()

    @staticmethod
    def source_snapshot(row):
        files = {}
        for key, value in (('video', row.get('path')), ('content', row.get('content_source')),
                           ('thumbnail', row.get('thumbnail'))):
            if value:
                path = Path(value)
                if not path.is_file():
                    files[key] = {'missing': str(path)}
                    continue
                files[key] = row.get('fingerprint') if key == 'video' else fingerprint(path)
        fields = {key: row.get(key) for key in SOURCE_FIELDS}
        fields['asset_errors'] = row.get('asset_errors', [])
        return {'fields': fields, 'files': files}

    def source_changed(self, job, row):
        current = self.source_snapshot(row)
        previous = job.get('source_snapshot')
        if previous:
            old_fields = dict(previous.get('fields') or {})
            old_fields.setdefault('asset_errors', [])
            previous = dict(previous, fields=old_fields)
            return previous != current, current
        # Legacy linked drafts only stored parsed metadata; keep their old behavior
        # where no content change can be proven from source hashes.
        old_fields = job.get('watch_source')
        if old_fields:
            return (any(old_fields.get(key) != row.get(key) for key in SOURCE_FIELDS)
                    or job.get('asset_errors', []) != row.get('asset_errors', [])), current
        return (any(job.get(key) != row.get(key) for key in SOURCE_FIELDS)
                or job.get('asset_errors', []) != row.get('asset_errors', [])), current

    def import_folder(self, body):
        aid = body.get('account', '')
        account = self.store.account(aid)
        if not account:
            raise ValueError('Kết nối và chọn một kênh trước khi nhập video.')
        rows, warnings = scan_folder(body.get('path', ''), channel_name=account['name'])
        duplicate_data = self.channel_duplicate_data(self.store.account(aid), rows)
        allowed = {(item.get('path'), item.get('fingerprint')) for item in body.get('allow_youtube', [])
                   if isinstance(item, dict)}
        added = duplicates = 0
        draft_duplicates = []
        youtube_duplicates = []
        added_jobs = []
        with self.mutation:
            account = self.store.account(aid)
            if not account:
                raise ValueError('Kênh đã bị ngắt kết nối.')
            same = {a['id'] for a in self.store.accounts() if a['channel_id'] == account['channel_id']}
            known = {j['fingerprint'] for j in self.store.jobs()
                     if (j['account'] in same or j.get('channel_id') == account['channel_id'])
                     and not j.get('video_id')}
            existing = {str(Path(j['path']).resolve()): j for j in self.store.jobs() if j['account'] == aid}
            for row in rows:
                row['import_root'] = str(Path(body['path']).expanduser().resolve())
                if not Path(row['path']).is_file() and not row.get('asset_errors'):
                    raise ValueError('Thư mục vừa thay đổi. Hãy thử nhập lại.')
                row.update(watch_root=str(Path(body['path']).expanduser().resolve()),
                           watch_source={k: row[k] for k in SOURCE_FIELDS},
                           source_snapshot=self.source_snapshot(row))
                previous = existing.get(row['path'])
                if previous and previous['fingerprint'] == row['fingerprint']:
                    # Migrate folder provenance only; preserve paused sessions and metadata.
                    updates = {'import_root': row['import_root']}
                    updates['watch_root'] = row['watch_root']
                    if not previous.get('source_snapshot'):
                        updates['source_snapshot'] = row['source_snapshot']
                        updates['watch_source'] = row['watch_source']
                    self.store.update(previous['id'], **updates)
                duplicate = duplicate_data[row['path']]
                if duplicate['draft_duplicate'] or row['fingerprint'] in known:
                    duplicates += 1
                    draft_duplicates.append(Path(row['path']).name)
                    continue
                if duplicate['youtube_matches'] and (row['path'], row['fingerprint']) not in allowed:
                    duplicates += 1
                    youtube_duplicates.append({'filename': Path(row['path']).name,
                                               'matches': duplicate['youtube_matches']})
                    continue
                if row['fingerprint'] not in known:
                    job = self.store.add(row, aid)
                    if job:
                        known.add(row['fingerprint'])
                        added_jobs.append(job)
                        added += 1
                    else:
                        duplicates += 1
                        draft_duplicates.append(Path(row['path']).name)
                else:
                    duplicates += 1
                    draft_duplicates.append(Path(row['path']).name)
            self.apply_automation(aid, added_jobs)
        self.store.event(f'Đã nhập {added} video, bỏ qua {duplicates} video trùng.', account=aid)
        return {'added': added, 'duplicates': duplicates, 'warnings': warnings,
                'draft_duplicates': draft_duplicates, 'youtube_duplicates': youtube_duplicates}

    def channel_duplicate_data(self, account, rows):
        """Compare exact video SHA-256 fingerprints with drafts and recent uploads."""
        related = {item['id'] for item in self.store.accounts()
                   if item['channel_id'] == account['channel_id']}
        jobs = [job for job in self.store.jobs()
                if job['account'] in related or job.get('channel_id') == account['channel_id']]
        by_fingerprint = {}
        for job in jobs:
            if not job.get('video_id'):
                by_fingerprint.setdefault(job['fingerprint'], []).append(job)
        result = {}
        for row in rows:
            matches = [{'id': item['video_id'], 'title': item['title'],
                        'url': 'https://www.youtube.com/watch?v=' + item['video_id']}
                       for item in self.store.uploaded_fingerprint_matches(
                           account['channel_id'], row['fingerprint'])]
            unique = {item['id']: item for item in matches}
            result[row['path']] = {
                'draft_duplicate': bool(by_fingerprint.get(row['fingerprint'])),
                'youtube_matches': list(unique.values())}
        return result

    def preview_import(self, body):
        aid = body.get('account', '')
        account = self.store.account(aid)
        if not account:
            raise ValueError('Kết nối và chọn một kênh trước khi nhập video.')
        rows, warnings = scan_folder(body.get('path', ''), channel_name=account['name'])
        duplicate_data = self.channel_duplicate_data(account, rows)
        return {'account': aid, 'path': str(Path(body['path']).expanduser().resolve()),
                'warnings': warnings,
                'videos': [{'path': row['path'], 'fingerprint': row['fingerprint'], 'title': row['title'],
                            'filename': Path(row['path']).name,
                            'asset_errors': row.get('asset_errors', []),
                            'draft_duplicate': duplicate_data[row['path']]['draft_duplicate'],
                            'youtube_matches': duplicate_data[row['path']]['youtube_matches']}
                           for row in rows]}

    def multi_import_plan(self, body, *, hash_files=False):
        aids = body.get('accounts')
        if not isinstance(aids, list) or not aids or any(not isinstance(aid, str) for aid in aids):
            raise ValueError('Chọn ít nhất một kênh theo thứ tự muốn gán nội dung.')
        if len(set(aids)) != len(aids):
            raise ValueError('Danh sách kênh bị trùng.')
        accounts = [self.store.account(aid) for aid in aids]
        if any(not account for account in accounts):
            raise ValueError('Một kênh đã bị xóa hoặc ngắt kết nối. Tải lại danh sách rồi thử lại.')
        if len({account['channel_id'] for account in accounts}) != len(accounts):
            raise ValueError('Không thể chọn cùng một kênh YouTube qua nhiều kết nối OAuth.')
        groups = scan_channel_variants(body.get('path', ''), len(accounts), hash_files=hash_files,
                                       channel_names=[account['name'] for account in accounts])
        return accounts, groups

    def preview_multi_import(self, body):
        accounts, groups = self.multi_import_plan(body, hash_files=True)
        by_account = {}
        for index, account in enumerate(accounts):
            rows = [group['variants'][index]['row'] for group in groups]
            by_account[account['id']] = self.channel_duplicate_data(account, rows)
        return {'channels': [{'id': account['id'], 'name': account['name']} for account in accounts],
                'videos': [{'folder': Path(group['folder']).name, 'video': Path(group['video']).name,
                            'variants': [{'account': account['id'], 'channel': account['name'],
                                          'path': variant['row']['path'], 'fingerprint': variant['row']['fingerprint'],
                                          'title': variant['row']['title'], 'thumbnail': Path(variant['thumbnail']).name,
                                          'asset_errors': variant['row'].get('asset_errors', []),
                                          'draft_duplicate': by_account[account['id']][variant['row']['path']]['draft_duplicate'],
                                          'youtube_matches': by_account[account['id']][variant['row']['path']]['youtube_matches']}
                                         for account, variant in zip(accounts, group['variants'])]}
                           for group in groups]}

    def import_multi_channel(self, body):
        accounts, groups = self.multi_import_plan(body, hash_files=True)
        duplicate_data = {}
        for index, account in enumerate(accounts):
            duplicate_data[account['id']] = self.channel_duplicate_data(
                account, [group['variants'][index]['row'] for group in groups])
        allowed = {(item.get('account'), item.get('path'), item.get('fingerprint'))
                   for item in body.get('allow_youtube', [])
                   if isinstance(item, dict)}
        added_by_account = {account['id']: [] for account in accounts}
        added = duplicates = 0
        draft_duplicates = []
        youtube_duplicates = []
        root = str(Path(body['path']).expanduser().resolve())
        with self.mutation:
            accounts = [self.store.account(account['id']) for account in accounts]
            if any(not account for account in accounts):
                raise ValueError('Một kênh đã bị xóa trong lúc nhập. Không có video nào được thêm.')
            known = {}
            for account in accounts:
                related = {item['id'] for item in self.store.accounts()
                           if item['channel_id'] == account['channel_id']}
                known[account['id']] = {job['fingerprint'] for job in self.store.jobs()
                                        if (job['account'] in related or job.get('channel_id') == account['channel_id'])
                                        and not job.get('video_id')}
            for group in groups:
                for account, variant in zip(accounts, group['variants']):
                    row = dict(variant['row'], import_root=root, watch_root=root,
                               watch_source={key: variant['row'].get(key) for key in SOURCE_FIELDS},
                               source_snapshot=self.source_snapshot(variant['row']))
                    duplicate = duplicate_data[account['id']][row['path']]
                    if duplicate['draft_duplicate'] or row['fingerprint'] in known[account['id']]:
                        duplicates += 1
                        draft_duplicates.append({'channel': account['name'], 'video': Path(row['path']).name})
                        continue
                    if duplicate['youtube_matches'] and (account['id'], row['path'], row['fingerprint']) not in allowed:
                        duplicates += 1
                        youtube_duplicates.append({'channel': account['name'],
                                                   'video': Path(row['path']).name,
                                                   'matches': duplicate['youtube_matches']})
                        continue
                    job = self.store.add(row, account['id'])
                    if job:
                        known[account['id']].add(row['fingerprint'])
                        added_by_account[account['id']].append(job)
                        added += 1
                    else:
                        duplicates += 1
            for account in accounts:
                self.apply_automation(account['id'], added_by_account[account['id']])
                if added_by_account[account['id']]:
                    self.store.event(f'Đã nhập {len(added_by_account[account["id"]])} video theo bộ nội dung nhiều kênh.',
                                     account=account['id'])
                self.store.watch(account['id'], root, mode='multi',
                                 accounts=[item['id'] for item in accounts])
        return {'added': added, 'duplicates': duplicates, 'warnings': [],
                'draft_duplicates': draft_duplicates, 'youtube_duplicates': youtube_duplicates}

    def linked_update_plan(self, body):
        if any(job['state'] in BUSY for job in self.store.jobs()):
            raise ValueError('Hãy tạm dừng và chờ mọi video ngừng tải lên trước khi cập nhật thư mục.')
        requested = body.get('accounts')
        if requested is None:
            requested = [account['id'] for account in self.store.accounts()]
        if not isinstance(requested, list) or any(not isinstance(aid, str) for aid in requested):
            raise ValueError('Danh sách kênh cập nhật không hợp lệ.')
        requested = set(requested)
        accounts = {item['id']: item for item in self.store.accounts() if item['id'] in requested}
        if requested - set(accounts):
            raise ValueError('Một kênh đã bị xóa hoặc ngắt kết nối. Hãy tải lại danh sách kênh.')
        links = [link for link in self.store.watches() if link['account'] in requested and link['path']]
        if not links:
            raise ValueError('Các kênh đã chọn chưa liên kết thư mục nào.')

        grouped_links = {}
        for link in links:
            if link.get('mode') == 'multi':
                key = (link['mode'], link['path'], tuple(link.get('accounts') or []))
            else:
                key = ('single', link['account'], link['path'])
            grouped_links.setdefault(key, link)

        rows_by_account = {aid: [] for aid in requested}
        errors = []
        failed_links = set()
        for key, link in grouped_links.items():
            try:
                if link.get('mode') == 'multi':
                    order = link.get('accounts') or []
                    linked_accounts = [self.store.account(aid) for aid in order]
                    if not order or any(not account for account in linked_accounts):
                        raise ValueError(f"{link['path']}: danh sách kênh của thư mục nhiều kênh không còn đầy đủ.")
                    root = Path(link['path']).expanduser().resolve()
                    if not root.is_dir():
                        raise ValueError(f'{root}: thư mục liên kết không tồn tại hoặc không truy cập được.')
                    children = list(root.iterdir())
                    empty_root = not any(item.is_dir() for item in children) and not any(
                        item.is_file() and item.suffix.lower() in VIDEO_EXTS for item in children)
                    groups = [] if empty_root else scan_channel_variants(
                        str(root), len(order), hash_files=True,
                        channel_names=[account['name'] for account in linked_accounts])
                    for index, aid in enumerate(order):
                        if aid in requested:
                            rows_by_account[aid].extend(group['variants'][index]['row'] for group in groups)
                else:
                    aid = link['account']
                    linked_account = self.store.account(aid)
                    rows, _ = scan_folder(link['path'], channel_name=(linked_account or {}).get('name', aid))
                    rows_by_account[aid].extend(rows)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
                if link.get('mode') == 'multi':
                    failed_links.update((aid, link['path']) for aid in (link.get('accounts') or [])
                                        if aid in requested)
                else:
                    failed_links.add((link['account'], link['path']))

        jobs = self.store.jobs()
        actions = []
        remote_checks = {}
        for aid in accounts:
            account = accounts[aid]
            roots = {link['path'] for link in links if link['account'] == aid}
            healthy_roots = roots - {path for account_id, path in failed_links if account_id == aid}
            managed = [job for job in jobs if job['account'] == aid and job.get('watch_root') in healthy_roots]
            by_path = {}
            for job in managed:
                by_path.setdefault(str(Path(job['path']).resolve()), []).append(job)
            current_rows = rows_by_account[aid]
            present = {str(Path(row['path']).resolve()) for row in current_rows}
            matched_jobs = set()
            for row in current_rows:
                row_path = str(Path(row['path']).resolve())
                if row_path in by_path:
                    matched_jobs.update(job['id'] for job in by_path[row_path])
                    continue
                renamed_path = next((path for path, path_jobs in by_path.items()
                                     if path not in present and any(job['fingerprint'] == row['fingerprint']
                                                                    and job['id'] not in matched_jobs
                                                                    for job in path_jobs)), None)
                if renamed_path:
                    old_jobs = by_path[renamed_path]
                    renamed_jobs = [job for job in old_jobs if job['id'] not in matched_jobs
                                    and job['fingerprint'] == row['fingerprint']]
                    if renamed_jobs:
                        remaining_jobs = [job for job in old_jobs if job not in renamed_jobs]
                        if remaining_jobs:
                            by_path[renamed_path] = remaining_jobs
                        else:
                            by_path.pop(renamed_path)
                        by_path[row_path] = renamed_jobs
                        matched_jobs.update(job['id'] for job in renamed_jobs)
            for path, path_jobs in by_path.items():
                row = next((item for item in current_rows if str(Path(item['path']).resolve()) == path), None)
                if row is None:
                    for job in path_jobs:
                        actions.append({'action': 'remove', 'account': aid, 'channel': account['name'],
                                        'path': job['path'], 'title': job['title'], 'job_id': job['id']})
                    continue
                # Prefer an editable draft if a previously published copy and a
                # later repost draft happen to share the same source path.
                job = next((item for item in path_jobs if not item.get('video_id') and item['state'] != 'done'),
                           path_jobs[-1])
                if job.get('video_id') or job['state'] == 'done':
                    actions.append({'action': 'repost', 'account': aid, 'channel': account['name'],
                                    'path': row['path'], 'fingerprint': row['fingerprint'],
                                    'title': row['title'], 'previous_title': job['title'],
                                    'video_id': job.get('video_id', ''), 'asset_errors': row.get('asset_errors', []),
                                    'row': row})
                else:
                    changed, snapshot = self.source_changed(job, row)
                    actions.append({'action': 'changed' if changed else 'unchanged',
                                    'account': aid, 'channel': account['name'], 'path': row['path'],
                                    'fingerprint': row['fingerprint'], 'title': row['title'],
                                    'asset_errors': row.get('asset_errors', []),
                                    'job_id': job['id'], 'queue_position': job.get('queue_position'),
                                    'row': row, 'source_snapshot': snapshot})
            managed_paths = set(by_path)
            new_rows = [row for row in current_rows if str(Path(row['path']).resolve()) not in managed_paths]
            if new_rows:
                remote_checks[aid] = self.channel_duplicate_data(account, new_rows)
            for row in new_rows:
                detail = remote_checks.get(aid, {}).get(row['path'], {})
                duplicate_job = next((job for job in jobs if job['account'] == aid
                                      and job['fingerprint'] == row['fingerprint']
                                      and job.get('watch_root') not in roots), None)
                actions.append({'action': 'duplicate-draft' if detail.get('draft_duplicate') or duplicate_job
                                else 'youtube-review' if detail.get('youtube_matches') else 'new',
                                'account': aid, 'channel': account['name'], 'path': row['path'],
                                'fingerprint': row['fingerprint'], 'title': row['title'], 'row': row,
                                'asset_errors': row.get('asset_errors', []),
                                'youtube_matches': detail.get('youtube_matches', [])})

        # A failed folder scan must never be interpreted as an empty folder and
        # therefore must not trigger removal of its existing drafts.
        public_actions = [{key: value for key, value in item.items()
                           if key not in ('row', 'source_snapshot', 'job_id', 'queue_position')}
                          for item in actions]
        counts = {name: sum(item['action'] == name for item in actions)
                  for name in ('new', 'changed', 'unchanged', 'remove', 'repost',
                               'duplicate-draft', 'youtube-review')}
        return {'accounts': sorted(requested), 'actions': public_actions, 'errors': errors,
                'counts': counts, '_actions': actions}

    def preview_linked_update(self, body):
        plan = self.linked_update_plan(body)
        plan.pop('_actions', None)
        return plan

    def update_linked_folders(self, body):
        plan = self.linked_update_plan(body)
        if plan['errors']:
            raise ValueError('Không cập nhật được vì có thư mục/file lỗi:\n' + '\n'.join(plan['errors']))
        if any(job['state'] in BUSY for job in self.store.jobs()):
            raise ValueError('Có video bắt đầu tải lên trong lúc chuẩn bị. Hãy dừng tải lên rồi cập nhật lại.')
        allow = {(item.get('account'), item.get('path'), item.get('fingerprint'))
                 for item in body.get('allow_youtube', []) if isinstance(item, dict)}
        repost = {(item.get('account'), item.get('path'), item.get('fingerprint'))
                  for item in body.get('repost', []) if isinstance(item, dict)}
        result = {'added': 0, 'replaced': 0, 'removed': 0, 'unchanged': 0,
                  'duplicates': 0, 'repost_skipped': 0, 'by_channel': {}}
        with self.mutation, self.engine.lock:
            if any(job['state'] in BUSY for job in self.store.jobs()):
                raise ValueError('Có tác vụ tải lên đang chạy. Hãy tạm dừng và thử cập nhật lại.')
            for action in plan['_actions']:
                aid = action['account']
                report = result['by_channel'].setdefault(aid, {'name': action['channel'], 'added': 0,
                    'replaced': 0, 'removed': 0, 'unchanged': 0, 'duplicates': 0, 'repost_skipped': 0})
                if action['action'] == 'unchanged':
                    existing_job = self.store.job(action['job_id'])
                    if not existing_job.get('source_snapshot'):
                        self.store.update(action['job_id'], source_snapshot=action['source_snapshot'],
                                          watch_source={field: action['row'].get(field)
                                                        for field in SOURCE_FIELDS})
                    result['unchanged'] += 1; report['unchanged'] += 1
                    continue
                if action['action'] == 'remove':
                    self.store.remove([action['job_id']])
                    result['removed'] += 1; report['removed'] += 1
                    continue
                if action['action'] == 'duplicate-draft':
                    result['duplicates'] += 1; report['duplicates'] += 1
                    continue
                key = (aid, action['path'], action['fingerprint'])
                if action['action'] == 'repost' and key not in repost:
                    result['repost_skipped'] += 1; report['repost_skipped'] += 1
                    continue
                if action['action'] == 'youtube-review' and key not in allow:
                    result['duplicates'] += 1; report['duplicates'] += 1
                    continue
                row = dict(action['row'], import_root=str(Path(action['path']).parent),
                           watch_root=next(link['path'] for link in self.store.watches()
                                           if link['account'] == aid and Path(action['path']).is_relative_to(Path(link['path']))),
                           watch_source={field: action['row'].get(field) for field in SOURCE_FIELDS},
                           source_snapshot=action.get('source_snapshot') or self.source_snapshot(action['row']))
                if action['action'] == 'changed':
                    self.store.remove([action['job_id']])
                    result['replaced'] += 1; report['replaced'] += 1
                    job = self.store.add(row, aid, position=action.get('queue_position'))
                else:
                    job = self.store.add(row, aid)
                    if action['action'] == 'repost':
                        result['added'] += 1; report['added'] += 1
                    else:
                        result['added'] += 1; report['added'] += 1
                if job:
                    self.apply_automation(aid, [job])
                elif action['action'] not in ('changed',):
                    result['duplicates'] += 1; report['duplicates'] += 1
            for aid, report in result['by_channel'].items():
                self.store.event(f"Cập nhật thư mục: thêm {report['added']}, thay {report['replaced']}, "
                                 f"gỡ {report['removed']}, giữ {report['unchanged']}, "
                                 f"bỏ qua {report['duplicates']} mục trùng.", account=aid)
        return result

    def apply_automation(self, aid, jobs, include_content=True, force_publishing=False):
        """Apply the latest channel defaults to every supplied unstarted draft."""
        if not jobs:
            return []
        rule = self.automation_rule(aid)
        content = rule.get('content', {}) if include_content else {}
        allowed = {'made_for_kids', 'synthetic', 'category', 'language', 'playlists'}
        content = {key: value for key, value in content.items() if key in allowed}
        planned = {}
        schedule_rule = rule.get('schedule')
        visibility = schedule_rule.get('visibility', 'schedule') if schedule_rule else ''
        publishing_jobs = [job for job in jobs if force_publishing or not job.get('publishing_override', False)]
        publishing_ids = {job['id'] for job in publishing_jobs}
        if schedule_rule and visibility == 'schedule' and publishing_jobs:
            account = self.store.account(aid)
            related = {a['id'] for a in self.store.accounts()
                       if account and a['channel_id'] == account['channel_id']}
            new_ids = {job['id'] for job in publishing_jobs}
            occupied = [job['publish_at'] for job in self.store.jobs()
                        if (job['account'] in related or (account and job.get('channel_id') == account['channel_id']))
                        and job['id'] not in new_ids and job.get('publish_at')]
            schedule_jobs = list(publishing_jobs)
            if schedule_jobs:
                times = schedule(len(schedule_jobs), schedule_rule['date'], schedule_rule['slots'],
                                 int(schedule_rule['interval']), int(schedule_rule['offset']), occupied)
                planned = dict(zip((job['id'] for job in schedule_jobs), times))
        updates = []
        for job in jobs:
            changes = dict(content)
            if job['id'] in publishing_ids and job['id'] in planned:
                changes.update(publish_at=planned[job['id']], privacy='private')
            elif job['id'] in publishing_ids and visibility in {'private', 'unlisted', 'public'}:
                changes.update(publish_at='', privacy=visibility)
            if changes:
                updates.append((job['id'], changes))
        return self.store.update_many(updates) if updates else jobs

    def resequence_schedule(self, aid):
        """Keep collision-safe slots, but assign them in the user's new queue order."""
        rule = self.automation_rule(aid).get('schedule')
        if not rule or rule.get('visibility', 'schedule') != 'schedule':
            return []
        jobs = [job for job in self.store.jobs() if job['account'] == aid
                and job['state'] == 'draft' and not job.get('session') and not job.get('video_id')
                and not job.get('publishing_override', False)]
        times = sorted(job['publish_at'] for job in jobs if job.get('publish_at'))
        if len(times) != len(jobs):
            return self.apply_automation(aid, jobs, include_content=False)
        return self.store.update_many([(job['id'], {'publish_at': when, 'privacy': 'private'})
                                       for job, when in zip(jobs, times)])

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
        if route == '/api/check-connections':
            def check_connections():
                checked, errors = [], []
                for account in self.store.accounts():
                    try:
                        self.google.access_token(account['id'], force=True)
                        checked.append(account['id'])
                    except ApiError as exc:
                        errors.append({'account': account['id'], 'error': str(exc),
                                       'reauth_required': exc.reason in self.google.AUTH_REASONS})
                    except ValueError as exc:
                        errors.append({'account': account['id'], 'error': str(exc), 'reauth_required': False})
                return {'checked': checked, 'errors': errors}
            return self.background(check_connections)
        if route == '/api/update-preview':
            return self.background(lambda: self.preview_linked_update(body))
        if route == '/api/update-folders':
            return self.background(lambda: self.update_linked_folders(body))
        if route == '/api/import-preview':
            return self.background(lambda: self.preview_import(body))
        if route == '/api/multi-import-preview':
            return self.background(lambda: self.preview_multi_import(body))
        if route == '/api/import-multi':
            return self.background(lambda: self.import_multi_channel(body))
        if route == '/api/import':
            def import_and_link():
                result = self.import_folder(body)
                with self.mutation:
                    if self.store.account(body['account']):
                        self.store.watch(body['account'], str(Path(body['path']).expanduser().resolve()))
                return result
            return self.background(import_and_link)
        if route == '/api/pick':
            return self.background(self.pick_folder)
        if route == '/api/connect':
            config = body.get('config', {})
            expected_channel_id = ''
            account_id = body.get('account', '')
            if account_id:
                current = self.store.account(account_id)
                oauth_client = config.get('installed', {}) if isinstance(config, dict) else {}
                client_id = oauth_client.get('client_id', '') if isinstance(oauth_client, dict) else ''
                if not current:
                    raise ValueError('Không tìm thấy kênh cần kết nối lại. Hãy tải lại danh sách kênh.')
                if client_id != current.get('client_id'):
                    raise ValueError('Để giữ dữ liệu kênh, hãy chọn đúng file OAuth Client JSON đã dùng khi kết nối kênh này.')
                expected_channel_id = current['channel_id']
            return {'url': self.google.begin(config, base + '/oauth/callback', expected_channel_id)}
        if route == '/api/reconnect':
            return {'url': self.google.begin_saved_reconnect(body.get('account', ''), base + '/oauth/callback')}
        if route == '/api/reconnect-replace':
            aid = body.get('account', '')
            if any(job['account'] == aid and job['state'] in BUSY_STATES for job in self.store.jobs()):
                raise ValueError('Hãy tạm dừng upload của kênh trước khi chuyển sang OAuth client mới.')
            return {'url': self.google.begin_account_replacement(
                aid, body.get('config', {}), base + '/oauth/callback')}
        if route == '/api/playlists':
            return self.background(lambda: self.google.playlists(body['account']))
        if route == '/api/shutdown':
            if body.get('confirmed') is not True:
                raise ValueError('Cần xác nhận trước khi thoát chương trình.')
            with self.engine.lock:
                running = [job['id'] for job in self.store.jobs() if job['state'] in BUSY]
                if running:
                    self.engine.pause(running)
            self.store.event('Người dùng đã yêu cầu thoát chương trình.')
            return {'ok': True, 'paused': len(running)}
        if route == '/api/remove':
            ids = list(dict.fromkeys(body.get('ids', [])))
            jobs = [self.store.job(jid) for jid in ids]
            if any(job['state'] in BUSY_STATES for job in jobs):
                def stop_and_remove():
                    with self.mutation, self.engine.lock:
                        self.engine.pause(ids)
                    deadline = time.monotonic() + 180
                    while any(self.store.job(jid)['state'] in BUSY_STATES for jid in ids):
                        if time.monotonic() >= deadline:
                            raise ValueError('Chưa dừng được tác vụ sau 3 phút. Hãy thử xóa lại khi trạng thái đã chuyển sang Tạm dừng.')
                        time.sleep(.1)
                    with self.mutation, self.engine.lock:
                        result = remove_jobs(self.store, ids)
                        self.store.event(f'Đã dừng tác vụ và xóa {result["count"]} video khỏi hàng đợi.')
                        return result
                return self.background(stop_and_remove)
        if route == '/api/schedule':
            def plan():
                aid = body['account']
                visibility = body.get('visibility', 'schedule')
                if not isinstance(visibility, str) or visibility not in {'private', 'unlisted', 'public', 'schedule'}:
                    raise ValueError('Chế độ hiển thị không hợp lệ.')
                sync = body.get('sync', True)
                if type(sync) is not bool:
                    raise ValueError('Lựa chọn đồng bộ lịch không hợp lệ.')
                overwrite = body.get('overwrite_overrides', False)
                if type(overwrite) is not bool:
                    raise ValueError('Lựa chọn ghi đè thiết lập riêng không hợp lệ.')
                if visibility == 'public' and body.get('confirmed_public') is not True:
                    raise ValueError('Xác nhận trước khi đặt video ở chế độ công khai ngay.')
                remote = self.google.scheduled(aid) if visibility == 'schedule' and sync else []
                with self.mutation, self.engine.lock:
                    ids = list(dict.fromkeys(body.get('ids', [])))
                    jobs = [self.store.job(jid) for jid in ids]
                    jobs = [j for j in jobs if j['state'] == 'draft' and not j['session'] and not j['video_id']]
                    if not jobs or any(j['account'] != aid for j in jobs):
                        raise ValueError('Chỉ xếp lịch cho video chưa bắt đầu của cùng một kênh.')
                    account = self.store.account(aid)
                    if not account:
                        raise ValueError('Kết nối lại kênh trước khi xếp lịch.')
                    channel_jobs = [j for j in self.store.jobs() if j['account'] == aid and j['state'] == 'draft'
                                    and not j['session'] and not j['video_id']]
                    jobs = [j for j in channel_jobs if overwrite or not j.get('publishing_override', False)]
                    ids = [j['id'] for j in jobs]
                    related = {a['id'] for a in self.store.accounts() if a['channel_id'] == account['channel_id']}
                    if visibility == 'schedule':
                        occupied = remote + [j['publish_at'] for j in self.store.jobs()
                                             if (j['account'] in related or j.get('channel_id') == account['channel_id'])
                                             and j['id'] not in ids and j['publish_at']]
                        planned = schedule(max(1, len(jobs)), body['date'], body['slots'], int(body['interval']), int(body['offset']), occupied)
                        times = planned[:len(jobs)]
                        self.store.update_many([(job['id'], {'publish_at': when, 'privacy': 'private',
                                                             'publishing_override': False})
                                                for job, when in zip(jobs, times)])
                    else:
                        times = []
                        self.store.update_many([(job['id'], {'publish_at': '', 'privacy': visibility,
                                                             'publishing_override': False})
                                                for job in jobs])
                    self.store.save_automation(aid, schedule={
                        'date': body['date'], 'slots': body['slots'], 'interval': int(body['interval']),
                        'offset': int(body['offset']), 'sync': sync, 'visibility': visibility})
                return {'count': len(jobs), 'skipped': len(channel_jobs)-len(jobs),
                        'times': times, 'visibility': visibility}
            return self.background(plan)
        with self.mutation, self.engine.lock:
            if route == '/api/unwatch':
                self.store.watch(body['account'], '')
                return {'ok': True}
            if route == '/api/remove-preview':
                jobs, preview = plan_removal(self.store, body.get('ids', []), allow_busy=True)
                return {'count': len(jobs), 'videos': preview}
            if route == '/api/remove':
                result = remove_jobs(self.store, body.get('ids', []))
                self.store.event(f'Đã xóa {result["count"]} video khỏi hàng đợi.')
                return result
            if route == '/api/reorder':
                jid, target = body.get('id', ''), body.get('target', '')
                if not jid or not target or jid == target or type(body.get('after', False)) is not bool:
                    raise ValueError('Vị trí sắp xếp không hợp lệ.')
                job = self.store.job(jid)
                self.store.reorder(jid, target, body.get('after', False))
                drafts = [item for item in self.store.jobs() if item['account'] == job['account']
                          and item['state'] == 'draft' and not item.get('session') and not item.get('video_id')]
                self.resequence_schedule(job['account'])
                return {'ok': True, 'jobs': [item['id'] for item in drafts]}
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
            if route == '/api/visibility':
                job = self.store.job(body['id'])
                if job['state'] != 'draft' or job['session'] or job['video_id']:
                    raise ValueError('Chỉ đổi chế độ xuất bản cho video chưa bắt đầu upload.')
                mode = body.get('mode')
                if not isinstance(mode, str) or mode not in {'inherit', 'private', 'unlisted', 'public', 'schedule'}:
                    raise ValueError('Chế độ xuất bản không hợp lệ.')
                if mode == 'public' and body.get('confirmed_public') is not True:
                    raise ValueError('Xác nhận trước khi đặt video ở chế độ công khai ngay.')
                if mode == 'inherit':
                    rule = self.automation_rule(job['account']).get('schedule')
                    if rule and rule.get('visibility') == 'public' and body.get('confirmed_public') is not True:
                        raise ValueError('Thiết lập kênh đang là Công khai ngay. Hãy xác nhận trước khi áp dụng.')
                    if rule:
                        self.apply_automation(job['account'], [job], include_content=False,
                                              force_publishing=True)
                        self.store.update(job['id'], publishing_override=False)
                    else:
                        self.store.update(job['id'], publish_at='', privacy='private',
                                          publishing_override=False)
                else:
                    changes = {'publishing_override': True, 'state': 'draft', 'error': ''}
                    if mode == 'schedule':
                        when = body.get('publish_at', '')
                        if not isinstance(when, str) or not when:
                            raise ValueError('Chọn ngày và giờ công khai cho video.')
                        changes.update(publish_at=when, privacy='private')
                    else:
                        changes.update(publish_at='', privacy=mode)
                    validate_source(dict(job, **changes))
                    self.store.update(job['id'], **changes)
                return {'ok': True, 'job': self.store.job(job['id'])}
            if route == '/api/edit':
                job = self.store.job(body['id'])
                if job['state'] in BUSY or job['session'] or job['video_id']:
                    raise ValueError('Không sửa nội dung khi video đã bắt đầu upload. Xem video trên YouTube Studio.')
                updates = {k: v for k, v in body.get('changes', {}).items() if k in EDITABLE}
                candidate = dict(job, **updates)
                validate_source(candidate)
                if 'privacy' in updates or 'publish_at' in updates:
                    updates['publishing_override'] = True
                self.store.update(job['id'], **updates, state='draft', error='')
                return {'ok': True}
            if route == '/api/start':
                return self.engine.start(body.get('ids', []))
            if route == '/api/pause':
                self.engine.pause(body.get('ids', []))
                return {'ok': True}
            if route == '/api/reset-session':
                job = self.store.job(body['id'])
                if job['state'] in BUSY or job['video_id'] or not body.get('confirmed'):
                    raise ValueError('Chỉ tạo lại phiên cho video lỗi sau khi đã kiểm tra YouTube Studio.')
                self.store.update(job['id'], session='', progress=0, uploaded=0, state='draft', error='')
                return {'ok': True}
            if route == '/api/delete-channel':
                aid = body['account']
                if not self.store.account(aid):
                    raise ValueError('Không tìm thấy kênh.')
                def delete_channel():
                    with self.mutation:
                        jobs = [j for j in self.store.jobs() if j['account'] == aid]
                        ids = [j['id'] for j in jobs]
                        with self.engine.lock:
                            self.engine.pause(ids)
                        deadline = time.monotonic() + 180
                        while any(self.store.job(jid)['state'] in BUSY for jid in ids):
                            if time.monotonic() >= deadline:
                                raise ValueError('Chưa dừng được upload sau 3 phút. Kênh vẫn được giữ nguyên; hãy thử xóa lại khi tác vụ đã tạm dừng.')
                            time.sleep(.1)
                        with self.engine.lock:
                            self.store.delete_channel(aid)
                    return {'ok': True}
                return self.background(delete_channel)
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
                success = False
                reason = ''
                try:
                    if params.get('error'):
                        reason = params.get('error', [''])[0]
                        if reason in app.google.AUTH_REASONS:
                            message = str(ApiError(401, reason))
                            app.google.mark_flow_error(params.get('state', [''])[0], reason, message)
                            raise ValueError(message)
                        raise ValueError('Bạn đã hủy hoặc chưa cấp quyền đăng nhập. Có thể quay lại và thử lại.')
                    account = app.google.finish(params.get('state', [''])[0], params.get('code', [''])[0])
                    message = 'Đã kết nối ' + account['name'] + '. Bạn có thể đóng tab này và quay lại UpVideo Studio.'
                    success = True
                except Exception as exc:
                    message = str(exc) if isinstance(exc, (ValueError, ApiError)) else 'Kết nối thất bại. Hãy thử lại.'
                    reason = exc.reason if isinstance(exc, ApiError) else params.get('error', [''])[0]
                result = json.dumps({'type': 'upvideo-oauth-result', 'state': params.get('state', [''])[0],
                                     'success': success, 'message': message, 'reason': reason},
                                    ensure_ascii=False).replace('<', '\\u003c')
                bridge = '<script id="oauth-result" type="application/json">' + result + '</script><script src="/oauth-callback.js" defer></script>'
                page = '<!doctype html><html lang="vi"><meta charset="utf-8"><title>Kết nối Google</title><body><h2>' + html.escape(message) + '</h2><a href="' + ui_url(self.server.server_port) + '/">Về UpVideo Studio</a>' + bridge + '</body></html>'
                return self.send(200, page, 'text/html; charset=utf-8')
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8'),
                     '/oauth-callback.js': ('oauth-callback.js', 'text/javascript; charset=utf-8')}
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
                if self.path == '/api/shutdown':
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ValueError, ApiError)) else 'Không thực hiện được thao tác. Kiểm tra dữ liệu và thử lại.'
                self.send(400, {'error': message})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server
