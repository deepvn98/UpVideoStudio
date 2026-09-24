import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, account TEXT NOT NULL,
                fingerprint TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY, data BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS watches(account TEXT PRIMARY KEY, path TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'single', accounts_json TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS automation(account TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL, account TEXT);
            CREATE TABLE IF NOT EXISTS uploaded_fingerprints(
                channel_id TEXT NOT NULL, fingerprint TEXT NOT NULL, video_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', uploaded_at TEXT NOT NULL,
                PRIMARY KEY(channel_id, fingerprint, video_id));
            CREATE INDEX IF NOT EXISTS uploaded_fingerprints_time ON uploaded_fingerprints(uploaded_at);
        ''')
        if 'account' not in {row['name'] for row in self.db.execute('PRAGMA table_info(events)')}:
            self.db.execute('ALTER TABLE events ADD COLUMN account TEXT')
        watch_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(watches)')}
        if 'mode' not in watch_columns:
            self.db.execute("ALTER TABLE watches ADD COLUMN mode TEXT NOT NULL DEFAULT 'single'")
        if 'accounts_json' not in watch_columns:
            self.db.execute("ALTER TABLE watches ADD COLUMN accounts_json TEXT NOT NULL DEFAULT '[]'")
        unique_job_indexes = [row['name'] for row in self.db.execute('PRAGMA index_list(jobs)')
                              if row['unique'] and [col['name'] for col in self.db.execute(
                                  f"PRAGMA index_info('{row['name']}')")] == ['account', 'fingerprint']]
        if unique_job_indexes:
            with self.lock, self.db:
                self.db.execute('DROP TABLE IF EXISTS jobs_new')
                self.db.execute('CREATE TABLE jobs_new(id TEXT PRIMARY KEY, account TEXT NOT NULL, '
                                'fingerprint TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)')
                self.db.execute('INSERT INTO jobs_new SELECT id,account,fingerprint,state,data FROM jobs')
                self.db.execute('DROP TABLE jobs')
                self.db.execute('ALTER TABLE jobs_new RENAME TO jobs')
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state)')
        self._normalize_queue_positions()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self.lock, self.db:
            self.db.execute('DELETE FROM uploaded_fingerprints WHERE uploaded_at < ?', (cutoff,))
            # Recover recent uploads from older app versions when their success event is still present.
            for job in self.jobs():
                if not job.get('video_id') or not job.get('fingerprint'):
                    continue
                account = self.account(job['account'])
                channel_id = job.get('channel_id') or (account or {}).get('channel_id')
                if not channel_id:
                    continue
                event = self.db.execute('''SELECT time FROM events WHERE account=? AND message=?
                    ORDER BY id DESC LIMIT 1''', (job['account'], 'Đã tải lên: ' + job.get('title', ''))).fetchone()
                if event and event['time'] >= cutoff:
                    self.db.execute('''INSERT OR IGNORE INTO uploaded_fingerprints
                        (channel_id,fingerprint,video_id,title,uploaded_at) VALUES(?,?,?,?,?)''',
                        (channel_id, job['fingerprint'], job['video_id'], job.get('title', ''), event['time']))
        # A restart never silently resumes publishing.
        for job in self.jobs():
            if job['state'] in ('uploading', 'queued', 'finishing'):
                self.update(job['id'], state='paused', error='Ứng dụng đã khởi động lại. Bấm Tiếp tục khi sẵn sàng.')

    def jobs(self, state=None):
        with self.lock:
            rows = (self.db.execute('SELECT rowid,data FROM jobs WHERE state=? ORDER BY rowid', (state,))
                    if state else self.db.execute('SELECT rowid,data FROM jobs ORDER BY rowid'))
            jobs = [(r[0], json.loads(r[1])) for r in rows]
            jobs.sort(key=lambda item: (item[1].get('queue_position', item[0]), item[0]))
            return [job for _, job in jobs]

    def _normalize_queue_positions(self):
        """Give legacy and current jobs a stable, contiguous order inside each account."""
        with self.lock, self.db:
            rows = [(row[0], json.loads(row[1])) for row in
                    self.db.execute('SELECT rowid,data FROM jobs ORDER BY rowid')]
            accounts = {}
            for rowid, job in rows:
                accounts.setdefault(job['account'], []).append((rowid, job))
            for items in accounts.values():
                items.sort(key=lambda item: (item[1].get('queue_position', item[0]), item[0]))
                for position, (_, job) in enumerate(items):
                    if job.get('queue_position') != position:
                        job['queue_position'] = position
                        self.db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(job), job['id']))

    def job(self, jid):
        with self.lock:
            row = self.db.execute('SELECT data FROM jobs WHERE id=?', (jid,)).fetchone()
            if not row:
                raise ValueError('Không tìm thấy công việc.')
            return json.loads(row[0])

    def add(self, data, account, position=None):
        with self.lock, self.db:
            channel = self.account(account)
            existing = [job for job in self.jobs() if job['account'] == account]
            if position is None:
                queue_position = max((job.get('queue_position', -1) for job in existing), default=-1) + 1
            else:
                queue_position = max(0, min(int(position), len(existing)))
                ordered = sorted(existing, key=lambda job: job.get('queue_position', 0))
                for index, item in enumerate(ordered):
                    new_position = index + (1 if index >= queue_position else 0)
                    if item.get('queue_position') != new_position:
                        item['queue_position'] = new_position
                        self.db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(item), item['id']))
            job = dict(data, id=uuid.uuid4().hex, account=account, state='draft', progress=0,
                       session='', video_id='', uploaded=0, error='', completed_playlists=[], thumbnail_done=False,
                       publishing_override=False, queue_position=queue_position,
                       channel_id=channel['channel_id'] if channel else account)
            try:
                self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',
                                (job['id'], account, job['fingerprint'], job['state'], json.dumps(job)))
            except sqlite3.IntegrityError:
                return None
        return job

    def update(self, jid, **changes):
        return self.update_many([(jid, changes)])[0]

    def update_many(self, updates):
        with self.lock, self.db:
            jobs = []
            for jid, changes in updates:
                job = self.job(jid)
                job.update(changes)
                self.db.execute('UPDATE jobs SET fingerprint=?, state=?, data=? WHERE id=?', (job['fingerprint'], job['state'], json.dumps(job), jid))
                if job.get('video_id') and job.get('fingerprint'):
                    account = self.account(job['account'])
                    channel_id = job.get('channel_id') or (account or {}).get('channel_id')
                    if channel_id:
                        self.db.execute('''INSERT OR IGNORE INTO uploaded_fingerprints
                            (channel_id,fingerprint,video_id,title,uploaded_at) VALUES(?,?,?,?,?)''',
                            (channel_id, job['fingerprint'], job['video_id'], job.get('title', ''),
                             datetime.now(timezone.utc).isoformat()))
                jobs.append(job)
            return jobs

    def uploaded_fingerprint_matches(self, channel_id, fingerprint):
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self.lock, self.db:
            self.db.execute('DELETE FROM uploaded_fingerprints WHERE uploaded_at < ?', (cutoff_iso,))
            rows = self.db.execute('''SELECT video_id,title,uploaded_at FROM uploaded_fingerprints
                WHERE channel_id=? AND fingerprint=? AND uploaded_at>=? ORDER BY uploaded_at DESC''',
                (channel_id, fingerprint, cutoff_iso)).fetchall()
            return [dict(row) for row in rows]

    def remove(self, ids):
        with self.lock, self.db:
            self.db.executemany('DELETE FROM jobs WHERE id=?', [(jid,) for jid in ids])
            self._normalize_queue_positions()

    def reorder(self, jid, target_id, after=False):
        """Move one queue item relative to another item from the same account."""
        with self.lock, self.db:
            job, target = self.job(jid), self.job(target_id)
            if job['account'] != target['account']:
                raise ValueError('Không thể chuyển video sang vùng của kênh khác.')
            ordered = [item for item in self.jobs() if item['account'] == job['account']]
            ordered = [item for item in ordered if item['id'] != jid]
            index = next((index for index, item in enumerate(ordered) if item['id'] == target_id), None)
            if index is None:
                raise ValueError('Không tìm thấy vị trí thả video.')
            ordered.insert(index + (1 if after else 0), job)
            for position, item in enumerate(ordered):
                if item.get('queue_position') != position:
                    item['queue_position'] = position
                    self.db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(item), item['id']))
            return ordered

    def accounts(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM accounts ORDER BY rowid')]

    def watches(self):
        with self.lock:
            result = []
            for row in self.db.execute('SELECT account,path,mode,accounts_json FROM watches'):
                item = dict(row)
                try:
                    item['accounts'] = json.loads(item.pop('accounts_json'))
                except (TypeError, ValueError):
                    item['accounts'] = []
                    item.pop('accounts_json', None)
                result.append(item)
            return result

    def watch(self, account, path, mode='single', accounts=None):
        with self.lock, self.db:
            if path:
                self.db.execute('INSERT OR REPLACE INTO watches(account,path,mode,accounts_json) VALUES(?,?,?,?)',
                                (account, path, mode, json.dumps(accounts or [])))
            else:
                self._remove_multi_links_for(account)
                self.db.execute('DELETE FROM watches WHERE account=?', (account,))

    def _remove_multi_links_for(self, account):
        rows = list(self.db.execute("SELECT account,accounts_json FROM watches WHERE mode='multi'"))
        affected = set()
        for row in rows:
            try:
                members = json.loads(row['accounts_json'])
            except (TypeError, ValueError):
                members = []
            if account in members:
                affected.update(members)
        if affected:
            self.db.executemany('DELETE FROM watches WHERE account=?', [(aid,) for aid in affected])

    def automation(self, account):
        with self.lock:
            row = self.db.execute('SELECT data FROM automation WHERE account=?', (account,)).fetchone()
            return json.loads(row[0]) if row else {}

    def save_automation(self, account, **sections):
        with self.lock, self.db:
            data = self.automation(account)
            data.update(sections)
            self.db.execute('INSERT OR REPLACE INTO automation VALUES(?,?)',
                            (account, json.dumps(data)))
            return data

    def account(self, aid):
        with self.lock:
            row = self.db.execute('SELECT data FROM accounts WHERE id=?', (aid,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_account(self, account, encrypted):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO accounts VALUES(?,?)', (account['id'], json.dumps(account)))
            self.db.execute('INSERT OR REPLACE INTO secrets VALUES(?,?)', (account['id'], encrypted))

    def reconnect_account(self, old_aid, account, encrypted):
        new_aid = account['id']
        with self.lock, self.db:
            old_account = self.account(old_aid)
            if not old_account:
                raise ValueError('Không tìm thấy kênh cũ để chuyển dữ liệu.')
            if old_account['channel_id'] != account['channel_id']:
                raise ValueError('Kênh YouTube không khớp; dữ liệu cũ chưa bị thay đổi.')
            if old_aid != new_aid:
                if self.account(new_aid):
                    raise ValueError('Kênh này đã được kết nối bằng OAuth client mới. Không thể tự gộp hai hàng đợi.')
                jobs = list(self.db.execute('SELECT id,state,data FROM jobs WHERE account=?', (old_aid,)))
                if any(row['state'] in ('queued', 'uploading', 'finishing') for row in jobs):
                    raise ValueError('Hãy tạm dừng upload của kênh trước khi chuyển sang OAuth client mới.')
                for row in jobs:
                    data = json.loads(row['data'])
                    data['account'] = new_aid
                    self.db.execute('UPDATE jobs SET account=?,data=? WHERE id=?',
                                    (new_aid, json.dumps(data), row['id']))
                self.db.execute('UPDATE watches SET account=? WHERE account=?', (new_aid, old_aid))
                automation = self.db.execute('SELECT data FROM automation WHERE account=?', (old_aid,)).fetchone()
                if automation:
                    self.db.execute('INSERT OR REPLACE INTO automation(account,data) VALUES(?,?)',
                                    (new_aid, automation['data']))
                    self.db.execute('DELETE FROM automation WHERE account=?', (old_aid,))
                self.db.execute('UPDATE events SET account=? WHERE account=?', (new_aid, old_aid))
                for row in self.db.execute("SELECT account,accounts_json FROM watches WHERE mode='multi'").fetchall():
                    members = json.loads(row['accounts_json'])
                    if old_aid in members:
                        members = [new_aid if item == old_aid else item for item in members]
                        self.db.execute('UPDATE watches SET accounts_json=? WHERE account=?',
                                        (json.dumps(members), row['account']))
                self.db.execute('DELETE FROM accounts WHERE id=?', (old_aid,))
                self.db.execute('DELETE FROM secrets WHERE id=?', (old_aid,))
                self._normalize_queue_positions()
            self.db.execute('INSERT OR REPLACE INTO accounts VALUES(?,?)', (new_aid, json.dumps(account)))
            self.db.execute('INSERT OR REPLACE INTO secrets VALUES(?,?)', (new_aid, encrypted))

    def set_auth_status(self, aid, status, error='', code=''):
        with self.lock, self.db:
            account = self.account(aid)
            if not account:
                return
            message = error if status == 'reauth_required' else ''
            error_code = code if status == 'reauth_required' else ''
            if (account.get('auth_status', 'connected') == status
                    and account.get('auth_error', '') == message
                    and account.get('auth_error_code', '') == error_code):
                return
            account['auth_status'] = status
            account['auth_error'] = message
            account['auth_error_code'] = error_code
            self.db.execute('UPDATE accounts SET data=? WHERE id=?', (json.dumps(account), aid))

    def secret(self, aid):
        with self.lock:
            row = self.db.execute('SELECT data FROM secrets WHERE id=?', (aid,)).fetchone()
            if not row:
                raise ValueError('Kênh chưa được kết nối.')
            return row[0]

    def renew_secret(self, aid, encrypted):
        with self.lock, self.db:
            if not self.account(aid):
                raise ValueError('Kênh đã ngắt kết nối. Hãy kết nối lại.')
            self.db.execute('UPDATE secrets SET data=? WHERE id=?', (encrypted, aid))

    def forget(self, aid):
        with self.lock, self.db:
            self._remove_multi_links_for(aid)
            self.db.execute('DELETE FROM accounts WHERE id=?', (aid,))
            self.db.execute('DELETE FROM secrets WHERE id=?', (aid,))
            self.db.execute('DELETE FROM watches WHERE account=?', (aid,))
            self.db.execute('DELETE FROM automation WHERE account=?', (aid,))

    def delete_channel(self, aid):
        """Remove all locally managed channel data; never touch source files or YouTube."""
        with self.lock, self.db:
            account = self.account(aid)
            if not account:
                raise ValueError('Không tìm thấy kênh.')
            self._remove_multi_links_for(aid)
            jobs = [job for job in self.jobs() if job['account'] == aid]
            # Remove legacy unscoped history entries that can be attributed to this channel.
            self.db.execute('DELETE FROM events WHERE account=?', (aid,))
            self.db.execute('DELETE FROM events WHERE message=?', ('Đã kết nối kênh ' + account.get('name', ''),))
            for job in jobs:
                title = job.get('title', '')
                prefix = title + ': '
                self.db.execute('DELETE FROM events WHERE message=? OR substr(message,1,?)=?',
                                ('Đã tải lên: ' + title, len(prefix), prefix))
            self.db.execute('DELETE FROM jobs WHERE account=?', (aid,))
            self.db.execute('DELETE FROM uploaded_fingerprints WHERE channel_id=?', (account['channel_id'],))
            self.db.execute('DELETE FROM accounts WHERE id=?', (aid,))
            self.db.execute('DELETE FROM secrets WHERE id=?', (aid,))
            self.db.execute('DELETE FROM watches WHERE account=?', (aid,))
            self.db.execute('DELETE FROM automation WHERE account=?', (aid,))

    def event(self, message, level='info', account=None):
        with self.lock, self.db:
            if account and not self.account(account):
                return
            self.db.execute('INSERT INTO events(time,level,message,account) VALUES(?,?,?,?)',
                            (datetime.now(timezone.utc).isoformat(), level, message, account))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)')

    def events(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT id,time,level,message FROM events ORDER BY id DESC LIMIT 100')]

    def close(self):
        with self.lock:
            self.db.close()
