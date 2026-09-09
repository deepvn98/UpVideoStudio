import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
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
                fingerprint TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL,
                UNIQUE(account, fingerprint));
            CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY, data BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS watches(account TEXT PRIMARY KEY, path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS automation(account TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
        ''')
        # A restart never silently resumes publishing.
        for job in self.jobs():
            if job['state'] in ('uploading', 'queued', 'finishing'):
                self.update(job['id'], state='paused', error='Ứng dụng đã khởi động lại. Bấm Tiếp tục khi sẵn sàng.')

    def jobs(self, state=None):
        with self.lock:
            rows = self.db.execute('SELECT data FROM jobs WHERE state=? ORDER BY rowid', (state,)) if state else self.db.execute('SELECT data FROM jobs ORDER BY rowid')
            return [json.loads(r[0]) for r in rows]

    def job(self, jid):
        with self.lock:
            row = self.db.execute('SELECT data FROM jobs WHERE id=?', (jid,)).fetchone()
            if not row:
                raise ValueError('Không tìm thấy công việc.')
            return json.loads(row[0])

    def add(self, data, account):
        channel = self.account(account)
        job = dict(data, id=uuid.uuid4().hex, account=account, state='draft', progress=0,
                   session='', video_id='', uploaded=0, error='', completed_playlists=[], thumbnail_done=False,
                   channel_id=channel['channel_id'] if channel else account)
        with self.lock, self.db:
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
                jobs.append(job)
            return jobs

    def remove(self, ids):
        with self.lock, self.db:
            self.db.executemany('DELETE FROM jobs WHERE id=?', [(jid,) for jid in ids])

    def accounts(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM accounts ORDER BY rowid')]

    def watches(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT account,path FROM watches')]

    def watch(self, account, path):
        with self.lock, self.db:
            if path:
                self.db.execute('INSERT OR REPLACE INTO watches VALUES(?,?)', (account, path))
            else:
                self.db.execute('DELETE FROM watches WHERE account=?', (account,))

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
            self.db.execute('DELETE FROM accounts WHERE id=?', (aid,))
            self.db.execute('DELETE FROM secrets WHERE id=?', (aid,))
            self.db.execute('DELETE FROM watches WHERE account=?', (aid,))
            self.db.execute('DELETE FROM automation WHERE account=?', (aid,))

    def event(self, message, level='info'):
        with self.lock, self.db:
            self.db.execute('INSERT INTO events(time,level,message) VALUES(?,?,?)',
                            (datetime.now(timezone.utc).isoformat(), level, message))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)')

    def events(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]

    def close(self):
        with self.lock:
            self.db.close()
