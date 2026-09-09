import tempfile
import unittest
from pathlib import Path
from studio.server import App


class WatcherTests(unittest.TestCase):
    def test_replacement_and_old_content_copy_in_same_poll(self):
        original = self.folder/'z.mp4'; original.write_bytes(b'old')
        self.sync()
        (self.folder/'a.mp4').write_bytes(b'old')
        original.write_bytes(b'new-content')
        self.sync()
        self.assertEqual(len(self.app.store.jobs()), 2)
        self.assertEqual(len({j['fingerprint'] for j in self.app.store.jobs()}), 2)

    def test_unwatch_releases_hash_cache(self):
        (self.folder/'a.mp4').write_bytes(b'video'); self.sync()
        self.assertTrue(self.app.watcher.hashes)
        self.app.store.watch('a', '')
        self.app.watcher.tick()
        self.assertEqual(self.app.watcher.hashes, {})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root/'videos'; self.folder.mkdir()
        self.app = App(self.root/'data', Path(__file__).resolve().parents[1]/'web')
        self.app.watcher.close()  # Drive polling deterministically.
        self.app.watcher.stop.clear()
        self.app.store.save_account({'id': 'a', 'channel_id': 'UC_A', 'client_id': 'client'}, b'fake')
        self.app.store.watch('a', str(self.folder))

    def tearDown(self):
        self.app.close(); self.app.store.close(); self.temp.cleanup()

    def sync(self):
        self.app.watcher.tick(); self.app.watcher.tick()

    def test_add_edit_delete_and_preserve_user_settings(self):
        video = self.folder/'a.mp4'; video.write_bytes(b'one')
        meta = self.folder/'a.txt'; meta.write_text('Title: First')
        self.app.watcher.tick()
        self.assertEqual(self.app.store.jobs(), [])
        self.app.watcher.tick()
        job = self.app.store.jobs()[0]
        self.app.store.update(job['id'], language='en', title='Manual', publish_at='2035-01-01T12:00:00Z')
        (self.folder/'b.mp4').write_bytes(b'two')
        self.sync()
        self.assertEqual(self.app.store.job(job['id'])['title'], 'Manual')
        meta.write_text('Title: Changed')
        self.sync()
        changed = self.app.store.job(job['id'])
        self.assertEqual(changed['title'], 'Changed')
        self.assertEqual(changed['language'], 'en')
        self.assertEqual(changed['publish_at'], '2035-01-01T12:00:00Z')
        video.unlink(); self.sync()
        self.assertEqual(len(self.app.store.jobs()), 1)

    def test_busy_job_and_offline_folder_are_preserved(self):
        video = self.folder/'a.mp4'; video.write_bytes(b'one'); self.sync()
        job = self.app.store.jobs()[0]
        self.app.store.update(job['id'], state='paused', session='saved')
        video.unlink(); self.sync()
        self.assertEqual(self.app.store.job(job['id'])['session'], 'saved')
        self.folder.rmdir(); self.sync()
        self.assertEqual(len(self.app.store.jobs()), 1)

    def test_rename_keeps_job_id_and_duplicate_is_skipped(self):
        video = self.folder/'a.mp4'; video.write_bytes(b'one'); self.sync()
        job = self.app.store.jobs()[0]
        video.rename(self.folder/'renamed.mp4'); self.sync()
        self.assertEqual(self.app.store.jobs()[0]['id'], job['id'])
        self.assertEqual(self.app.store.jobs()[0]['path'], str(self.folder/'renamed.mp4'))
        (self.folder/'duplicate.mp4').write_bytes(b'one'); self.sync()
        self.assertEqual(len(self.app.store.jobs()), 1)

    def test_replaced_file_updates_fingerprint_index(self):
        video = self.folder/'a.mp4'; video.write_bytes(b'one'); self.sync()
        job = self.app.store.jobs()[0]
        video.write_bytes(b'replacement'); self.sync()
        changed = self.app.store.job(job['id'])
        self.assertNotEqual(job['fingerprint'], changed['fingerprint'])
        self.assertIsNone(self.app.store.add(changed, 'a'))

    def test_subscription_survives_restart_and_can_stop(self):
        self.app.store.watch('a', str(self.folder))
        self.app.close(); self.app.store.close()
        self.app = App(self.root/'data', Path(__file__).resolve().parents[1]/'web')
        self.app.watcher.close(); self.app.watcher.stop.clear()
        self.assertEqual(self.app.store.watches()[0]['path'], str(self.folder))
        self.app.action('/api/unwatch', {'account': 'a'}, '')
        (self.folder/'a.mp4').write_bytes(b'one'); self.sync()
        self.assertEqual(self.app.store.jobs(), [])

    def test_completed_video_is_not_imported_again(self):
        (self.folder/'a.mp4').write_bytes(b'one'); self.sync()
        job = self.app.store.jobs()[0]
        self.app.store.update(job['id'], state='done', video_id='youtube-id')
        (self.folder/'copy.mp4').write_bytes(b'one'); self.sync()
        self.assertEqual(len(self.app.store.jobs()), 1)

    def test_linked_import_tracks_deletion_before_first_poll(self):
        video = self.folder/'a.mp4'; video.write_bytes(b'one')
        self.app.import_folder({'account': 'a', 'path': str(self.folder), 'watch': True})
        self.assertEqual(len(self.app.store.jobs()), 1)
        video.unlink(); self.sync()
        self.assertEqual(self.app.store.jobs(), [])

    def test_new_files_inherit_saved_content_and_next_schedule_slots(self):
        self.app.store.save_automation('a',
            content={'language': 'en-US', 'category': '22', 'made_for_kids': False,
                     'synthetic': False, 'playlists': ['PL_TRAVEL']},
            schedule={'date': '2035-01-01', 'slots': '08:00', 'interval': 1, 'offset': 0})
        (self.folder/'a.mp4').write_bytes(b'one')
        (self.folder/'b.mp4').write_bytes(b'two')
        self.sync()
        jobs = self.app.store.jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual([j['publish_at'] for j in jobs],
                         ['2035-01-01T08:00:00+00:00', '2035-01-02T08:00:00+00:00'])
        for job in jobs:
            self.assertEqual(job['language'], 'en-US')
            self.assertFalse(job['made_for_kids'])
            self.assertFalse(job['synthetic'])
            self.assertEqual(job['playlists'], ['PL_TRAVEL'])
            self.assertEqual(job['privacy'], 'private')

    def test_legacy_unconfigured_draft_inherits_rules_from_configured_videos(self):
        for name, content in [('a.mp4', b'one'), ('b.mp4', b'two'), ('c.mp4', b'three')]:
            (self.folder/name).write_bytes(content)
        self.sync()
        first, second, third = self.app.store.jobs()
        shared = {'language': 'en-US', 'category': '22', 'made_for_kids': False,
                  'synthetic': False, 'playlists': ['PL_TRAVEL'], 'privacy': 'private'}
        self.app.store.update(first['id'], **shared, publish_at='2035-01-01T08:00:00+00:00')
        self.app.store.update(second['id'], **shared, publish_at='2035-01-03T08:00:00+00:00')
        # Simulate a draft that the previous application version already discovered.
        self.app.watcher.applied.clear()
        self.app.watcher.tick()
        migrated = self.app.store.job(third['id'])
        self.assertEqual(migrated['language'], 'en-US')
        self.assertFalse(migrated['made_for_kids'])
        self.assertEqual(migrated['playlists'], ['PL_TRAVEL'])
        self.assertEqual(migrated['publish_at'], '2035-01-05T08:00:00+00:00')
