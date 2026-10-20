"""Real local Git completion and interruption checks; no company accounts."""
import json
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

from checks.test_system import Fixture, delivery, load, task_file
from checks.fixture_helpers import execute_with_current_tokens_for_fixture


class Completion(Fixture):
    def prepared(self, before_prepare=None):
        (self.root / "work").mkdir(exist_ok=True)
        self.seed()
        remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        delivery.git(self.root, 'remote', 'add', 'origin', str(remote))
        if before_prepare:
            before_prepare()
        prepared = delivery.prepare(self.root, str(remote), 'main')
        from pathlib import Path
        candidate = Path(prepared['path'])
        (candidate / 'work').mkdir(exist_ok=True)
        (candidate / 'work/result.md').write_text('Verified synthetic result\n', encoding='utf-8')
        delivery.commit(candidate, ['work/result.md'], 'Synthetic result')
        return remote, candidate

    def finish(self, remote, candidate):
        return delivery.deliver(candidate, str(remote), finish=True)

    def test_default_candidate_is_ignored_and_source_finishes(self):
        remote, candidate = self.prepared()
        self.assertEqual(delivery.git(self.root, 'status', '--porcelain'), '')
        result = self.cli('deliver', str(remote), '--finish', root=candidate)
        self.assertTrue(result['workspace_reconciled'])
        self.assertEqual(delivery.git(self.root, 'rev-parse', 'HEAD'), delivery.git(remote, 'rev-parse', 'main'))
        self.assertEqual((self.root / 'work/result.md').read_bytes(), (candidate / 'work/result.md').read_bytes())
        self.assertEqual(delivery.git(self.root, 'status', '--porcelain'), '')
        native = delivery.git
        with patch.object(delivery, 'git', wraps=native) as calls:
            self.assertTrue(self.finish(remote, candidate)['workspace_reconciled'])
        self.assertFalse(any('push' in call.args[1:] for call in calls.call_args_list))

    def test_changed_candidate_after_complete_is_pending_without_push(self):
        remote,candidate=self.prepared()
        self.assertTrue(self.finish(remote,candidate)['workspace_reconciled'])
        source=(self.root/'work/result.md').read_bytes()
        changed=candidate/'work/result.md';changed.write_text('New unsaved candidate edit')
        extra=candidate/'work/owner-extra.md';extra.write_text('Untracked candidate work')
        with patch.object(delivery,'git',wraps=delivery.git) as calls:
            result=self.finish(remote,candidate)
        self.assertFalse(result['workspace_reconciled']);self.assertEqual(result['status'],'delivered-local-pending')
        self.assertEqual(result['reason_code'],'local_pending')
        self.assertEqual(changed.read_text(),'New unsaved candidate edit');self.assertTrue(extra.is_file())
        self.assertEqual((self.root/'work/result.md').read_bytes(),source)
        self.assertFalse(any('push' in c.args[1:] for c in calls.call_args_list))

    def test_staged_candidate_after_pending_cannot_resume_source(self):
        remote,candidate=self.prepared()
        draft=self.root/'work/owner-draft.md';draft.write_text('Owner draft')
        result=self.finish(remote,candidate);self.assertFalse(result['workspace_reconciled'])
        source_head=delivery.git(self.root,'rev-parse','HEAD')
        file=candidate/'work/result.md';file.write_text('New staged candidate edit')
        delivery.git(candidate,'add','--','work/result.md')
        draft.unlink() # Synthetic owner resolves only its own draft.
        with patch.object(delivery,'git',wraps=delivery.git) as calls:
            result=self.finish(remote,candidate)
        self.assertFalse(result['workspace_reconciled']);self.assertEqual(result['reason_code'],'local_pending')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),source_head)
        self.assertEqual(file.read_text(),'New staged candidate edit')
        self.assertTrue(delivery.git(candidate,'diff','--cached','--name-only'))
        self.assertFalse((self.root/'work/result.md').exists())
        self.assertFalse(any('push' in c.args[1:] for c in calls.call_args_list))

    def test_record_permission_failure_returns_receipt_and_resumes_without_push(self):
        remote,candidate=self.prepared();native_write=delivery.write
        before=delivery.git(self.root,'rev-parse','HEAD')
        def readonly(file,value):
            if Path(file).name=='cws-finish.json':raise PermissionError('Synthetic private detail')
            return native_write(file,value)
        with patch.object(delivery,'write',side_effect=readonly):
            result=self.finish(remote,candidate)
        self.assertEqual(result['status'],'delivered-local-pending')
        self.assertEqual(result['reason_code'],'git_metadata_write')
        self.assertFalse(result['workspace_reconciled'])
        self.assertEqual(result['sha256'],delivery.git(remote,'rev-parse','HEAD'))
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),before)
        self.assertNotIn('Synthetic private detail',json.dumps(result))
        with patch.object(delivery,'git',wraps=delivery.git) as calls:
            resumed=self.finish(remote,candidate)
        self.assertTrue(resumed['workspace_reconciled'])
        self.assertFalse(any('push' in c.args[1:] for c in calls.call_args_list))

    def test_explicit_nonignored_and_alias_destinations_rejected_before_clone(self):
        remote, _ = self.prepared()
        with self.assertRaisesRegex(delivery.GitFailure, 'candidate_not_ignored'):
            delivery.prepare(self.root, str(remote), 'main', self.root / 'work/retained-clone')
        self.assertFalse((self.root / 'work/retained-clone').exists())
        alias = self.root / '.local/alias'
        try:
            alias.symlink_to(self.base, target_is_directory=True)
        except OSError:
            self.skipTest('Native symlink creation unavailable')
        with self.assertRaises(ValueError):
            delivery.prepare(self.root, str(remote), 'main', alias / 'new-clone')
        self.assertFalse((self.base / 'new-clone').exists())

    def test_task_candidate_uses_owning_ignored_directory(self):
        self.task(); self.seed()
        remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        result = self.cli('prepare', str(remote), '--task', 'test-task')
        self.assertTrue(result['path'].startswith(str(self.root / 'work/test-task/.local/delivery')))
        self.assertEqual(delivery.git(self.root, 'status', '--porcelain'), '')

    def test_dirty_original_retains_delivery_and_resumes_without_another_push(self):
        remote, candidate = self.prepared()
        draft = self.root / 'work/unrelated.md'; draft.write_text('User unfinished draft')
        result = self.cli('deliver', str(remote), '--finish', root=candidate, success=False)
        self.assertEqual(result['status'], 'delivered-local-pending')
        self.assertEqual(result['reason_code'], 'local_pending')
        self.assertEqual(draft.read_text(), 'User unfinished draft')
        self.assertNotEqual(delivery.git(self.root, 'rev-parse', 'HEAD'), result['sha256'])
        draft.unlink()  # The synthetic draft owner resolves its own file.
        with patch.object(delivery, 'deliver', wraps=delivery.deliver) as calls:
            result = delivery.finish_delivery(candidate, str(remote), 'main', None, None)
        self.assertTrue(result['workspace_reconciled'])
        self.assertEqual(calls.call_count, 0)

    def test_same_sha_branch_switch_rejected_before_publication(self):
        remote, candidate = self.prepared()
        old = delivery.git(remote, 'rev-parse', 'main')
        delivery.git(self.root, 'switch', '-c', 'other')
        with self.assertRaises(delivery.GitFailure):
            self.finish(remote, candidate)
        self.assertEqual(delivery.git(remote, 'rev-parse', 'main'), old)
        self.assertEqual(delivery.git(self.root, 'symbolic-ref', '--short', 'HEAD'), 'other')

    def test_source_commit_absent_from_candidate_is_not_published(self):
        self.seed()
        remote=self.base/'shared.git'
        delivery.git(self.root,'clone','--bare',str(self.root),str(remote))
        local=self.root/'company/local-only.md';local.write_text('Unpublished local decision')
        delivery.commit(self.root,['company/local-only.md'],'Local pending decision')
        candidate=self.base/'candidate'
        delivery.prepare(self.root,str(remote),'main',candidate)
        (candidate/'work').mkdir(exist_ok=True)
        (candidate/'work/result.md').write_text('Synthetic result')
        delivery.commit(candidate,['work/result.md'],'Synthetic result')
        before=delivery.git(remote,'rev-parse','main')
        with self.assertRaises(delivery.GitFailure):self.finish(remote,candidate)
        self.assertEqual(delivery.git(remote,'rev-parse','main'),before)
        self.assertEqual(local.read_text(),'Unpublished local decision')

    def test_real_source_locks_prevent_native_branch_switch_during_checkout(self):
        remote, candidate = self.prepared()
        original = delivery.native_admin
        attempted = []
        @contextmanager
        def probe(root, head, worktree=None):
            if worktree == self.root:
                attempt = delivery.git(self.root, 'symbolic-ref', 'HEAD', 'refs/heads/other', check=False)
                attempted.append(attempt.returncode)
            with original(root, head, worktree) as private:
                yield private
        with patch.object(delivery, 'native_admin', side_effect=probe):
            result = self.finish(remote, candidate)
        self.assertTrue(result['workspace_reconciled'])
        self.assertTrue(attempted and all(attempted))
        self.assertEqual(delivery.git(self.root, 'symbolic-ref', 'HEAD'), 'refs/heads/main')

    def interrupted(self, stage):
        remote, candidate = self.prepared()
        original = delivery.git
        def interrupt(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if stage in args:
                raise OSError('Synthetic interruption after native operation')
            return result
        if stage == 'index-install':
            fault = patch.object(delivery, 'install_commit_index', side_effect=OSError('Synthetic interrupted index install'))
        else:
            fault = patch.object(delivery, 'git', side_effect=interrupt)
        with fault:
            result = self.finish(remote, candidate)
        self.assertFalse(result['workspace_reconciled'])
        self.assertEqual(result['status'], 'delivered-local-pending')
        return remote, candidate

    def test_resume_after_worktree_update_does_not_replay_checkout(self):
        remote, candidate = self.interrupted('merge')
        with patch.object(delivery, 'git', wraps=delivery.git) as calls:
            result = self.finish(remote, candidate)
        self.assertTrue(result['workspace_reconciled'])
        self.assertFalse(any('merge' in call.args[1:] for call in calls.call_args_list))

    def test_resume_after_branch_cas(self):
        remote, candidate = self.interrupted('index-install')
        self.assertEqual(delivery.git(self.root, 'rev-parse', 'HEAD'), delivery.git(candidate, 'rev-parse', 'HEAD'))
        self.assertTrue(self.finish(remote, candidate)['workspace_reconciled'])
        self.assertEqual(delivery.git(self.root, 'status', '--porcelain'), '')

    def test_intervening_edit_after_interruption_is_preserved(self):
        remote, candidate = self.interrupted('index-install')
        file = self.root / 'work/result.md'; file.write_text('Later user edit')
        result = self.finish(remote, candidate)
        self.assertFalse(result['workspace_reconciled'])
        self.assertEqual(file.read_text(), 'Later user edit')

    def test_foreign_lock_after_readback_is_retained(self):
        remote, candidate = self.prepared()
        lock = self.root / '.git/index.lock'; lock.write_text('Foreign process')
        result = self.finish(remote, candidate)
        self.assertFalse(result['workspace_reconciled'])
        self.assertEqual(result['reason_code'], 'git_locked')
        self.assertEqual(lock.read_text(), 'Foreign process')
        lock.unlink()  # Release this test's own simulated process lock.
        self.assertTrue(self.finish(remote, candidate)['workspace_reconciled'])

    def test_replaced_lock_before_index_install_is_not_touched(self):
        remote, candidate = self.prepared()
        install = delivery.install_commit_index
        def replace(index_lock, index, identity=None):
            index_lock.rename(index_lock.with_name('owned-lock-preserved'))
            index_lock.write_text('Foreign replacement')
            return install(index_lock, index, identity)
        with patch.object(delivery, 'install_commit_index', side_effect=replace):
            result = self.finish(remote, candidate)
        self.assertFalse(result['workspace_reconciled'])
        self.assertEqual((self.root / '.git/index.lock').read_text(), 'Foreign replacement')

    def test_remote_already_contains_candidate_is_readback_not_stale_push(self):
        remote, candidate = self.prepared()
        base = delivery.git(remote, 'rev-parse', 'main')
        delivery.deliver(candidate, str(remote), expected_base=base)
        newer = self.base / 'newer'
        delivery.prepare(self.root, str(remote), 'main', newer)
        (newer / 'work/colleague.md').write_text('Colleague result')
        delivery.commit(newer, ['work/colleague.md'], 'Colleague')
        delivery.deliver(newer, str(remote))
        with patch.object(delivery, 'git', wraps=delivery.git) as calls:
            result = delivery.deliver(candidate, str(remote), expected_base=base)
        self.assertTrue(result['repeated']); self.assertTrue(result['newer_remote'])
        self.assertFalse(any('push' in call.args[1:] for call in calls.call_args_list))

    def test_finish_includes_acknowledgment_commit(self):
        self.task()
        execute_with_current_tokens_for_fixture(self.root, 'test-task', self.artifact(), self.critical())
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        from pathlib import Path
        candidate = Path(delivery.prepare(self.root, str(remote), 'main')['path'])
        result = delivery.deliver(candidate, str(remote), task_id='test-task', finish=True)
        self.assertTrue(result['workspace_reconciled'])
        self.assertEqual(result['local_head'], result['acknowledgment_commit'])
        self.assertEqual(load(task_file(self.root, 'test-task'))['delivery']['status'], 'delivered')

    def test_diagnostics_do_not_expose_subprocess_text(self):
        secret = 'ghp_' + 'x' * 36
        for message, expected in [('Could not resolve host github.com', 'network_dns'),
                                  ('Failed to connect to host', 'network_connection'),
                                  ('Authentication failed', 'authentication'),
                                  ('error: 403', 'authorization'),
                                  ('.git/index.lock: Permission denied', 'git_metadata_write'),
                                  ('.git/index.lock: File exists', 'git_locked'),
                                  ('An unfamiliar failure', 'unknown')]:
            detail = delivery.diagnostic('fetch', message + ' ' + secret)
            self.assertEqual(detail['reason_code'], expected)
            self.assertNotIn(secret, json.dumps(detail))
        with patch.object(delivery.subprocess, 'run', return_value=subprocess.CompletedProcess([], 128, '', secret)):
            with self.assertRaises(delivery.GitFailure) as raised:
                delivery.git(self.root, 'fetch', 'origin')
        self.assertNotIn(secret, str(raised.exception))

    def test_ignored_collision_preserves_private_file_and_original_ref(self):
        remote,candidate=self.prepared()
        private=self.root/'.local/keep.txt';private.write_text('Private original')
        incoming=candidate/'.local/keep.txt';incoming.parent.mkdir(exist_ok=True);incoming.write_text('Incoming collision')
        delivery.git(candidate,'add','-f','.local/keep.txt')
        delivery.git(candidate,'commit','-m','Synthetic ignored collision fault')
        old=delivery.git(self.root,'rev-parse','HEAD')
        result=self.finish(remote,candidate)
        self.assertFalse(result['workspace_reconciled'])
        self.assertEqual(private.read_text(),'Private original')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),old)

    def test_partial_checkout_or_later_edits_are_not_overwritten_on_resume(self):
        remote,candidate=self.interrupted('merge')
        file=self.root/'company/projects/README.md';file.write_text('Later owner content')
        result=self.finish(remote,candidate)
        self.assertFalse(result['workspace_reconciled'])
        self.assertEqual(file.read_text(),'Later owner content')
        self.assertEqual((self.root/'work/result.md').read_text(),'Verified synthetic result\n')

    def test_native_merge_hook_runs_without_being_disabled(self):
        def hook():
            effective=delivery.commit_context(self.root)[1]
            hooks=self.root/'.local/test-native-hooks'
            shutil.copytree(effective,hooks)
            file=hooks/'post-merge'
            file.write_text('#!/bin/sh\nprintf ran > .local/native-merge-marker\n')
            file.chmod(0o755)
            delivery.git(self.root,'config','core.hooksPath',str(hooks))
        remote,candidate=self.prepared(before_prepare=hook)
        self.assertTrue(self.finish(remote,candidate)['workspace_reconciled'])
        self.assertEqual((self.root/'.local/native-merge-marker').read_text(),'ran')

    def test_push_timeout_is_resolved_by_readback_before_retry(self):
        remote,candidate=self.prepared();native=delivery.git;pushes=[]
        def timeout(root,*args,**kwargs):
            value=native(root,*args,**kwargs)
            if args and args[0]=='push':
                pushes.append(args)
                raise subprocess.TimeoutExpired('Synthetic push response',30)
            return value
        with patch.object(delivery,'git',side_effect=timeout):result=self.finish(remote,candidate)
        self.assertTrue(result['workspace_reconciled']);self.assertEqual(result['push_exit_code'],124)
        self.assertEqual(len(pushes),1)

    def test_missing_prepare_witness_cannot_publish_with_finish(self):
        remote,candidate=self.prepared();old=delivery.git(remote,'rev-parse','main')
        (candidate/'.git/cws-prepare.json').unlink()
        with self.assertRaises(delivery.GitFailure):self.finish(remote,candidate)
        self.assertEqual(delivery.git(remote,'rev-parse','main'),old)

    def test_foreign_staging_is_not_published_by_finish(self):
        remote,candidate=self.prepared();old=delivery.git(remote,'rev-parse','main')
        file=self.root/'work/other.md';file.write_text('Foreign staged bytes')
        delivery.git(self.root,'add','work/other.md')
        index=delivery.git(self.root,'write-tree')
        with self.assertRaises(delivery.GitFailure):self.finish(remote,candidate)
        self.assertEqual(delivery.git(self.root,'write-tree'),index)
        self.assertEqual(delivery.git(remote,'rev-parse','main'),old)


class MetadataDiagnostics(unittest.TestCase):
    def test_native_lock_and_permission_failures_are_structured(self):
        with tempfile.TemporaryDirectory(prefix='cws-metadata-diagnostic-') as temporary:
            root=Path(temporary);lock=root/'index.lock';lock.write_text('Foreign operation')
            with self.assertRaises(delivery.GitFailure) as raised:
                with delivery.commit_locks(root):pass
            self.assertEqual(raised.exception.diagnostic['reason_code'],'git_locked')
            self.assertEqual(lock.read_text(),'Foreign operation')
            with patch.object(delivery.os,'open',side_effect=PermissionError('Synthetic private detail')):
                with self.assertRaises(delivery.GitFailure) as raised:
                    with delivery.commit_locks(root):pass
            self.assertEqual(raised.exception.diagnostic['reason_code'],'git_metadata_write')
            self.assertNotIn('Synthetic private detail',str(raised.exception))
            before=lock.read_bytes();index=root/'index';index.write_text('Original index')
            with patch.object(delivery.os,'replace',side_effect=PermissionError('Synthetic private detail')):
                with self.assertRaises(delivery.GitFailure) as raised:
                    delivery.install_commit_index(lock,index,delivery.lock_identity(lock))
            self.assertEqual(raised.exception.diagnostic['reason_code'],'git_metadata_write')
            self.assertEqual(lock.read_bytes(),before);self.assertEqual(index.read_text(),'Original index')

    def test_read_only_metadata_and_unknown_io_preserve_bytes(self):
        import errno
        with tempfile.TemporaryDirectory(prefix='cws-readonly-metadata-') as temporary:
            root=Path(temporary);marker=root/'index.lock';marker.write_text('Owned lock bytes')
            index=root/'index';index.write_text('Original index bytes')
            for error in [PermissionError(errno.EACCES,'Synthetic private detail'),
                          PermissionError(errno.EPERM,'Synthetic private detail'),
                          OSError(errno.EROFS,'Synthetic private detail')]:
                with self.subTest(errno=error.errno):
                    for operation in ['lock','write-index','install-index','record']:
                        with self.subTest(operation=operation):
                            if operation=='lock':
                                fault=patch.object(delivery.os,'open',side_effect=error)
                            elif operation=='write-index':
                                fault=patch.object(delivery.os,'open',side_effect=error)
                            elif operation=='install-index':
                                fault=patch.object(delivery.os,'replace',side_effect=error)
                            else:
                                fault=patch.object(delivery,'write',side_effect=error)
                            with fault,self.assertRaises(delivery.GitFailure) as raised:
                                if operation=='lock':
                                    with delivery.commit_locks(root):pass
                                elif operation=='write-index':delivery.write_owned_index(marker,b'New bytes',delivery.lock_identity(marker))
                                elif operation=='install-index':delivery.install_commit_index(marker,index,delivery.lock_identity(marker))
                                else:delivery.write_delivery_record(root/'record.json',{'status':'confirmed'})
                            self.assertEqual(raised.exception.diagnostic['reason_code'],'git_metadata_write')
                            self.assertNotIn('Synthetic private detail',str(raised.exception))
                            self.assertEqual(marker.read_text(),'Owned lock bytes')
                            self.assertEqual(index.read_text(),'Original index bytes')
            with patch.object(delivery.os,'replace',side_effect=OSError(errno.ENOSPC,'Synthetic private detail')):
                with self.assertRaises(delivery.GitFailure) as raised:delivery.install_commit_index(marker,index,delivery.lock_identity(marker))
            self.assertEqual(raised.exception.diagnostic['reason_code'],'unknown')
            self.assertNotIn('Synthetic private detail',str(raised.exception))

    def test_network_deadlines_preserve_override_and_noninteractive(self):
        with tempfile.TemporaryDirectory(prefix='cws-network-deadline-') as temporary:
            root=Path(temporary)
            for stage,default in [('clone',120),('fetch',30),('push',30)]:
                for override in [None,7,0]:
                    with self.subTest(stage=stage,override=override):
                        with patch.object(delivery.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
                            delivery.git(root,stage,**({} if override is None else {'timeout':override}))
                        self.assertEqual(run.call_args.kwargs['timeout'],default if override is None else override)
                        self.assertEqual(run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'],'0')
                        self.assertEqual(run.call_args.kwargs['env']['GCM_INTERACTIVE'],'never')
            with patch.object(delivery.subprocess,'run',side_effect=subprocess.TimeoutExpired(['git','clone'],120)):
                with self.assertRaises(delivery.GitFailure) as raised:delivery.git(root,'clone')
                self.assertEqual(raised.exception.diagnostic['reason_code'],'timeout')
                self.assertEqual(raised.exception.diagnostic['stage'],'clone')
                with self.assertRaises(subprocess.TimeoutExpired):delivery.git(root,'clone',check=False)
