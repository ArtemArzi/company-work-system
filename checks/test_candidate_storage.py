"""Owned temporary copies stay bounded; uncertain work survives real local Git."""
import json
import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import contextmanager
from unittest.mock import patch
from types import SimpleNamespace

from checks.test_system import Fixture, delivery, lifecycle, Rejected, TEMPLATE
from checks import test_delivery_completion as completion_checks
from checks import test_system as system_checks
import candidate_storage
import platform_runtime


class CandidateStorage(Fixture):
    prepared = completion_checks.Completion.prepared
    finish = completion_checks.Completion.finish

    def records(self):
        return candidate_storage.status(self.root)['candidates']

    def next_candidate(self, remote):
        return Path(delivery.prepare(self.root, str(remote), 'main')['path'])

    def test_successive_completed_copies_are_bounded_and_receipts_survive(self):
        remote, previous = self.prepared()
        for _ in range(5):
            result = self.finish(remote, previous)
            self.assertTrue(result['workspace_reconciled'])
            self.assertEqual(result['storage_status'], 'sealed', result)
            current = self.next_candidate(remote)
            self.assertFalse(previous.exists())
            self.assertEqual(len(list((self.root / '.local/delivery').glob('*/.git'))), 1)
            closed = [r for r in self.records() if r['state'] == 'closed']
            self.assertTrue(closed)
            self.assertTrue(closed[-1]['receipt']['workspace_reconciled'])
            self.assertEqual(delivery.git(self.root, 'status', '--porcelain'), '')
            previous = current

    def test_changed_after_seal_and_unknown_ignored_data_are_retained(self):
        for when in ('before', 'after'):
            with self.subTest(when=when):
                # Each scenario uses its own source and proof records.
                other = CandidateStorage('runTest'); other.setUp()
                try:
                    remote, candidate = other.prepared()
                    private = candidate / '.env'
                    if when == 'before': private.write_text('Unique private bytes')
                    result = other.finish(remote, candidate)
                    self.assertTrue(result['workspace_reconciled'])
                    if when == 'before': self.assertEqual(result['storage_status'], 'retained')
                    if when == 'after': private.write_text('Unique private bytes')
                    other.next_candidate(remote)
                    self.assertEqual(private.read_text(), 'Unique private bytes')
                    self.assertNotEqual(other.records()[0]['state'], 'closed')
                finally:
                    other.doCleanups()

    def test_repeat_finish_keeps_seal_without_duplicate_push_or_new_clone(self):
        remote, candidate = self.prepared()
        first = self.finish(remote, candidate)
        self.assertEqual(first['storage_status'], 'sealed', first)
        with patch.object(delivery, 'git', wraps=delivery.git) as calls:
            repeated = self.finish(remote, candidate)
        self.assertTrue(repeated['workspace_reconciled'])
        self.assertEqual(repeated['storage_status'], 'sealed', repeated)
        self.assertFalse(any('push' in c.args[1:] for c in calls.call_args_list))
        self.next_candidate(remote)
        self.assertFalse(candidate.exists())

    def test_dangling_commit_and_blob_before_seal_are_not_lost(self):
        for kind in ('blob', 'commit', 'tree'):
            with self.subTest(kind=kind):
                other = CandidateStorage('runTest'); other.setUp()
                try:
                    remote, candidate = other.prepared()
                    raw = b'Unique unreferenced work\n'
                    if kind == 'commit':
                        tree = delivery.git(candidate, 'rev-parse', 'HEAD^{tree}')
                        raw = (f'tree {tree}\nauthor Synthetic <test@example.invalid> 1700000000 +0000\n'
                               'committer Synthetic <test@example.invalid> 1700000000 +0000\n\nUnique orphan\n').encode()
                    elif kind == 'tree':
                        child = delivery.git(candidate, 'rev-parse', 'HEAD:company/README.md')
                        raw = b'100644 unique-hidden.txt\0' + bytes.fromhex(child)
                    oid = subprocess.check_output(['git', '-C', str(candidate), 'hash-object', '-w', '-t', kind, '--stdin'],
                                                  input=raw, env=delivery.git_environment()).decode().strip()
                    result = other.finish(remote, candidate)
                    self.assertTrue(result['workspace_reconciled'])
                    self.assertEqual(result['storage_status'], 'retained', result)
                    other.next_candidate(remote)
                    self.assertEqual(delivery.git(candidate, 'cat-file', '-t', oid), kind)
                finally:
                    other.doCleanups()

    def proof_candidate(self):
        (self.root / 'work').mkdir(exist_ok=True)
        (self.root / 'work/kept.md').write_text('Protected synthetic work')
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        delivery.git(self.root, 'remote', 'add', 'origin', str(remote))
        with lifecycle._company_candidate(self.root, self.base / 'tree-proof',
                                          delivery.git(self.root, 'rev-parse', 'HEAD')) as (candidate, _, issues):
            self.assertFalse(issues)
            return remote, candidate

    def test_saved_index_tree_is_idempotent_and_corruption_prevents_retirement(self):
        remote, candidate = self.proof_candidate()
        first = candidate_storage.preserve_index_tree(candidate)
        record = next(r for r in self.records() if r['candidate'] == str(candidate))
        repeated = candidate_storage.preserve_index_tree(candidate)
        self.assertEqual(first['tree'], repeated['tree'])
        self.assertEqual(len(record['tree_proofs']), 1)
        current = next(r for r in self.records() if r['id'] == record['id'])
        self.assertEqual(current['tree_proofs'], record['tree_proofs'])
        self.assertEqual(current['updated_at'], record['updated_at'])
        file = self.root / '.local/cws-candidates/records' / (record['id'] + '.json')
        corrupted = json.loads(file.read_text())
        corrupted['tree_proofs'][first['tree']] = 'corrupted-base64'
        file.write_text(json.dumps(corrupted))
        result = self.finish(remote, candidate)
        self.assertTrue(result['workspace_reconciled'])
        self.assertEqual(result['storage_status'], 'retained', result)
        self.next_candidate(remote)
        self.assertTrue(candidate.is_dir())

    def test_saved_tree_does_not_authorize_missing_child_blob(self):
        remote, candidate = self.proof_candidate()
        file = candidate / 'company/README.md'; original = file.read_bytes()
        file.write_bytes(b'Unique abandoned staged work\n')
        delivery.git(candidate, 'add', '--', 'company/README.md')
        child = delivery.git(candidate, 'rev-parse', ':company/README.md')
        candidate_storage.preserve_index_tree(candidate)
        # Restore only the generated fixture's index/working file; the object
        # remains as the sole copy of the abandoned staged payload.
        delivery.git(candidate, 'read-tree', 'HEAD'); file.write_bytes(original)
        result = self.finish(remote, candidate)
        self.assertTrue(result['workspace_reconciled'])
        self.assertEqual(result['storage_status'], 'retained', result)
        self.next_candidate(remote)
        self.assertEqual(delivery.git(candidate, 'cat-file', 'blob', child), 'Unique abandoned staged work')

    def test_unknown_or_changed_git_admin_data_before_seal_is_retained(self):
        for relative in ('info/unique-private.txt', 'description', 'objects/info/unique-private.txt'):
            with self.subTest(relative=relative):
                other = CandidateStorage('runTest'); other.setUp()
                try:
                    remote, candidate = other.prepared()
                    private = candidate / '.git' / relative
                    private.parent.mkdir(parents=True, exist_ok=True)
                    private.write_bytes(b'ONLY COPY OF PRIVATE WORK\n')
                    result = other.finish(remote, candidate)
                    self.assertTrue(result['workspace_reconciled'])
                    self.assertEqual(result['storage_status'], 'retained', result)
                    other.next_candidate(remote)
                    self.assertEqual(private.read_bytes(), b'ONLY COPY OF PRIVATE WORK\n')
                finally:
                    other.doCleanups()

    def test_failed_prepare_without_directory_retries_exact_reservation(self):
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        destination = self.base / 'retry-copy'
        native = delivery.git
        def fail_clone(root, *args, **kwargs):
            if args and args[0] == 'clone':
                raise delivery.GitFailure('clone', code='network_connection')
            return native(root, *args, **kwargs)
        with patch.object(delivery, 'git', side_effect=fail_clone):
            with self.assertRaises(delivery.GitFailure):
                delivery.prepare(self.root, str(remote), 'main', destination)
        self.assertFalse(destination.exists())
        original_id = self.records()[0]['id']
        result = delivery.prepare(self.root, str(remote), 'main', destination)
        self.assertEqual(Path(result['path']), destination)
        self.assertTrue((destination / '.git/HEAD').is_file())
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]['id'], original_id)

    def test_cli_release_only_closes_missing_unbound_preparation(self):
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        destination = self.base / 'failed-copy'
        native = delivery.git
        def fail_clone(root, *args, **kwargs):
            if args and args[0] == 'clone': raise delivery.GitFailure('clone', code='network_connection')
            return native(root, *args, **kwargs)
        with patch.object(delivery, 'git', side_effect=fail_clone):
            with self.assertRaises(delivery.GitFailure): delivery.prepare(self.root, str(remote), 'main', destination)
        token = self.records()[0]['id']
        # A partial directory can contain the only remaining copy of work.
        destination.mkdir(); private = destination / 'private.txt'; private.write_text('Keep this')
        self.cli('candidate-release', token, success=False)
        self.assertEqual(private.read_text(), 'Keep this')
        self.assertEqual(self.records()[0]['state'], 'reserved')
        # This test's owner explicitly removes only its generated partial data.
        private.unlink(); destination.rmdir()
        changed = self.root / 'company/changed.md'; changed.write_text('A newer source version')
        delivery.commit(self.root, ['company/changed.md'], 'Synthetic source advances')
        self.cli('candidate-release', token)
        self.assertEqual(self.records()[0]['state'], 'closed')
        current = self.next_candidate(remote)
        prepared = next(r for r in self.records() if r['candidate'] == str(current))
        self.cli('candidate-release', prepared['id'], success=False)
        self.assertTrue((current / '.git/HEAD').is_file())

    def test_linked_worktree_at_same_target_prevents_retirement(self):
        remote, candidate = self.prepared()
        linked = self.base / 'dependent-tree'
        delivery.git(candidate, 'worktree', 'add', '--detach', str(linked), 'HEAD')
        result = self.finish(remote, candidate)
        self.assertTrue(result['workspace_reconciled'])
        self.assertEqual(result['storage_status'], 'retained', result)
        self.next_candidate(remote)
        self.assertTrue(candidate.is_dir())
        self.assertEqual(delivery.git(linked, 'rev-parse', 'HEAD'), result['local_head'])

    def test_keep_and_four_unfinished_copies_stop_a_fifth_clone(self):
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        kept = Path(delivery.prepare(self.root, str(remote), 'main', keep_candidate=True)['path'])
        self.assertTrue(self.finish(remote, kept)['workspace_reconciled'])
        for _ in range(3): self.next_candidate(remote)
        destination = self.base / 'fifth'
        with self.assertRaises(Rejected): delivery.prepare(self.root, str(remote), 'main', destination)
        self.assertFalse(destination.exists())
        self.assertTrue(kept.is_dir())
        self.assertEqual(len([r for r in self.records() if r['state'] != 'closed']), 4)

    def test_legacy_clone_is_never_swept(self):
        remote, candidate = self.prepared()
        self.finish(remote, candidate)
        legacy = self.root / '.local/legacy-copy'
        delivery.git(self.root, 'clone', '--no-local', str(remote), str(legacy))
        self.next_candidate(remote)
        self.assertTrue((legacy / '.git/HEAD').is_file())
        self.assertNotIn(str(legacy), [r['candidate'] for r in self.records()])

    def test_retirement_failure_keeps_proof_and_can_resume_intact_quarantine(self):
        remote, candidate = self.prepared()
        self.finish(remote, candidate)
        with patch.object(candidate_storage.shutil, 'rmtree', side_effect=PermissionError('Synthetic busy directory')):
            self.next_candidate(remote)
        records = self.records()
        record = next(r for r in records if r['candidate'] == str(candidate))
        self.assertTrue(record['receipt']['workspace_reconciled'])
        retained = Path(record.get('quarantine') or str(candidate))
        self.assertTrue(retained.is_dir(), record)
        self.assertTrue((self.root / 'work/result.md').is_file())
        self.next_candidate(remote)
        self.assertFalse(retained.exists())
        record = next(r for r in self.records() if r['candidate'] == str(candidate))
        self.assertEqual(record['state'], 'closed')

    def test_replaced_registry_lock_is_not_accepted_or_deleted(self):
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        native = platform_runtime.process_lock
        replaced = []
        @contextmanager
        def stolen(file):
            with native(file) as stream:
                file = Path(file)
                try:
                    file.rename(file.with_name(file.name + '.original'))
                except PermissionError:
                    self.skipTest('Native filesystem prevents replacement of an open lock')
                file.write_text('Foreign lock owner')
                replaced.append(file)
                yield stream
        destination = self.base / 'new-copy'
        with patch.object(platform_runtime, 'process_lock', side_effect=stolen):
            with self.assertRaises((Rejected, OSError)):
                delivery.prepare(self.root, str(remote), 'main', destination)
        self.assertFalse(destination.exists())
        self.assertTrue(replaced)
        self.assertEqual(replaced[-1].read_text(), 'Foreign lock owner')

    def test_replaced_operation_lock_is_detected_and_foreign_file_preserved(self):
        remote, candidate = self.prepared()
        record = next(r for r in self.records() if r['candidate'] == str(candidate))
        lockfile = self.root / '.local/cws-candidates/locks' / (record['id'] + '.lock')
        with self.assertRaises(Rejected):
            with candidate_storage.operation(candidate) as guard:
                try:
                    lockfile.rename(lockfile.with_name(lockfile.name + '.original'))
                except PermissionError:
                    self.skipTest('Native filesystem prevents replacement of an open lock')
                lockfile.write_text('Foreign replacement owner')
                guard.check()
        self.assertEqual(lockfile.read_text(), 'Foreign replacement owner')
        self.assertEqual((candidate / 'work/result.md').read_text(), 'Verified synthetic result\n')

    def test_partial_quarantine_is_preserved_and_not_blindly_retried(self):
        remote, candidate = self.prepared()
        self.finish(remote, candidate)
        def partial(directory, *args, **kwargs):
            (Path(directory) / '.git/description').unlink()
            raise OSError('Synthetic interruption after one removal')
        with patch.object(candidate_storage.shutil, 'rmtree', side_effect=partial):
            self.next_candidate(remote)
        record = next(r for r in self.records() if r['candidate'] == str(candidate))
        quarantine = Path(record['quarantine'])
        self.assertTrue(quarantine.is_dir())
        self.next_candidate(remote)
        self.assertTrue((quarantine / 'work/result.md').is_file())
        current = next(r for r in self.records() if r['id'] == record['id'])
        self.assertEqual(current['state'], 'retained')
        self.assertEqual(current['receipt']['status'], 'readback-confirmed')

    def wait_file(self, file, process):
        deadline = time.monotonic() + 30
        while not file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(process.communicate(timeout=5)[1])
            time.sleep(.02)
        self.assertTrue(file.exists(), 'Synthetic subprocess did not reach mutation seam')

    def test_child_validation_outlives_parent_lease_and_blocks_retirement(self):
        remote, candidate = self.prepared(); self.finish(remote, candidate)
        ready, release = self.base / 'validation-ready', self.base / 'validation-release'
        script = '''
import runpy,sys,time
from pathlib import Path
candidate,ready,release=map(Path,sys.argv[1:])
sys.path.insert(0,str(candidate/'scripts'))
import validation
native=validation.repository
def paused(root):
 Path(ready).write_text('ready')
 deadline=time.monotonic()+35
 while not release.exists() and time.monotonic()<deadline:time.sleep(.02)
 if not release.exists():raise RuntimeError('Synthetic validation barrier timeout')
 return native(root)
validation.repository=paused
sys.argv=[str(candidate/'scripts/system.py'),'--root',str(candidate),'validate']
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        process = None
        try:
            with candidate_storage.operation(candidate):
                # Disable bytecode writes so this lease test changes no files
                # or directory metadata in the already sealed candidate.
                process = subprocess.Popen([sys.executable, '-B', '-c', script, str(candidate), str(ready), str(release)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           env=delivery.git_environment())
                self.wait_file(ready, process)
                # A real child mutator still cannot borrow the parent's lease.
                denied = subprocess.run([sys.executable, '-B', str(candidate / 'scripts/system.py'),
                                         '--root', str(candidate), 'commit', 'company/README.md',
                                         '--message', 'Must stay blocked'], capture_output=True, text=True,
                                        env=delivery.git_environment(), timeout=30)
                self.assertEqual(denied.returncode, 2, denied.stdout + denied.stderr)
                self.assertIn('already running', json.loads(denied.stderr)['reason'])
            # The child independently prevents deletion after its parent
            # releases the mutation lease, without changing candidate files.
            with candidate_storage.operation(candidate): pass
            self.next_candidate(remote)
            self.assertTrue(candidate.is_dir())
            self.assertEqual(next(r for r in self.records() if r['candidate'] == str(candidate))['state'], 'sealed')
            release.write_text('continue')
            stdout, stderr = process.communicate(timeout=40)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertTrue(json.loads(stdout)['checked'])
            self.next_candidate(remote)
            self.assertFalse(candidate.exists(), [(r['state'], r.get('reason')) for r in self.records()])
        finally:
            if process is not None and process.poll() is None:
                release.write_text('continue'); process.communicate(timeout=40)

    def test_replaced_validation_lock_retains_candidate_and_foreign_file(self):
        remote, candidate = self.prepared(); self.finish(remote, candidate)
        record = next(r for r in self.records() if r['candidate'] == str(candidate))
        lockfile = self.root / '.local/cws-candidates/locks' / (record['id'] + '.validation.lock')
        with self.assertRaises(Rejected):
            with candidate_storage.validation_operation(candidate) as guard:
                try:
                    lockfile.rename(lockfile.with_name(lockfile.name + '.original'))
                except PermissionError:
                    self.skipTest('Native filesystem prevents replacement of an open lock')
                lockfile.write_text('Foreign validation owner')
                guard.check()
        self.assertEqual(lockfile.read_text(), 'Foreign validation owner')
        self.assertTrue(candidate.is_dir())

    def test_real_sweep_cannot_retire_candidate_during_commit_or_finish(self):
        for action in ('commit', 'finish'):
            with self.subTest(action=action):
                other = CandidateStorage('runTest'); other.setUp()
                process = None
                try:
                    remote, candidate = other.prepared(); other.finish(remote, candidate)
                    ready, release = other.base / 'ready', other.base / 'release'
                    script = '''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import delivery
candidate,remote,ready,release,action=map(str,sys.argv[2:])
def pause(original):
 def wrapped(*a,**kw):
  Path(ready).write_text('ready')
  deadline=time.monotonic()+35
  while not Path(release).exists() and time.monotonic()<deadline:time.sleep(.02)
  if not Path(release).exists():raise RuntimeError('Synthetic barrier timeout')
  return original(*a,**kw)
 return wrapped
if action=='commit':
 delivery.validation.repository=pause(delivery.validation.repository)
 delivery.commit(Path(candidate),['company/README.md'],'Synthetic no-op')
else:
 delivery.complete_source=pause(delivery.complete_source)
 assert delivery.deliver(Path(candidate),remote,finish=True)['workspace_reconciled']
'''
                    process = subprocess.Popen([sys.executable, '-c', script, str(TEMPLATE / 'scripts'),
                                                str(candidate), str(remote), str(ready), str(release), action],
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                               env=delivery.git_environment())
                    other.wait_file(ready, process)
                    with self.assertRaises((Rejected, BlockingIOError)):
                        with candidate_storage.operation(candidate): pass
                    other.next_candidate(remote)
                    self.assertTrue(candidate.is_dir())
                    release.write_text('continue')
                    stdout, stderr = process.communicate(timeout=40)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                finally:
                    if process is not None and process.poll() is None:
                        (other.base / 'release').write_text('continue')
                        process.communicate(timeout=40)
                    other.doCleanups()

    def test_parallel_prepare_cannot_exceed_pending_limit(self):
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        for _ in range(3): self.next_candidate(remote)
        gate = self.base / 'gate'
        script = '''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import delivery
while not Path(sys.argv[5]).exists():time.sleep(.01)
try:delivery.prepare(Path(sys.argv[2]),sys.argv[3],'main',Path(sys.argv[4]));print('prepared')
except Exception:print('preserved')
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(TEMPLATE / 'scripts'), str(self.root),
                                       str(remote), str(self.base / f'parallel-{i}'), str(gate)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      env=delivery.git_environment()) for i in range(2)]
        try:
            gate.write_text('start')
            outputs = [p.communicate(timeout=40) for p in processes]
            self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
            self.assertLessEqual(sum((self.base / f'parallel-{i}').exists() for i in range(2)), 1)
            self.assertLessEqual(len([r for r in self.records() if r['state'] != 'closed']), 4)
            with self.assertRaises(Rejected): self.next_candidate(remote)
        finally:
            for p in processes:
                if p.poll() is None:
                    p.terminate(); p.communicate(timeout=5)

    def test_same_reservation_has_only_one_real_clone_owner(self):
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        destination = self.base / 'shared-attempt'
        ready, release = self.base / 'clone-ready', self.base / 'clone-release'
        script = '''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import delivery
source,remote,destination,ready,release=sys.argv[2:]
native=delivery.git
def paused(root,*args,**kw):
 if args and args[0]=='clone':
  Path(ready).write_text('ready')
  deadline=time.monotonic()+35
  while not Path(release).exists() and time.monotonic()<deadline:time.sleep(.02)
  if not Path(release).exists():raise RuntimeError('Synthetic clone barrier timeout')
 return native(root,*args,**kw)
delivery.git=paused
delivery.prepare(Path(source),remote,'main',Path(destination))
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(TEMPLATE / 'scripts'), str(self.root),
                                    str(remote), str(destination), str(ready), str(release)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=delivery.git_environment())
        try:
            self.wait_file(ready, process)
            self.assertFalse(destination.exists())
            with self.assertRaises(Rejected): delivery.prepare(self.root, str(remote), 'main', destination)
            self.assertFalse(destination.exists())
            release.write_text('continue')
            stdout, stderr = process.communicate(timeout=40)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertTrue((destination / '.git/HEAD').is_file())
            self.assertEqual(len(self.records()), 1)
        finally:
            if process.poll() is None:
                release.write_text('continue'); process.communicate(timeout=40)

    def test_darwin_uses_exclusive_rename_and_preserves_on_native_error(self):
        if os.name == 'nt': self.skipTest('POSIX libc ABI simulation runs on POSIX')
        source, target = self.base / 'rename-source', self.base / 'rename-target'
        source.mkdir(); (source / 'kept.txt').write_text('Owned bytes')
        calls = []
        class NativeRename:
            error = None
            def __call__(self, old, new, flags):
                calls.append((old, new, flags))
                if self.error:
                    ctypes.set_errno(self.error); return -1
                os.rename(os.fsdecode(old), os.fsdecode(new)); return 0
        native = NativeRename()
        with patch.object(candidate_storage.sys, 'platform', 'darwin'), \
             patch.object(candidate_storage.ctypes, 'CDLL', return_value=SimpleNamespace(renamex_np=native)):
            for error in (errno.EEXIST, errno.EPERM, errno.ENOTSUP):
                native.error = error
                with self.assertRaises(OSError): candidate_storage._rename(source, target)
                self.assertTrue(source.is_dir()); self.assertFalse(target.exists())
            native.error = None
            candidate_storage._rename(source, target)
        self.assertEqual(calls[-1], (os.fsencode(source), os.fsencode(target), 4))
        self.assertEqual(native.argtypes, [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint])
        self.assertEqual((target / 'kept.txt').read_text(), 'Owned bytes')
        other = self.base / 'other-rename'; other.mkdir()
        with patch.object(candidate_storage.sys, 'platform', 'darwin'), \
             patch.object(candidate_storage.ctypes, 'CDLL', return_value=SimpleNamespace()):
            with self.assertRaises(Rejected): candidate_storage._rename(target, other)
            self.assertTrue((target / 'kept.txt').exists())
            with self.assertRaises(Rejected): candidate_storage._rename(target, self.base / 'missing-primitive-target')
        self.assertTrue((target / 'kept.txt').is_file())

    def test_actual_update_and_rollback_keep_candidate_leased_until_result(self):
        product, release_repo = system_checks.GitAcceptance.product_release(self)
        self.root = self.base / 'lifecycle-company'
        lifecycle.create(release_repo, self.root, 'test-company', 'test-owner')
        remote = self.base / 'lifecycle-common.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        delivery.git(self.root, 'remote', 'set-url', 'origin', str(remote))
        version = self.root / 'release.yaml'
        original_version = version.read_bytes()
        data = system_checks.load(product / 'template/release.yaml')
        data['version'] = '1.8.1'
        system_checks.write(product / 'template/release.yaml', data)
        delivery.git(product, 'add', '--', 'template/release.yaml')
        delivery.git(product, 'commit', '-m', 'Synthetic next release')
        system_checks.product.release(product, release_repo)
        script = '''
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import lifecycle
source,release,destination,ready,resume,action=sys.argv[2:]
native=lifecycle.git
def paused(root,*args,**kwargs):
 gate=(args and args[0]=='fetch') if action=='update' else (args and args[0]=='diff' and '--binary' in args)
 if Path(root)==Path(destination) and gate:
  Path(ready).write_text('ready')
  deadline=time.monotonic()+35
  while not Path(resume).exists() and time.monotonic()<deadline:time.sleep(.02)
  if not Path(resume).exists():raise RuntimeError('Synthetic lifecycle barrier timeout')
 return native(root,*args,**kwargs)
lifecycle.git=paused
result=lifecycle.update(Path(source),Path(release),Path(destination)) if action=='update' else lifecycle.rollback(Path(source),Path(destination))
assert result['status']=='verified-candidate',result
print(json.dumps(result))
'''
        for action in ('update', 'rollback'):
            with self.subTest(action=action):
                candidate = self.base / ('live-' + action)
                ready, resume = self.base / (action + '-ready'), self.base / (action + '-resume')
                process = subprocess.Popen([sys.executable, '-c', script, str(TEMPLATE / 'scripts'), str(self.root),
                                            str(release_repo), str(candidate), str(ready), str(resume), action],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           env=delivery.git_environment())
                try:
                    self.wait_file(ready, process)
                    blocked = False
                    try:
                        self.finish(remote, candidate)
                    except Rejected:
                        blocked = True
                    self.next_candidate(remote)
                    self.assertTrue(candidate.is_dir(), 'An executing lifecycle candidate was retired')
                    self.assertTrue(blocked, 'Concurrent finish entered an executing lifecycle')
                    resume.write_text('continue')
                    stdout, stderr = process.communicate(timeout=60)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                    result = self.finish(remote, candidate)
                    self.assertTrue(result['workspace_reconciled'])
                    self.assertEqual(result['storage_status'], 'sealed', result)
                finally:
                    if process.poll() is None:
                        resume.write_text('continue'); process.communicate(timeout=60)
        self.assertEqual(version.read_bytes(), original_version)
