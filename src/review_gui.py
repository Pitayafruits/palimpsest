"""Focused, one-conflict-at-a-time review shared by Windows and macOS."""
import difflib
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QTextBrowser, QTextEdit, QToolButton, QMenu, QProgressBar)
from lxml import etree
from review import ReviewService, ReviewChanged

STYLE='''
QDialog { background:#f5f5f7; }
QWidget { color:#1d1d1f; font-family:"Microsoft YaHei UI","PingFang SC",sans-serif; font-size:14px; }
QLabel#title { font-size:26px; font-weight:600; }
QLabel#muted { color:#86868b; font-size:12px; }
QLabel#counter { color:#6e6e73; background:#eaeaec; border-radius:12px; padding:4px 12px; font-size:12px; }
QLabel#location { color:#515155; font-size:14px; }
QLabel#source { color:#3a3a3c; font-size:14px; font-weight:600; }
QLabel#notice { color:#9c641b; background:#fff4df; border-radius:8px; padding:10px; margin-bottom:12px; }
QFrame#option { background:white; border:1px solid #e5e5ea; border-radius:18px; }
QFrame#option[selected="true"] { border:1px solid #007aff; background:#fbfdff; }
QPushButton { border:1px solid #dedee3; background:white; border-radius:10px; padding:9px 20px; }
QPushButton:hover { background:#f1f1f5; }
QPushButton#primary { background:#007aff; color:white; border:1px solid #007aff; }
QPushButton#primary:hover { background:#0068df; }
QPushButton#selectedChoice { background:#eaf3ff; color:#0071e3; border:1px solid #eaf3ff; }
QPushButton#choice { background:#f2f2f7; border:1px solid #f2f2f7; }
QPushButton#choice:hover { background:#e9e9ef; }
QPushButton#quiet { color:#007aff; background:transparent; border:0; padding:5px 10px; font-size:13px; }
QPushButton#textButton { color:#6e6e73; background:transparent; border:0; padding-left:0; }
QPushButton:disabled { color:#a1a1a8; background:#eeeef1; border-color:#eeeef1; }
QPushButton#primary:disabled { color:#a1a1a8; background:#eeeef1; border:1px solid #eeeef1; }
QTextBrowser { border:0; background:transparent; padding:0; }
QTextEdit { background:white; border:0; padding:8px; }
QProgressBar { border:0; background:#eaeaec; border-radius:1px; }
QProgressBar::chunk { background:#007aff; border-radius:1px; }
QToolButton { color:#007aff; background:transparent; border:0; padding:4px; font-size:12px; }
QScrollBar:vertical { background:transparent; width:7px; margin:0; }
QScrollBar::handle:vertical { background:#c7c7cc; min-height:30px; border-radius:3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QMenu { background:white; padding:5px; border:1px solid #e1e1e6; }
QMenu::item { padding:9px 18px; }
QMenu::item:selected { background:#eaf2ff; color:#007aff; }
'''



def difference_spans(renderer,left,right):
    def plain(markdown):
        return ''.join(etree.HTML(renderer.render(markdown)).itertext())
    a,b=plain(left),plain(right);la,rb=[],[]
    for tag,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if tag!='equal':
            if i!=j:la.append((i,j))
            if k!=l:rb.append((k,l))
    return la,rb


def highlight_html(rendered,ranges):
    pieces=re.split(r'(<[^>]*>)',rendered);offset=0
    for index in range(0,len(pieces),2):
        text=html.unescape(pieces[index]);end=offset+len(text)
        boundaries=sorted({offset,end}|{max(offset,min(end,x)) for pair in ranges for x in pair})
        result=[]
        for a,b in zip(boundaries,boundaries[1:]):
            escaped=html.escape(text[a-offset:b-offset])
            result.append('<span style="background-color:#fff2d6">'+escaped+'</span>'
                          if any(start<=a<finish for start,finish in ranges) else escaped)
        pieces[index]=''.join(result);offset=end
    return ''.join(pieces)


class SelectionMark(QWidget):
    """A small vector indicator, independent of installed symbol fonts."""
    def __init__(self,selected,parent=None):
        super().__init__(parent);self.selected=selected;self.setFixedSize(22,22)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#007aff' if self.selected else '#d9d9df'),1.3))
        painter.setBrush(QColor('#007aff' if self.selected else '#ffffff'))
        painter.drawEllipse(2,2,18,18)
        if self.selected:
            painter.setPen(QPen(QColor('white'),1.8,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(7,11,10,14);painter.drawLine(10,14,15,8)


class DocumentRenderer:
    def __init__(self, session):
        self.session=session; self.images={}; self.originals={}

    def render(self,text,marks=None):
        from sync_engine import md_to_storage,xml_root,AC
        if not text.strip(): return '<p style="color:#96969f">这部分已删除</p>'
        try:
            root=xml_root(md_to_storage(text,raw_images=True))
            for node in list(root.iter()):
                if not isinstance(node.tag,str): continue
                name=etree.QName(node).localname
                ns=etree.QName(node).namespace
                if ns:
                    if node.getparent() is not None and node.getparent().tag=='root' or ns==AC:
                        if node.getparent() is None: continue
                        label=etree.Element('span'); label.text='〔网页组件：'+(node.get('{'+AC+'}name') or name)+' · 原样保留〕'
                        label.tail=node.tail; node.getparent().replace(node,label)
                    continue
                # QTextDocument is not a browser. Strip active content and remote resources anyway.
                for attribute in list(node.attrib):
                    if attribute not in ('href','src','alt','colspan','rowspan'): del node.attrib[attribute]
                if name=='img':
                    src=node.get('src',''); path=Path(unquote(src))
                    if '://' in src or src.startswith('//'):
                        label=etree.Element('span'); label.text='〔外部图片 · 打开网页查看〕'; label.tail=node.tail
                        node.getparent().replace(node,label); continue
                    path=(path if path.is_absolute() else Path(self.session['file']).parent/path).resolve()
                    image=QImage(str(path))
                    if image.isNull():
                        label=etree.Element('span');label.text='〔图片暂不可预览〕';label.tail=node.tail
                        node.getparent().replace(node,label);continue
                    identity='review-image:'+str(len(self.images))
                    self.images[identity]=image.scaled(460,320,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                    self.originals[identity]=path
                    node.set('src',identity)
                    node.set('width',str(self.images[identity].width()))
                    node.set('height',str(self.images[identity].height()))
                    parent=node.getparent()
                    link=etree.Element('a',href=identity)
                    link.tail=node.tail;node.tail=None
                    parent.replace(node,link);link.append(node)
                elif name=='a':
                    if not re.match(r'^https?://',node.get('href','')): node.attrib.pop('href',None)
            rendered=''.join(etree.tostring(x,encoding='unicode',method='html') for x in root)
            if marks:
                rendered=highlight_html(rendered,marks)
            return rendered
        except Exception:
            return '<pre>'+html.escape(text)+'</pre>'


class Paper(QTextBrowser):
    def __init__(self,renderer,text,marks=None,flow=True):
        super().__init__(); self.renderer=renderer; self.flow=flow
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.open_link)
        self.setStyleSheet('QTextBrowser {border:0; background:transparent;}')
        self.document().setDefaultStyleSheet('''
            body {line-height:1.6;} p {margin:8px 0;} h1,h2,h3,h4 {margin-top:16px;}
            table {border-collapse:collapse; width:100%;} td,th {border:1px solid #ececf1; padding:11px;}
            th {background:#f6f7f9;} blockquote {color:#666674;} pre {background:#f3f3f6; padding:12px;}
        ''')
        self.setHtml(renderer.render(text,marks))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents if flow else QScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.fit()

    def loadResource(self,kind,url):
        if url.toString() in self.renderer.images: return self.renderer.images[url.toString()]
        return None

    def open_link(self,url):
        if url.toString() in self.renderer.originals:
            dialog=QDialog(self);dialog.setWindowTitle('查看图片');dialog.resize(1000,740)
            layout=QVBoxLayout(dialog);scroll=QScrollArea();scroll.setWidgetResizable(True)
            label=QLabel();label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            from PySide6.QtGui import QPixmap
            image=QPixmap(str(self.renderer.originals[url.toString()]))
            label.setPixmap(image.scaled(1400,1200,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
            scroll.setWidget(label);layout.addWidget(scroll);dialog.exec()
        elif url.scheme() in ('https','http'): QDesktopServices.openUrl(url)

    def fit(self):
        self.document().setTextWidth(max(220,self.viewport().width()))
        if not self.flow:return
        height=int(self.document().size().height())+40
        self.setFixedHeight(max(50,min(900,height)))

    def resizeEvent(self,event):
        super().resizeEvent(event); self.fit()


class RichEditor(QTextEdit):
    def __init__(self,session,text):
        super().__init__(); self.session=session;self.protected={}
        def protect(match):
            key='〔网页组件 '+str(len(self.protected)+1)+'，请保留〕'
            self.protected[key]=match[0];return key
        text=re.sub(r'<!-- confluence-storage:[A-Za-z0-9+/=]+ -->',protect,text)
        self.document().setBaseUrl(QUrl.fromLocalFile(str(Path(session['file']).parent)+'/' ))
        self.setMarkdown(text)

    def loadResource(self,kind,url):
        if url.scheme() not in ('','file'): return None
        path=Path(url.toLocalFile() if url.scheme()=='file' else unquote(url.toString()))
        path=path if path.is_absolute() else Path(self.session['file']).parent/path
        image=QImage(str(path))
        return image.scaled(600,360,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation) if not image.isNull() else None

    def value(self):
        result=self.toMarkdown()
        for key,token in self.protected.items():
            if key not in result: raise ValueError('这一段包含网页组件，请保留组件占位文字，或选择原版本。')
            result=result.replace(key,token)
        return result


class Operation(QThread):
    completed=Signal(object,object,str)
    def __init__(self,config,credentials,session=None):
        super().__init__();self.config=config;self.credentials=credentials;self.session=session

    def run(self):
        sync=None;service=None;result=None;error=''
        try:
            import sync_engine as engine
            config=json.loads(Path(self.config).read_text(encoding='utf-8-sig'))
            config['_data_dir']=str(Path(self.config).parent)
            client=engine.Client(config,*self.credentials)
            sync=engine.Sync(config,client,lambda *a:None)
            service=ReviewService(sync)
            result=service.submit(self.session) if self.session else service.prepare()
        except ReviewChanged as failure:
            error=str(failure)
            if service:
                try: result=service.prepare()
                except Exception as refresh_error: error+='\n'+str(refresh_error)
        except Exception as failure: error=str(failure)
        finally:
            self.credentials=None
            if sync: sync.client.auth='';sync.close()
        self.completed.emit(result,service,error)


class ReviewDialog(QDialog):
    """One decision at a time. Unchanged and auto-merged text never enters this view."""
    def __init__(self,config,credentials,parent=None,session=None,service=None):
        super().__init__(parent)
        self.config,self.credentials=config,credentials
        self.session,self.service=session,service
        self.operation=None;self.succeeded=False;self.index=0;self.editing=False
        self.edit_header='';self.editor=None;self.scrolling=False
        self.setWindowTitle('确认文档内容')
        self.resize(1040,600);self.setMinimumSize(820,520)
        self.setStyleSheet(STYLE)
        layout=QVBoxLayout(self);layout.setContentsMargins(36,30,36,24);layout.setSpacing(0)

        self.filename=QLabel('');self.filename.setObjectName('muted')
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.filename);layout.addSpacing(9)
        header=QHBoxLayout();header.setSpacing(18)
        self.title=QLabel('选择要保留的内容');self.title.setObjectName('title')
        header.addWidget(self.title,1)
        self.counter=QLabel('');self.counter.setObjectName('counter');header.addWidget(self.counter)
        layout.addLayout(header);layout.addSpacing(19)
        self.progress_line=QProgressBar();self.progress_line.setTextVisible(False)
        self.progress_line.setFixedHeight(2);layout.addWidget(self.progress_line)
        layout.addSpacing(21)
        self.location=QLabel('正在读取文档…');self.location.setObjectName('location')
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.location);layout.addSpacing(13)
        self.notice=QLabel('');self.notice.setWordWrap(True);self.notice.setObjectName('notice');self.notice.hide()
        layout.addWidget(self.notice)
        self.body=QWidget();self.body_layout=QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0,0,0,0);self.body_layout.setSpacing(12)
        layout.addWidget(self.body,1);layout.addSpacing(12)
        self.adjust=QPushButton('自己调整…');self.adjust.setObjectName('quiet')
        self.adjust.clicked.connect(self.begin_edit)
        adjust_row=QHBoxLayout();adjust_row.addStretch();adjust_row.addWidget(self.adjust);adjust_row.addStretch()
        layout.addLayout(adjust_row);layout.addSpacing(21)
        footer=QHBoxLayout();footer.setSpacing(10)
        self.later=QPushButton('稍后处理');self.later.setObjectName('textButton')
        self.later.clicked.connect(self.reject);footer.addWidget(self.later)
        footer.addStretch()
        self.previous=QPushButton('上一处');self.previous.clicked.connect(self.go_previous);footer.addWidget(self.previous)
        self.confirm=QPushButton('下一处');self.confirm.setObjectName('primary')
        self.confirm.setMinimumWidth(132);self.confirm.clicked.connect(self.advance);footer.addWidget(self.confirm)
        layout.addLayout(footer)
        if session:self.rebuild(initial=True)
        else:QTimer.singleShot(0,self.prepare)

    def conflicts(self):
        return [block for block in self.session['blocks'] if block['kind']=='conflict'] if self.session else []

    def outstanding(self):
        return [block for block in self.conflicts() if block['id'] not in self.session['decisions']]

    def current_block(self):
        blocks=self.conflicts()
        return blocks[self.index] if blocks else None

    def prepare(self):self.run_operation()

    def run_operation(self,session=None):
        if self.operation:return
        self.set_busy(True)
        self.operation=Operation(self.config,self.credentials,session)
        self.operation.completed.connect(self.completed)
        self.operation.finished.connect(self.operation_finished)
        self.operation.start()

    def completed(self,session,service,error):
        if session:
            self.session,self.service=session,service
            self.succeeded=session.get('phase')=='resolved'
            self.editing=False;self.rebuild(initial=True)
        if error:self.notice.setText(error);self.notice.show()

    def operation_finished(self):
        old,self.operation=self.operation,None;old.deleteLater();self.set_busy(False)
        if self.succeeded:self.accept()

    def set_busy(self,busy):
        for control in (self.body,self.adjust,self.later,self.previous,self.confirm):
            control.setEnabled(not busy)
        if busy:self.confirm.setText('正在同步…' if self.session else '读取中…')
        elif self.session:self.update_controls()

    def update_controls(self):
        blocks=self.conflicts();block=self.current_block()
        total=len(blocks)
        chosen=sum(b['id'] in self.session['decisions'] for b in blocks)
        self.counter.setText(str(self.index+1)+' / '+str(total) if total else '')
        self.progress_line.setRange(0,max(total,1));self.progress_line.setValue(chosen if total else 1)
        self.previous.setVisible(not self.editing and total>1)
        self.previous.setEnabled(self.index>0 and not self.operation)
        self.adjust.setVisible(bool(block))
        self.adjust.setText('取消调整' if self.editing else '自己调整…')
        if self.editing:
            self.confirm.setText('保留这段内容');self.confirm.setEnabled(not self.operation)
        else:
            self.confirm.setText('下一处' if self.outstanding() else '同步两端')
            self.confirm.setEnabled(not self.operation and (block is None or block['id'] in self.session['decisions']))

    def clear_body(self):
        while self.body_layout.count():
            item=self.body_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.source_buttons={};self.source_papers={}

    def rebuild(self,initial=False):
        if not self.session:return
        blocks=self.conflicts()
        if initial:
            self.index=next((i for i,b in enumerate(blocks) if b['id'] not in self.session['decisions']),max(0,len(blocks)-1))
            if blocks and blocks[self.index]['id'] in self.session.get('edit_drafts',{}):self.editing=True
        self.index=max(0,min(self.index,len(blocks)-1))
        name=Path(self.session['file']).name
        self.filename.setText(self.fontMetrics().elidedText(name,Qt.TextElideMode.ElideMiddle,max(500,self.width()-100)))
        self.filename.setToolTip(name)
        self.renderer=DocumentRenderer(self.session)
        self.clear_body()
        block=self.current_block()
        if block is None:
            self.title.setText('没有需要处理的冲突')
            self.location.setText('两端的修改可以合并。')
            self.body_layout.addStretch();self.update_controls();return
        self.title.setText('选择要保留的内容')
        heading=re.sub(r'^\d+(?:\.\d+)*[.)、]?\s*','',block['heading'])
        first_row=(block['local'] or block['remote']).lstrip().splitlines()
        row_name=''
        if block.get('table_header') and first_row and first_row[0].startswith('|'):
            row_name=first_row[0].split('|')[1].strip()
        self.location.setText(heading+('  /  '+row_name if row_name and row_name!=heading else ''))
        decision=self.session['decisions'].get(block['id'])
        if self.editing:
            self.show_editor(block)
        elif decision and decision['source'] in ('custom','both'):
            self.show_custom_result(block,decision)
        else:
            self.show_comparison(block,decision)
        self.update_controls()

    def show_comparison(self,block,decision):
        row=QWidget();columns=QHBoxLayout(row);columns.setContentsMargins(0,0,0,0);columns.setSpacing(16)
        local_marks,remote_marks=difference_spans(self.renderer,block['table_header']+block['local'],block['table_header']+block['remote'])
        for source,caption,marks in [('local','本地文件',local_marks),('remote','网页文档',remote_marks)]:
            selected=bool(decision and decision['source']==source)
            card=QFrame();card.setObjectName('option');card.setProperty('selected',selected)
            content=QVBoxLayout(card);content.setContentsMargins(22,20,22,20);content.setSpacing(18)
            header=QHBoxLayout()
            label=QLabel(caption);label.setObjectName('source');header.addWidget(label,1)
            header.addWidget(SelectionMark(selected));content.addLayout(header)
            paper=Paper(self.renderer,block['table_header']+block[source],marks,flow=False)
            paper.setMinimumHeight(120);content.addWidget(paper,1);self.source_papers[source]=paper
            button=QPushButton(('已选' if selected else '保留')+('本地' if source=='local' else '网页'))
            button.setObjectName('selectedChoice' if selected else 'choice')
            button.clicked.connect(lambda checked=False,s=source:self.choose(self.current_block(),s))
            button.setFixedWidth(150)
            self.source_buttons[source]=button;content.addWidget(button,0,Qt.AlignmentFlag.AlignHCenter)
            columns.addWidget(card,1)
        self.body_layout.addWidget(row,1)
        for side,other in [('local','remote'),('remote','local')]:
            self.source_papers[side].verticalScrollBar().valueChanged.connect(
                lambda value,s=side,o=other:self.sync_scroll(value,s,o))

    def sync_scroll(self,value,source,other):
        if self.scrolling or other not in self.source_papers:return
        self.scrolling=True
        left=self.source_papers[source].verticalScrollBar();right=self.source_papers[other].verticalScrollBar()
        right.setValue(round(value*right.maximum()/max(1,left.maximum())))
        self.scrolling=False

    def show_custom_result(self,block,decision):
        card=QFrame();card.setObjectName('option');card.setProperty('selected',True)
        content=QVBoxLayout(card);content.setContentsMargins(22,18,22,18)
        header=QHBoxLayout();caption=QLabel('保留调整后的内容');caption.setObjectName('source');header.addWidget(caption,1)
        reset=QPushButton('重新对比');reset.setObjectName('quiet');reset.clicked.connect(lambda:self.choose(block,'reset'))
        header.addWidget(reset);content.addLayout(header)
        content.addWidget(Paper(self.renderer,block['table_header']+decision['text'],flow=False),1)
        self.body_layout.addWidget(card,1)

    def begin_edit(self):
        if not self.current_block() or self.operation:return
        self.editing=not self.editing;self.rebuild()

    def show_editor(self,block):
        card=QFrame();card.setObjectName('option')
        content=QVBoxLayout(card);content.setContentsMargins(20,16,20,20);content.setSpacing(12)
        toolbar=QHBoxLayout()
        label=QLabel('编辑要保留的内容');label.setObjectName('source');toolbar.addWidget(label,1)
        seed=QToolButton();seed.setText('起始内容');seed.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QMenu(seed)
        for text,source in [('从本地开始','local'),('从网页开始','remote'),('合并两份内容','both')]:
            menu.addAction(text,lambda checked=False,s=source:self.reseed_editor(s))
        seed.setMenu(menu);toolbar.addWidget(seed);content.addLayout(toolbar)
        self.edit_header=block.get('table_header','')
        text=self.session['decisions'].get(block['id'],{}).get('text',block['local'])
        self.editor=RichEditor(self.session,self.session.get('edit_drafts',{}).get(block['id'],self.edit_header+text))
        content.addWidget(self.editor,1);self.body_layout.addWidget(card,1)
        self.editor.setFocus()

    def reseed_editor(self,source):
        block=self.current_block()
        text=block[source] if source in ('local','remote') else block['local'].rstrip()+('\n' if self.edit_header else '\n\n')+block['remote']
        old=self.editor
        self.editor=RichEditor(self.session,self.edit_header+text)
        old.parentWidget().layout().replaceWidget(old,self.editor);old.deleteLater();self.editor.setFocus()

    def save_editor(self):
        from sync_engine import md_to_storage
        text=self.editor.value()
        if self.edit_header:
            rows=text.strip().splitlines(True)
            if len(rows)<2 or not re.fullmatch(r'[| :\-\r\n]+',rows[1]):
                raise ValueError('请保留表格的列标题，修改下方内容即可。')
            text=''.join(rows[2:])
        if text.strip():md_to_storage(text,raw_images=True)
        self.choose(self.current_block(),'custom',text)

    def choose(self,block,source,text=None):
        if not block or not self.service:return
        try:
            self.service.save_decision(self.session,block['id'],source,text)
            self.editing=False;self.rebuild()
        except Exception as error:
            self.notice.setText(str(error));self.notice.show()

    def go_previous(self):
        if self.index>0 and not self.operation:
            self.editing=False;self.index-=1;self.rebuild()

    def advance(self):
        if self.operation or not self.session:return
        if self.editing:
            try:self.save_editor()
            except Exception as error:self.notice.setText(str(error));self.notice.show()
            return
        block=self.current_block()
        if block and block['id'] not in self.session['decisions']:return
        pending=self.outstanding()
        if pending:
            blocks=self.conflicts()
            targets=[i for i,b in enumerate(blocks) if b['id'] not in self.session['decisions']]
            self.index=next((i for i in targets if i>self.index),targets[0])
            self.rebuild();return
        self.notice.hide();self.run_operation(self.session)

    def submit(self):self.advance()

    def reject(self):
        if self.operation:return
        if self.editing:
            # An unfinished edit is a draft, not an accepted decision.
            try:self.save_edit_draft()
            except Exception as error:self.notice.setText(str(error));self.notice.show();return
        self.credentials=None;super().reject()

    def save_edit_draft(self):
        from review import save
        current=self.service.load()
        if not current or current['snapshot']!=self.session['snapshot']:
            raise ReviewChanged('文档已更新，请重新打开后继续。')
        self.session.setdefault('edit_drafts',{})[self.current_block()['id']]=self.editor.value()
        save(self.service.path,self.session)

    def closeEvent(self,event):
        if self.operation:event.ignore();return
        if self.editing:
            try:self.save_edit_draft()
            except Exception as error:self.notice.setText(str(error));self.notice.show();event.ignore();return
        self.credentials=None;super().closeEvent(event)


def standalone():
    credentials=json.loads(sys.stdin.readline())
    config=Path(sys.argv[sys.argv.index('--config')+1]).resolve()
    app=QApplication.instance() or QApplication(sys.argv);app.setApplicationName('Palimpsest')
    dialog=ReviewDialog(config,(credentials['username'],credentials['password']));credentials.clear()
    dialog.exec()
    return 0


def smoke_test():
    app=QApplication.instance() or QApplication([])
    import tempfile
    from review import merge_document
    with tempfile.TemporaryDirectory() as temporary:
        file=Path(temporary)/'example.md';file.write_text('# 示例\n',encoding='utf-8')
        session=dict(file=str(file),blocks=merge_document('# 规则\n\n最多 3 人\n','# 规则\n\n最多 5 人\n','# 规则\n\n最多 8 人\n'),decisions={},has_baseline=True)
        dialog=ReviewDialog('',('', ''),session=session)
        dialog.show();app.processEvents()
        assert dialog.outstanding() and dialog.confirm.text()=='下一处' and not dialog.confirm.isEnabled()
        dialog.close();app.processEvents()
    print('PASS: frozen document review window, rendering, navigation and conflict controls')
    return 0
