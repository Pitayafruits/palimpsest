import json
from pathlib import Path
import tempfile
import unittest

from associations import Catalogue, parse_page


class AssociationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalogue = Catalogue(self.root / 'data')
        self.a = self.root / '需求 A.md'
        self.b = self.root / '需求 B.md'
        self.a.write_text('# A', encoding='utf-8')
        self.b.write_text('# B', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()


    def test_saved_separate_associations_and_no_password(self):
        first = self.catalogue.save('123', str(self.a), 'local', 'user')
        second = self.catalogue.save('456', str(self.b), 'remote', 'user')
        reload = Catalogue(self.catalogue.root)
        self.assertEqual(len(reload.bindings), 2)
        self.assertEqual(reload.data['last_id'], second['id'])
        self.assertNotEqual(first['id'], second['id'])
        for item in (first, second):
            config = json.loads((reload.directory(item) / 'mapping.json').read_text(encoding='utf-8'))
            self.assertEqual(config['page_id'], item['page_id'])
        self.assertNotIn('password', reload.path.read_text(encoding='utf-8'))

    def test_one_to_one_and_invalid_file(self):
        self.catalogue.save('123', str(self.a), 'local', '')
        self.catalogue.save('123', str(self.b), 'local', '')
        self.assertEqual(self.catalogue.bindings[0]['local_file'], str(self.b.resolve()))
        self.catalogue.save('456', str(self.b), 'local', '')
        self.assertEqual(self.catalogue.bindings[0]['page_id'], '456')
        with self.assertRaises(ValueError):
            self.catalogue.save('789', str(self.root / 'missing.md'), 'local', '')
        self.assertEqual(len(self.catalogue.bindings), 1)

    def test_symlink_cannot_bypass_one_to_one(self):
        alias = self.root / 'alias.md'
        try:
            alias.symlink_to(self.a)
        except OSError:
            self.skipTest('symlinks unavailable')
        self.catalogue.save('123', str(self.a), 'local', '')
        self.catalogue.save('456', str(alias), 'local', '')
        self.assertEqual(len(self.catalogue.bindings), 1)
        self.assertEqual(self.catalogue.bindings[0]['page_id'], '456')

    def test_remove_preserves_documents_and_baseline(self):
        binding = self.catalogue.save('123', str(self.a), 'local', '')
        state = self.catalogue.directory(binding) / 'state.json'
        state.write_text('{"marker":1}', encoding='utf-8')
        self.catalogue.remove(binding['id'])
        self.assertEqual(self.catalogue.bindings, [])
        self.assertTrue(self.a.exists())
        self.assertTrue(state.exists())
        again = self.catalogue.save('123', str(self.a), 'remote', '')
        self.assertEqual(binding['id'], again['id'])
        self.assertFalse(state.exists())
        history = list((state.parent / 'state-history').glob('*.json'))
        self.assertEqual(json.loads(history[0].read_text())['marker'], 1)

    def test_unchanged_pair_retains_baseline(self):
        binding = self.catalogue.save('123', str(self.a), 'local', '')
        state = self.catalogue.directory(binding) / 'state.json'
        state.write_text('{"marker":1}')
        self.catalogue.save('123', str(self.a), 'remote', '')
        self.assertTrue(state.exists())

    def test_replaced_pair_return_archives_stale_baseline(self):
        binding = self.catalogue.save('123', str(self.a), 'local', '')
        state = self.catalogue.directory(binding) / 'state.json'
        state.write_text('{"marker":1}')
        self.catalogue.save('123', str(self.b), 'local', '')
        self.catalogue.save('123', str(self.a), 'local', '')
        self.assertFalse(state.exists())
        self.assertEqual(len(list((state.parent / 'state-history').glob('*.json'))), 1)

    def test_corrupt_catalogue_not_overwritten(self):
        self.catalogue.root.mkdir(parents=True)
        self.catalogue.path.write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            Catalogue(self.catalogue.root)
        self.assertEqual(self.catalogue.path.read_text(), 'broken')


if __name__ == '__main__':
    unittest.main(verbosity=2)
