"""Document-first review workbench shared by Windows and macOS."""
import difflib
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QTextDocument, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QTextBrowser, QTextEdit, QListWidget,
    QListWidgetItem, QSplitter, QMessageBox, QToolButton, QMenu, QDialogButtonBox)
from lxml import etree
from review import ReviewService, ReviewChanged, apply_decisions

STYLE='''
QDialog { background:#f5f5f7; }
QWidget { color:#25252a; font-family:"Microsoft YaHei UI","PingFang SC",sans-serif; font-size:14px; }
QFrame#paper { background:white; border:1px solid #e5e5eb; border-radius:14px; }
QLabel#title { font-size:26px; font-weight:600; }
QLabel#muted { color:#777780; font-size:12px; }
QLabel#eyebrow { color:#927444; font-size:12px; }
QLabel#section { font-size:16px; font-weight:600; }
QPushButton { border:1px solid #dfdfe6; background:white; border-radius:8px; padding:9px 15px; }
QPushButton:hover { background:#eeeef4; }
QPushButton#primary { background:#007aff; color:white; border:0; }
QPushButton#primary:hover { background:#0066da; }
QPushButton#quiet { color:#007aff; border:0; background:transparent; }
QPushButton:disabled { color:#a0a0a8; background:#ededf1; }
QTextBrowser { border:0; background:transparent; padding:3px; }
QTextEdit { border:1px solid #d9d9e1; border-radius:9px; background:white; padding:14px; }
QListWidget { border:0; background:transparent; outline:0; }
QListWidget::item { padding:12px 8px; border-radius:8px; margin:2px; }
QListWidget::item:selected { background:#e7effc; color:#0064d2; }
QScrollArea { border:0; background:transparent; }
QWidget#reviewBody { background:transparent; }
QSplitter::handle { background:transparent; width:14px; }
QToolButton { border:0; color:#777780; padding:8px; }
'''


def tokens(text):
    return re.findall(r'[\u4e00-\u9fff]|[A-Za-z0-9_]+|\s+|.',text,re.S)


def changed_words(left,right):
    a,b=tokens(left),tokens(right); la,rb=set(),set()
    for tag,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if tag!='equal': la.update(a[i:j]); rb.update(b[k:l])
    return {x for x in la if x.strip()}, {x for x in rb if x.strip()}


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
                pieces=re.split(r'(<[^>]*>)',rendered)
                for index in range(0,len(pieces),2):
                    pieces[index]=''.join('<span style="background-color:#fff0c7">'+html.escape(t)+'</span>'
                        if t in marks else html.escape(t) for t in tokens(html.unescape(pieces[index])))
                rendered=''.join(pieces)
            return rendered
        except Exception:
            return '<pre>'+html.escape(text)+'</pre>'


class Paper(QTextBrowser):
    def __init__(self,renderer,text,marks=None):
        super().__init__(); self.renderer=renderer
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.open_link)
        self.setStyleSheet('QTextBrowser {border:0; background:transparent;}')
        self.document().setDefaultStyleSheet('''
            body {line-height:1.6;} p {margin:8px 0;} h1,h2,h3,h4 {margin-top:16px;}
            table {border-collapse:collapse; width:100%;} td,th {border:1px solid #e0e0e7; padding:9px;}
            th {background:#f5f5f8;} blockquote {color:#666674;} pre {background:#f3f3f6; padding:12px;}
        ''')
        self.setHtml(renderer.render(text,marks))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents)
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


class EditDialog(QDialog):
    def __init__(self,parent,session,block):
        super().__init__(parent);self.setWindowTitle('调整这一处内容');self.resize(840,580)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,24,24,24)
        title=QLabel('把这一处写成你希望的样子');title.setObjectName('section');layout.addWidget(title)
        toolbar=QHBoxLayout()
        self.table_header=block.get('table_header','')
        self.editor=RichEditor(session,self.table_header+session['decisions'].get(block['id'],{}).get('text',block['local']))
        for caption,action in [('加粗',self.bold),('撤销',self.editor.undo),('重做',self.editor.redo)]:
            button=QPushButton(caption);button.clicked.connect(action);toolbar.addWidget(button)
        toolbar.addStretch();layout.addLayout(toolbar);layout.addWidget(self.editor)
        note=QLabel('可直接编辑文字、列表与表格。保留的图片和网页组件会随最终文档同步。');note.setObjectName('muted');layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('采用这段内容')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.check);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.text=None

    def bold(self):
        self.editor.setFontWeight(QFont.Weight.Normal if self.editor.fontWeight()>500 else QFont.Weight.Bold)

    def check(self):
        try:
            self.text=self.editor.value()
            if self.table_header:
                rows=self.text.strip().splitlines(True)
                if len(rows)<2 or not re.fullmatch(r'[| :\-\r\n]+',rows[1]):
                    raise ValueError('请保留表格的列标题；可以调整下方内容。')
                self.text=''.join(rows[2:])
            from sync_engine import md_to_storage
            if self.text.strip(): md_to_storage(self.text,raw_images=True)
            self.accept()
        except Exception as error: QMessageBox.warning(self,'请检查内容',str(error))


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
    def __init__(self,config,credentials,parent=None,session=None,service=None):
        super().__init__(parent);self.config=config;self.credentials=credentials;self.session=session;self.service=service
        self.operation=None;self.succeeded=False;self.cards={};self.expanded=set();self.full_preview=False
        self.setWindowTitle('Palimpsest · 确认文档更新');self.resize(1220,820);self.setMinimumSize(880,600);self.setStyleSheet(STYLE)
        layout=QVBoxLayout(self);layout.setContentsMargins(28,25,28,20);layout.setSpacing(12)
        top=QHBoxLayout();texts=QVBoxLayout()
        self.title=QLabel('让两端重新保持一致');self.title.setObjectName('title');texts.addWidget(self.title)
        self.summary=QLabel('正在检查本地与网页的最新内容…');self.summary.setObjectName('muted');self.summary.setWordWrap(True);texts.addWidget(self.summary)
        top.addLayout(texts,1)
        self.more=QToolButton();self.more.setText('更多 ···');self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QMenu(self)
        menu.addAction('整篇采用本地写法',lambda:self.adopt_all('local'))
        menu.addAction('整篇采用网页写法',lambda:self.adopt_all('remote'))
        menu.addSeparator();menu.addAction('打开网页文档',self.open_web);menu.addAction('查看本次备份',self.open_backup)
        self.more.setMenu(menu);top.addWidget(self.more);layout.addLayout(top)
        self.notice=QLabel();self.notice.setWordWrap(True);self.notice.setStyleSheet('color:#936325; padding:8px; background:#fff6e5; border-radius:8px;');self.notice.hide();layout.addWidget(self.notice)
        splitter=QSplitter();self.navigation=QListWidget();self.navigation.setMaximumWidth(220);self.navigation.setMinimumWidth(160)
        self.navigation.itemClicked.connect(self.navigate);splitter.addWidget(self.navigation)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);splitter.addWidget(self.scroll);splitter.setStretchFactor(1,1);layout.addWidget(splitter,1)
        footer=QHBoxLayout();self.progress=QLabel('');self.progress.setObjectName('muted');footer.addWidget(self.progress,1)
        self.preview=QPushButton('查看完整文档');self.preview.setObjectName('quiet');self.preview.clicked.connect(self.toggle_preview);footer.addWidget(self.preview)
        self.later=QPushButton('稍后处理');self.later.clicked.connect(self.reject);footer.addWidget(self.later)
        self.confirm=QPushButton('确认并同步');self.confirm.setObjectName('primary');self.confirm.clicked.connect(self.submit);footer.addWidget(self.confirm);layout.addLayout(footer)
        self.footnote=QLabel('更新前会自动备份两端。关闭此窗口会保留你的选择，同步继续暂停。');self.footnote.setObjectName('muted');layout.addWidget(self.footnote)
        if session: self.rebuild()
        else: QTimer.singleShot(0,self.prepare)

    def prepare(self): self.run_operation()

    def run_operation(self,session=None):
        if self.operation: return
        self.set_busy(True)
        self.operation=Operation(self.config,self.credentials,session)
        self.operation.completed.connect(self.completed)
        self.operation.finished.connect(self.operation_finished)
        self.operation.start()

    def operation_finished(self):
        old,self.operation=self.operation,None;old.deleteLater();self.set_busy(False)
        if self.succeeded: self.accept()

    def completed(self,session,service,error):
        if session:
            self.session,self.service=session,service
            self.succeeded=session.get('phase')=='resolved'
            self.rebuild()
        if error:
            self.notice.setText(error);self.notice.show()
        if self.succeeded:
            self.summary.setText('两端已更新，自动同步即将恢复。')

    def set_busy(self,busy):
        for control in (self.more,self.preview,self.confirm,self.later,self.scroll):control.setEnabled(not busy)
        if busy:self.progress.setText('正在安全更新两端，请稍候…' if self.session else '正在读取文档…')
        elif self.session:self.update_progress()

    def outstanding(self):
        return [b for b in self.session['blocks'] if b['kind']=='conflict' and b['id'] not in self.session['decisions']] if self.session else []

    def update_progress(self):
        count=len(self.outstanding())
        self.progress.setText('还有 '+str(count)+' 处待确认' if count else '内容已确认 · 将更新本地与网页')
        self.confirm.setText('查看下一处待确认' if count else '确认并同步')

    def rebuild(self):
        self.renderer=DocumentRenderer(self.session);self.cards={};self.navigation.clear()
        old=self.scroll.takeWidget()
        if old:old.deleteLater()
        body=QWidget();body.setObjectName('reviewBody');stack=QVBoxLayout(body);stack.setContentsMargins(2,2,12,16);stack.setSpacing(15)
        auto=sum(b['kind']=='auto' for b in self.session['blocks']);remaining=len(self.outstanding())
        self.summary.setText(Path(self.session['file']).name+'\n'+
            ('已合并 '+str(auto)+' 处互不影响的修改，'+str(remaining)+' 处需要你确认。' if self.session['has_baseline'] else
             '这份文档来自旧版本，没有保留上次正文；为避免误合并，请确认不同的部分。'))
        if self.full_preview:
            text=''.join(self.session['decisions'].get(b['id'],{}).get('text',b['result'] if b['result'] is not None else b['local']) for b in self.session['blocks'])
            heading=QLabel('最终文档预览'+(' · 尚有未确认内容，暂显示本地写法' if remaining else ''));heading.setObjectName('section');stack.addWidget(heading)
            stack.addWidget(Paper(self.renderer,text))
        else:
            for block in self.session['blocks']:
                card=self.make_card(block);self.cards[block['id']]=card;stack.addWidget(card)
                if block['kind']!='same':
                    item=QListWidgetItem(('待确认 · ' if block in self.outstanding() else '已确认 · ' if block['id'] in self.session['decisions'] else '已合并 · ')+block['heading'])
                    item.setData(Qt.ItemDataRole.UserRole,block['id']);self.navigation.addItem(item)
        stack.addStretch();self.scroll.setWidget(body)
        self.preview.setText('返回逐处确认' if self.full_preview else '查看完整文档');self.update_progress()

    def make_card(self,block):
        card=QFrame();card.setObjectName('paper');layout=QVBoxLayout(card);layout.setContentsMargins(20,17,20,18);layout.setSpacing(12)
        if block['kind']=='same':
            rows=block['local'].splitlines();button=QPushButton(('收起' if block['id'] in self.expanded else '展开')+'相同的 '+str(len(rows))+' 行内容')
            button.setObjectName('quiet');button.clicked.connect(lambda checked=False,b=block:self.expand(b['id']));layout.addWidget(button)
            if block['id'] in self.expanded:layout.addWidget(Paper(self.renderer,block['local']))
            else:
                headings=re.findall(r'(?m)^#{1,6}\s+(.+)$',block['local'])
                if headings:
                    label=QLabel(' · '.join(headings[-2:]));label.setObjectName('muted');layout.addWidget(label)
            return card
        decision=self.session['decisions'].get(block['id'])
        head=QHBoxLayout();title=QLabel(block['heading']);title.setObjectName('section');head.addWidget(title,1)
        badge=QLabel('需要确认' if block['kind']=='conflict' and not decision else '已确认' if decision else '已自动合并')
        badge.setObjectName('eyebrow');head.addWidget(badge);layout.addLayout(head)
        if block['kind']=='conflict' and not decision:
            left_marks,right_marks=changed_words(block['local'],block['remote'])
            columns=QHBoxLayout();columns.setSpacing(18)
            for source,caption,marks in [('local','本地文档的写法',left_marks),('remote','网页文档的写法',right_marks)]:
                column=QVBoxLayout();label=QLabel(caption);label.setObjectName('muted');column.addWidget(label)
                column.addWidget(Paper(self.renderer,block['table_header']+block[source],marks))
                button=QPushButton('采用'+('本地' if source=='local' else '网页')+'写法')
                button.clicked.connect(lambda checked=False,b=block,s=source:self.choose(b,s));column.addWidget(button)
                columns.addLayout(column,1)
            layout.addLayout(columns)
            actions=QHBoxLayout()
            for caption,source in [('我来调整','custom'),('两段都保留','both'),('查看上次内容','base')]:
                if source=='base' and not self.session['has_baseline']:continue
                button=QPushButton(caption);button.setObjectName('quiet');button.clicked.connect(lambda checked=False,b=block,s=source:self.choose(b,s));actions.addWidget(button)
            actions.addStretch();layout.addLayout(actions)
        else:
            layout.addWidget(Paper(self.renderer,block['table_header']+(decision['text'] if decision else block['result'])))
            if decision:
                row=QHBoxLayout();caption=QLabel({'local':'采用了本地写法','remote':'采用了网页写法','custom':'已按你的调整保留','both':'已保留两段内容'}.get(decision['source'],''));caption.setObjectName('muted');row.addWidget(caption,1)
                button=QPushButton('重新选择');button.setObjectName('quiet');button.clicked.connect(lambda checked=False,b=block:self.choose(b,'reset'));row.addWidget(button);layout.addLayout(row)
            else:
                note=QLabel('两端修改互不影响，已保留在最终文档中。');note.setObjectName('muted');layout.addWidget(note)
        return card

    def choose(self,block,source):
        try:
            if source=='base':
                dialog=QDialog(self);dialog.setWindowTitle('上次同步时的内容');dialog.resize(750,480);layout=QVBoxLayout(dialog);layout.addWidget(Paper(self.renderer,block['table_header']+block['base']));dialog.exec();return
            text=None
            if source=='custom':
                editor=EditDialog(self,self.session,block)
                if editor.exec()!=QDialog.DialogCode.Accepted:return
                text=editor.text
            position=self.scroll.verticalScrollBar().value()
            self.service.save_decision(self.session,block['id'],source,text)
            self.rebuild();QTimer.singleShot(0,lambda:self.scroll.verticalScrollBar().setValue(position))
        except Exception as error:QMessageBox.warning(self,'未能保存选择',str(error))

    def expand(self,identity):
        if identity in self.expanded:self.expanded.remove(identity)
        else:self.expanded.add(identity)
        position=self.scroll.verticalScrollBar().value();self.rebuild();QTimer.singleShot(0,lambda:self.scroll.verticalScrollBar().setValue(position))

    def navigate(self,item):
        identity=item.data(Qt.ItemDataRole.UserRole)
        if self.full_preview:self.full_preview=False;self.rebuild()
        if identity in self.cards:self.scroll.ensureWidgetVisible(self.cards[identity],0,24)

    def toggle_preview(self):self.full_preview=not self.full_preview;self.rebuild()

    def adopt_all(self,source):
        if not self.session:return
        answer=QMessageBox.question(self,'采用整篇文档？','这会放弃另一端独有的修改。更新前仍会保留两端备份。',QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer!=QMessageBox.StandardButton.Ok:return
        # Whole-document adoption also replaces automatically merged ranges.
        text=self.session[source]
        self.session['blocks']=[dict(id='whole-'+source,kind='conflict',base='',local=self.session['local'],remote=self.session['remote'],result=None,source='',heading='整篇文档',table_header='')]
        from review import save
        save(self.service.path,self.session)
        self.service.save_decision(self.session,'whole-'+source,source,text)
        self.full_preview=True;self.rebuild()

    def submit(self):
        if not self.session:return
        outstanding=self.outstanding()
        if outstanding:
            if self.full_preview:self.full_preview=False;self.rebuild()
            self.scroll.ensureWidgetVisible(self.cards[outstanding[0]['id']],0,24);return
        self.notice.hide();self.run_operation(self.session)

    def open_web(self):
        if self.session:QDesktopServices.openUrl(QUrl(self.session['base_url']+'/pages/viewpage.action?pageId='+self.session['page']))

    def open_backup(self):
        if self.session:QDesktopServices.openUrl(QUrl.fromLocalFile(self.session['backup']))

    def reject(self):
        if not self.operation:self.credentials=None;super().reject()

    def closeEvent(self,event):
        if self.operation:event.ignore()
        else:self.credentials=None;super().closeEvent(event)


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
        assert dialog.outstanding() and dialog.confirm.text()=='查看下一处待确认'
        dialog.close();app.processEvents()
    print('PASS: frozen document review window, rendering, navigation and conflict controls')
    return 0
