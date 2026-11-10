import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QDialog
from review import merge_document, save, ReviewService
from review_gui import ReviewDialog, RichEditor, DocumentRenderer, difference_spans

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

    def test_choice_is_explicit_and_can_be_changed(self):
        block=self.dialog.outstanding()[0]
        self.assertFalse(self.dialog.confirm.isEnabled())
        self.dialog.choose(block,'remote');APP.processEvents()
        self.assertEqual(self.dialog.confirm.text(),'同步两端')
        self.assertTrue(self.dialog.confirm.isEnabled())
        self.assertEqual(self.service.load()['decisions'][block['id']]['source'],'remote')
        self.dialog.choose(block,'local')
        self.assertEqual(self.service.load()['decisions'][block['id']]['source'],'local')
        self.dialog.choose(block,'reset');APP.processEvents()
        self.assertFalse(self.dialog.confirm.isEnabled())

    def test_same_and_automatic_changes_are_completely_hidden(self):
        self.session['blocks']=merge_document('不该展示\na\nb\nc\n','不该展示\nA\nb\nC1\n','不该展示\na\nB\nC2\n')
        self.dialog.rebuild(initial=True)
        self.assertEqual(self.dialog.counter.text(),'1 / 1')
        visible=''.join(p.toPlainText() for p in self.dialog.source_papers.values())
        self.assertNotIn('不该展示',visible)
        self.assertNotIn('A',visible)
        self.assertIn('C1',visible)

    def test_one_conflict_at_a_time_and_back_retains_choice(self):
        self.session['blocks']=merge_document('a\n\nb\n','A1\n\nB1\n','A2\n\nB2\n')
        self.dialog.rebuild(initial=True)
        first=self.dialog.current_block()
        self.dialog.choose(first,'remote')
        self.dialog.advance()
        self.assertEqual(self.dialog.counter.text(),'2 / 2')
        self.assertFalse(self.dialog.confirm.isEnabled())
        self.dialog.go_previous()
        self.assertEqual(self.dialog.current_block()['id'],first['id'])
        self.assertEqual(self.dialog.source_buttons['remote'].text(),'已选网页')

    def test_final_action_submits_only_after_all_choices(self):
        self.dialog.run_operation=Mock()
        self.dialog.advance();self.dialog.run_operation.assert_not_called()
        self.dialog.choose(self.dialog.current_block(),'local')
        self.dialog.advance();self.dialog.run_operation.assert_called_once_with(self.session)

    def test_inline_edit_saved_as_a_decision(self):
        self.dialog.begin_edit()
        self.assertTrue(self.dialog.editing)
        self.dialog.editor.setPlainText('最多6名')
        self.dialog.advance()
        self.assertFalse(self.dialog.editing)
        decision=self.service.load()['decisions'][self.dialog.current_block()['id']]
        self.assertEqual(decision['source'],'custom')
        self.assertIn('最多6名',decision['text'])

    def test_unfinished_edit_survives_reopening_without_being_accepted(self):
        self.dialog.begin_edit();self.dialog.editor.setPlainText('尚未确认的新写法')
        self.dialog.save_edit_draft()
        self.assertFalse(self.service.load()['decisions'])
        reopened=ReviewDialog('',('',''),session=self.service.load(),service=self.service)
        self.assertTrue(reopened.editing)
        self.assertIn('尚未确认的新写法',reopened.editor.toPlainText())
        reopened.close()

    def test_editor_preserves_protected_macro(self):
        token='<!-- confluence-storage:PGI+eDwvYj4= -->'
        editor=RichEditor(self.session,'正文\n\n'+token)
        self.assertIn(token,editor.value())

    def test_table_editor_returns_only_edited_rows(self):
        block=self.dialog.current_block()
        block.update(local='| 绑定数量 | 5名 |\n',table_header='| 项目 | 规则 |\n| --- | --- |\n')
        save(self.service.path,self.session)
        self.dialog.begin_edit();self.dialog.save_editor()
        text=self.service.load()['decisions'][block['id']]['text']
        self.assertIn('绑定数量',text)
        self.assertNotIn('项目',text)

    def test_remote_image_is_not_downloaded_by_preview(self):
        renderer=DocumentRenderer(self.session)
        result=renderer.render('![](https://external.example.com/image.png)')
        self.assertNotIn('<img',result);self.assertIn('外部图片',result)

    def test_difference_highlight_does_not_color_identical_row_labels(self):
        from lxml import etree
        renderer=DocumentRenderer(self.session)
        header='| 项目 | 规则 |\n| --- | --- |\n'
        left=header+'| 重复绑定 | 更新关系，绑定成功。 |\n'
        right=header+'| 重复绑定 | 不重复添加，提示你已绑定。 |\n'
        lm,rm=difference_spans(renderer,left,right)
        for text,marks in [(left,lm),(right,rm)]:
            root=etree.HTML(renderer.render(text,marks))
            self.assertFalse(root.xpath('//td[1]//span[@style]'))
            self.assertTrue(root.xpath('//td[2]//span[@style]'))
