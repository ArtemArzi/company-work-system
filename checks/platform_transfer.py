"""Actual OS-to-OS synthetic backup proof. Product-only test support."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT))
from checks.test_system import Fixture
from core import config, digest, load, write
import delivery
import hooks
import lifecycle
import operations

BASELINE = '7b38b92cb0f792481b1a27652f31a4df13f457c2'
EXPECTED = {f'synthetic-backup-{osname}/current/manifest.json' for osname in
            ['ubuntu-24.04', 'windows-2022', 'macos-14']} | {'synthetic-backup-ubuntu-24.04/legacy/manifest.json'}
RAW = 'Сохранить исходные bytes\r\n'.encode('utf-8')


def transfer_inventory(source):
    found = {file.relative_to(source).as_posix() for file in source.rglob('manifest.json')}
    if found != EXPECTED:
        raise RuntimeError(f'OS backup inventory differs: missing={sorted(EXPECTED-found)}, extra={sorted(found-EXPECTED)}')
    return [source / relative for relative in sorted(EXPECTED)]


def required_scenarios(manifest, task):
    required = {'.codex/hooks.json', '.system/hooks-project-codex.json',
                'company/projects/transfer-source.md', f'work/{task}/task.json'}
    if not required <= set(load(manifest)['files']):
        raise RuntimeError('Missing historical task/settings/proof/raw source in backup')


def prepared_cli(root):
    """Exercise the managed interpreter with the actual company CLI, no stub."""
    entry = (['powershell.exe', '-NoProfile', '-File', str(root / 'scripts/run.ps1')]
             if os.name == 'nt' else ['/bin/sh', str(root / 'scripts/run.sh')])
    results = {}
    for action in ['setup', 'setup', 'bootstrap-status', 'doctor', 'context']:
        process = subprocess.run(entry + [action], cwd=root, capture_output=True, timeout=360)
        if process.returncode:
            raise RuntimeError(f'Prepared real CLI {action} failed: {process.stderr.decode("utf-8", errors="replace")[-1500:]}')
        value = json.loads(process.stdout)
        if action == "setup" and action in results and value.get("changed") is not False:
            raise RuntimeError("Repeated setup changed an already prepared company")
        results[action] = value
    if results['doctor']['status'] != 'ready' or results['context']['company_id'] != 'test-company':
        raise RuntimeError('Prepared real CLI did not read adapted company')
    if Path(results['doctor']['interpreter']).resolve() != Path(results['setup']['python']).resolve():
        raise RuntimeError('Real CLI used a different interpreter')
    print(json.dumps({'prepared_real_cli':'pass', 'os':sys.platform, 'actions':list(results),
                     'interpreter':results['doctor']['interpreter']}))


def old_cli(code, root, *args):
    process = subprocess.run([sys.executable, str(code / 'scripts/system.py'), '--root', str(root), *args],
                             capture_output=True, encoding='utf-8', timeout=120)
    if process.returncode:
        raise RuntimeError(process.stderr[-1500:])
    return json.loads(process.stdout)


def old_code(destination):
    """Materialize the actual released source, never a rewritten legacy fixture."""
    destination.mkdir(parents=True)
    for row in delivery.git(PRODUCT, 'ls-tree', '-r', '-z', BASELINE + ':template', binary=True).split(b'\0'):
        if not row: continue
        header, rawpath = row.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        relative = rawpath.decode('utf-8')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = delivery.git(PRODUCT, 'cat-file', 'blob', oid, binary=True)
        if mode == '120000' and os.name != 'nt': target.symlink_to(data.decode(), target_is_directory=True)
        else: target.write_bytes(data)
    if load(destination / 'release.yaml')['version'] != '1.2.0':
        raise RuntimeError('Legacy release identity differs')
    return destination


def produce(destination, legacy=False):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    fixture = Fixture(); fixture.setUp()
    try:
        fixture.task(); operations.execute(fixture.root, 'test-task', fixture.artifact(), fixture.critical())
        raw = fixture.root / 'company/projects/transfer-source.md'
        raw.write_bytes(RAW)
        if hooks.project(fixture.root, 'codex', apply=True, enabled=True)['status'] != 'projected':
            raise RuntimeError('Synthetic hook projection did not apply')
        fixture.seed()
        if os.environ.get('CWS_BOOTSTRAP_NETWORK') == '1':
            prepared_cli(fixture.root)
        lifecycle.backup(fixture.root, destination / 'current')
        required_scenarios(destination / 'current/manifest.json', 'test-task')
        if legacy:
            code = old_code(destination / 'legacy-code')
            # A distinct real1.2 company; old commands author its historical task.
            cfg = config(code); cfg.update(id='test-company', owner='test-owner')
            cfg['permissions']['local_work'] = True
            cfg['sources']['test-data'] = {'type': 'export', 'path': 'company/sources/input.json', 'approved_by': 'test-owner',
                'account': 'synthetic-export', 'scope': 'synthetic-scope', 'period': '2026-10-01/2026-10-05', 'unit': 'test-units',
                'max_age_seconds': 3600, 'max_pages': 3, 'timeout_seconds': 1}
            write(code / 'company/config.yaml', cfg)
            # The artifact binds these exact source bytes, including observed_at.
            (code / 'company/sources/input.json').write_bytes((fixture.root / 'company/sources/input.json').read_bytes())
            (code / 'company/projects/transfer-source.md').write_bytes(RAW)
            acceptance = destination / 'acceptance.json'; write(acceptance, {'kind':'metrics', 'expected_count':2, 'expected_total':5,
                'period':'2026-10-01/2026-10-05', 'unit':'test-units'})
            artifact = destination / 'artifact.json'; write(artifact, fixture.artifact())
            critical = destination / 'critical.json'; write(critical, fixture.critical())
            old_cli(code, code, 'intake', 'legacy-task', '--request', 'Preserve original historical evidence', '--owner', 'test-owner',
                    '--acceptance', str(acceptance), '--confirmed', '--input', 'company/sources/input.json')
            old_cli(code, code, 'execute', 'legacy-task', '--artifact', str(artifact), '--critical', str(critical))
            if old_cli(code, code, 'hooks-project', 'codex', '--apply', '--enabled')['status'] != 'projected':
                raise RuntimeError('Original1.2 hook projection did not apply')
            delivery.git(code, 'init', '-b', 'main'); delivery.identity(code)
            delivery.git(code, 'add', '.'); delivery.git(code, 'commit', '-m', 'Actual1.2 synthetic company with historical task')
            old_cli(code, code, 'backup', str(destination / 'legacy'))
            required_scenarios(destination / 'legacy/manifest.json', 'legacy-task')
            # Code and request candidates are test machinery, not transfer inputs.
            shutil.rmtree(code)
            for file in [acceptance, artifact, critical]: file.unlink()
        print(json.dumps({'status':'produced', 'os':sys.platform, 'legacy_source':BASELINE if legacy else None}))
    finally:
        fixture.tmp.cleanup()


def verify(source, destination, old_reader=False):
    source, destination = source.resolve(), destination.resolve()
    manifests = transfer_inventory(source)
    old = old_code(destination / 'legacy-reader') if old_reader else None
    proofs = []
    for manifest in manifests:
        backup = manifest.parent
        task = 'legacy-task' if backup.name == 'legacy' else 'test-task'
        required_scenarios(manifest, task)
        name = backup.relative_to(source).as_posix().replace('/', '-')
        restored = destination / ('current-' + name)
        result = lifecycle.restore(backup, restored)
        # Restore itself checks all exact bytes and historical validation.
        cfg = config(restored)
        if cfg['id'] != 'test-company' or not (restored / f'work/{task}/task.json').is_file():
            raise RuntimeError('Transferred historical task/company missing')
        if (restored / 'company/projects/transfer-source.md').read_bytes() != RAW:
            raise RuntimeError('Raw CRLF source changed during transfer')
        proofs.append({'source':name, 'reader':'1.3', 'status':result['status'], 'head':result['head']})
        if old:
            recovered = destination / ('old-' + name)
            verdict = old_cli(old, old, 'restore', str(backup), str(recovered))
            if (recovered / 'company/projects/transfer-source.md').read_bytes() != RAW:
                raise RuntimeError('Original1.2 reader changed raw source')
            proofs.append({'source':name, 'reader':'original1.2-Linux', 'status':verdict['status']})
    if len(proofs) != (8 if old_reader else 4): raise RuntimeError('Transfer coverage differs')
    print(json.dumps({'status':'verified', 'os':sys.platform, 'proofs':proofs,
                     'boundary':'synthetic tracked bytes/history/settings; no provider or human/business proof'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('produce'); create.add_argument('destination', type=Path); create.add_argument('--legacy', action='store_true')
    check = sub.add_parser('verify'); check.add_argument('source', type=Path); check.add_argument('destination', type=Path); check.add_argument('--old-reader', action='store_true')
    args = parser.parse_args()
    if args.action == 'produce': produce(args.destination, args.legacy)
    else: verify(args.source, args.destination, args.old_reader)
