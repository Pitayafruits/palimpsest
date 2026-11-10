import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import sync_engine as engine
from review import merge_document, apply_decisions, ReviewService, ConflictPending, ReviewChanged


class MergeTests(unittest.TestCase):
    def text(self,blocks):
        return apply_decisions(dict(blocks=blocks,decisions={}))

    def test_independent_edits_combine(self):
        blocks=merge_document('# 标题\n\n甲\n乙\n丙\n','# 标题\n\n甲改\n乙\n丙\n','# 标题\n\n甲\n乙\n丙改\n')
        self.assertEqual(self.text(blocks),'# 标题\n\n甲改\n乙\n丙改\n')

    def test_adjacent_edits_are_independent(self):
        self.assertEqual(self.text(merge_document('a\nb\n','A\nb\n','a\nB\n')),'A\nB\n')

    def test_identical_edits_need_no_decision(self):
        self.assertEqual(self.text(merge_document('a\n','A\n','A\n')),'A\n')

    def test_competing_edits_require_decision(self):
        blocks=merge_document('最多3名\n','最多5名\n','最多8名\n')
        self.assertEqual(len([b for b in blocks if b['kind']=='conflict']),1)
        with self.assertRaises(ValueError): self.text(blocks)

    def test_delete_versus_change(self):
        self.assertTrue(any(b['kind']=='conflict' for b in merge_document('a\nb\n','a\n','a\nB\n')))

    def test_insert_at_same_position_conflicts(self):
        self.assertTrue(any(b['kind']=='conflict' for b in merge_document('a\n','x\na\n','y\na\n')))

    def test_legacy_without_base_never_guesses(self):
        self.assertTrue(any(b['kind']=='conflict' for b in merge_document(None,'local\n','remote\n')))

    def test_br_noise_and_identical_image_bytes(self):
        blocks=merge_document(None,'| a<br /><br />b ![](one.png) |\n',
            '| a<br />b ![](two.png) |\n',local_images={'one.png':'abc'},remote_images={'two.png':'abc'})
        self.assertTrue(all(b['kind']=='same' for b in blocks))

    def test_image_byte_change_is_a_real_conflict(self):
        blocks=merge_document('![](base.png)\n','![](left.png)\n','![](right.png)\n',
            {'base.png':'1'},{'left.png':'2'},{'right.png':'3'})
        self.assertEqual(blocks[0]['kind'],'conflict')

    def test_empty_remote_is_explicit_deletion(self):
        blocks=merge_document('a\n','A\n','')
        self.assertEqual(blocks[0]['remote'],'')
        self.assertEqual(blocks[0]['kind'],'conflict')

    def test_different_table_cells_merge_automatically(self):
        self.assertEqual(self.text(merge_document('| a | b |\n','| A | b |\n','| a | B |\n')),'| A | B |\n')

    def test_fenced_code_whitespace_is_not_filtered(self):
        blocks=merge_document(None,'```html\n<br /><br />\n```\n','```html\n<br />\n```\n')
        self.assertTrue(any(x['kind']=='conflict' for x in blocks))


class Client:
    def __init__(self):
        self.page='123'
        self.body={'id':'123','title':'需求','type':'page','version':{'number':1},
            'body':{'storage':{'value':'<h1>规则</h1><p>最多3名</p>'}}}
        self.writes=0
    def get(self):return copy.deepcopy(self.body)
    def put(self,page,storage):
        if page['version']['number']!=self.body['version']['number']:raise RuntimeError('version conflict')
        self.writes+=1;self.body['version']['number']+=1
        self.body['body']['storage']['value']=storage
        return self.get()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.old_root=engine.ROOT;engine.ROOT=self.root
        self.file=self.root/'需求.md';self.file.write_text('# 规则\n\n最多3名\n',encoding='utf-8')
        self.client=Client()
        self.config=dict(local_file=str(self.file),page_id='123',base_url='https://wiki.example.com',
                         initial_sync='local',settle_seconds=0,_data_dir=str(self.root))
        self.sync=engine.Sync(self.config,self.client,lambda *a:None);self.sync.tick()
        self.file.write_text('# 规则\n\n最多5名\n',encoding='utf-8')
        self.client.body['body']['storage']['value']='<h1>规则</h1><p>最多8名</p>'
        self.client.body['version']['number']+=1
        self.service=ReviewService(self.sync)

    def tearDown(self):
        self.sync.close();engine.ROOT=self.old_root;self.temp.cleanup()

    def session(self):
        with self.assertRaises(ConflictPending): self.sync.tick()
        return self.service.load()

    def choose(self,session,side='local'):
        for block in session['blocks']:
            if block['kind']=='conflict':self.service.save_decision(session,block['id'],side)

    def test_capture_has_readable_images_ready_snapshots_and_no_overwrite(self):
        session=self.session()
        self.assertEqual(self.client.writes,1)
        self.assertIn('最多5名',self.file.read_text(encoding='utf-8'))
        self.assertTrue(Path(session['backup'],'remote.storage.xml').exists())
        self.assertTrue(session['has_baseline'])

    def test_choices_survive_reopen_and_submit_updates_both(self):
        session=self.session();self.choose(session,'remote')
        reopened=self.service.prepare()
        self.assertEqual(reopened['decisions'],session['decisions'])
        result=self.service.submit(reopened)
        self.assertEqual(result['phase'],'resolved')
        self.assertIn('最多8名',self.file.read_text(encoding='utf-8'))
        self.sync.tick();self.assertEqual(self.client.writes,2)

    def test_new_local_edit_is_not_overwritten(self):
        session=self.session();self.choose(session)
        self.file.write_text('# 规则\n\n最多10名\n',encoding='utf-8')
        with self.assertRaises(ReviewChanged):self.service.submit(session)
        self.assertIn('最多10名',self.file.read_text(encoding='utf-8'))
        self.assertEqual(self.client.writes,1)
        self.assertEqual(len(self.service.load()['decisions']),0)

    def test_new_remote_edit_is_not_overwritten(self):
        session=self.session();self.choose(session)
        self.client.body['version']['number']+=1
        self.client.body['body']['storage']['value']='<p>another edit</p>'
        with self.assertRaises(ReviewChanged):self.service.submit(session)
        self.assertEqual(self.client.writes,1)

    def test_ambiguous_network_success_recovers_without_duplicate_write(self):
        session=self.session();self.choose(session)
        put=self.client.put
        def ambiguous(page,storage):
            put(page,storage);raise OSError('connection lost after server saved')
        self.client.put=ambiguous
        with self.assertRaises(OSError):self.service.submit(session)
        self.client.put=put
        result=self.service.submit(session)
        self.assertEqual(result['phase'],'resolved');self.assertEqual(self.client.writes,2)
        self.assertIn('最多5名',self.file.read_text(encoding='utf-8'))

    def test_remote_saved_then_local_edit_gets_a_new_review(self):
        session=self.session();self.choose(session)
        put=self.client.put
        def concurrent(page,storage):
            result=put(page,storage)
            self.file.write_text('# 规则\n\n另一项新要求\n',encoding='utf-8')
            return result
        self.client.put=concurrent
        with self.assertRaisesRegex(ReviewChanged,'本地未被覆盖'):self.service.submit(session)
        self.assertIn('另一项新要求',self.file.read_text(encoding='utf-8'))
        self.assertEqual(self.service.load()['phase'],'pending')

    def test_unresolved_content_cannot_be_submitted(self):
        session=self.session()
        with self.assertRaises(ValueError):self.service.submit(session)
        self.assertEqual(self.client.writes,1)

    def test_stale_dialog_cannot_replace_newer_review(self):
        session=self.session();self.choose(session)
        self.service.capture(self.sync.read(),self.client.get())
        with self.assertRaises(ReviewChanged):self.service.submit(session)

    def test_invalid_custom_content_never_touches_remote(self):
        session=self.session()
        for block in session['blocks']:
            if block['kind']=='conflict':self.service.save_decision(session,block['id'],'custom','<script>bad</script>')
        with self.assertRaises(ValueError):self.service.submit(session)
        self.assertEqual(self.client.writes,1)

    def test_legacy_baseline_is_supported(self):
        self.sync.state.pop('baseline_md');self.sync.state.pop('baseline_images')
        session=self.session();self.assertFalse(session['has_baseline'])
        self.choose(session);self.service.submit(session)
        self.assertIn('最多5名',self.file.read_text(encoding='utf-8'))

    def test_missing_baseline_does_not_reset_first_sync_direction(self):
        self.sync.state.pop('baseline_md')
        self.session();self.sync.config['initial_sync']='remote'
        with self.assertRaises(ConflictPending):self.sync.tick()
        self.assertEqual(self.client.writes,1)

    def test_new_remote_version_after_put_is_not_treated_as_our_write(self):
        session=self.session();self.choose(session)
        put=self.client.put
        def concurrent(page,storage):
            result=put(page,storage);self.client.body['version']['number']+=1
            self.client.body['body']['storage']['value']+='<p>another author</p>'
            return result
        self.client.put=concurrent
        with self.assertRaises(ReviewChanged):self.service.submit(session)
        self.assertNotIn('another author',self.file.read_text(encoding='utf-8'))


import test_images as image_fixture


class ImageReviewTests(unittest.TestCase):
    setUp=image_fixture.ImageSyncTests.setUp
    tearDown=image_fixture.ImageSyncTests.tearDown
    create=image_fixture.ImageSyncTests.create
    srcs=image_fixture.ImageSyncTests.srcs

    def review(self):
        sync=self.create();sync.tick();original=self.srcs()[0]
        original.write_bytes(image_fixture.BLUE);self.client.update(image_fixture.GREEN)
        with self.assertRaises(ConflictPending):sync.tick()
        service=ReviewService(sync);return service,service.load(),original

    def select(self,service,session,source):
        for block in session['blocks']:
            if block['kind']=='conflict':service.save_decision(session,block['id'],source)
        service.submit(session)

    def test_keep_local_image_uploads_immutable_snapshot(self):
        service,session,original=self.review()
        self.assertIn('.assets/review/',session['local'])
        self.select(service,session,'local')
        self.assertEqual(self.srcs()[0].read_bytes(),image_fixture.BLUE)
        self.assertIn(image_fixture.BLUE,self.client.files.values())
        self.assertEqual(original.read_bytes(),image_fixture.BLUE)

    def test_keep_remote_image_does_not_overwrite_original_asset(self):
        service,session,original=self.review()
        self.select(service,session,'remote')
        self.assertEqual(self.srcs()[0].read_bytes(),image_fixture.GREEN)
        self.assertEqual(original.read_bytes(),image_fixture.BLUE)

    def test_changed_asset_during_review_blocks_submission(self):
        service,session,original=self.review()
        for block in session['blocks']:
            if block['kind']=='conflict':service.save_decision(session,block['id'],'local')
        original.write_bytes(image_fixture.RED)
        with self.assertRaises(ReviewChanged):service.submit(session)
        self.assertEqual(self.client.writes,0)
