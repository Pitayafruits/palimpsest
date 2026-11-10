"""Conservative three-way document merge; persistent, version-checked review."""
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote


class ConflictPending(RuntimeError):
    pass


class ReviewChanged(RuntimeError):
    pass


def hash_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def line_key(line, images=None):
    # Only display whitespace is normalized. Never normalize code or business text.
    line = line.rstrip('\r\n')
    if '`' not in line:
        line = re.sub(r'(?:<br\s*/?>\s*)+', '<br />', line, flags=re.I)
    for src, checksum in sorted((images or {}).items(), key=lambda x: -len(x[0])):
        line = line.replace(src, 'image-sha256:' + checksum)
    return line


def edits(base, variant):
    result=[]
    for tag,a,b,c,d in difflib.SequenceMatcher(None,base,variant,autojunk=False).get_opcodes():
        if tag=='equal':continue
        if tag=='replace' and b-a==d-c and all(x.lstrip().startswith('|') for x in base[a:b]+variant[c:d]):
            result.extend((a+i,a+i+1,c+i,c+i+1) for i in range(b-a))
        else:result.append((a,b,c,d))
    return result


def document_keys(lines,images=None):
    result=[]; fence=None
    for line in lines:
        match=re.match(r'^\s{0,3}(`{3,}|~{3,})',line)
        if match:
            marker=match[1]
            if fence is None:fence=marker
            elif marker[0]==fence[0] and len(marker)>=len(fence):fence=None
            result.append(line.rstrip('\r\n'))
        else:result.append(line.rstrip('\r\n') if fence else line_key(line,images))
    return result


def merge_table_row(base, local, remote):
    if not all(x.strip().startswith('|') and len(x.splitlines())==1 for x in (base,local,remote)):
        return None
    rows=[re.split(r'(?<!\\)\|',x.rstrip('\n')) for x in (base,local,remote)]
    if len({len(x) for x in rows})!=1:return None
    cells=[]
    for b,l,r in zip(*rows):
        if line_key(l)==line_key(r):cells.append(l)
        elif line_key(l)==line_key(b):cells.append(r)
        elif line_key(r)==line_key(b):cells.append(l)
        else:return None
    return '|'.join(cells)+ ('\n' if local.endswith('\n') or remote.endswith('\n') else '')


def merge_document(base, local, remote, base_images=None, local_images=None, remote_images=None):
    """Non-overlapping base ranges merge automatically; overlaps stay explicit.

    Without an old text baseline, every different range requires a decision.
    IDs use all three texts so draft decisions only survive unchanged ranges.
    """
    left, right = local.splitlines(True), remote.splitlines(True)
    lk = document_keys(left,local_images)
    rk = document_keys(right,remote_images)
    result = []

    def add(kind, b, l, r, chosen=None, source=''):
        if not b and not l and not r:
            return
        identity = hash_text(json.dumps([b, l, r], ensure_ascii=False))[:24]
        duplicate = sum(x['id'].startswith(identity+'-') for x in result)
        result.append(dict(id=identity + '-' + str(duplicate), kind=kind, base=b, local=l,
                           remote=r, result=chosen, source=source))

    if base is None:
        for tag, a, b, c, d in difflib.SequenceMatcher(None, lk, rk, autojunk=False).get_opcodes():
            l, r = ''.join(left[a:b]), ''.join(right[c:d])
            add('same' if tag == 'equal' else 'conflict', '', l, r, l if tag == 'equal' else None)
    else:
        original = base.splitlines(True)
        bk = document_keys(original,base_images)
        changes = sorted([(a,b,c,d,'local') for a,b,c,d in edits(bk,lk)] +
                         [(a,b,c,d,'remote') for a,b,c,d in edits(bk,rk)])
        groups = []
        for change in changes:
            a,b,*_ = change
            # Adjacent replacements do not conflict. Inserts at an edited boundary do.
            if groups and (a < groups[-1][1] or
                           a == groups[-1][1] and (a == b or groups[-1][0] == groups[-1][1])):
                groups[-1][1] = max(groups[-1][1], b)
                groups[-1][2].append(change)
            else:
                groups.append([a,b,[change]])

        def apply(start, end, group, side):
            cursor, pieces = start, []
            lines = left if side == 'local' else right
            for a,b,c,d,s in group:
                if s == side:
                    pieces.extend(original[cursor:a]); pieces.extend(lines[c:d]); cursor=b
            pieces.extend(original[cursor:end])
            return ''.join(pieces)

        cursor = 0
        for start,end,group in groups:
            unchanged = ''.join(original[cursor:start])
            add('same', unchanged, unchanged, unchanged, unchanged)
            b = ''.join(original[start:end])
            l,r = apply(start,end,group,'local'), apply(start,end,group,'remote')
            sides = {x[4] for x in group}
            if len(sides) == 1:
                source = next(iter(sides)); chosen = l if source == 'local' else r
                add('auto',b,l,r,chosen,source)
            elif document_keys(l.splitlines(True),local_images) == document_keys(r.splitlines(True),remote_images):
                add('auto',b,l,r,l,'both')
            else:
                table = merge_table_row(b,l,r)
                add('auto' if table is not None else 'conflict',b,l,r,table,'both' if table is not None else '')
            cursor=end
        unchanged=''.join(original[cursor:])
        add('same',unchanged,unchanged,unchanged,unchanged)
    heading, table_header = '文档内容', ''
    for block in result:
        context = block['result'] if block['result'] is not None else block['local'] or block['remote']
        headings = re.findall(r'(?m)^#{1,6}\s+(.+)$', context)
        if headings: heading=headings[-1]
        block['heading']=heading
        block['table_header']=table_header if context.lstrip().startswith('|') else ''
        rows=context.splitlines()
        for index,line in enumerate(rows[:-1]):
            if line.startswith('|') and re.fullmatch(r'[| :\-]+', rows[index+1]):
                table_header=line+'\n'+rows[index+1]+'\n'
    return result


def apply_decisions(session):
    parts=[]
    for block in session['blocks']:
        decision=session.get('decisions',{}).get(block['id'])
        if block['kind']=='conflict' and decision is None:
            raise ValueError('请先确认所有待处理内容。')
        parts.append(decision['text'] if decision else block['result'])
    return ''.join(parts)


def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,path)


def freeze_images(sync, md):
    """Keep immutable review assets beside the original document, not in a temp dir."""
    for src in sync.images.local_signature(md):
        data=sync.images.read_file(sync.images.file_for(src))
        from image_sync import sha, image_type
        extension,_=image_type(data)
        directory=sync.file.with_name(sync.file.stem+'.assets')/'review'
        directory.mkdir(parents=True,exist_ok=True)
        target=directory/(sha(data)+'.'+extension)
        if not target.exists(): target.write_bytes(data)
        elif target.read_bytes()!=data: raise RuntimeError('审阅图片副本已被修改，请检查文件。')
        replacement=quote(target.relative_to(sync.file.parent).as_posix(),safe='/')
        from urllib.parse import unquote
        for original in {src,unquote(src)}:
            # Rewrite image references, never literal prose or code containing a filename.
            md=re.sub(r'(!\[[^\]]*\]\(<?)'+re.escape(original)+r'(?=>?(?:\s+["\x27].*?["\x27])?\))',
                      lambda match:match[1]+replacement,md)
            md=re.sub(r'(<img\b[^>]*\bsrc=["\x27])'+re.escape(original)+r'(?=["\x27])',
                      lambda match:match[1]+replacement,md,flags=re.I)
    return md


class ReviewService:
    def __init__(self,sync):
        self.sync=sync
        self.path=sync.data/'conflict.json'
        self.journal=sync.data/'resolution.json'

    def load(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else None

    def capture(self, md, page):
        from sync_engine import storage_to_md, md_to_storage, semantic
        s=self.sync
        previous=self.load() or {}
        original_signature=s.images.remote_signature(page,refresh=True)
        remote=storage_to_md(page['body']['storage']['value'],s.images)
        if semantic(md_to_storage(remote,s.images,upload=False))!=semantic(page['body']['storage']['value']):
            raise RuntimeError('网页含无法完整转换的内容，已保留两端；请先检查网页格式。')
        local_signature=s.images.local_signature(md)
        remote_signature=s.images.remote_signature(page,refresh=True)
        if original_signature!=remote_signature:
            raise RuntimeError('网页图片仍在修改，请稍后重新打开审阅。')
        local=freeze_images(s,md)
        base=(s.state or {}).get('baseline_md')
        blocks=merge_document(base,local,remote,(s.state or {}).get('baseline_images'),
                              s.images.local_signature(local),s.images.local_signature(remote))
        directory=s.data/'conflicts'/time.strftime('%Y%m%d-%H%M%S')
        directory=directory.with_name(directory.name+'-'+str(time.time_ns())[-8:])
        directory.mkdir(parents=True)
        for name,text in [('local.md',md),('remote.md',remote),('remote.storage.xml',page['body']['storage']['value'])]:
            (directory/name).write_text(text,encoding='utf-8')
        session=dict(schema=1,created=time.time(),snapshot=str(time.time_ns()),
            file=str(s.file),page=str(s.config['page_id']),base_url=s.config.get('base_url',''),
            local_original=md,local=local,remote=remote,remote_page=page,
            local_images=local_signature,remote_images=remote_signature,
            blocks=blocks,decisions={},backup=str(directory),has_baseline=base is not None,phase='pending')
        known={x['id']:x for x in blocks}
        session['decisions']={k:v for k,v in previous.get('decisions',{}).items() if k in known}
        save(self.path,session)
        return session

    def current(self,session,page=None):
        s=self.sync
        if (session['file']!=str(s.file) or session['page']!=str(s.config['page_id'])
                or session['base_url']!=s.config.get('base_url','')):
            raise RuntimeError('待确认内容不属于当前文档。')
        page=page or s.client.get()
        return (s.read()==session['local_original'] and
            s.images.local_signature(s.read())==session['local_images'] and
            page['version']['number']==session['remote_page']['version']['number'] and
            page['body']['storage']['value']==session['remote_page']['body']['storage']['value'] and
            s.images.remote_signature(page,refresh=True)==session['remote_images'])

    def prepare(self):
        s=self.sync; s.images.begin()
        if self.recover(): return self.load()
        old=self.load()
        if old and old.get('phase')=='resolved': return old
        page=s.client.get()
        if old and self.current(old,page): return old
        return self.capture(s.read(),page)

    def save_decision(self,session,identity,source,text=None):
        current=self.load()
        if not current or current['snapshot']!=session['snapshot']: raise ReviewChanged('内容已更新，请重新查看。')
        block=next(x for x in session['blocks'] if x['id']==identity)
        if source=='reset': session['decisions'].pop(identity,None)
        else:
            value=(block[source] if source in ('local','remote') else
                   block['local'].rstrip()+('\n' if block.get('table_header') else '\n\n')+block['remote'] if source=='both' else text)
            if value is None: raise ValueError('请输入调整后的内容。')
            session['decisions'][identity]=dict(source=source,text=value)
        save(self.path,session)

    def submit(self,session):
        from sync_engine import md_to_storage, semantic
        s=self.sync; s.images.begin()
        if self.recover(): return self.load()
        stored=self.load()
        if not stored or stored['snapshot']!=session['snapshot']:
            raise ReviewChanged('待确认内容已更新，请重新查看。')
        final=apply_decisions(session)
        if not final.strip(): raise ValueError('最终文档为空，请检查你的选择。')
        md_to_storage(final,raw_images=True)  # Validate before any upload.
        if not self.current(session):
            self.capture(s.read(),s.client.get())
            raise ReviewChanged('文档刚刚有了新修改。已保留仍适用的选择，请确认新增差异。')
        final_images=s.images.local_signature(final)
        storage=md_to_storage(final,s.images)
        if not self.current(session):
            self.capture(s.read(),s.client.get())
            raise ReviewChanged('准备更新时文档发生变化，请重新确认。')
        if s.images.local_signature(final)!=final_images: raise RuntimeError('选中的图片已改变，请重新确认。')
        journal=dict(snapshot=session['snapshot'],phase='prepared',final=final,storage=storage,
                     local_original=session['local_original'],local_images=session['local_images'],
                     expected_images=s.images.remote_signature(dict(session['remote_page'],body={'storage':{'value':storage}})),
                     final_images=final_images,backup=session['backup'])
        save(self.journal,journal)
        # The remote version is checked by Confluence's PUT. A retry never blindly PUTs again.
        try:
            s.client.put(session['remote_page'],storage)
            return self.finish(journal,session)
        except Exception:
            # Keep journal + decisions, including ambiguous network-success outcomes.
            raise

    def finish(self,journal,session):
        from sync_engine import semantic
        s=self.sync; page=s.client.get()
        if page['version']['number'] != session['remote_page']['version']['number']+1:
            self.capture(s.read(),page)
            raise ReviewChanged('网页在更新后又有新修改，本地未被覆盖，请确认最新内容。')
        if semantic(page['body']['storage']['value'])!=semantic(journal['storage']):
            raise ReviewChanged('网页结果与确认内容不同，已保留备份和选择，请重新检查。')
        if s.images.remote_signature(page,refresh=True)!=journal['expected_images']:
            raise ReviewChanged('网页图片又有变化，已保留选择，请重新检查。')
        journal['phase']='remote_written'; save(self.journal,journal)
        if s.read()!=journal['local_original'] or s.images.local_signature(s.read())!=journal['local_images']:
            self.capture(s.read(),page)
            raise ReviewChanged('网页已更新，但本地刚刚又被修改；本地未被覆盖，请确认新差异。')
        final=journal['final']
        if s.images.local_signature(final)!=journal['final_images']:
            raise RuntimeError('网页已更新，但选中的本地图片发生变化；请重新确认。')
        temporary=s.file.with_name(s.file.name+'.review-tmp')
        temporary.write_text(final,encoding='utf-8')
        if s.read()!=journal['local_original'] or s.images.local_signature(s.read())!=journal['local_images']:
            temporary.unlink(missing_ok=True)
            raise ReviewChanged('网页已更新，本地又有修改；已取消覆盖，请重新确认。')
        os.replace(temporary,s.file)
        s.baseline(final,page,local_images=journal['final_images'],remote_images=journal['expected_images'])
        session['phase']='resolved'; session['resolved_at']=time.time(); save(self.path,session)
        journal['phase']='complete'; save(self.journal,journal)
        return session

    def recover(self):
        if not self.journal.exists(): return False
        journal=json.loads(self.journal.read_text(encoding='utf-8'))
        if journal.get('phase')=='complete': return False
        session=self.load()
        if not session or journal['snapshot']!=session['snapshot']: return False
        from sync_engine import semantic
        s=self.sync; s.images.begin(); page=s.client.get()
        if page['version']['number'] != session['remote_page']['version']['number']+1: return False
        if semantic(page['body']['storage']['value'])!=semantic(journal['storage']): return False
        if s.read()==journal['final']:
            if s.images.local_signature(s.read())!=journal['final_images']: return False
            if s.images.remote_signature(page,refresh=True)!=journal['expected_images']: return False
            s.baseline(journal['final'],page,journal['final_images'],journal['expected_images'])
            session['phase']='resolved'; save(self.path,session)
            journal['phase']='complete'; save(self.journal,journal)
            return True
        self.finish(journal,session)
        return True
