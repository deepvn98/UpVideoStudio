import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from studio.domain import fingerprint, parse_text, scan_folder, schedule, validate
from studio.engine import Engine
from studio.google import ApiError, Google
from studio.server import App, make_server
from studio.store import Store
from studio.vault import seal, unseal


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()


def metadata(path):
    return dict(path=str(path), fingerprint=fingerprint(path), size=path.stat().st_size,
                title='Một câu chuyện', description='Mô tả', tags=['test'], thumbnail='',
                category='22', language='vi', privacy='private', publish_at='',
                made_for_kids=False, synthetic=False, playlists=[])


class DomainTests(TempCase):
    def test_directory_read_error_is_not_silently_ignored(self):
        def denied(*args, **kwargs):
            kwargs['onerror'](PermissionError('denied'))
            return iter(())
        with patch('studio.domain.os.walk', side_effect=denied), self.assertRaises(PermissionError):
            scan_folder(str(self.root))

    def test_invalid_catalog_and_playlist_values(self):
        path = self.root/'test.mp4'; path.write_bytes(b'video')
        for changes in ({'category': '999'}, {'language': 'invalid'}, {'language': []},
                        {'playlists': 'PL123'}, {'playlists': ['bad/id']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(dict(metadata(path), **changes))

    def test_hashing_can_be_cancelled(self):
        path = self.root/'test.mp4'; path.write_bytes(b'video')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            fingerprint(path, lambda: (_ for _ in ()).throw(ValueError('cancelled')))

    def test_schedule_does_not_extend_horizon_with_interval(self):
        with self.assertRaises(ValueError):
            schedule(12, '2035-01-01', '12:00', 365, 0, [])

    def test_vietnamese_headers_and_bom(self):
        data = parse_text('\ufeffTiêu đề:\nXin chào\nGiới thiệu:\nMột\n\nHai\nThẻ tag video:\na\n,b, a', 'fallback')
        self.assertEqual(data, dict(title='Xin chào', description='Một\n\nHai', tags=['a', 'b']))

    def test_header_inside_description_is_not_a_header(self):
        data = parse_text('Title: Real\nDescription:\nExample Title: quoted\nTail\nTags: one,two', 'fallback')
        self.assertEqual(data['title'], 'Real')
        self.assertEqual(data['description'], 'Example Title: quoted\nTail')

    def test_multiple_videos_each_have_own_metadata(self):
        (self.root/'a.mp4').write_bytes(b'one')
        (self.root/'b.MOV').write_bytes(b'two')
        (self.root/'a.txt').write_text('Title: First', encoding='utf8')
        (self.root/'b.txt').write_text('Title: Second', encoding='utf8')
        (self.root/'done.json').write_text('{}')
        rows, warnings = scan_folder(str(self.root))
        self.assertEqual([r['title'] for r in rows], ['First','Second'])
        self.assertEqual(warnings, [])

    def test_shared_info_is_not_assigned_to_multiple_videos(self):
        for name in ['a.mp4','b.mp4']:
            (self.root/name).write_bytes(name.encode())
        (self.root/'info.txt').write_text('Title: Shared')
        rows, warnings = scan_folder(str(self.root))
        self.assertEqual([r['title'] for r in rows], ['a','b'])
        self.assertEqual(len(warnings), 2)

    def test_recursive_and_no_audio_only(self):
        (self.root/'nested').mkdir()
        (self.root/'nested'/'a.webm').write_bytes(b'one')
        (self.root/'audio.mp3').write_bytes(b'two')
        self.assertEqual(len(scan_folder(str(self.root))[0]), 1)

    def test_docx(self):
        import zipfile
        (self.root/'a.mp4').write_bytes(b'x')
        with zipfile.ZipFile(self.root/'a.docx', 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Title: DOCX title</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(scan_folder(str(self.root))[0][0]['title'], 'DOCX title')

    def test_identity_is_content_based(self):
        (self.root/'a.mp4').write_bytes(b'same')
        (self.root/'b.mp4').write_bytes(b'same')
        self.assertEqual(fingerprint(self.root/'a.mp4'),fingerprint(self.root/'b.mp4'))

    def test_daily_schedule_and_remote_cursor(self):
        occupied=['2035-01-01T12:00:00+00:00']  # 19:00 Vietnam
        result=schedule(3,'2035-01-01','08:00,19:00',1,420,occupied)
        self.assertEqual(result,['2035-01-02T01:00:00+00:00','2035-01-02T12:00:00+00:00','2035-01-03T01:00:00+00:00'])

    def test_every_two_days_means_every_two_days(self):
        result=schedule(2,'2035-01-01','08:00',2,420,[])
        self.assertEqual(result[1],'2035-01-03T01:00:00+00:00')

    def test_invalid_slots_raise(self):
        with self.assertRaises(ValueError):schedule(1,'2035-01-01','25:99',1,420,[])
        with self.assertRaises(ValueError):schedule(1,'2035-01-01','',1,420,[])

    def test_explicit_flags_required(self):
        p=self.root/'a.mp4';p.write_bytes(b'a');row=metadata(p)
        row['made_for_kids']=None
        with self.assertRaisesRegex(ValueError,'trẻ em'):validate(row)
        row['made_for_kids']=0
        with self.assertRaises(ValueError):validate(row)

    def test_empty_video_is_rejected(self):
        p=self.root/'empty.mp4';p.touch()
        with self.assertRaisesRegex(ValueError,'trống'):validate(metadata(p))

    def test_utf8_description_and_tag_limits(self):
        p=self.root/'a.mp4';p.write_bytes(b'a');row=metadata(p)
        row['description']='ộ'*1700
        with self.assertRaisesRegex(ValueError,'5.000'):validate(row)
        row['description']='';row['tags']=['a '*250]
        with self.assertRaisesRegex(ValueError,'500'):validate(row)

    def test_scheduled_public_is_rejected(self):
        p=self.root/'a.mp4';p.write_bytes(b'a');row=metadata(p)
        row.update(privacy='public',publish_at='2035-01-01T01:00:00+00:00')
        with self.assertRaises(ValueError):validate(row)


class StoreTests(TempCase):
    def test_refresh_cannot_restore_forgotten_account(self):
        store = Store(self.root/'db.sqlite')
        try:
            store.save_account({'id':'a'}, b'old')
            store.watch('a', str(self.root))
            store.save_automation('a', content={'language':'en-US'})
            store.forget('a')
            with self.assertRaises(ValueError):
                store.renew_secret('a', b'new')
            self.assertEqual(store.accounts(), [])
            self.assertEqual(store.watches(), [])
            self.assertEqual(store.automation('a'), {})
        finally:
            store.close()

    def test_bulk_update_rolls_back_if_a_job_is_missing(self):
        path = self.root/'test.mp4'; path.write_bytes(b'video')
        store = Store(self.root/'db.sqlite')
        try:
            job = store.add(metadata(path), 'a')
            with self.assertRaises(ValueError):
                store.update_many([(job['id'], {'state': 'queued'}), ('missing', {'state': 'queued'})])
            self.assertEqual(store.job(job['id'])['state'], 'draft')
            self.assertEqual(store.jobs(state='queued'), [])
        finally:
            store.close()

    def test_duplicate_per_account_and_restart(self):
        path=self.root/'a.mp4';path.write_bytes(b'video')
        store=Store(self.root/'db.sqlite')
        job=store.add(metadata(path),'channel-a')
        self.assertIsNone(store.add(metadata(path),'channel-a'))
        self.assertIsNotNone(store.add(metadata(path),'channel-b'))
        store.update(job['id'],state='uploading',session='session-123',uploaded=2,video_id='VID')
        store.save_automation('channel-a', content={'language':'en-US'},
                              schedule={'date':'2035-01-01','slots':'08:00','interval':1,'offset':0})
        store.close()
        recovered=Store(self.root/'db.sqlite')
        self.assertEqual(recovered.job(job['id'])['state'],'paused')
        self.assertEqual(recovered.job(job['id'])['session'],'session-123')
        self.assertEqual(recovered.job(job['id'])['video_id'],'VID')
        self.assertEqual(recovered.automation('channel-a')['content']['language'],'en-US')
        self.assertEqual(recovered.automation('channel-a')['schedule']['slots'],'08:00')
        recovered.close()


class FakeGoogle:
    def __init__(self):
        self.calls=[];self.thumbnail_fail=False;self.query_complete=False;self.upload_count=0
        self.lost_final=False;self.failed_once=False;self.playlists_added=[];self.pages_seen=[]
    def raw(self, aid, method, url, data=None, headers=None):
        self.calls.append((method,url,data,headers))
        if method=='POST' and 'uploadType=resumable' in url:
            return 200,{'Location':'https://www.googleapis.com/upload/session'},b''
        if method=='PUT' and data==b'':
            if self.query_complete or self.failed_once:
                return 201,{},b'{"id":"video-id"}'
            return 308,{},b''
        if method=='PUT':
            self.upload_count+=1
            if self.lost_final and not self.failed_once:
                self.failed_once=True
                raise ApiError(0,'network')
            return 201,{},b'{"id":"video-id"}'
        if 'thumbnails' in url and self.thumbnail_fail:
            raise ApiError(403,'forbidden')
        return 200,{},b'{}'
    def api(self,*args,**kwargs):
        return {'items':[{'status':{'privacyStatus':'private'},'processingDetails':{'processingStatus':'processing'}}]}
    def ensure_playlist(self,aid,playlist,video):
        self.playlists_added.append(playlist)


class EngineTests(TempCase):
    def test_quota_pauses_only_pending_jobs_of_same_project(self):
        pending_path = self.root/'pending.mp4'; pending_path.write_bytes(b'pending')
        pending = self.store.add(metadata(pending_path), 'a')
        self.store.update(pending['id'], state='queued')
        self.store.save_account({'id':'b','channel_id':'UC_TWO','client_id':'other'}, b'fake')
        other = self.store.add(metadata(pending_path), 'b')
        self.store.update(other['id'], state='queued')
        with patch.object(self.google, 'raw', side_effect=ApiError(403,'quotaExceeded')):
            self.engine.run(self.job['id'])
        self.assertEqual(self.store.job(pending['id'])['state'], 'paused')
        self.assertEqual(self.store.job(other['id'])['state'], 'queued')

    def test_mismatched_remote_schedule_keeps_video_for_retry(self):
        self.store.update(self.job['id'], video_id='existing', publish_at='2035-01-01T12:00:00+00:00')
        with patch.object(self.google, 'api', return_value={'items': [{'status': {
            'privacyStatus': 'private', 'publishAt': '2035-01-02T12:00:00Z'}}]}):
            self.engine.run(self.job['id'])
        result = self.store.job(self.job['id'])
        self.assertEqual(result['state'], 'warning')
        self.assertEqual(result['video_id'], 'existing')
        self.assertEqual(self.google.upload_count, 0)

    def setUp(self):
        super().setUp()
        self.store=Store(self.root/'db.sqlite');self.google=FakeGoogle()
        self.engine=Engine(self.store,self.google,dispatch=False)
        self.store.save_account({'id':'a','channel_id':'UC_ONE','client_id':'client'},b'not-used')
        path=self.root/'a.mp4';path.write_bytes(b'video')
        self.job=self.store.add(metadata(path),'a')
    def tearDown(self):
        self.engine.close();self.store.close();super().tearDown()

    def test_success_persists_id_and_actual_flags(self):
        self.store.update(self.job['id'],made_for_kids=True,synthetic=False)
        self.engine.run(self.job['id'])
        result=self.store.job(self.job['id']);self.assertEqual(result['state'],'done')
        self.assertEqual(result['video_id'],'video-id')
        payload=json.loads(self.google.calls[0][2]);self.assertTrue(payload['status']['selfDeclaredMadeForKids'])
        self.assertFalse(payload['status']['containsSyntheticMedia'])

    def test_thumbnail_failure_retry_never_reuploads(self):
        p=self.root/'a.png';p.write_bytes(b'image')
        self.store.update(self.job['id'],thumbnail=str(p),playlists=['PL_A','PL_B'])
        self.google.thumbnail_fail=True;self.engine.run(self.job['id'])
        self.assertEqual(self.store.job(self.job['id'])['state'],'warning')
        self.assertEqual(self.store.job(self.job['id'])['video_id'],'video-id')
        self.google.thumbnail_fail=False;self.engine.run(self.job['id'])
        self.assertEqual(self.google.upload_count,1)
        self.assertEqual(self.google.playlists_added,['PL_A','PL_B'])
        self.assertEqual(self.store.job(self.job['id'])['state'],'done')

    def test_lost_final_response_queries_instead_of_reupload(self):
        self.google.lost_final=True
        with patch.object(self.engine,'backoff'):
            self.engine.run(self.job['id'])
        self.assertEqual(self.google.upload_count,1)
        self.assertEqual(self.store.job(self.job['id'])['video_id'],'video-id')

    def test_existing_completed_session_recovers_id(self):
        self.store.update(self.job['id'],session='https://www.googleapis.com/upload/session')
        self.google.query_complete=True;self.engine.run(self.job['id'])
        self.assertEqual(self.google.upload_count,0)
        self.assertEqual(self.store.job(self.job['id'])['state'],'done')

    def test_resume_uses_server_offset_not_local_offset(self):
        self.store.update(self.job['id'],session='https://www.googleapis.com/upload/session',uploaded=0)
        sent=[]
        def raw(aid,method,url,data=None,headers=None):
            if data==b'':return 308,{'range':'bytes=0-1'},b''
            sent.append((data,headers))
            return 201,{},b'{"id":"video-id"}'
        with patch.object(self.google,'raw',side_effect=raw):self.engine.run(self.job['id'])
        self.assertEqual(sent[0][0],b'deo')
        self.assertEqual(sent[0][1]['Content-Range'],'bytes 2-4/5')

    def test_expired_session_is_not_automatically_recreated(self):
        self.store.update(self.job['id'],session='https://www.googleapis.com/upload/session')
        with patch.object(self.google,'raw',side_effect=ApiError(404,'notFound')):
            self.engine.run(self.job['id'])
        result=self.store.job(self.job['id'])
        self.assertEqual(result['state'],'error');self.assertTrue(result['session'])
        self.assertIn('hết hạn',result['error'])

    def test_pause_preserves_state_and_no_request(self):
        self.engine.cancel.add(self.job['id']);self.engine.run(self.job['id'])
        self.assertEqual(self.store.job(self.job['id'])['state'],'paused')
        self.assertEqual(self.google.calls,[])

    def test_changed_file_is_rejected(self):
        Path(self.job['path']).write_bytes(b'changed')
        self.engine.run(self.job['id'])
        self.assertEqual(self.store.job(self.job['id'])['state'],'error')
        self.assertEqual(self.google.calls,[])

    def test_past_schedule_does_not_send_video_bytes(self):
        self.store.update(self.job['id'],session='https://www.googleapis.com/upload/session',publish_at='2020-01-01T00:00:00+00:00')
        self.engine.run(self.job['id'])
        self.assertEqual(self.google.upload_count,0)
        self.assertEqual(self.store.job(self.job['id'])['state'],'error')

    def test_start_is_atomic_when_one_video_invalid(self):
        path=self.root/'b.mp4';path.write_bytes(b'b')
        other=self.store.add(metadata(path),'a');self.store.update(other['id'],made_for_kids=None)
        with self.assertRaises(ValueError):self.engine.start([self.job['id'],other['id']])
        self.assertEqual(self.store.job(self.job['id'])['state'],'draft')

    def test_same_channel_different_clients_are_serialized(self):
        self.store.save_account({'id':'b','channel_id':'UC_ONE','client_id':'another'},b'unused')
        p=self.root/'b.mp4';p.write_bytes(b'other')
        other=self.store.add(metadata(p),'b')
        entered=threading.Event();release=threading.Event();calls=[]
        def slow(jid):
            calls.append(jid);entered.set();release.wait(3)
        with patch.object(self.engine,'run',side_effect=slow):
            self.engine.start([self.job['id'],other['id']])
            self.engine.thread.start()
            self.assertTrue(entered.wait(2))
            time.sleep(.4)
            self.assertEqual(len(calls),1)
            self.assertEqual(self.store.job(other['id'])['state'],'queued')
            self.engine.stop.set();release.set();self.engine.thread.join(2)


class GoogleTests(unittest.TestCase):
    def test_playlists_are_paginated_and_duplicate_names_preserved(self):
        google=Google(None)
        with patch.object(google,'api',side_effect=[{'items':[{'id':'A','snippet':{'title':'Same'}}],'nextPageToken':'NEXT'},
                                                     {'items':[{'id':'B','snippet':{'title':'Same'}}]}]) as call:
            data=google.playlists('account')
            self.assertEqual([p['id'] for p in data],['A','B'])
            self.assertEqual(call.call_args.args[2]['pageToken'],'NEXT')

    def test_oauth_wrong_state_fails_before_network(self):
        with self.assertRaises(ValueError):Google(None).finish('wrong','code')

    def test_oauth_uses_pkce_and_fixed_google_endpoint(self):
        google=Google(None)
        url=google.begin({'installed':{'client_id':'demo.apps.googleusercontent.com','client_secret':'secret',
                                      'auth_uri':'https://evil.invalid'}},'http://127.0.0.1:8765/oauth/callback')
        self.assertTrue(url.startswith('https://accounts.google.com/'))
        self.assertIn('code_challenge_method=S256',url)
        self.assertNotIn('client_secret',url)

    def test_playlist_retry_checks_existing_membership(self):
        google=Google(None)
        with patch.object(google,'api',return_value={'items':[{'id':'exists'}]}) as call:
            google.ensure_playlist('a','PL','VID')
            self.assertEqual(call.call_count,1)

    @unittest.skipUnless(os.name=='nt','DPAPI requires Windows')
    def test_dpapi_round_trip(self):
        token={'refresh_token':'sensitive-value'}
        ciphertext=seal(token)
        self.assertNotIn(b'sensitive-value',ciphertext)
        self.assertEqual(unseal(ciphertext),token)


class ServerTests(TempCase):
    def test_reimport_migrates_paused_legacy_job_without_reset(self):
        self.app.store.save_account({'id':'a','channel_id':'UC_A','name':'QA','client_id':'test'},b'cipher')
        folder=self.root/'linked'/'clip';folder.mkdir(parents=True)
        path=folder/'a.mp4';path.write_bytes(b'video')
        job=self.app.store.add(metadata(path),'a')
        before=self.app.store.update(job['id'],state='paused',session='saved',uploaded=3,title='Manual')
        result=self.app.import_folder({'account':'a','path':str(folder.parent)})
        after=self.app.store.job(job['id'])
        self.assertEqual(after.pop('import_root'),str(folder.parent))
        self.assertEqual(after,before)
        self.assertEqual(result['added'],0)
        self.assertEqual(len(self.app.action('/api/remove-preview',{'ids':[job['id']]},self.base)['moved']),1)

    def mixed_jobs(self):
        self.app.store.save_account({'id':'a','channel_id':'UC_A','name':'QA','client_id':'test'},b'cipher')
        jobs=[]
        for state in ('draft','paused','error','done'):
            path=self.root/(state+'.mp4');path.write_bytes(state.encode())
            job=self.app.store.add(metadata(path),'a')
            jobs.append(self.app.store.update(job['id'],state=state))
        return jobs

    def test_bulk_updates_every_unstarted_draft_of_selected_channel(self):
        jobs=self.mixed_jobs()
        extra_path=self.root/'extra-draft.mp4';extra_path.write_bytes(b'extra-draft')
        extra=self.app.store.add(metadata(extra_path),'a')
        result=self.app.action('/api/bulk',{'ids':[jobs[0]['id']],
            'account':'a','playlists':['PL_A','PL_B'],'made_for_kids':True,'synthetic':True},self.base)
        self.assertEqual(result['count'],2)
        self.assertEqual(self.app.store.job(jobs[0]['id'])['playlists'],['PL_A','PL_B'])
        self.assertEqual(self.app.store.job(extra['id'])['playlists'],['PL_A','PL_B'])
        self.assertTrue(self.app.store.job(extra['id'])['made_for_kids'])
        self.assertEqual(self.app.store.automation('a')['content']['playlists'],['PL_A','PL_B'])
        self.assertTrue(self.app.store.automation('a')['content']['made_for_kids'])
        for job in jobs[1:]:self.assertEqual(self.app.store.job(job['id']),job)

    def test_bulk_playlists_require_explicit_matching_channel(self):
        jobs=self.mixed_jobs()
        for account in (None,'other'):
            with self.assertRaises(ValueError):
                self.app.action('/api/bulk',{'ids':[jobs[0]['id']], 'account':account,
                    'playlists':['PL_A'],'made_for_kids':True,'synthetic':True},self.base)
        self.assertEqual(self.app.store.job(jobs[0]['id']),jobs[0])

    def test_mixed_schedule_keeps_paused_schedule_occupied(self):
        jobs=self.mixed_jobs()
        extra_path=self.root/'extra-schedule.mp4';extra_path.write_bytes(b'extra-schedule')
        extra=self.app.store.add(metadata(extra_path),'a')
        self.app.store.update(jobs[1]['id'],publish_at='2035-01-05T12:00:00+00:00')
        task=self.app.action('/api/schedule',{'ids':[jobs[0]['id']], 'account':'a',
            'date':'2035-01-01','slots':'12:00','interval':1,'offset':0,'sync':False},self.base)
        for _ in range(100):
            with self.app.task_lock: result=self.app.tasks[task['task']]
            if result['state']!='running':break
            time.sleep(.01)
        self.assertEqual(result['state'],'done',result)
        self.assertEqual(result['result']['count'],2)
        self.assertEqual(self.app.store.job(jobs[0]['id'])['publish_at'],'2035-01-06T12:00:00+00:00')
        self.assertEqual(self.app.store.job(extra['id'])['publish_at'],'2035-01-07T12:00:00+00:00')
        self.assertEqual(self.app.store.automation('a')['schedule'],
                         {'date':'2035-01-01','slots':'12:00','interval':1,'offset':0})
        self.assertEqual(self.app.store.job(jobs[1]['id'])['publish_at'],'2035-01-05T12:00:00+00:00')
        for job in jobs[2:]:self.assertEqual(self.app.store.job(job['id']),job)

    def setUp(self):
        super().setUp()
        self.app=App(self.root,Path(__file__).resolve().parents[1]/'web')
        self.server=make_server(self.app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.app.close();self.app.store.close();super().tearDown()
    def fetch(self,path,token=True,body=None,origin=None):
        headers={'X-Studio-Token':self.app.token} if token else {}
        if origin:headers['Origin']=origin
        return urllib.request.urlopen(urllib.request.Request(self.base+path,headers=headers,
                    data=json.dumps(body).encode() if body is not None else None),timeout=5)

    def test_api_requires_token_and_rejects_foreign_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.fetch('/api/state',token=False)
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError):self.fetch('/api/state',origin='https://evil.invalid')
        with self.fetch('/api/state') as r:self.assertEqual(json.load(r)['jobs'],[])

    def test_page_and_assets(self):
        with self.fetch('/',token=False) as r:
            self.assertIn('UpVideo Studio',r.read().decode())
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        for asset in ['/app.js','/style.css']:
            with self.fetch(asset,token=False) as r:self.assertEqual(r.status,200)

    def test_connect_post_from_browser_origin(self):
        payload={'config':{'installed':{'client_id':'test.apps.googleusercontent.com','client_secret':'fake-for-test'}}}
        with self.fetch('/api/connect',body=payload,origin=self.base) as r:
            result=json.load(r)
        self.assertTrue(result['url'].startswith('https://accounts.google.com/'))
        self.assertNotIn('fake-for-test',result['url'])

    def test_ip_host_and_origin_with_loopback_oauth_callback(self):
        import urllib.parse
        host=f'127.0.0.1:{self.server.server_port}'
        body={'config':{'installed':{'client_id':'qa.apps.googleusercontent.com','client_secret':'fake'}}}
        req=urllib.request.Request(self.base+'/api/connect',data=json.dumps(body).encode(),
            headers={'Host':host,'Origin':'http://'+host,'X-Studio-Token':self.app.token})
        with urllib.request.urlopen(req,timeout=5) as response:
            auth=json.load(response)['url']
        params=urllib.parse.parse_qs(urllib.parse.urlparse(auth).query)
        self.assertEqual(params['redirect_uri'],[self.base+'/oauth/callback'])
        req.add_header('Origin','http://untrusted.localhost:'+str(self.server.server_port))
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req,timeout=5)
        self.assertEqual(error.exception.code,403)

    def test_existing_instance_url_is_verified(self):
        from run import existing_url
        (self.root/'instance.json').write_text(json.dumps({'port':self.server.server_port}))
        self.assertEqual(existing_url(self.root),self.base)
        (self.root/'instance.json').write_text(json.dumps({'port':'https://wrong.invalid'}))
        self.assertIsNone(existing_url(self.root))

    def test_import_bulk_edit_and_secret_redaction(self):
        self.app.store.save_account({'id':'a','channel_id':'UC_A','name':'QA','client_id':'test'},b'cipher')
        (self.root/'a.mp4').write_bytes(b'v1');(self.root/'b.mp4').write_bytes(b'v2')
        result=self.app.import_folder({'account':'a','path':str(self.root)})
        self.assertEqual(result['added'],2)
        jobs=self.app.store.jobs();ids=[j['id'] for j in jobs]
        self.app.action('/api/bulk',{'ids':ids,'made_for_kids':False,'synthetic':True},self.base)
        self.app.action('/api/edit',{'id':ids[0],'changes':{'title':'Edited','playlists':['PL_A','PL_B']}},self.base)
        self.assertEqual(self.app.store.job(ids[0])['playlists'],['PL_A','PL_B'])
        self.app.store.update(ids[0],session='secret-session')
        with self.fetch('/api/state') as r:
            snapshot=json.load(r)
            self.assertNotIn('session',snapshot['jobs'][0]);self.assertTrue(snapshot['jobs'][0]['has_session'])
        self.assertEqual(self.app.import_folder({'account':'a','path':str(self.root)})['added'],0)

    def test_duplicate_survives_forget_and_new_client(self):
        self.app.store.save_account({'id':'a','channel_id':'UC_A','name':'QA','client_id':'test'},b'cipher')
        (self.root/'a.mp4').write_bytes(b'video')
        self.app.import_folder({'account':'a','path':str(self.root)})
        self.app.store.forget('a')
        self.app.store.save_account({'id':'b','channel_id':'UC_A','name':'QA','client_id':'other'},b'cipher')
        self.assertEqual(self.app.import_folder({'account':'b','path':str(self.root)})['added'],0)

    def test_remove_only_unstarted_jobs_and_preserve_file(self):
        folder=self.root/'linked'/'clip';folder.mkdir(parents=True)
        p=folder/'a.mp4';p.write_bytes(b'video')
        job=self.app.store.add(dict(metadata(p),import_root=str(folder.parent)),'a')
        self.app.action('/api/remove',{'ids':[job['id']]},self.base)
        self.assertFalse(p.exists());self.assertEqual(self.app.store.jobs(),[])
        moved=self.root/'linked-remove video'/'clip'/'a.mp4'
        self.assertEqual(moved.read_bytes(),b'video')
        job=self.app.store.add(metadata(moved),'a');self.app.store.update(job['id'],state='queued',session='saved')
        with self.assertRaises(ValueError):self.app.action('/api/remove',{'ids':[job['id']]},self.base)


if __name__=='__main__':unittest.main()
