"""Regressions found by cross-repository review; synthetic data only."""
import copy
import datetime as dt
import os
from pathlib import Path
from unittest.mock import patch
from test_system import Fixture, GitAcceptance
from core import Rejected, config, digest, load, now, task_file, write
import delivery, knowledge, lifecycle, operations, validation


class Consistency(Fixture):
    def test_cancelled_incident_preserves_terminal_state_and_bytes(self):
        self.export_config(); self.task()
        operations.decide(self.root, 'test-task', load(task_file(self.root, 'test-task'))['revision'], 'cancel', 'Owner cancellation', 'test-owner')
        file = task_file(self.root, 'test-task'); before = file.read_bytes()
        with self.assertRaisesRegex(Rejected, 'cancelled'):
            operations.incident(self.root, 'test-task', 'failure', 'assumption', 'repair')
        self.assertEqual(file.read_bytes(), before)
        with self.assertRaisesRegex(Rejected, 'cancelled'):
            operations.execute(self.root, 'test-task', self.artifact(), self.critical())

    def test_metrics_reject_bad_primary_metadata_despite_valid_output(self):
        self.export_config(); self.input(); artifact = self.artifact()
        criterion = {'kind': 'metrics', 'expected_count': 2, 'expected_total': 5,
                     'period': artifact['period'], 'unit': artifact['unit']}
        original = self.source()
        for field, value in [('company_id', 'wrong-company'), ('account', 'wrong-account'),
                             ('scope', 'wrong-scope'), ('period', 'wrong-period'),
                             ('unit', 'wrong-unit'), ('complete', False),
                             ('observed_at', '2000-01-01T00:00:00+00:00')]:
            with self.subTest(field=field):
                write(self.root / 'company/sources/input.json', {**original, field: value})
                artifact['sources'][0]['sha256'] = digest(self.root / 'company/sources/input.json')
                with self.assertRaises(Rejected): validation.result(self.root, artifact, criterion)

    def test_metrics_require_unambiguous_independent_source_admission(self):
        self.input(); artifact = self.artifact()
        criterion = {'kind': 'metrics', 'expected_count': 2, 'period': artifact['period'], 'unit': artifact['unit']}
        with self.assertRaisesRegex(Rejected, 'source'): validation.result(self.root, artifact, criterion)
        self.export_config(); cfg = config(self.root)
        cfg['sources']['second-source'] = copy.deepcopy(cfg['sources']['test-data'])
        write(self.root / 'company/config.yaml', cfg)
        with self.assertRaisesRegex(Rejected, 'ambiguous'): validation.result(self.root, artifact, criterion)
        artifact['sources'][0]['source_id'] = 'test-data'
        self.assertTrue(validation.result(self.root, artifact, criterion))

    def test_foreign_index_is_not_used_or_modified_by_delivery(self):
        self.seed(); original_head = delivery.git(self.root, 'rev-parse', 'HEAD')
        alien_index = self.base / 'foreign.index'
        alien_index.write_bytes((self.root / '.git/index').read_bytes()); before = digest(alien_index)
        f = self.root / 'README.md'; f.write_text(f.read_text() + '\nPermitted result\n')
        with patch.dict(os.environ, {'GIT_INDEX_FILE': str(alien_index)}):
            head = delivery.commit(self.root, ['README.md'], 'Scoped result')
        self.assertNotEqual(head, original_head); self.assertEqual(digest(alien_index), before)
        self.assertEqual(delivery.git(self.root, 'status', '--porcelain'), '')

    def test_relative_prepare_is_relative_to_caller_not_source_checkout(self):
        self.seed(); remote = self.base / 'shared.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        cwd = Path.cwd()
        try:
            os.chdir(self.base)
            prepared = delivery.prepare(self.root, str(remote), 'main', 'relative-candidate')
        finally: os.chdir(cwd)
        self.assertEqual(Path(prepared['path']), self.base / 'relative-candidate')
        self.assertFalse((self.root / 'relative-candidate').exists())

    def test_corrupt_optional_cache_rebuilds_but_primary_errors_surface(self):
        file = self.root / 'company/sources/lesson/source.yaml'
        write(file, {'id': 'lesson', 'owner': 'test-owner', 'origin': 'synthetic', 'status': 'observation', 'summary': 'needle'})
        cache = self.root / '.system/cache/graph.json'; cache.parent.mkdir(parents=True)
        for malformed in ['{', '[]', '{"schema_version":99}']:
            with self.subTest(cache=malformed):
                cache.write_text(malformed)
                self.assertEqual(len(knowledge.search(self.root, 'needle')['hits']), 1)
                self.assertEqual(load(cache), knowledge.build(self.root))
        data = load(file); data['material'] = 'company/sources/missing.txt'; write(file, data)
        with self.assertRaisesRegex(Rejected, 'material missing'): knowledge.search(self.root, 'needle')

    def test_reconciliation_requires_complete_explicit_comparable_metadata(self):
        left = self.source()
        for field in ['company_id', 'account', 'scope', 'period', 'unit', 'complete']:
            with self.subTest(field=field):
                missing = copy.deepcopy(left); missing.pop(field)
                with self.assertRaises(Rejected): operations.reconcile(missing, missing)
        right = {**left, 'account': 'other-accepted-account'}
        self.assertEqual(operations.reconcile(left, right)['matched'], 2)

    def test_resume_and_supersede_keep_original_verification_in_task_history(self):
        self.task(); verified = operations.execute(self.root, 'test-task', self.artifact(), self.critical())
        original = copy.deepcopy(verified)
        resumed = operations.decide(self.root, 'test-task', verified['revision'], 'resume', 'Reconcile versions', 'test-owner')
        archived = resumed['history'][-1]['detail']['previous_verification']
        self.assertEqual(archived['evidence'], original['evidence'])
        self.assertEqual(archived['critical'], original['critical'])
        self.assertEqual(archived['output'], original['output'])
        verified = operations.execute(self.root, 'test-task', self.artifact(), self.critical())
        changed = self.artifact(); changed['summary'] = 'Second accepted presentation of the same source'
        newer = operations.execute(self.root, 'test-task', changed, self.critical())
        archived = [h['detail'] for h in newer['history'] if h['action'] == 'superseded-result'][-1]
        self.assertEqual(archived['evidence'], verified['evidence'])
        self.assertEqual(archived['critical'], verified['critical'])


class EvolutionConsistency(GitAcceptance):
    # Only the new cases are collected; importing GitAcceptance above retains its own suite.
    def test_interrupted_finish_update_preserves_original_rollback_base(self):
        p, release = self.product_release(); company = self.base / 'client'
        lifecycle.create(release, company, 'client-company', 'test-owner')
        previous = load(company / '.system/base.json')['installed']; head = delivery.git(company, 'rev-parse', 'HEAD')
        f = p / 'template/standards/runtime.md'; f.write_text(f.read_text() + '\nSynthetic compatible update\n')
        delivery.git(p, 'add', 'template'); delivery.git(p, 'commit', '-m', 'Synthetic update')
        import product
        product.release(p, release); target = delivery.git(release, 'rev-parse', 'main')
        candidate = self.base / 'interrupted'; original = lifecycle.git
        def interrupted(root, *args, **kwargs):
            if args[0] == 'commit': raise Rejected('interrupted after metadata')
            return original(root, *args, **kwargs)
        with patch.object(lifecycle, 'git', side_effect=interrupted), self.assertRaisesRegex(Rejected, 'interrupted'):
            lifecycle.update(company, release, candidate)
        lifecycle.finish_update(candidate, target, head)
        self.assertEqual(load(candidate / '.system/base.json')['previous'], previous)
        self.assertEqual(lifecycle.rollback(candidate, self.base / 'rolled-back')['status'], 'verified-candidate')

    def test_relative_company_update_backup_restore_and_rollback(self):
        p, release = self.product_release(); cwd = Path.cwd()
        try:
            os.chdir(self.base)
            company = self.base / 'relative-company'
            lifecycle.create(release, 'relative-company', 'client-company', 'test-owner')
            lifecycle.backup(company, 'relative-backup')
            lifecycle.restore('relative-backup', 'relative-restored')
            guide = p / 'template/docs/company-system-guide.html'; original_guide = digest(guide)
            guide.write_text(guide.read_text() + '\n<!-- Synthetic release guide change -->\n')
            f = p / 'template/standards/runtime.md'; f.write_text(f.read_text() + '\nSynthetic compatible addition\n')
            delivery.git(p, 'add', 'template'); delivery.git(p, 'commit', '-m', 'Synthetic addition')
            import product
            product.release(p, release)
            self.assertEqual(lifecycle.update(company, release, 'relative-update')['status'], 'verified-candidate')
            self.assertEqual(lifecycle.rollback(self.base / 'relative-update', 'relative-rollback')['status'], 'verified-candidate')
            self.assertEqual(digest(self.base / 'relative-rollback/docs/company-system-guide.html'), original_guide)
        finally: os.chdir(cwd)
        for name in ['relative-company', 'relative-backup', 'relative-restored', 'relative-update', 'relative-rollback']:
            self.assertTrue((self.base / name).is_dir())
        self.assertFalse((company / 'relative-backup').exists())

    def test_delivered_metrics_keep_historical_admission_after_age_and_config_change(self):
        self.export_config(); self.task(); operations.execute(self.root, 'test-task', self.artifact(), self.critical()); self.seed()
        remote = self.base / 'common.git'; delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        delivery.deliver(self.root, str(remote), task_id='test-task')
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
        class FutureClock(dt.datetime):
            @classmethod
            def now(cls, tz=None): return future.astimezone(tz) if tz else future.replace(tzinfo=None)
        cfg = config(self.root); cfg['sources']['test-data']['account'] = 'new-accepted-account'
        write(self.root / 'company/config.yaml', cfg)
        with patch.object(validation.dt, 'datetime', FutureClock):
            self.assertTrue(validation.repository(self.root)['checked'])
            self.assertTrue(validation.repository(self.root, freshness=False)['checked'])
            delivery.commit(self.root, ['company/config.yaml'], 'Later accepted configuration')
            with self.assertRaises(Rejected): validation.result(self.root, self.artifact(), load(task_file(self.root, 'test-task'))['acceptance'])
            lifecycle.backup(self.root, self.base / 'historical-backup')
            restored = self.base / 'historical-restored'; lifecycle.restore(self.base / 'historical-backup', restored)
        self.assertEqual(load(task_file(restored, 'test-task')), load(task_file(self.root, 'test-task')))

    def test_blocked_update_cannot_finish_without_owner_binding_reconciliation(self):
        p, release = self.product_release(); company = self.base / 'client'
        lifecycle.create(release, company, 'client-company', 'test-owner')
        operations.intake(company, 'blocked-task', 'Pending source decision', 'test-owner', {'kind': 'action-summary'}, True)
        operations.incident(company, 'blocked-task', 'missing data', 'data available', 'request data')
        delivery.commit(company, ['work/blocked-task/task.json'], 'Record blocked work')
        head = delivery.git(company, 'rev-parse', 'HEAD')
        f = p / 'template/standards/runtime.md'; f.write_text(f.read_text() + '\nSynthetic compatible addition\n')
        delivery.git(p, 'add', 'template'); delivery.git(p, 'commit', '-m', 'Synthetic addition')
        import product
        product.release(p, release); target = delivery.git(release, 'rev-parse', 'main')
        candidate = self.base / 'blocked-update'; update = lifecycle.update(company, release, candidate)
        self.assertEqual(update['status'], 'needs-reconciliation')
        with self.assertRaisesRegex(Rejected, 'reconcile'):
            lifecycle.finish_update(candidate, target, head)
        t = load(task_file(candidate, 'blocked-task'))
        operations.decide(candidate, 'blocked-task', t['revision'], 'resume', 'Owner reconciles exact changed methods', 'test-owner')
        delivery.git(candidate, 'add', '--', 'work/blocked-task/task.json')
        self.assertEqual(lifecycle.finish_update(candidate, target, head)['status'], 'verified-candidate')


# unittest must not rediscover the imported base test classes or inherited cases.
def load_tests(loader, tests, pattern):
    import unittest
    suite = unittest.TestSuite()
    for cls in (Consistency, EvolutionConsistency):
        for name in cls.__dict__:
            if name.startswith('test_'): suite.addTest(cls(name))
    return suite
