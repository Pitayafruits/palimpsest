"""Entity and reported-page regression tests, shared by both engines."""
import json
from pathlib import Path
import tempfile
import os
import subprocess
import time
import unittest
from lxml import etree
import test_images as fixtures

s = fixtures.s


class EntityTests(unittest.TestCase):
    def strict(self, storage):
        return etree.fromstring(('<root xmlns:ac="'+s.AC+'" xmlns:ri="'+s.RI+'">'+storage+'</root>').encode())

    def test_query_parameters_are_xml_safe_and_unchanged(self):
        url = 'https://www.figma.com/design/test?node-id=641-1048&p=f&t=sample-0'
        for md in ('[Design]('+url+')', '<a href="'+url+'">Design</a>'):
            with self.subTest(md=md):
                root = self.strict(s.md_to_storage(md))
                self.assertEqual(root.find('.//a').get('href'), url)

    def test_entities_not_double_escaped_or_decoded_into_markup(self):
        text = '<p>A &amp; B &lt;tag&gt; &nbsp; &ldquo;中文&rdquo; &nvlt; &#38; &#x26; &unknown;</p>'
        normalized = s.xml_entities(text)
        self.assertEqual(normalized, s.xml_entities(normalized))
        root = self.strict(normalized)
        self.assertEqual(len(root[0]), 0)
        self.assertEqual(root[0].text, 'A & B <tag> \xa0 “中文” <\u20d2 & & &unknown;')

    def test_cdata_and_comments_keep_literal_code(self):
        text = '<!-- A & B &nbsp; --><ac:plain-text-body><![CDATA[if (a && b) x="&nbsp;";]]></ac:plain-text-body>'
        self.assertEqual(s.xml_entities(text), text)
        self.assertEqual(s.xml_root(text)[1].text, 'if (a && b) x="&nbsp;";')

    def test_raw_text_ampersands(self):
        result = self.strict(s.md_to_storage('<p>R&D & unknown; &copy;</p>'))
        self.assertEqual(''.join(result.itertext()).strip(), 'R&D & unknown; ©')

    def test_malformed_markup_still_rejected(self):
        with self.assertRaises(etree.XMLSyntaxError):
            s.xml_root('<p><b>broken</p>')

    def test_bold_macro_and_punctuation_preserve_content(self):
        body = '<p>原组<strong>。</strong>后续</p><p><strong><ac:structured-macro ac:name="view-file"><ac:parameter ac:name="height">250</ac:parameter></ac:structured-macro><br /></strong></p>'
        self.assertEqual(s.semantic(body), s.semantic(s.md_to_storage(s.storage_to_md(body))))

    def test_lists_in_table_preserve_structure(self):
        body = '<table><tr><td><p>通知：</p><ul><li>第一项</li><li><em>第二项</em></li></ul></td></tr></table>'
        result = s.md_to_storage(s.storage_to_md(body))
        self.assertEqual(s.semantic(body), s.semantic(result))
        self.assertEqual(len(self.strict(result).xpath('.//li')), 2)



    def test_inline_styles_with_quotes_punctuation_and_whitespace(self):
        for tag in ('em', 'i', 'strong', 'b', 's', 'del'):
            for content in ('小组名称} &quot;', ' 前后空格 ', '。', '(小组)', '普通中文', 'English'):
                for table in (False, True):
                    with self.subTest(tag=tag, content=content, table=table):
                        body = '<p>确认删除&quot;{<'+tag+'>'+content+'</'+tag+'>吗？</p>'
                        if table:
                            body = '<table><tr><td>'+body+'</td></tr></table>'
                        result = s.md_to_storage(s.storage_to_md(body))
                        self.assertEqual(s.semantic(body), s.semantic(result))
                        normalized_tag = 'strong' if tag in ('strong', 'b') else 'em' if tag in ('em', 'i') else 'del'
                        selector = './/del | .//s' if normalized_tag == 'del' else './/'+normalized_tag
                        self.assertEqual(len(self.strict(result).xpath(selector)), 1)



if __name__ == '__main__':
    unittest.main()
