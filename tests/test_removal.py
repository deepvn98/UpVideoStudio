import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.domain import scan_folder
from studio.removal import plan_removal, remove_jobs
from studio.server import App


class RemovalTests(unittest.TestCase):
    def test_paused_legacy_outside_current_watch_goes_to_current_archive(self):
        folder=self.root/'OldCollection'/'OldVideo';folder.mkdir(parents=True)
        (folder/'a.mp4').write_bytes(b'legacy')
        row=scan_folder(str(folder))[0][0]
        job=self.store.add(row,'a')
        self.store.update(job['id'],state='paused',session='saved')
        result=remove_jobs(self.store,[job['id']])
        self.assertEqual(Path(result['moved'][0]['destination']),self.root/'Channel-remove video'/'OldVideo')
        self.assertFalse(folder.exists())

    def test_legacy_without_watch_uses_enclosing_collection(self):
        job=self.add()
        self.store.update(job['id'],watch_root='',state='paused',session='saved')
        self.store.watch('a','')
        result=remove_jobs(self.store,[job['id']])
        self.assertEqual(Path(result['moved'][0]['destination']),self.root/'Channel-remove video'/'clip')

    def test_legacy_job_uses_known_root_of_same_account(self):
        legacy=self.add(folder='old',content=b'old')
        self.store.update(legacy['id'],watch_root='',state='paused',session='saved')
        self.store.watch('a','')
        self.add(folder='new',content=b'new')
        plans=plan_removal(self.store,[legacy['id']])[1]
        self.assertEqual(Path(plans[0]['destination']).parent,self.root/'Channel-remove video')

    def test_error_and_paused_sessions_can_be_archived(self):
        for state in ('error', 'paused'):
            job = self.add(folder=state, content=state.encode())
            self.store.update(job['id'], state=state, session='saved-upload-session')
            remove_jobs(self.store,[job['id']])
            self.assertFalse(Path(job['path']).exists())
        self.assertEqual(self.store.jobs(), [])

    def test_busy_states_require_stopping_before_archive(self):
        job = self.add()
        for state in ('queued', 'uploading', 'finishing'):
            self.store.update(job['id'], state=state, video_id='youtube-id')
            with self.assertRaises(ValueError): remove_jobs(self.store,[job['id']])
            self.assertTrue(Path(job['path']).exists())

    def test_other_states_with_video_id_can_be_archived(self):
        for state in ('draft', 'error', 'paused', 'warning', 'done'):
            job = self.add(folder=state, content=state.encode())
            self.store.update(job['id'], state=state, video_id='existing-youtube-id')
            result = remove_jobs(self.store, [job['id']])
            self.assertTrue((Path(result['moved'][0]['destination'])/'a.mp4').exists())
        self.assertEqual(self.store.jobs(), [])

    def test_missing_source_can_still_be_removed_from_queue(self):
        job = self.add()
        Path(job['path']).unlink()
        plans = plan_removal(self.store, [job['id']])[1]
        self.assertTrue(plans[0]['missing'])
        remove_jobs(self.store, [job['id']])
        self.assertEqual(self.store.jobs(), [])

    def test_archive_cannot_contain_another_watch(self):
        job = self.add()
        self.store.watch('other', str(self.root/'Channel-remove video'/'clip'))
        with self.assertRaises(ValueError):
            plan_removal(self.store, [job['id']])
        self.assertTrue(Path(job['path']).exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.linked = self.root/'Channel'; self.linked.mkdir()
        self.app = App(self.root/'data', Path(__file__).resolve().parents[1]/'web')
        self.app.watcher.close(); self.app.watcher.stop.clear()
        self.store = self.app.store
        self.store.save_account({'id':'a','channel_id':'UC_A','client_id':'fake'}, b'fake')
        self.store.watch('a', str(self.linked))

    def tearDown(self):
        self.app.close(); self.store.close(); self.temp.cleanup()

    def add(self, folder='clip', name='a.mp4', content=b'video'):
        directory = self.linked/folder; directory.mkdir(parents=True, exist_ok=True)
        path = directory/name; path.write_bytes(content)
        rows, _ = scan_folder(str(directory))
        row = next(r for r in rows if r['path']==str(path))
        return self.store.add(dict(row, watch_root=str(self.linked)), 'a')

    def test_folder_moves_with_assets_and_does_not_reappear(self):
        job = self.add()
        source = Path(job['path']).parent
        (source/'info.txt').write_text('Title: Example')
        (source/'thumb.jpg').write_bytes(b'image')
        before = self.app.watcher.snapshot(str(self.linked))
        stale_rows, _ = scan_folder(str(self.linked))
        preview = plan_removal(self.store, [job['id']])[1]
        self.assertTrue(source.exists())
        remove_jobs(self.store, [job['id']])
        dest = Path(preview[0]['destination'])
        self.assertEqual(dest.parent, self.root/'Channel-remove video')
        self.assertEqual((dest/'thumb.jpg').read_bytes(), b'image')
        self.assertTrue((dest/'info.txt').exists())
        self.app.watcher.reconcile(self.store.watches()[0], stale_rows, before)
        self.app.watcher.tick(); self.app.watcher.tick()
        self.assertEqual(self.store.jobs(), [])

    def test_destination_collision_never_overwrites(self):
        job = self.add()
        existing = self.root/'Channel-remove video'/'clip';existing.mkdir(parents=True)
        (existing/'keep').write_bytes(b'keep')
        result = remove_jobs(self.store, [job['id']])
        self.assertEqual(Path(result['moved'][0]['destination']).name, 'clip (2)')
        self.assertEqual((existing/'keep').read_bytes(), b'keep')

    def test_shared_folder_requires_all_videos(self):
        first = self.add(); second = self.add(name='b.mp4', content=b'other')
        with self.assertRaises(ValueError): remove_jobs(self.store, [first['id']])
        self.assertTrue(Path(first['path']).exists())
        remove_jobs(self.store, [first['id'],second['id']])
        self.assertEqual(self.store.jobs(), [])

    def test_failed_second_move_rolls_back_first(self):
        first = self.add(); second = self.add(folder='second', content=b'other')
        rename = Path.rename
        def fail_second(source, dest):
            if source == Path(second['path']).parent: raise PermissionError('locked')
            return rename(source, dest)
        with patch.object(Path, 'rename', fail_second), self.assertRaises(ValueError):
            remove_jobs(self.store, [first['id'],second['id']])
        self.assertTrue(Path(first['path']).exists())
        self.assertTrue(Path(second['path']).exists())
        self.assertEqual(len(self.store.jobs()), 2)

    def test_database_failure_restores_folder(self):
        job = self.add()
        with patch.object(self.store, 'remove', side_effect=RuntimeError('db')), self.assertRaises(ValueError):
            remove_jobs(self.store, [job['id']])
        self.assertTrue(Path(job['path']).exists())
        self.assertEqual(len(self.store.jobs()), 1)

    def test_root_video_and_other_watched_ancestor_are_rejected(self):
        job = self.add(folder='.')
        with self.assertRaises(ValueError): plan_removal(self.store,[job['id']])
        nested = self.add(folder='nested', content=b'nested')
        self.store.watch('other',str(self.root))
        with self.assertRaises(ValueError): plan_removal(self.store,[nested['id']])

    def test_other_channel_using_file_blocks_move(self):
        job = self.add()
        row, _ = scan_folder(str(self.linked))
        self.store.add(row[0], 'other')
        with self.assertRaises(ValueError): remove_jobs(self.store,[job['id']])
