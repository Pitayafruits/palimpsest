import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QDialog
from review import merge_document, save, ReviewService
from review_gui import ReviewDialog, RichEditor, DocumentRenderer, EditDialog

APP=QApplication.instance() or QApplication([])


class ReviewGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.file=Path(self.temp.name)/'需求.md'
        self.file.write_text('sample',encoding='utf-8')
        self.session=dict(file=str(self.file),blocks=merge_document('# 规则\n\n最多3名\n','# 规则\n\n最多5名\n','# 规则\n\n最多8名\n'),
            snapshot='test',decisions={},has_baseline=True,phase='pending')
        self.service=ReviewService(Mock(data=Path(self.temp.name)))
        save(self.service.path,self.session)
        self.dialog=ReviewDialog('',('user','test'),session=self.session,service=self.service)
        self.dialog.show();APP.processEvents()

    def tearDown(self):
        self.dialog.close();APP.processEvents();self.temp.cleanup()

    def test_choose_preview_reopen_and_reset(self):
        block=self.dialog.outstanding()[0]
        self.dialog.choose(block,'remote');APP.processEvents()
        self.assertEqual(self.dialog.confirm.text(),'确认并同步')
        self.assertEqual(self.service.load()['decisions'][block['id']]['source'],'remote')
        self.dialog.toggle_preview();self.assertTrue(self.dialog.full_preview)
        self.dialog.choose(block,'reset');APP.processEvents()
        self.assertTrue(self.dialog.outstanding())

    def test_unchanged_content_starts_folded(self):
        self.assertFalse(self.dialog.expanded)
        block=next(b for b in self.session['blocks'] if b['kind']=='same')
        self.dialog.expand(block['id']);self.assertIn(block['id'],self.dialog.expanded)

    def test_editor_preserves_protected_macro(self):
        token='<!-- confluence-storage:PGI+eDwvYj4= -->'
        editor=RichEditor(self.session,'正文\n\n'+token)
        self.assertIn(token,editor.value())

    def test_table_editor_returns_only_edited_rows(self):
        block=dict(local='| 绑定数量 | 5名 |\n',id='row',table_header='| 项目 | 规则 |\n| --- | --- |\n')
        dialog=EditDialog(self.dialog,self.session,block)
        dialog.check()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertIn('绑定数量',dialog.text)
        self.assertNotIn('项目',dialog.text)

    def test_remote_image_is_not_downloaded_by_preview(self):
        renderer=DocumentRenderer(self.session)
        result=renderer.render('![](https://external.example.com/image.png)')
        self.assertNotIn('<img',result);self.assertIn('外部图片',result)
