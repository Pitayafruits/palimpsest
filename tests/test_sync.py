import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src/sync_engine.py'
spec = importlib.util.spec_from_file_location('sync', SOURCE)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
REAL_ROOT = s.ROOT


class Client:
    def __init__(self, body='<p>remote original</p>'):
        self.page = {'id': '12345', 'type': 'page', 'title': 'test',
                     'version': {'number': 1}, 'body': {'storage': {'value': body}}}
        self.writes = 0

    def get(self):
        return copy.deepcopy(self.page)

    def put(self, page, body):
        assert page['version']['number'] == self.page['version']['number']
        self.page['version']['number'] += 1
        self.page['body']['storage']['value'] = body
        self.writes += 1
        return self.get()


class ConversionTests(unittest.TestCase):
    def test_literal_numbering_and_split_spans(self):
        for body in ('<p>1. First<br />2. Second<br />3. Third</p>',
                     '<p><span>1</span><span>. First</span><br /><span>2</span><span>) Second</span></p>',
                     '<blockquote><p>1. First</p><p>2. Second</p></blockquote>',
                     '<ol><li><p>First</p></li><li><p>Second</p></li></ol>'):
            with self.subTest(body=body):
                self.assertEqual(s.semantic(body), s.semantic(s.md_to_storage(s.storage_to_md(body))))

    def test_markdown_formats(self):
        md = '# 标题\n\n| 项目 | 值 |\n| --- | --- |\n| 榜单 | **100** |\n\n- 第一个\n- 第二个\n\n~~移除~~\n\n```python\nprint(1)\n```\n'
        root = s.xml_root(s.md_to_storage(md))
        self.assertEqual(len(root.xpath('.//table')), 1)
        self.assertEqual(len(root.xpath('.//li')), 2)
        self.assertIn('100', ''.join(root.itertext()))
        self.assertEqual(len(root.xpath('.//s | .//del')), 1)
        self.assertEqual(len(root.xpath('.//pre')), 1)

    def test_macros_images_table_and_literal_roundtrip(self):
        body = '<h2>标题</h2><p>literal *star* &amp; &lt;abc&gt;</p><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td><strong>bold</strong><br />next</td></tr></table><p><ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">SF-2281</ac:parameter></ac:structured-macro><ac:image><ri:attachment ri:filename="x.png" /></ac:image></p>'
        md = s.storage_to_md(body)
        self.assertEqual(md.count('confluence-storage:'), 2)
        self.assertEqual(s.semantic(body), s.semantic(s.md_to_storage(md)))

    def test_merged_table_is_preserved(self):
        body = '<table><tr><td colspan="2">a</td></tr></table>'
        self.assertIn('confluence-storage:', s.storage_to_md(body))
        self.assertEqual(s.semantic(body), s.semantic(s.md_to_storage(s.storage_to_md(body))))

    def test_rejects_local_image_and_empty(self):
        for md in ['', '![img](./local.png)', '<script>alert(1)</script>']:
            with self.assertRaises(ValueError):
                s.md_to_storage(md)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = s.ROOT
        s.ROOT = Path(self.tmp.name)
        self.file = s.ROOT / 'document.md'
        self.file.write_text('## Local\n\nFirst version.\n', encoding='utf-8')
        self.client = Client()
        self.engine = s.Sync({'local_file': str(self.file), 'page_id': '12345',
                              'initial_sync': 'local', 'settle_seconds': 0}, self.client, lambda *args: None)

    def tearDown(self):
        self.engine.close()
        s.ROOT = self.old_root
        self.tmp.cleanup()

    def test_both_directions_and_no_feedback_loop(self):
        self.engine.tick()
        self.assertEqual(self.client.writes, 1)
        self.engine.tick()
        self.assertEqual(self.client.writes, 1)
        self.file.write_text('## Local\n\nSecond version.\n', encoding='utf-8')
        self.engine.tick()
        self.assertEqual(self.client.writes, 2)
        self.client.page['body']['storage']['value'] += '<p>Edited online.</p>'
        self.client.page['version']['number'] += 1
        self.engine.tick()
        self.assertIn('Edited online.', self.file.read_text())
        self.assertTrue(list((s.ROOT / 'local-backups').glob('*.md')))
        self.engine.tick()
        self.assertEqual(self.client.writes, 2)

    def test_conflict_does_not_overwrite(self):
        self.engine.tick()
        self.file.write_text('local change', encoding='utf-8')
        self.client.page['body']['storage']['value'] = '<p>remote change</p>'
        self.client.page['version']['number'] += 1
        with self.assertRaisesRegex(RuntimeError, '两边都已修改'):
            self.engine.tick()
        self.assertEqual(self.file.read_text(), 'local change')
        self.assertEqual(self.client.writes, 1)
        self.assertEqual(len(list((s.ROOT / 'conflicts').glob('*/remote.md'))), 1)

    def test_remote_change_during_push_aborts(self):
        self.engine.tick()
        md = 'Changed again'
        self.file.write_text(md, encoding='utf-8')
        old = self.client.get()
        self.client.page['version']['number'] += 1
        with self.assertRaisesRegex(RuntimeError, '网页在转换期间'):
            self.engine.push(md, old)
        self.assertEqual(self.client.writes, 1)

    def test_first_sync_from_remote_preserves_local_backup(self):
        self.engine.config['initial_sync'] = 'remote'
        original = self.file.read_text()
        self.engine.tick()
        self.assertEqual(self.client.writes, 0)
        self.assertIn('remote original', self.file.read_text())
        backup = next((s.ROOT / 'local-backups').glob('*.md'))
        self.assertEqual(backup.read_text(), original)

    def test_profiles_have_independent_state_and_resume(self):
        self.engine.close()
        config1 = {'local_file': str(self.file), 'page_id': '123456', 'initial_sync': 'local',
                   'settle_seconds': 0, '_data_dir': str(s.ROOT / 'profile-one')}
        self.engine = s.Sync(config1, self.client, lambda *args: None)
        self.engine.tick()
        first_hash = self.engine.state['local_hash']
        self.engine.close()
        second = s.ROOT / 'another.md'
        second.write_text('# Second document', encoding='utf-8')
        config2 = dict(config1, local_file=str(second), page_id='654321', _data_dir=str(s.ROOT / 'profile-two'))
        self.engine = s.Sync(config2, Client(), lambda *args: None)
        self.assertIsNone(self.engine.state)
        self.engine.tick()
        self.assertNotEqual(self.engine.state['local_hash'], first_hash)
        self.engine.close()
        self.engine = s.Sync(config1, self.client, lambda *args: None)
        self.assertEqual(self.engine.state['local_hash'], first_hash)
        self.engine.tick()
        self.assertEqual(self.client.writes, 1)

    def test_page_id_is_configurable_and_invalid_values_rejected(self):
        client = s.Client({'base_url': 'https://wiki.example.com', 'page_id': '123456789'}, 'user', 'password')
        self.assertEqual(client.page, '123456789')
        for page in ['../other', '123?x=2', '0', 'abc']:
            with self.assertRaises(ValueError):
                s.Client({'base_url': 'https://wiki.example.com', 'page_id': page}, 'user', 'password')


if __name__ == '__main__':
    unittest.main(verbosity=2)
