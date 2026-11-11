"""Shared image regressions, run against each platform's engine using IMAGE_ENGINE."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock
import urllib.error
import zlib

SOURCE = Path(os.environ.get('IMAGE_ENGINE', str(Path(__file__).resolve().parents[1]/'src/sync_engine.py'))).resolve()
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location('image_test_engine', SOURCE)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def png(color):
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+bytes(color)))+chunk(b'IEND',b'')

RED, BLUE, GREEN = png((255,0,0)), png((0,0,255)), png((0,255,0))
IMAGE = '<ac:image ac:height="250" ac:alt="示意图"><ri:attachment ri:filename="设计 图.png" /></ac:image>'

class Client:
    page = '123'
    def __init__(self, storage):
        self.body = {'id':'123','type':'page','title':'Test','version':{'number':1},'body':{'storage':{'value':storage}}}
        self.items, self.files = {}, {}
        self.uploads = self.writes = self.downloads = 0
        self.on_download = None
        self.add('设计 图.png',RED)
    def add(self, name, data):
        self.items[name] = {'id': str(len(self.items)+100), 'title': name,
            'version': {'number': 1}, 'extensions': {'fileSize': len(data)},
            '_links': {'download': 'https://wiki.example.com/fake/'+name}}
        self.files[name] = data
        return copy.deepcopy(self.items[name])
    def get(self): return copy.deepcopy(self.body)
    def put(self,page,storage):
        self.writes+=1;self.body['version']['number']+=1;self.body['body']['storage']['value']=storage
        return self.get()
    def list_attachments(self,page): return copy.deepcopy(list(self.items.values()))
    def download_attachment(self,item):
        self.downloads+=1
        data=self.files[item['title']]
        if self.on_download:
            callback,self.on_download=self.on_download,None
            callback()
        return data
    def upload_image(self,name,data,media_type):
        self.uploads+=1
        return self.add(name,data)
    def update(self,data):
        self.files['设计 图.png']=data
        self.items['设计 图.png']['version']['number']+=1
        self.items['设计 图.png']['extensions']['fileSize']=len(data)

class ImageSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.old_root=s.ROOT;s.ROOT=self.root
        self.file=self.root/'需求 文档.md';self.file.write_text('# Local',encoding='utf-8')
        self.client=Client('<p>图片：'+IMAGE+'</p>')
        self.sync=None
    def tearDown(self):
        if self.sync:self.sync.close()
        s.ROOT=self.old_root;self.temp.cleanup()
    def create(self,direction='remote'):
        self.sync=s.Sync(dict(local_file=str(self.file),page_id='123',initial_sync=direction,settle_seconds=0),self.client,lambda *args:None)
        return self.sync
    def srcs(self):
        md=self.file.read_text(encoding='utf-8')
        root=s.xml_root(s.md_to_storage(md,raw_images=True))
        return [self.sync.images.file_for(n.get('src')) for n in root.iter('img')]
    def test_pull_styled_image_with_storage_newline(self):
        body = '<blockquote><p><strong>'+IMAGE+'<br />\r\n</strong></p></blockquote>'
        self.client.body['body']['storage']['value'] = body
        sync = self.create()
        sync.tick()
        markdown = self.file.read_text(encoding='utf-8')
        self.assertIn('![', markdown)
        self.assertEqual(self.srcs()[0].read_bytes(), RED)
        self.assertEqual(s.semantic(body), s.semantic(s.md_to_storage(markdown, sync.images, upload=False)))
        self.assertEqual(self.client.writes, 0)
        self.assertEqual(self.client.uploads, 0)
        self.assertTrue(list((sync.data / 'local-backups').glob('*.md')))
    def test_download_visible_markdown_and_preserve_layout(self):
        sync=self.create();sync.tick()
        self.assertIn('![示意图](',self.file.read_text(encoding='utf-8'))
        self.assertEqual(self.srcs()[0].read_bytes(),RED)
        self.assertEqual(s.semantic(self.client.body['body']['storage']['value']),s.semantic(s.md_to_storage(sync.read(),sync.images,upload=False)))
        sync.tick();self.assertEqual(self.client.uploads,0);self.assertEqual(self.client.writes,0)
    def test_local_upload_spaces_unicode_and_no_repeat(self):
        image=self.root/'本地 图片.png';image.write_bytes(BLUE)
        self.file.write_text('![design](本地%20图片.png)',encoding='utf-8')
        sync=self.create('local');sync.tick();sync.tick()
        self.assertEqual(self.client.uploads,1);self.assertEqual(self.client.writes,1)
        self.assertIn('mdsync-',self.client.body['body']['storage']['value'])
    def test_image_only_local_edit_is_uploaded(self):
        sync=self.create();sync.tick()
        self.srcs()[0].write_bytes(BLUE)
        sync.tick();sync.tick()
        self.assertEqual(self.client.uploads,1);self.assertEqual(self.client.writes,1)
        self.assertIn(BLUE,self.client.files.values())
    def test_attachment_only_update_downloads_without_body_version_change(self):
        sync=self.create();sync.tick();old=self.srcs()[0]
        self.client.update(BLUE);sync.tick();sync.tick()
        self.assertEqual(self.srcs()[0].read_bytes(),BLUE)
        self.assertEqual(old.read_bytes(),RED)
        self.assertEqual(self.client.writes,0)
    def test_concurrent_image_edits_conflict(self):
        sync=self.create();sync.tick();image=self.srcs()[0];image.write_bytes(BLUE)
        self.client.update(GREEN)
        with self.assertRaisesRegex(RuntimeError,'两边都已修改'):sync.tick()
        self.assertEqual(image.read_bytes(),BLUE);self.assertEqual(self.client.uploads,0)
        self.assertTrue(list((self.root/'conflicts').glob('*/remote.storage.xml')))
    def test_missing_local_image_never_changes_remote(self):
        self.file.write_text('![missing](missing.png)',encoding='utf-8')
        sync=self.create('local')
        with self.assertRaisesRegex(ValueError,'找不到本地图片'):sync.tick()
        self.assertEqual(self.client.uploads,0);self.assertEqual(self.client.writes,0)
    def test_identical_files_uploaded_once(self):
        for name in ('a.png','b.png'):(self.root/name).write_bytes(BLUE)
        self.file.write_text('![one](a.png) ![two](b.png)',encoding='utf-8')
        sync=self.create('local');sync.tick()
        self.assertEqual(self.client.uploads,1)
    def test_complex_table_and_repeated_images_are_visible(self):
        self.client.body['body']['storage']['value']='<p>'+IMAGE+'</p><table><tr><td colspan="2">'+IMAGE+'</td></tr></table><p>'+IMAGE.replace('250','100')+'</p>'
        sync=self.create();sync.tick()
        self.assertEqual(len(self.srcs()),3)
        self.assertTrue(all(p.read_bytes()==RED for p in self.srcs()))
        self.assertIn('<img',sync.read())
    def test_download_failure_does_not_overwrite_document(self):
        self.client.download_attachment=Mock(side_effect=OSError('offline'))
        sync=self.create()
        with self.assertRaises(OSError):sync.tick()
        self.assertEqual(sync.read(),'# Local');self.assertFalse(sync.state_file.exists())
    def test_concurrent_remote_attachment_update_cancels_pull(self):
        self.client.on_download=lambda:self.client.update(BLUE)
        sync=self.create()
        with self.assertRaisesRegex(RuntimeError,'网页图片仍在修改'):sync.tick()
        self.assertEqual(sync.read(),'# Local')
    def test_upgrade_materializes_retained_tokens(self):
        sync=self.create();sync.tick()
        legacy=s.storage_to_md(self.client.body['body']['storage']['value'])
        self.file.write_text(legacy,encoding='utf-8')
        sync.state['local_hash']=s.digest(legacy)
        for key in ('image_sync','local_images','remote_images'):sync.state.pop(key,None)
        sync.tick()
        self.assertIn('![示意图](',sync.read());self.assertEqual(self.client.writes,0)
    def test_upload_checksum_mismatch_stops_body_update(self):
        (self.root/'a.png').write_bytes(BLUE)
        self.file.write_text('![](a.png)',encoding='utf-8')
        self.client.download_attachment=Mock(return_value=RED)
        sync=self.create('local')
        with self.assertRaisesRegex(RuntimeError,'内容校验失败'):sync.tick()
        self.assertEqual(self.client.writes,0)
    def test_text_edit_after_pull_does_not_reupload(self):
        sync=self.create();sync.tick()
        self.file.write_text(sync.read()+'\nExtra paragraph.\n',encoding='utf-8')
        sync.tick();sync.tick()
        self.assertEqual(self.client.writes,1);self.assertEqual(self.client.uploads,0)
    def test_uploaded_image_replaced_before_failed_push_retry_is_rejected(self):
        (self.root/'a.png').write_bytes(BLUE)
        self.file.write_text('![](a.png)',encoding='utf-8')
        original_put=self.client.put
        self.client.put=Mock(side_effect=RuntimeError('temporary body error'))
        sync=self.create('local')
        with self.assertRaises(RuntimeError):sync.tick()
        name=next(name for name in self.client.items if name.startswith('mdsync-'))
        self.client.files[name]=RED;self.client.items[name]['version']['number']+=1
        self.client.put=original_put
        with self.assertRaisesRegex(RuntimeError,'网页图片与本地图片已不一致'):sync.tick()
        self.assertEqual(self.client.writes,0)
    def test_restart_restores_image_baseline_without_uploading_again(self):
        (self.root/'a.png').write_bytes(BLUE)
        self.file.write_text('![](a.png)',encoding='utf-8')
        sync=self.create('local');sync.tick();sync.close();self.sync=None
        self.create('local').tick()
        self.assertEqual(self.client.uploads,1);self.assertEqual(self.client.writes,1)

class AttachmentTransportTests(unittest.TestCase):
    def client(self):return s.Client(dict(base_url='https://wiki.example.com',page_id='123'),'user','secret')
    def response(self,value):return io.BytesIO(json.dumps(value).encode())
    def test_cross_origin_download_rejected_before_auth_is_sent(self):
        client=self.client();client.opener=Mock()
        with self.assertRaises(ValueError):client._image_http('https://example.com/image.png',binary=True)
        client.opener.open.assert_not_called()
    def test_401_and_403_have_distinct_meanings(self):
        for code in (401,403):
            client=self.client();client.opener.open=Mock(side_effect=urllib.error.HTTPError('https://wiki.example.com',code,'error',{},None))
            expected=s.AuthenticationExpired if code==401 else RuntimeError
            with self.assertRaises(expected) as context:client.list_attachments('123')
            if code==403:self.assertNotIsInstance(context.exception,s.AuthenticationExpired)
    def test_multipart_upload_has_csrf_header_and_image_bytes(self):
        client=self.client();client.opener.open=Mock(return_value=self.response({'results':[{'title':'safe.png'}]}))
        client.upload_image('safe.png',RED,'image/png')
        request=client.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(),'POST')
        self.assertEqual(request.get_header('X-atlassian-token'),'nocheck')
        self.assertIn(RED,request.data);self.assertIn(b'filename="safe.png"',request.data)
    def test_attachment_pagination(self):
        client=self.client()
        client.opener.open=Mock(side_effect=[self.response({'results':[{'title':'a'}],'_links':{'next':'/rest/api/content/123/child/attachment?start=1'}}),self.response({'results':[{'title':'b'}]})])
        self.assertEqual([x['title'] for x in client.list_attachments('123')],['a','b'])
    def test_non_image_download_is_rejected(self):
        client=self.client();client.opener.open=Mock(return_value=io.BytesIO(b'<html>Login</html>'))
        with self.assertRaises(ValueError):client.download_attachment({'_links':{'download':'/download/x'}})

if __name__=='__main__':unittest.main(verbosity=2)
