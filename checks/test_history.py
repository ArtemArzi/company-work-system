"""Byte framing, aliases, export authority and guarded staging on synthetic Git."""
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from checks import test_system as fixtures
from core import Rejected, config, write
import delivery
import history


class History(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='history-fixture-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
        delivery.git(self.root,'init','-b','main');delivery.identity(self.root)
        self.policy=history.Policy('company',roots=('public',))

    def commit(self,relative='public/note.md',content='Synthetic note'):
        file=self.root/relative;file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes(content if isinstance(content,bytes) else content.encode())
        delivery.git(self.root,'add','--',relative);delivery.git(self.root,'commit','-m','Synthetic snapshot')
        return delivery.git(self.root,'rev-parse','HEAD')

    def test_shared_positive_and_same_blob_all_paths_checked(self):
        self.commit();first=history.scan(self.root,self.policy)
        self.assertEqual(first['commits'],1);self.assertEqual(first['blobs'],1)
        self.commit('public/alias.md');second=history.scan(self.root,self.policy)
        self.assertEqual(second['blobs'],1);self.assertEqual(second['bytes'],len('Synthetic note'))
        self.commit('forbidden/alias.md')
        with self.assertRaisesRegex(Rejected,'excludes'):history.scan(self.root,self.policy)

    def test_secret_removed_from_head_is_still_rejected(self):
        # Synthetic marker composed at runtime; no actual credential is used.
        self.commit(content='gh'+'p_'+'A'*35)
        self.commit(content='Removed synthetic marker')
        with self.assertRaisesRegex(Rejected,'credential-like'):history.scan(self.root,self.policy)

    def test_additional_high_signal_forms_are_rejected_without_logging_values(self):
        for marker in [('github'+'_pat_'+'A'*25).encode(),('AK'+'IA'+'0'*16).encode()]:
            with self.assertRaisesRegex(Rejected,'credential-like'):self.policy.content('public/note.md',[marker])

    def test_baseline_exact_content_and_foreign_merge(self):
        baseline=self.commit();self.commit('public/new.md','New note')
        report=history.scan(self.root,self.policy,baseline=baseline)
        self.assertEqual(report['bytes'],len('New note'))
        delivery.git(self.root,'checkout','--orphan','foreign')
        self.commit('public/foreign.md','Foreign branch')
        delivery.git(self.root,'checkout','main')
        delivery.git(self.root,'merge','--allow-unrelated-histories','foreign','-m','Synthetic foreign merge')
        with self.assertRaisesRegex(Rejected,'baseline ancestry'):history.scan(self.root,self.policy,baseline=baseline)

    def test_wrong_baseline_and_missing_object(self):
        base=self.commit();delivery.git(self.root,'checkout','--orphan','other');other=self.commit('public/other.md')
        delivery.git(self.root,'checkout','main')
        with self.assertRaises(Rejected):history.scan(self.root,self.policy,baseline=other)
        context=history.Context(self.root);reader=history.Batch(context)
        try:
            with self.assertRaisesRegex(Rejected,'missing/invalid'):reader.value('0'*40,'blob')
        finally:reader.close()

    def test_all_refs_release_alias_tags_and_direct_blob(self):
        self.commit('standards/allowed.md');delivery.git(self.root,'tag','-a','v-test','-m','Synthetic tag')
        self.assertTrue(history.scan(self.root,history.release_policy(),all_refs=True)['commits'])
        blob=delivery.git(self.root,'rev-parse','HEAD:standards/allowed.md')
        delivery.git(self.root,'update-ref','refs/tags/direct-object',blob)
        with self.assertRaises(Rejected):history.scan(self.root,history.release_policy(),all_refs=True)

    def test_release_forbidden_historical_alias_even_if_head_removed(self):
        self.commit('standards/allowed.md');self.commit('docs/private-note.md')
        delivery.git(self.root,'rm','docs/private-note.md');delivery.git(self.root,'commit','-m','Remove alias')
        with self.assertRaisesRegex(Rejected,'non-template history'):history.scan(self.root,history.release_policy(),all_refs=True)

    def test_binary_permission_does_not_skip_secret_or_text_alias(self):
        self.commit('public/object.bin',b'\xff\xfe')
        policy=history.Policy('company',roots=('public',),binary_suffixes=('.bin',))
        self.assertEqual(history.scan(self.root,policy)['bytes'],2)
        self.commit('public/alias.md',b'\xff\xfe')
        with self.assertRaisesRegex(Rejected,'binary/unreadable'):history.scan(self.root,policy)
        # A distinct history proves binary scan still rejects marker bytes.
        second=self.root.parent/'second';second.mkdir();delivery.git(second,'init','-b','main');delivery.identity(second)
        (second/'public').mkdir();(second/'public/a.bin').write_bytes(b'\xff'+('gh'+'p_'+'A'*35).encode())
        delivery.git(second,'add','.');delivery.git(second,'commit','-m','Synthetic binary marker')
        with self.assertRaisesRegex(Rejected,'credential-like'):history.scan(second,policy)

    def test_large_blob_is_streamed_and_cross_chunk_marker_rejected(self):
        self.commit(content='x'*(4*1024*1024))
        self.assertEqual(history.scan(self.root,self.policy)['bytes'],4*1024*1024)
        self.commit(content='x'*65530+' '+('gh'+'p_'+'A'*35))
        with self.assertRaisesRegex(Rejected,'credential-like'):history.scan(self.root,self.policy)

    def test_noncanonical_names_modes_and_symlinks(self):
        self.commit('public/new\nline.md')
        with self.assertRaisesRegex(Rejected,'control character'):history.scan(self.root,self.policy)
        other=self.root.parent/'symlink';other.mkdir();delivery.git(other,'init','-b','main');delivery.identity(other)
        (other/'skills/test-skill').mkdir(parents=True);(other/'skills/test-skill/SKILL.md').write_text('Synthetic')
        (other/'.agents/skills').mkdir(parents=True);(other/'.agents/skills/test-skill').symlink_to('../../skills/test-skill')
        delivery.git(other,'add','.');delivery.git(other,'commit','-m','Canonical projection')
        history.scan(other,history.release_policy())
        (other/'skills/outside').symlink_to('/etc/passwd');delivery.git(other,'add','.');delivery.git(other,'commit','-m','Bad projection')
        with self.assertRaisesRegex(Rejected,'noncanonical symlink'):history.scan(other,history.release_policy())
        with self.assertRaisesRegex(Rejected,'submodule'):self.policy.permits('public/repo','160000')

    def test_git_selectors_cleared_and_rewrites_rejected(self):
        head=self.commit()
        with patch.dict(os.environ,{'GIT_DIR':'/missing','GIT_INDEX_FILE':'/missing','GIT_OBJECT_DIRECTORY':'/missing'}):
            self.assertEqual(history.scan(self.root,self.policy)['commits'],1)
        delivery.git(self.root,'update-ref','refs/replace/'+head,head)
        with self.assertRaisesRegex(Rejected,'replace refs'):history.scan(self.root,self.policy)

    def test_shallow_and_alternate_store_rejected(self):
        head=self.commit();file=self.root/'.git/shallow';file.write_text(head+'\n')
        with self.assertRaisesRegex(Rejected,'incomplete'):history.scan(self.root,self.policy)
        file.unlink();file=self.root/'.git/objects/info/alternates';file.write_text('/missing\n')
        with self.assertRaisesRegex(Rejected,'alternate'):history.scan(self.root,self.policy)

    def test_truncated_reader_detects_eof(self):
        self.commit();reader=history.Batch(history.Context(self.root))
        try:
            reader.proc.stdin.close();reader.proc.wait(timeout=2);reader.deadline=__import__('time').monotonic()+1
            with self.assertRaisesRegex(Rejected,'truncated'):reader.read(1)
        finally:
            # close is idempotent for stdin already closed.
            reader.close()

    def test_bundle_rechecks_actual_exported_refs_after_source_race(self):
        self.commit('README.md');release=self.root.parent/'release.git'
        delivery.git(self.root,'clone','--bare',str(self.root),str(release))
        bad=self.root.parent/'bad-source';bad.mkdir();delivery.git(bad,'init','-b','main');delivery.identity(bad)
        (bad/'PRIVATE-DEVELOPMENT.md').write_text('Synthetic private development')
        delivery.git(bad,'add','.');delivery.git(bad,'commit','-m','Private fixture history')
        original=fixtures.product.git
        def racing(root,*args,**kwargs):
            if args[:2]==('bundle','create'):
                delivery.git(release,'fetch',str(bad),'refs/heads/main:refs/heads/unchecked')
            return original(root,*args,**kwargs)
        with patch.object(fixtures.product,'git',side_effect=racing):
            with self.assertRaisesRegex(Rejected,'non-template history'):
                fixtures.product.package(release,self.root.parent/'unsafe.bundle')


class GuardedDelivery(fixtures.Fixture):
    def test_author_new_skill_pair_and_safe_projections_use_common_commit(self):
        self.seed();name='company-local-feature';directory=self.root/'skills'/name
        shutil.copytree(self.root/'skills/company-context',directory)
        file=directory/'SKILL.md';file.write_text(file.read_text().replace('name: company-context','name: '+name))
        value=__import__('core').load(directory/'workflow.yaml');value['id']=name;write(directory/'workflow.yaml',value)
        index=self.root/'skills/README.md';index.write_text(index.read_text()+f'\n[Local feature]({name}/SKILL.md)\n')
        for relative in ('.agents/skills/','.claude/skills/'):(self.root/relative/name).symlink_to('../../skills/'+name)
        relatives=[f'skills/{name}/SKILL.md',f'skills/{name}/workflow.yaml','skills/README.md',f'.agents/skills/{name}',f'.claude/skills/{name}']
        head=delivery.commit(self.root,relatives,'Share new canonical method through common delivery')
        self.assertEqual(delivery.git(self.root,'show',f'{head}:.agents/skills/{name}'),'../../skills/'+name)
        malicious=self.root/'company/projects/unsafe';malicious.symlink_to('/etc/passwd')
        with self.assertRaises(Rejected):delivery.commit(self.root,['company/projects/unsafe'],'Reject noncanonical source link')

    def export_policy(self):
        cfg=config(self.root)
        # Exact synthetic tracked files only, explicitly approved by fixture owner.
        files=delivery.git(self.root,'ls-files','-z').split('\x00');files=[f for f in files if f]
        cfg['export']={'schema_version':1,'approved_by':'test-owner','files':files,'roots':['work'],'binary_suffixes':[],'baseline':None}
        write(self.root/'company/config.yaml',cfg)
        delivery.git(self.root,'add','company/config.yaml');delivery.git(self.root,'commit','-m','Approve fixture paths')

    def test_rejection_preserves_working_file_and_index(self):
        self.seed();self.export_policy();file=self.root/'company/projects/secret.md';file.write_text('gh'+'p_'+'A'*35)
        before=delivery.git(self.root,'write-tree')
        with self.assertRaises(Rejected):delivery.commit(self.root,['company/projects/secret.md'],'Attempt rejected')
        self.assertEqual(delivery.git(self.root,'write-tree'),before);self.assertTrue(file.exists())

    def test_preview_tree_uses_owned_index_and_safe_commit_passes(self):
        self.seed();self.export_policy();file=self.root/'company/README.md';file.write_text(file.read_text()+'\nSynthetic allowed change\n')
        before=delivery.git(self.root,'write-tree');candidate=history.preview_tree(self.root,['company/README.md'],delivery.git(self.root,'rev-parse','HEAD'))
        self.assertNotEqual(candidate,before);self.assertEqual(delivery.git(self.root,'write-tree'),before)
        head=delivery.commit(self.root,['company/README.md'],'Allowed fixture change')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),head)

    def test_concurrent_policy_change_before_index_blocks(self):
        self.seed();self.export_policy();file=self.root/'company/README.md';file.write_text(file.read_text()+'\nAllowed candidate\n')
        old=delivery.git(self.root,'write-tree');scan=history.scan
        def race(*args,**kwargs):
            result=scan(*args,**kwargs);cfg=config(self.root);cfg['export']['approved_by']='different-owner';write(self.root/'company/config.yaml',cfg);return result
        with patch.object(history,'scan',side_effect=race):
            with self.assertRaisesRegex(Rejected,'owner approval'):delivery.commit(self.root,['company/README.md'],'Must stop')
        self.assertEqual(delivery.git(self.root,'write-tree'),old)

    def test_foreign_staged_work_is_preserved(self):
        self.seed();file=self.root/'company/README.md';file.write_text(file.read_text()+'\nForeign staged bytes\n');delivery.git(self.root,'add','company/README.md')
        staged=delivery.git(self.root,'write-tree')
        with self.assertRaisesRegex(Rejected,'foreign staged'):delivery.commit(self.root,['release.yaml'],'Must stop')
        self.assertEqual(delivery.git(self.root,'write-tree'),staged)

    def test_index_race_before_actual_staging_preserves_foreign_bytes(self):
        self.seed();file=self.root/'company/README.md';file.write_text(file.read_text()+'\nOwn candidate\n')
        other=self.root/'release.yaml';other.write_text(other.read_text()+'\n# Foreign bytes\n')
        preview=history.preview_tree;seen=[]
        def racing(*args,**kwargs):
            tree=preview(*args,**kwargs);delivery.git(self.root,'add','release.yaml');seen.append(delivery.git(self.root,'write-tree'));return tree
        with patch.object(history,'preview_tree',side_effect=racing):
            with self.assertRaisesRegex(Rejected,'index changed'):delivery.commit(self.root,['company/README.md'],'Must stop')
        self.assertEqual(delivery.git(self.root,'write-tree'),seen[0])
        self.assertEqual(delivery.git(self.root,'diff','--cached','--name-only'),'release.yaml')

    def test_head_race_does_not_attach_own_result_to_new_parent(self):
        self.seed();old=delivery.git(self.root,'rev-parse','HEAD')
        file=self.root/'company/README.md';file.write_text(file.read_text()+'\nOwn candidate\n')
        preview=history.preview_tree
        def racing(*args,**kwargs):
            tree=preview(*args,**kwargs)
            # Real unrelated Git mutation; no index/working-file rewrite.
            delivery.git(self.root,'commit','--allow-empty','-m','Concurrent actor commit')
            return tree
        with patch.object(history,'preview_tree',side_effect=racing):
            with self.assertRaisesRegex(Rejected,'HEAD changed'):delivery.commit(self.root,['company/README.md'],'Must stop')
        self.assertNotEqual(delivery.git(self.root,'rev-parse','HEAD'),old)
        self.assertEqual(delivery.git(self.root,'diff','--cached','--name-only'),'')

    def test_commit_lock_cleanup_preserves_consumed_or_replaced_foreign_locks(self):
        admin=self.base/'lock-admin';admin.mkdir()
        with delivery.commit_locks(admin) as index_lock:
            index_lock.write_text('Own candidate index')
            delivery.install_commit_index(index_lock,admin/'index')
            index_lock.write_text('Foreign next actor lock')
            # Model replacement by an independent actor, without touching its file.
            head=admin/'HEAD.lock';saved=admin/'consumed-own-head-lock';head.rename(saved)
            head.write_text('Foreign HEAD lock')
        self.assertEqual((admin/'index.lock').read_text(),'Foreign next actor lock')
        self.assertEqual((admin/'HEAD.lock').read_text(),'Foreign HEAD lock')
        self.assertEqual((admin/'index').read_text(),'Own candidate index')
        with self.assertRaisesRegex(Rejected,'concurrent Git operation'):
            with delivery.commit_locks(admin):pass
        self.assertEqual((admin/'index.lock').read_text(),'Foreign next actor lock')
        clean_admin=self.base/'clean-lock-admin';clean_admin.mkdir()
        with delivery.commit_locks(clean_admin):pass
        self.assertFalse((clean_admin/'index.lock').exists());self.assertFalse((clean_admin/'HEAD.lock').exists())

    def test_late_foreign_staging_cannot_enter_checked_commit(self):
        self.seed(); self.export_policy()
        own=self.root/'company/README.md';own.write_text(own.read_text()+'\nOwn candidate\n')
        foreign=self.root/'company/projects/foreign.txt';foreign.write_text('Unapproved preserved bytes')
        location=history.repository_location; calls=0; staged=[]
        def racing(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==3:
                delivery.git(self.root,'add','company/projects/foreign.txt')
                staged.append(delivery.git(self.root,'write-tree'))
            return location(*args,**kwargs)
        old=delivery.git(self.root,'rev-parse','HEAD')
        with patch.object(history,'repository_location',side_effect=racing):
            with self.assertRaisesRegex(Rejected,'index changed'):
                delivery.commit(self.root,['company/README.md'],'Checked candidate only')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),old)
        self.assertEqual(delivery.git(self.root,'write-tree'),staged[0])
        self.assertEqual(foreign.read_text(),'Unapproved preserved bytes')

    def test_native_hook_tree_change_rejects_before_publication(self):
        self.seed();old=delivery.git(self.root,'rev-parse','HEAD');original=delivery.git
        own=self.root/'company/README.md';own.write_text(own.read_text()+'\nOwn candidate\n')
        def changed(root,*args,**kwargs):
            if Path(root)!=self.root and 'commit' in args:
                extra=Path(root)/'company/projects/native-added.txt';extra.write_text('Native changed candidate')
                original(root,'add','company/projects/native-added.txt')
            return original(root,*args,**kwargs)
        with patch.object(delivery,'git',side_effect=changed):
            with self.assertRaisesRegex(Rejected,'hook changed checked commit tree'):
                delivery.commit(self.root,['company/README.md'],'Exact tree')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),old)
        self.assertEqual(delivery.git(self.root,'diff','--cached','--name-only'),'')

    def test_native_commit_rejection_preserves_original_branch_and_index(self):
        self.seed();old=delivery.git(self.root,'rev-parse','HEAD');tree=delivery.git(self.root,'write-tree');original=delivery.git
        own=self.root/'company/README.md';own.write_text(own.read_text()+'\nOwn candidate\n')
        def reject(root,*args,**kwargs):
            if Path(root)!=self.root and 'commit' in args:raise Rejected('Native commit hook rejected fixture')
            return original(root,*args,**kwargs)
        with patch.object(delivery,'git',side_effect=reject):
            with self.assertRaisesRegex(Rejected,'Native commit hook'):
                delivery.commit(self.root,['company/README.md'],'Rejected native commit')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),old);self.assertEqual(delivery.git(self.root,'write-tree'),tree)

    def test_config_drift_after_scratch_commit_blocks_publication(self):
        self.seed();old=delivery.git(self.root,'rev-parse','HEAD');original=delivery.git
        own=self.root/'company/README.md';own.write_text(own.read_text()+'\nOwn candidate\n')
        def drift(root,*args,**kwargs):
            value=original(root,*args,**kwargs)
            if Path(root)!=self.root and 'commit' in args:original(self.root,'config','company.fixtureChanged','true')
            return value
        with patch.object(delivery,'git',side_effect=drift):
            with self.assertRaisesRegex(Rejected,'config/hooks changed'):
                delivery.commit(self.root,['company/README.md'],'Must preserve native config')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),old)

    def test_compare_and_swap_preserves_external_new_head(self):
        self.seed();old=delivery.git(self.root,'rev-parse','HEAD');other=self.base/'concurrent-clone'
        delivery.git(self.root,'clone','--no-local',str(self.root),str(other));delivery.identity(other)
        (other/'company/projects/concurrent.txt').write_text('Concurrent actor')
        delivery.git(other,'add','company/projects/concurrent.txt');delivery.git(other,'commit','-m','Concurrent actor')
        foreign=delivery.git(other,'rev-parse','HEAD');delivery.git(self.root,'fetch',str(other),'HEAD')
        own=self.root/'company/README.md';own.write_text(own.read_text()+'\nOwn candidate\n');original=delivery.git
        def moved(root,*args,**kwargs):
            if 'update-ref' in args and '--no-deref' in args:original(root,*args[:args.index('update-ref')],'update-ref','--no-deref','refs/heads/main',foreign,old)
            return original(root,*args,**kwargs)
        with patch.object(delivery,'git',side_effect=moved):
            with self.assertRaises(Rejected):delivery.commit(self.root,['company/README.md'],'Must reject moved parent')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),foreign)
        self.assertIn('Own candidate',own.read_text())

    def test_interrupted_index_install_preserves_published_commit_and_old_index(self):
        self.seed();tree=delivery.git(self.root,'write-tree');old=delivery.git(self.root,'rev-parse','HEAD')
        own=self.root/'company/README.md';own.write_text(own.read_text()+'\nOwn candidate\n')
        with patch.object(delivery,'install_commit_index',side_effect=OSError('Observed interrupted install')):
            with self.assertRaisesRegex(OSError,'interrupted install'):
                delivery.commit(self.root,['company/README.md'],'Preserve interruption')
        self.assertNotEqual(delivery.git(self.root,'rev-parse','HEAD'),old)
        self.assertEqual(delivery.git(self.root,'write-tree'),tree)
        self.assertTrue(delivery.git(self.root,'diff','--cached','--name-only'))
        self.assertIn('Own candidate',own.read_text())
        with self.assertRaisesRegex(Rejected,'foreign staged'):
            delivery.commit(self.root,['company/README.md'],'No automatic recovery rewrite')

    def test_initial_and_linked_worktree_commit_keep_exact_tree_after_cleanup(self):
        # Fixture has no HEAD: the private per-worktree initial ref must not leak.
        delivery.git(self.root,'init','-b','main');delivery.identity(self.root)
        paths=[p for p in delivery.git(self.root,'ls-files','--others','--exclude-standard','-z').split('\x00') if p]
        projections=[str(p.relative_to(self.root)) for h in ['.agents','.claude'] for p in (self.root/h/'skills').iterdir() if p.is_symlink()]
        paths=sorted(set(paths+projections))
        initial=delivery.commit(self.root,paths,'Initial checked company')
        self.assertEqual(delivery.git(self.root,'write-tree'),delivery.git(self.root,'rev-parse',initial+'^{tree}'))
        self.assertEqual(delivery.git(self.root,'for-each-ref','--format=%(refname)','refs/worktree'),'')
        linked=self.base/'linked';delivery.git(self.root,'worktree','add','-b','codex/linked',str(linked),initial)
        own=linked/'company/README.md';own.write_text(own.read_text()+'\nLinked candidate\n')
        committed=delivery.commit(linked,['company/README.md'],'Linked checked commit')
        self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),initial)
        self.assertEqual(delivery.git(linked,'write-tree'),delivery.git(linked,'rev-parse',committed+'^{tree}'))

    def test_split_sparse_and_relative_hooks_fail_closed(self):
        self.seed();old=delivery.git(self.root,'rev-parse','HEAD')
        own=self.root/'company/README.md';own.write_text(own.read_text()+'\nOwn candidate\n')
        for key,value,reason in [('core.splitIndex','true','split/sparse'),('index.sparse','true','split/sparse'),('core.hooksPath','untracked-hooks','relative native')]:
            with self.subTest(key=key):
                # Local fixture config is never used for a native commit in this negative.
                prior=delivery.git(self.root,'config','--get',key,check=False)
                delivery.git(self.root,'config',key,value)
                try:
                    with self.assertRaisesRegex(Rejected,reason):delivery.commit(self.root,['company/README.md'],'Unsupported index/config')
                finally:
                    if prior.returncode==0:delivery.git(self.root,'config',key,prior.stdout.strip())
                    else:delivery.git(self.root,'config','--unset',key)
                self.assertEqual(delivery.git(self.root,'rev-parse','HEAD'),old)

    def test_external_delivery_requires_explicit_policy_before_network(self):
        self.seed();cfg=config(self.root);cfg['permissions'].update(external_delivery=True,remotes=['https://example.invalid/synthetic.git']);write(self.root/'company/config.yaml',cfg)
        delivery.git(self.root,'add','company/config.yaml');delivery.git(self.root,'commit','-m','Fixture remote authority')
        with self.assertRaisesRegex(Rejected,'export policy missing'):delivery.deliver(self.root,'https://example.invalid/synthetic.git')
