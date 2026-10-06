"""One byte-safe traversal for release isolation and explicitly approved exports.

Git objects are immutable. Only the current operation may reuse checked contents;
every path/mode is checked independently. No persistent cache authorizes export.
"""
from __future__ import annotations
import codecs
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import queue
import threading
import subprocess
import time
from core import require, ident, config, config_snapshot, object_hash, path
import platform_runtime as platform

# High-signal credential forms; source-code regexes and placeholder names are not tokens.
SECRET = re.compile(rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}|\bsk-[A-Za-z0-9_-]{32,}|\bBearer[ \t]+[A-Za-z0-9._~-]{24,}|(?i:api[_-]?key|access[_-]?token|client[_-]?secret)["\x27 \t]*[:=]["\x27 \t]*[A-Za-z0-9._~-]{24,}')
PRIVATE_PARTS = {'.git', '.local', 'private', 'runtime'}


def canonical(relative):
    require(isinstance(relative,str) and relative and not relative.startswith('/'), 'history path must be relative')
    require(relative == PurePosixPath(relative).as_posix() and not any(p in {'', '.', '..'} for p in relative.split('/')), 'noncanonical history path')
    require(not any(ord(c)<32 or ord(c)==127 for c in relative), 'control character in history path')
    try: platform.portable_path(relative)
    except ValueError as exc: require(False, str(exc))
    return relative


@dataclass(frozen=True)
class Policy:
    name: str
    files: tuple = ()
    roots: tuple = ()
    binary_suffixes: tuple = ()
    scan_secrets: bool = True
    projections: bool = False

    def permits(self, relative, mode):
        canonical(relative)
        parts=relative.split('/')
        require(not any(p in PRIVATE_PARTS for p in parts) and not any(p=='.env' or p.startswith('.env.') for p in parts), 'private/control path cannot be exported: '+relative)
        require(relative != '.claude/settings.local.json', 'local settings cannot be exported')
        allowed=relative in self.files or any(relative==r or relative.startswith(r+'/') for r in self.roots)
        if mode=='40000':
            allowed=allowed or any(f.startswith(relative+'/') for f in self.files)
        require(allowed, ('release includes non-template history: ' if self.name=='release' else 'export policy excludes: ')+relative)
        require(mode in {'40000','100644','100755','120000'}, 'submodule/unknown mode cannot be exported')
        if mode=='120000':
            require(self.projections and re.fullmatch(r'\.(?:agents|claude)/skills/[a-z][a-z0-9-]{1,63}',relative), 'noncanonical symlink cannot be exported')

    def content(self, relative, chunks):
        binary=Path(relative).suffix in self.binary_suffixes
        decoder=codecs.getincrementaldecoder('utf-8')() if not binary else None
        tail=b''; total=0
        for chunk in chunks:
            total+=len(chunk)
            require(not SECRET.search(tail+chunk), 'credential-like content cannot be exported: '+relative)
            tail=(tail+chunk)[-256:]
            if decoder:
                try: decoder.decode(chunk,final=False)
                except UnicodeDecodeError: require(False,'binary/unreadable content requires explicit export policy: '+relative)
        if decoder:
            try: decoder.decode(b'',final=True)
            except UnicodeDecodeError: require(False,'binary/unreadable content requires explicit export policy: '+relative)
        return total


def release_policy():
    return Policy('release',files=('README.md','AGENTS.md','.gitignore','.gitattributes','requirements.txt','release.yaml','docs/company-system-guide.html','.codex/hooks.json'),
                  roots=('company','standards','skills','workflows','scripts','adapters','.agents','.claude','checks','hooks'),
                  scan_secrets=False,projections=True)


def company_policy(root, required=False):
    cfg,sha=config_snapshot(root)
    value=cfg.get('export')
    if value is None:
        require(not required,'external export policy missing; owner must define exact data scope')
        return None
    require(isinstance(value,dict) and value.get('schema_version')==1,'export policy format')
    require(cfg.get('owner') and value.get('approved_by')==cfg['owner'],'export policy requires company owner approval')
    require(set(value)<= {'schema_version','approved_by','files','roots','binary_suffixes','baseline'},'unknown export policy field')
    for field in ['files','roots','binary_suffixes']:
        require(isinstance(value.get(field),list) and all(isinstance(p,str) for p in value[field]),'export policy list missing: '+field)
    for relative in value['files']+value['roots']:canonical(relative)
    require(value['files'] or value['roots'],'empty export policy')
    require(all(re.fullmatch(r'\.[a-zA-Z0-9]+',x) for x in value['binary_suffixes']),'invalid binary suffix')
    baseline=value.get('baseline')
    require(baseline is None or isinstance(baseline,str) and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',baseline),'export baseline must be full approved SHA or null')
    policy=Policy('company',tuple(value['files']),tuple(value['roots']),tuple(value['binary_suffixes']),projections=True)
    return {'policy':policy,'baseline':baseline,'config_sha256':sha,'fingerprint':object_hash(value),'company_id':cfg['id']}


class Context:
    def __init__(self,root):
        from delivery import git_environment
        self.root=Path(root).resolve();self.env=git_environment();self.env['GIT_NO_REPLACE_OBJECTS']='1';self.processes=0
        metadata=self.git('rev-parse','--path-format=absolute','--absolute-git-dir','--git-common-dir','--show-object-format','--is-bare-repository').decode().splitlines()
        require(len(metadata)==4,'unreadable repository metadata')
        self.gitdir=Path(metadata[0]).resolve();common=Path(metadata[1]).resolve();fmt,bare=metadata[2:]
        require(bare in {'true','false'},'unreadable repository kind')
        if bare=='false':
            require(Path(self.git('rev-parse','--show-toplevel').decode('utf-8').strip()).resolve()==self.root, 'history requires the exact repository root')
        else:
            require(self.gitdir==self.root,'history requires the exact bare repository root')
        self.common=common
        self.location={'gitdir': str(self.gitdir), 'common': str(common),
                       'identity': [(p.stat().st_dev,p.stat().st_ino) for p in (self.root,self.gitdir,common)]}
        for relative in ['shallow','info/grafts','objects/info/alternates']:
            file=common/relative
            require(not file.exists() or file.stat().st_size==0,'incomplete/rewritten/alternate history cannot authorize export')
        require(not self.git('for-each-ref','--format=%(refname)','refs/replace').strip(),'replace refs cannot authorize export')
        partial=self.git('config','--get-regexp',r'^(extensions\.partialclone|remote\..*\.promisor)$',check=False)
        require(not partial.strip(),'partial/promisor history cannot authorize export')
        require(fmt in {'sha1','sha256'},'unsupported Git object format');self.oid_bytes=20 if fmt=='sha1' else 32
        self.env['GIT_TERMINAL_PROMPT']='0'

    def git(self,*args,check=True):
        from delivery import git
        self.processes+=1
        selectors=('--no-replace-objects','--git-dir',str(self.gitdir),'--work-tree',str(self.root)) if hasattr(self,'gitdir') else ('--no-replace-objects',)
        p=git(self.root,*selectors,*args,check=False,binary=True,timeout=60)
        require(p.returncode==0 or not check and p.returncode==1,'history Git operation failed; missing object or invalid repository')
        return p.stdout


class Batch:
    def __init__(self,ctx):
        self.ctx=ctx;ctx.processes+=1
        self.proc=subprocess.Popen(['git','--git-dir',str(ctx.gitdir),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=ctx.env)
        self.queue=queue.Queue(maxsize=2);self.stop=threading.Event()
        def pump():
            try:
                while not self.stop.is_set():
                    value=os.read(self.proc.stdout.fileno(),65536)
                    while not self.stop.is_set():
                        try:self.queue.put(value,timeout=.1);break
                        except queue.Full:pass
                    if not value:return
            except OSError:
                while not self.stop.is_set():
                    try:self.queue.put(b'',timeout=.1);return
                    except queue.Full:pass
        self.thread=threading.Thread(target=pump,daemon=True);self.thread.start()
        self.buffer=bytearray();self.deadline=0

    def fill(self):
        try:value=self.queue.get(timeout=max(0,self.deadline-time.monotonic()))
        except queue.Empty:require(False,'history object read timed out')
        require(value,'history object stream truncated');self.buffer.extend(value)

    def read(self,n):
        while len(self.buffer)<n:self.fill()
        result=bytes(self.buffer[:n]);del self.buffer[:n];return result

    def header(self,oid):
        require(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',oid),'invalid object ID')
        self.deadline=time.monotonic()+60
        try:self.proc.stdin.write((oid+'\n').encode());self.proc.stdin.flush()
        except (BrokenPipeError,OSError):require(False,'history object process failed')
        while b'\n' not in self.buffer:
            require(len(self.buffer)<1024,'invalid history object header');self.fill()
        line,self.buffer=self.buffer.split(b'\n',1)
        fields=line.split();require(len(fields)==3 and fields[0].decode()==oid and fields[2].isdigit(),'missing/invalid history object')
        size=int(fields[2])
        require(fields[1]!=b'tree' or size<=16*1024*1024,'history tree exceeds bounded parser memory; explicit preparation required')
        return fields[1].decode(),size

    def chunks(self,oid,expected):
        typ,left=self.header(oid);require(typ==expected,'history object type mismatch')
        while left:
            count=min(65536,left);yield self.read(count);left-=count
        require(self.read(1)==b'\n','history object framing mismatch')

    def value(self,oid,expected):return b''.join(self.chunks(oid,expected))

    def close(self):
        self.stop.set();self.proc.stdin.close()
        try:self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=2)
        self.thread.join(timeout=2)
        self.proc.stdout.close()


def scan(root,policy,tips=('HEAD',),all_refs=False,baseline=None,trees=()):
    ctx=Context(root);reader=Batch(ctx)
    entries_cache={};contents=set();coverage=set();total=0;blobids=set()
    def walk(tree,prefix=''):
        if tree not in entries_cache:
            raw=reader.value(tree,'tree');entries=[];offset=0
            while offset<len(raw):
                space=raw.index(b' ',offset);nul=raw.index(b'\0',space)
                mode=raw[offset:space].decode();name=raw[space+1:nul].decode('utf-8');end=nul+1+ctx.oid_bytes
                require(end<=len(raw),'truncated history tree')
                entries.append((name,mode,raw[nul+1:end].hex()));offset=end
            entries_cache[tree]=entries
        for name,mode,oid in entries_cache[tree]:
            require('/' not in name,'malformed history tree path')
            relative=prefix+name;policy.permits(relative,mode)
            if mode=='40000':yield from walk(oid,relative+'/')
            else:yield relative,mode,oid
    try:
        if all_refs:
            refs=dict(row.split(' ',1) for row in ctx.git('for-each-ref','--format=%(refname) %(objectname)').decode().splitlines())
            # Peel immutable captured IDs; a moving ref cannot change this checked snapshot.
            heads=[ctx.git('rev-parse','--verify','--end-of-options',oid+'^{commit}').decode().strip() for oid in refs.values()]
        else:heads=[ctx.git('rev-parse','--verify','--end-of-options',t+'^{commit}').decode().strip() for t in tips]
        baseline_entries={}
        if baseline:
            require(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',baseline),'invalid approved baseline')
            for head in heads:ctx.git('merge-base','--is-ancestor',baseline,head)
            base_tree=ctx.git('rev-parse',baseline+'^{tree}').decode().strip()
            baseline_entries={p:(m,o) for p,m,o in walk(base_tree)}
        args=['rev-list','--parents','--format=%T','--topo-order','--reverse',*heads]
        if baseline:args+=['^'+baseline]
        output=ctx.git(*args).decode().splitlines() if heads else []
        require(len(output)%2==0 and all(row.startswith('commit ') for row in output[::2]),'unreadable commit/tree inventory')
        rows=[row.removeprefix('commit ') for row in output[::2]]
        commits=[row.split()[0] for row in rows]
        if baseline:
            anchored={baseline}
            for row in rows:
                fields=row.split();require(any(parent in anchored for parent in fields[1:]),'foreign history lacks approved baseline ancestry');anchored.add(fields[0])
        tree_ids=set(trees)|set(output[1::2])
        for tree in sorted(tree_ids):
            inventory=list(walk(tree));paths={p for p,_,_ in inventory}
            try:platform.path_collisions(paths)
            except ValueError as exc:require(False,str(exc))
            for relative,mode,oid in inventory:
                coverage.add((relative,mode,oid));blobids.add(oid)
                if mode=='120000':
                    content=reader.value(oid,'blob')
                    skill=relative.rsplit('/',1)[1];ident(skill)
                    require(content==('../../skills/'+skill).encode() and f'skills/{skill}/SKILL.md' in paths,'symlink projection target is not canonical')
                    continue
                binary=Path(relative).suffix in policy.binary_suffixes
                key=(oid,binary)
                if not policy.scan_secrets or baseline_entries.get(relative)==(mode,oid) or key in contents:continue
                total+=policy.content(relative,reader.chunks(oid,'blob'));contents.add(key)
        return {'commits':len(commits),'blobs':len(blobids),'bytes':total,'git_processes':ctx.processes,
                'coverage_sha256':hashlib.sha256(json.dumps(sorted(coverage)).encode()).hexdigest(),
                'policy':policy.name,'content_checked':policy.scan_secrets, **({'ref_tips':refs} if all_refs else {})}
    finally:reader.close()


def unchanged(root,snapshot):
    if snapshot is not None:
        current=company_policy(root,required=True)
        require(current['config_sha256']==snapshot['config_sha256'] and current['fingerprint']==snapshot['fingerprint'],'export policy/config changed during operation')


def repository_location(root):
    """Short identity check before a mutation; scans pin all object reads to this store."""
    from delivery import git
    root=Path(root).resolve()
    gitdir=Path(git(root,'rev-parse','--absolute-git-dir')).resolve()
    common=Path(git(root,'rev-parse','--git-common-dir'))
    common=(common if common.is_absolute() else root/common).resolve()
    if gitdir!=root:
        require(Path(git(root,'rev-parse','--show-toplevel')).resolve()==root,'delivery requires the exact repository root')
    return {'gitdir':str(gitdir),'common':str(common),
            'identity':[(p.stat().st_dev,p.stat().st_ino) for p in (root,gitdir,common)]}


def working(root,relatives,snapshot):
    if snapshot is None:return
    policy=snapshot['policy']
    for relative in relatives:
        from delivery import commit_path
        file=commit_path(root,relative)
        policy.permits(relative,'120000' if file.is_symlink() else '100644')
        if file.is_symlink():
            continue  # Canonical metadata is checked above and in the prepared Git tree.
        if file.is_file():
            with file.open('rb') as stream:
                policy.content(relative,iter(lambda:stream.read(65536),b''))
    unchanged(root,snapshot)


def preview_tree(root,relatives,head=None):
    """Stage only into an owned temporary index; failed guard never touches user index."""
    import tempfile
    from delivery import git
    with tempfile.TemporaryDirectory(prefix='company-export-index-') as directory:
        index=Path(directory)/'index'
        git(root,'read-tree',head if head else '--empty',private_index=index)
        git(root,'add','--',*relatives,private_index=index)
        return git(root,'write-tree',private_index=index)
