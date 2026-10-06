"""Start-of-work Git checks on isolated companies; no employee/live accounts."""
from pathlib import Path
import shutil
from unittest.mock import patch
import subprocess
import unittest
from checks.test_system import Fixture, TEMPLATE
import delivery
import product
from core import Rejected, config, load, write


class Preflight(Fixture):
    def common(self):
        self.seed()
        remote = self.base / 'common.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        a, b = self.base/'alice', self.base/'bob'
        for directory in [a,b]:
            delivery.prepare(self.root, str(remote), 'main', directory)
        return remote, a, b

    def change(self, root, relative, text):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        delivery.commit(root, [relative], 'Synthetic authorized change')

    def test_receive_latest_and_deliver_other_employee_without_manual_sync(self):
        remote, a, b = self.common()
        initial = self.cli('preflight', root=b)
        self.assertEqual(initial['mode'], 'current')
        self.change(a, 'work/alice.txt', 'Alice completed an artifact')
        delivery.deliver(a, str(remote))
        fresh = self.cli('preflight', root=b)
        self.assertEqual(fresh['mode'], 'updated')
        self.assertEqual((b/'work/alice.txt').read_text(encoding="utf-8"), 'Alice completed an artifact')
        self.assertEqual(self.cli('preflight', root=b)['mode'], 'current')
        self.change(b, 'work/bob.txt', 'Bob next action')
        ahead = self.cli('preflight', root=b)
        self.assertTrue(ahead['pending_delivery'])
        delivery.deliver(b, str(remote))
        self.assertEqual(self.cli('preflight', root=a)['mode'], 'updated')
        self.assertEqual((a/'work/bob.txt').read_text(encoding="utf-8"), 'Bob next action')

    def test_dirty_staged_and_untracked_preserved_even_with_new_remote(self):
        remote, a, b = self.common()
        self.change(a, 'work/shared.txt', 'New remote work'); delivery.deliver(a,str(remote))
        head = delivery.git(b,'rev-parse','HEAD')
        (b/'company/projects/README.md').write_text('Unfinished local work', encoding="utf-8")
        (b/'work').mkdir(exist_ok=True); (b/'work/untracked.txt').write_text('Never auto-commit this', encoding="utf-8")
        delivery.git(b,'add','company/projects/README.md')
        before = delivery.git(b,'status','--porcelain','--untracked-files=all')
        blocked = self.cli('preflight',root=b,success=False)
        self.assertIn('Uncommitted',blocked['reason'])
        self.assertEqual(delivery.git(b,'rev-parse','HEAD'),head)
        self.assertEqual(delivery.git(b,'status','--porcelain','--untracked-files=all'),before)
        self.assertEqual((b/'work/untracked.txt').read_text(encoding="utf-8"),'Never auto-commit this')
        self.assertFalse((b/'work/shared.txt').exists())

    def test_diverged_keeps_both_committed_histories(self):
        remote,a,b=self.common()
        self.change(a,'work/a.txt','A'); self.change(b,'work/b.txt','B')
        delivery.deliver(a,str(remote))
        before=delivery.git(b,'rev-parse','HEAD')
        blocked=self.cli('preflight',root=b,success=False)
        self.assertIn('diverged',blocked['reason'])
        self.assertEqual(delivery.git(b,'rev-parse','HEAD'),before)
        self.assertEqual((b/'work/b.txt').read_text(encoding="utf-8"),'B')
        self.assertFalse((b/'work/a.txt').exists())

    def test_offline_reports_local_snapshot_without_freshness(self):
        remote,a,b=self.common()
        original=delivery.git
        def unavailable(root,*args,**kwargs):
            if 'fetch' in args:
                return subprocess.CompletedProcess([],1,'','synthetic offline')
            return original(root,*args,**kwargs)
        head=delivery.git(b,'rev-parse','HEAD')
        with patch.object(delivery,'git',side_effect=unavailable): result=delivery.preflight(b)
        self.assertEqual(result['status'],'blocked');self.assertFalse(result['fresh'])
        self.assertEqual(result['local_head'],head)
        self.assertEqual(delivery.git(b,'rev-parse','HEAD'),head)

    def test_wrong_company_and_unapproved_external_are_rejected(self):
        remote,a,b=self.common()
        cfg=config(a);cfg['id']='other-company';write(a/'company/config.yaml',cfg)
        delivery.commit(a,['company/config.yaml'],'Other company identity')
        delivery.git(a,'push',str(remote),'HEAD:main')
        blocked=self.cli('preflight',root=b,success=False)
        self.assertIn('different company',blocked['reason'])
        self.assertEqual(config(b)['id'],'test-company')
        original=delivery.git
        def forbid_network(root,*args,**kwargs):
            self.assertNotIn('fetch',args,'Unapproved external URL must be refused before any fetch')
            return original(root,*args,**kwargs)
        with patch.object(delivery,'git',side_effect=forbid_network), self.assertRaisesRegex(Rejected,'external remote not authorized'):
            delivery.preflight(b,'https://example.invalid/unapproved.git')
        directory=self.base/'product';shutil.copytree(TEMPLATE,directory/'template',symlinks=True,ignore=shutil.ignore_patterns('__pycache__'))
        self.seed(directory)
        delivery.git(directory,'remote','add','origin','https://example.invalid/not-approved.git')
        with self.assertRaisesRegex(Rejected,'approved repository'):
            product.product_preflight(directory)

    def test_fetch_timeout_is_bounded_and_preserves_snapshot(self):
        remote,a,b=self.common()
        original=delivery.git
        def timeout(root,*args,**kwargs):
            if 'fetch' in args:
                self.assertEqual(kwargs['timeout'],30)
                self.assertTrue(kwargs['noninteractive'])
                raise subprocess.TimeoutExpired('synthetic fetch',30)
            return original(root,*args,**kwargs)
        head=delivery.git(b,'rev-parse','HEAD')
        with patch.object(delivery,'git',side_effect=timeout): result=delivery.preflight(b)
        self.assertIn('timed out',result['reason']);self.assertFalse(result['fresh'])
        self.assertEqual(delivery.git(b,'rev-parse','HEAD'),head)

    def test_fast_forward_uses_received_validator_and_blocks_invalid_version(self):
        remote,a,b=self.common()
        self.change(a,'scripts/system.py','import sys\n# New-version validation intentionally fails on this negative fixture\nsys.exit(2)\n')
        delivery.deliver(a,str(remote))
        shared=delivery.git(a,'rev-parse','HEAD')
        blocked=self.cli('preflight',root=b,success=False)
        self.assertIn('its own validator',blocked['reason'])
        self.assertEqual(blocked['local_head'],shared)
        self.assertEqual(delivery.git(b,'rev-parse','HEAD'),shared)
        self.assertFalse(blocked['fresh'])

    def test_unfinished_git_and_detached_and_no_remote_do_not_change_head(self):
        self.seed()
        head=delivery.git(self.root,'rev-parse','HEAD')
        self.assertIn('not configured',self.cli('preflight',success=False)['reason'])
        remote,a,b=self.common_after_seed()
        marker=b/'.git/MERGE_HEAD';marker.write_text(head+'\n', encoding="utf-8")
        blocked=self.cli('preflight',root=b,success=False)
        self.assertEqual(blocked['operation'],'MERGE_HEAD')
        self.assertEqual(marker.read_text(encoding="utf-8"),head+'\n')
        delivery.git(a,'checkout','--detach',head)
        self.assertIn('Detached',self.cli('preflight',root=a,success=False)['reason'])
        self.assertEqual(delivery.git(a,'rev-parse','HEAD'),head)

    def common_after_seed(self):
        remote=self.base/'common.git'
        delivery.git(self.root,'clone','--bare',str(self.root),str(remote))
        a,b=self.base/'alice',self.base/'bob'
        for directory in [a,b]:delivery.prepare(self.root,str(remote),'main',directory)
        return remote,a,b

    def test_ignored_local_file_cannot_be_overwritten_by_fast_forward(self):
        remote,a,b=self.common()
        (b/'.local').mkdir();(b/'.local/preserved.txt').write_text('Untracked ignored personal work', encoding="utf-8")
        (a/'.local').mkdir();(a/'.local/preserved.txt').write_text('Tracked collision', encoding="utf-8")
        delivery.git(a,'add','-f','.local/preserved.txt')
        delivery.git(a,'commit','-m','Synthetic ignored-path collision')
        delivery.deliver(a,str(remote))
        head=delivery.git(b,'rev-parse','HEAD')
        blocked=self.cli('preflight',root=b,success=False)
        self.assertIn('Fast-forward refused',blocked['reason'])
        self.assertEqual((b/'.local/preserved.txt').read_text(encoding="utf-8"),'Untracked ignored personal work')
        self.assertEqual(delivery.git(b,'rev-parse','HEAD'),head)


if __name__=='__main__':unittest.main()
