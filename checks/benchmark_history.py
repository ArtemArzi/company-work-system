"""Synthetic equivalent-workload reference; never reads installed company history."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts'))
from delivery import git_environment


def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=git_environment(), timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace'))
    return p.stdout


def reference(root):
    """Old full-tree/per-blob process pattern, same synthetic path/content gate."""
    commits = git(root, 'rev-list', 'HEAD').splitlines()
    processes = 1
    seen = set(); entries = set(); total = 0
    for commit in commits:
        rows = git(root, 'ls-tree', '-r', '-z', commit.decode()).split(b'\0'); processes += 1
        for row in rows:
            if not row:
                continue
            info, path = row.split(b'\t', 1)
            mode, typ, oid = info.split()
            assert mode == b'100644' and typ == b'blob' and path.startswith(b'files/')
            entries.add((path.decode(), mode.decode(), oid.decode()))
            if oid not in seen:
                content = git(root, 'cat-file', 'blob', oid.decode()); processes += 1
                assert b'fixture payload' in content
                total += len(content); seen.add(oid)
    return {'commits': len(commits), 'blobs': len(seen), 'bytes': total, 'git_processes': processes,
            'coverage_sha256': hashlib.sha256(json.dumps(sorted(entries)).encode()).hexdigest()}


def fixture(root, count, files=40):
    root.mkdir(); (root/'files').mkdir()
    git(root,'init','-b','main');git(root,'config','user.name','Synthetic Benchmark');git(root,'config','user.email','benchmark@example.invalid')
    for n in range(files):
        (root/'files'/f'{n}.txt').write_text(f'fixture payload initial {n}\n')
    git(root,'add','.');git(root,'commit','-m','Synthetic initial tree')
    for n in range(1,count):
        (root/'files'/f'{n % files}.txt').write_text(f'fixture payload revision {n}\n')
        git(root,'add','.');git(root,'commit','-m',f'Synthetic revision {n}')


def measure(fn, root, runs):
    expected=fn(root); values=[]
    for _ in range(runs):
        started=time.perf_counter(); actual=fn(root); values.append((time.perf_counter()-started)*1000)
        assert actual==expected,'unstable correctness/workload'
    return {**expected,'runs':runs,'median_ms':round(statistics.median(values),3),
            'p95_ms':round(sorted(values)[max(0,int(runs*.95)-1)],3)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--candidate',action='store_true');p.add_argument('--runs',type=int,default=20)
    args=p.parse_args(); results=[]
    source_hashes={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [ROOT/'template/scripts/history.py',ROOT/'checks/benchmark_history.py']}
    with tempfile.TemporaryDirectory(prefix='company-history-benchmark-') as tmp:
        for count,files in [(1,1),(1,40),(20,40),(100,40)]:
            root=Path(tmp)/f'{count}-{files}';fixture(root,count,files)
            entry={'commits':count,'files':files,'reference':measure(reference,root,args.runs)}
            if args.candidate:
                from history import scan, Policy
                policy=Policy('benchmark',roots=('files',), scan_secrets=True)
                candidate=measure(lambda r:scan(r,policy),root,args.runs)
                for key in ['commits','blobs','bytes','coverage_sha256']:
                    assert candidate[key]==entry['reference'][key],(key,candidate,entry)
                entry['small_case_bound_pass']=all(candidate[k] <= entry['reference'][k]+max(10,entry['reference'][k]*.2) for k in ['median_ms','p95_ms']) if count==1 else None
                entry.update(candidate=candidate,speedup=round(entry['reference']['median_ms']/candidate['median_ms'],3))
            results.append(entry)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'scope':'synthetic company histories; naive reference of repeated ls-tree/cat-file, NOT a live Work benchmark','git':git(ROOT,'--version').decode().strip(),'python':sys.version.split()[0],'source_hashes':source_hashes,'variants':results},indent=2)+'\n')
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
