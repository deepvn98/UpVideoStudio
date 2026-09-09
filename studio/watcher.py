"""Persistent folder subscriptions; reconcile only stable snapshots and unstarted drafts."""
import threading
from collections import Counter

from .domain import scan_folder, folder_files, VIDEO_EXTS

SOURCE_FIELDS = ('title', 'description', 'tags', 'thumbnail', 'size', 'fingerprint')


class FolderWatcher:
    def __init__(self, app):
        self.app = app
        self.stop = threading.Event()
        self.previous = {}
        self.applied = {}
        self.hashes = {}
        self.errors = {}
        self.thread = threading.Thread(target=self.run, daemon=True, name='folder-watch')
        self.thread.start()

    def checkpoint(self):
        if self.stop.is_set():
            raise ValueError('Đã dừng theo dõi thư mục.')

    def snapshot(self, root):
        result = []
        for path in folder_files(root, self.checkpoint):
            if path.suffix.lower() in VIDEO_EXTS | {'.txt', '.docx', '.jpg', '.jpeg', '.png'}:
                stat = path.stat()
                result.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return tuple(sorted(result))

    def tick(self):
        watches = self.app.store.watches()
        live = {(w['account'], w['path']) for w in watches}
        for cache in (self.previous, self.applied, self.errors):
            for key in set(cache) - live:
                del cache[key]
        valid = {entry for snap in self.previous.values() for entry in snap}
        self.hashes = {k: v for k, v in self.hashes.items() if k in valid}
        for watch in watches:
            key = (watch['account'], watch['path'])
            if not self.app.store.account(key[0]):
                continue
            try:
                snapshot = self.snapshot(key[1])
                if self.previous.get(key) != snapshot:
                    self.previous[key] = snapshot
                    continue  # Two matching polls, five seconds apart.
                if self.applied.get(key) == snapshot:
                    continue
                rows, _ = scan_folder(key[1], self.hashes, self.checkpoint)
                if self.snapshot(key[1]) != snapshot:
                    continue
                if not self.reconcile(watch, rows, snapshot):
                    continue
                self.applied[key] = snapshot
                self.errors.pop(key, None)
                valid = {entry for snap in self.previous.values() for entry in snap}
                self.hashes = {k: v for k, v in self.hashes.items() if k in valid}
            except (OSError, ValueError) as exc:
                message = str(exc)
                if self.errors.get(key) != message and not self.stop.is_set():
                    self.app.store.event('Theo dõi thư mục: ' + message, 'error')
                self.errors[key] = message
                self.previous.pop(key, None)

    def reconcile(self, watch, rows, expected=None):
        store = self.app.store
        with self.app.mutation, self.app.engine.lock, store.lock:
            if watch not in store.watches():
                return False
            if expected is not None and self.snapshot(watch['path']) != expected:
                self.previous.pop((watch['account'], watch['path']), None)
                return False
            account = store.account(watch['account'])
            if not account:
                return False
            jobs = store.jobs()
            related = {a['id'] for a in store.accounts() if a['channel_id'] == account['channel_id']}
            known = Counter(j['fingerprint'] for j in jobs if j['account'] in related or j.get('channel_id') == account['channel_id'])
            by_path = {j['path']: j for j in jobs if j['account'] == watch['account']}
            present = {r['path'] for r in rows}
            changed = 0
            for row in rows:
                job = by_path.get(row['path'])
                if not job:
                    job = next((j for j in jobs if j['account'] == watch['account']
                                and j.get('watch_root') == watch['path'] and j['path'] not in present
                                and j['fingerprint'] == row['fingerprint'] and j['state'] == 'draft'
                                and not j['session'] and not j['video_id']), None)
                    if job:
                        store.update(job['id'], path=row['path'])
                        job['path'] = row['path']
                        by_path[row['path']] = job
                if job:
                    if job['state'] != 'draft' or job['session'] or job['video_id']:
                        continue
                    old = job.get('watch_source', {})
                    updates = {k: row[k] for k in SOURCE_FIELDS if old.get(k) != row[k]}
                    if row['fingerprint'] != job['fingerprint'] and known[row['fingerprint']] > 0:
                        store.remove([job['id']])
                        known[job['fingerprint']] -= 1
                        changed += 1
                        continue
                    if updates or job.get('watch_root') != watch['path']:
                        store.update(job['id'], **updates, watch_root=watch['path'],
                                     watch_source={k: row[k] for k in SOURCE_FIELDS})
                        known[job['fingerprint']] -= 1
                        known[row['fingerprint']] += 1
                        changed += 1
                elif known[row['fingerprint']] == 0:
                    added = store.add(dict(row, watch_root=watch['path'], watch_source={k: row[k] for k in SOURCE_FIELDS}), watch['account'])
                    if added:
                        by_path[row['path']] = added
                        known[row['fingerprint']] += 1
                        changed += 1
            removed = [j['id'] for j in jobs if j['account'] == watch['account']
                       and j.get('watch_root') == watch['path'] and j['path'] not in present
                       and j['state'] == 'draft' and not j['session'] and not j['video_id']]
            store.remove(removed)
            # Recheck files skipped before another draft released its old fingerprint.
            for row in rows:
                if row['path'] not in by_path and known[row['fingerprint']] == 0:
                    added = store.add(dict(row, watch_root=watch['path'], watch_source={k: row[k] for k in SOURCE_FIELDS}), watch['account'])
                    if added:
                        known[row['fingerprint']] += 1
                        changed += 1
            automation_jobs = [j for j in store.jobs() if j['account'] == watch['account']
                               and j['state'] == 'draft' and not j['session'] and not j['video_id']]
            self.app.apply_automation(watch['account'], automation_jobs)
            if changed or removed:
                store.event(f'Tự đồng bộ thư mục: {changed} bản nháp thêm/cập nhật, {len(removed)} bản nháp bỏ khỏi hàng đợi.')
            return True

    def run(self):
        while not self.stop.wait(5):
            try:
                self.tick()
            except Exception:
                # A transient failure must not permanently stop subsequent polling.
                if not self.stop.is_set():
                    self.app.store.event('Chưa đồng bộ được thư mục. Sẽ tự thử lại.', 'error')

    def close(self):
        self.stop.set()
        self.thread.join()
