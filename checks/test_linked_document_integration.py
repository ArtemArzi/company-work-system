"""Linked-document checks at public validation, Git and dependency boundaries."""
import copy
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from unittest.mock import patch

from checks import test_system as fixtures
from checks.fixture_helpers import execute_with_current_tokens_for_fixture
from core import Rejected, changed_bindings, config, digest, load, task_file, write
import delivery
import dependencies
import history
import linked_documents
import lifecycle
import operations
import validation


class LinkedDocumentIntegration(fixtures.Fixture):
    consumer = 'company/projects/README.md'

    def rule(self, rule_id='document-limit', consumer=None, source=None, value=8):
        consumer = consumer or self.consumer
        source = source or ['definitions', rule_id]
        cfg = config(self.root)
        cfg[source[0]][source[1]] = value
        cfg.setdefault('documentation', {'schema_version': 1, 'rules': {}})['rules'][rule_id] = {
            'approved_by': 'test-owner', 'template': 'Limit ${limit}.',
            'values': {'limit': {'source': source, 'format': 'integer'}},
            'consumers': [consumer]}
        write(self.root / 'company/config.yaml', cfg)
        file = self.root / consumer
        file.write_text(file.read_text(encoding='utf-8') + '\n' + linked_documents.render(cfg, rule_id),
                        encoding='utf-8', newline='\n')

    def change_limit(self, value, rule_id='document-limit', repair=True):
        cfg = config(self.root)
        spec = cfg['documentation']['rules'][rule_id]
        source = spec['values']['limit']['source']
        old = linked_documents.render(cfg, rule_id)
        cfg[source[0]][source[1]] = value
        write(self.root / 'company/config.yaml', cfg)
        if repair:
            for consumer in spec['consumers']:
                file = self.root / consumer
                file.write_text(file.read_text(encoding='utf-8').replace(old, linked_documents.render(cfg, rule_id)),
                                encoding='utf-8', newline='\n')

    def native_hook(self):
        hooks = self.base / 'native-hooks'
        hooks.mkdir()
        marker = self.base / 'native-hook-ran'
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\nprintf ran > ' + shlex.quote(str(marker)) + '\n', encoding='utf-8')
        hook.chmod(0o700)
        delivery.git(self.root, 'config', 'core.hooksPath', str(hooks))
        return marker

    def assert_preserved(self, before, root=None):
        root = root or self.root
        self.assertEqual(delivery.git(root, 'rev-parse', 'HEAD'), before['head'])
        self.assertEqual(delivery.git(root, 'write-tree'), before['index'])
        for relative, content in before['files'].items():
            self.assertEqual((root / relative).read_bytes(), content)

    def snapshot(self, root=None, paths=None):
        root = root or self.root
        return {'head': delivery.git(root, 'rev-parse', 'HEAD'),
                'index': delivery.git(root, 'write-tree'),
                'files': {p: (root / p).read_bytes() for p in (paths or ['company/config.yaml', self.consumer])}}

    def test_working_validation_always_rejects_stale_document(self):
        self.assertEqual(Path(linked_documents.__file__).resolve().parent, fixtures.TEMPLATE / 'scripts')
        self.rule()
        validation.repository(self.root)
        self.change_limit(16, repair=False)
        for freshness in (True, False):
            with self.subTest(freshness=freshness), self.assertRaisesRegex(Rejected, 'documentation'):
                validation.repository(self.root, freshness=freshness)

    def test_partial_commit_rejects_stale_blob_before_native_hooks_and_preserves_work(self):
        self.rule(); self.seed()
        self.change_limit(16)
        validation.repository(self.root)
        marker = self.native_hook()
        before = self.snapshot()
        with self.assertRaises(Rejected):
            delivery.commit(self.root, ['company/config.yaml'], 'Must include updated consumer')
        self.assert_preserved(before)
        self.assertFalse(marker.exists())
        # Selecting both files keeps the ordinary native hook path intact.
        head = delivery.commit(self.root, ['company/config.yaml', self.consumer], 'Update linked pair')
        self.assertNotEqual(head, before['head'])
        self.assertTrue(marker.exists())
        self.assertEqual(delivery.git(self.root, 'show', f'{head}:{self.consumer}'),
                         (self.root / self.consumer).read_text(encoding='utf-8').strip())

    def test_foreign_staging_is_preserved_on_rejection(self):
        self.rule(); self.seed(); self.change_limit(16)
        delivery.git(self.root, 'add', 'company/config.yaml')
        before = self.snapshot()
        with self.assertRaisesRegex(Rejected, 'foreign staged'):
            delivery.commit(self.root, [self.consumer], 'Preserve another actor staging')
        self.assert_preserved(before)

    def test_noop_selection_cannot_accept_stale_head_with_repaired_working_document(self):
        self.rule(); self.seed(); self.change_limit(16, repair=False)
        delivery.git(self.root, 'add', 'company/config.yaml')
        delivery.git(self.root, 'commit', '-m', 'Synthetic stale tip')
        cfg = config(self.root)
        file = self.root / self.consumer
        file.write_text(file.read_text(encoding='utf-8').replace('Limit 8.', 'Limit 16.'), encoding='utf-8', newline='\n')
        validation.repository(self.root)
        self.assertIn('Limit 16.', linked_documents.render(cfg, 'document-limit'))
        before = self.snapshot()
        self.assertEqual(history.preview_tree(self.root, ['release.yaml'], before['head']),
                         delivery.git(self.root, 'rev-parse', before['head'] + '^{tree}'))
        with self.assertRaises(Rejected):
            delivery.commit(self.root, ['release.yaml'], 'Unchanged selection still checks tip')
        self.assert_preserved(before)

    def test_clean_smudge_cannot_hide_outdated_raw_candidate(self):
        self.rule(); self.seed(); self.change_limit(16)
        script = self.base / 'document-filter.py'
        script.write_text("import sys\nvalue = sys.stdin.buffer.read()\n"
                          "old, new = (b'Limit 16.', b'Limit 8.') if sys.argv[1] == 'clean' else (b'Limit 8.', b'Limit 16.')\n"
                          "sys.stdout.buffer.write(value.replace(old, new))\n", encoding='utf-8')
        command = shlex.join([sys.executable, str(script)])
        delivery.git(self.root, 'config', 'filter.document-fixture.clean', command + ' clean')
        delivery.git(self.root, 'config', 'filter.document-fixture.smudge', command + ' smudge')
        attributes = self.root / '.gitattributes'
        attributes.write_text(attributes.read_text(encoding='utf-8') + '\n/' + self.consumer + ' filter=document-fixture\n',
                              encoding='utf-8', newline='\n')
        paths = ['company/config.yaml', self.consumer, '.gitattributes']
        tree = history.preview_tree(self.root, paths, delivery.git(self.root, 'rev-parse', 'HEAD'))
        self.assertIn('Limit 8.', delivery.git(self.root, 'show', f'{tree}:{self.consumer}'))
        self.assertIn('Limit 16.', delivery.git(self.root, 'cat-file', '--filters', f'{tree}:{self.consumer}'))
        validation.repository(self.root)
        marker = self.native_hook()
        before = self.snapshot(paths=paths)
        with self.assertRaises(Rejected):
            delivery.commit(self.root, paths, 'Reject outdated raw filtered bytes')
        self.assert_preserved(before)
        self.assertFalse(marker.exists())

    def delivery_race(self, repeated):
        self.rule(); self.seed()
        remote = self.base / 'common.git'
        delivery.git(self.root, 'clone', '--bare', str(self.root), str(remote))
        original = validation.repository
        delivered_heads = []

        def validated_then_changed(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            # A concurrent actor publishes an invalid raw tip after working validation.
            self.change_limit(16, repair=False)
            delivery.git(self.root, 'add', 'company/config.yaml')
            delivery.git(self.root, 'commit', '-m', 'Synthetic concurrent stale tip')
            bad_head = delivery.git(self.root, 'rev-parse', 'HEAD')
            delivered_heads.append(bad_head)
            if repeated:
                delivery.git(self.root, 'push', str(remote), bad_head + ':refs/heads/main')
            file = self.root / self.consumer
            file.write_text(file.read_text(encoding='utf-8').replace('Limit 8.', 'Limit 16.'), encoding='utf-8', newline='\n')
            return result

        before_remote = delivery.git(remote, 'rev-parse', 'HEAD')
        with patch.object(validation, 'repository', side_effect=validated_then_changed):
            with self.assertRaises(Rejected):
                delivery.deliver(self.root, str(remote))
        self.assertEqual(len(delivered_heads), 1)
        expected_remote = delivered_heads[0] if repeated else before_remote
        self.assertEqual(delivery.git(remote, 'rev-parse', 'HEAD'), expected_remote)
        self.assertIn('Limit 16.', (self.root / self.consumer).read_text(encoding='utf-8'))
        self.assertIn('Limit 8.', delivery.git(self.root, 'show', delivered_heads[0] + ':' + self.consumer))

    def test_deliver_checks_raw_captured_head_before_repeated_success(self):
        self.delivery_race(repeated=True)

    def test_deliver_checks_raw_captured_head_before_push(self):
        self.delivery_race(repeated=False)

    def test_product_prefix_checks_selected_tree_and_repeated_delivery(self):
        self.rule()
        product = self.base / 'product'
        shutil.copytree(self.root, product / 'template', symlinks=True)
        self.seed(product)
        cfg = config(product / 'template')
        old = linked_documents.render(cfg, 'document-limit')
        cfg['definitions']['document-limit'] = 16
        write(product / 'template/company/config.yaml', cfg)
        file = product / 'template' / self.consumer
        file.write_text(file.read_text(encoding='utf-8').replace(old, linked_documents.render(cfg, 'document-limit')),
                        encoding='utf-8', newline='\n')
        paths = ['template/company/config.yaml', 'template/' + self.consumer]
        before = self.snapshot(product, paths)
        with self.assertRaises(Rejected):
            delivery.commit(product, paths[:1], 'Reject stale product consumer')
        self.assert_preserved(before, product)
        head = delivery.commit(product, paths, 'Update product linked pair')
        self.assertEqual(delivery.git(product, 'rev-parse', 'HEAD'), head)
        remote = self.base / 'product-common.git'
        delivery.git(product, 'clone', '--bare', str(product), str(remote))
        self.assertTrue(delivery.deliver(product, str(remote))['repeated'])

    def test_cli_check_and_raw_render_are_read_only_and_reject_unknown_rule(self):
        self.rule()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}
        result = self.cli('documentation-check')
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['read_only'])
        rendered = subprocess.run([sys.executable, str(self.root / 'scripts/system.py'), '--root', str(self.root),
                                   'documentation-render', 'document-limit'], capture_output=True, text=True, timeout=30)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertEqual(rendered.stdout, linked_documents.render(config(self.root), 'document-limit'))
        rejected = self.cli('documentation-render', 'unknown-rule', success=False)
        self.assertEqual(rejected['status'], 'rejected')
        for relative, content in before.items():
            self.assertEqual((self.root / relative).read_bytes(), content)
        self.change_limit(16, repair=False)
        self.assertEqual(self.cli('documentation-check', success=False)['status'], 'rejected')

    def test_relevant_document_source_and_registration_changes_invalidate_scope(self):
        selected = 'standards/data-access.md'
        self.rule(consumer=selected)
        task = self.task()
        scope = task['binding_scope']
        self.assertIn('document-limit', scope['facets']['documentation'])
        self.assertIn('scripts/linked_documents.py', scope['paths'])
        self.assertEqual(changed_bindings(self.root, task['bindings'], scope), [])
        file = self.root / selected
        saved = file.read_bytes()
        file.write_bytes(saved + b'\nChanged selected instructions\n')
        self.assertIn(selected, changed_bindings(self.root, task['bindings'], scope))
        file.write_bytes(saved)
        self.change_limit(16, repair=False)
        self.assertIn('config:documentation', changed_bindings(self.root, task['bindings'], scope))
        cfg = config(self.root)
        cfg['documentation']['rules']['document-limit']['consumers'].append(self.consumer)
        write(self.root / 'company/config.yaml', cfg)
        changes = changed_bindings(self.root, task['bindings'], scope)
        self.assertIn(self.consumer, changes)
        self.assertIn('config:documentation', changes)

    def test_unrelated_rule_document_and_definition_do_not_invalidate_scope(self):
        self.rule('selected-rule', 'standards/data-access.md')
        self.rule('other-rule')
        task = self.task()
        scope = task['binding_scope']
        self.assertEqual(set(scope['facets']['documentation']), {'selected-rule'})
        self.assertNotIn(self.consumer, scope['paths'])
        self.change_limit(32, 'other-rule')
        self.rule('additional-unrelated-rule', 'company/README.md')
        self.assertEqual(changed_bindings(self.root, task['bindings'], scope), [])

    def test_config_source_facet_selects_consumers_outside_declared_paths(self):
        self.rule(source=['permissions', 'document_limit'])
        task = self.task()
        scope = task['binding_scope']
        self.assertIn(self.consumer, scope['paths'])
        self.assertIn('document-limit', scope['facets']['documentation'])

    def test_new_empty_documentation_facet_has_no_checker_dependency(self):
        task = self.task()
        scope = task['binding_scope']
        self.assertEqual(scope['facets']['documentation'], {})
        self.assertNotIn('scripts/linked_documents.py', scope['paths'])
        self.rule()
        self.assertEqual(changed_bindings(self.root, task['bindings'], scope), [])

    def test_previous_reader_scope_without_documentation_keeps_original_shape(self):
        task = self.task()
        scope = copy.deepcopy(task['binding_scope'])
        scope['facets'].pop('documentation')
        scope['sha256'] = dependencies.seal(scope)
        task['binding_scope'] = scope
        write(task_file(self.root, task['id']), task)
        before = task_file(self.root, task['id']).read_bytes()
        self.rule(source=['permissions', 'document_limit'])
        expected = {'skill', 'harness', 'inputs', 'source_ids'}
        self.assertEqual(set(scope['selection']), expected)
        self.assertEqual(changed_bindings(self.root, task['bindings'], scope), ['config:permissions'])
        cfg = config(self.root)
        cfg['permissions'].pop('document_limit')
        cfg['definitions']['document-limit'] = 8
        cfg['documentation']['rules']['document-limit']['values']['limit']['source'] = ['definitions', 'document-limit']
        write(self.root / 'company/config.yaml', cfg)
        self.assertEqual(changed_bindings(self.root, task['bindings'], scope), [])
        self.assertTrue(validation.task(self.root, task))
        self.assertEqual(task_file(self.root, task['id']).read_bytes(), before)
        self.assertNotIn('documentation', load(task_file(self.root, task['id']))['binding_scope']['facets'])

    def test_delivered_result_and_historical_scope_remain_byte_exact(self):
        self.rule(consumer='standards/data-access.md')
        self.task()
        task = execute_with_current_tokens_for_fixture(self.root, 'test-task', self.artifact(), self.critical())
        task['delivery'] = {'status': 'delivered', 'result_sha256': task['output']['sha256'],
                            'commit': 'synthetic-historical-proof', 'readback': True}
        write(task_file(self.root, task['id']), task)
        files = ['work/test-task/task.json', task['output']['path'], 'company/sources/input.json']
        before = {p: (self.root / p).read_bytes() for p in files}
        evidence = copy.deepcopy(task['evidence'])
        self.change_limit(16)
        validation.repository(self.root)
        self.assertEqual(load(task_file(self.root, task['id']))['evidence'], evidence)
        for relative, content in before.items():
            self.assertEqual((self.root / relative).read_bytes(), content)

    def test_portable_release_two_company_rules_survive_update_rollback_and_restore(self):
        product, release = fixtures.GitAcceptance.product_release(self)
        initial_version = load(product / 'template/release.yaml')['version']
        companies = []
        source = 'company/sources/design-basis/source.yaml'
        for name, value, consumer in [('alpha', 8, self.consumer), ('beta', 24, 'company/README.md')]:
            root = self.base / name
            lifecycle.create(release, root, name + '-company', 'test-owner')
            rule_id = name + '-limit'
            cfg = config(root)
            cfg['definitions'][rule_id] = value
            cfg['documentation'] = {'schema_version': 1, 'rules': {rule_id: {
                'approved_by': 'test-owner', 'template': 'Limit ${limit}.',
                'values': {'limit': {'source': ['definitions', rule_id], 'format': 'integer'}},
                'consumers': [consumer]}}}
            write(root / 'company/config.yaml', cfg)
            document = root / consumer
            document.write_text(document.read_text(encoding='utf-8') + '\n' + linked_documents.render(cfg, rule_id),
                                encoding='utf-8', newline='\n')
            delivery.commit(root, ['company/config.yaml', consumer], 'Accept independent synthetic company rule')
            task = operations.intake(root, 'historical-note', 'Preserve a source-backed historical note', 'test-owner',
                                     {'kind': 'note'}, True, inputs=[source], skill='company-execute')
            artifact = {'schema_version': 1, 'company_id': name + '-company', 'kind': 'note',
                        'summary': 'An immutable source supports the historical note',
                        'next_action': 'Read the note and its original source', 'limitations': ['Synthetic local scenario'],
                        'sources': [{'path': source, 'sha256': digest(root / source)}]}
            critical = {'request_alignment': 'The note preserves the accepted original source',
                        'counterexample': 'A current method document cannot replace historical source bytes',
                        'limitations': [], 'references': [source]}
            result = execute_with_current_tokens_for_fixture(root, task['id'], artifact, critical)
            delivery.commit(root, ['work/historical-note/task.json', result['output']['path']], 'Save verified synthetic note')
            remote = self.base / (name + '-common.git')
            delivery.git(root, 'clone', '--bare', str(root), str(remote))
            receipt = delivery.deliver(root, str(remote), task_id=task['id'])
            self.assertEqual(receipt['status'], 'readback-confirmed')
            delivered_task = load(task_file(root, task['id']))
            self.assertEqual(delivered_task['delivery']['status'], 'delivered')
            baseline = delivery.git(root, 'rev-parse', 'HEAD')
            self.assertEqual(delivery.git(remote, 'rev-parse', 'HEAD'), baseline)
            protected = {p: (root / p).read_bytes() for p in delivery.git(root, 'ls-files', '-z').split('\0')
                         if p.startswith(('company/', 'work/'))}
            companies.append({'name': name, 'root': root, 'remote': remote, 'base': baseline, 'value': value,
                              'consumer': consumer, 'rule': rule_id, 'config': copy.deepcopy(config(root)),
                              'protected': protected, 'history': copy.deepcopy(delivered_task['history'])})

        # Transfer a real release-only bundle containing the shared checker.
        runtime = product / 'template/standards/runtime.md'
        runtime.write_text(runtime.read_text(encoding='utf-8') + '\nSynthetic linked-document release clarification.\n',
                           encoding='utf-8', newline='\n')
        meta = load(product / 'template/release.yaml')
        parts = meta['version'].split('.')
        meta['version'] = '.'.join([*parts[:2], str(int(parts[2]) + 1)])
        write(product / 'template/release.yaml', meta)
        delivery.git(product, 'add', 'template')
        delivery.git(product, 'commit', '-m', 'Release synthetic linked-document clarification')
        target = fixtures.product.release(product, release)
        bundle = self.base / 'linked-document-release.bundle'
        exported = fixtures.product.package(release, bundle)
        self.assertEqual(exported['release_sha'], target)
        self.assertEqual(exported['bundle_sha256'], digest(bundle))
        received = self.base / 'received-linked-document-release.git'
        delivery.git(self.base, 'clone', '--mirror', str(bundle), str(received))
        lifecycle.release_isolated(received)
        self.assertEqual(delivery.git(received, 'rev-parse', 'refs/heads/main'), target)
        self.assertNotIn('PRIVATE-DEVELOPMENT', delivery.git(received, 'rev-list', '--objects', '--all'))
        delivery.git(received, 'cat-file', '-e', target + ':scripts/linked_documents.py')

        def preserved(record, root):
            self.assertEqual(config(root), record['config'])
            for relative, raw in record['protected'].items():
                self.assertEqual((root / relative).read_bytes(), raw, str(root / relative))
            self.assertEqual(load(task_file(root, 'historical-note'))['history'], record['history'])
            validation.repository(root)
            self.assertEqual(linked_documents.check_tree(root, delivery.git(root, 'rev-parse', 'HEAD'))['status'], 'verified')

        for record in companies:
            name = record['name']
            candidate = self.base / (name + '-updated')
            updated = lifecycle.update(record['root'], received, candidate)
            self.assertEqual(updated['status'], 'verified-candidate', updated)
            self.assertEqual(load(candidate / 'release.yaml')['version'], meta['version'])
            preserved(record, candidate)
            receipt = delivery.deliver(candidate, str(record['remote']), expected_base=record['base'])
            self.assertEqual(receipt['status'], 'readback-confirmed')
            self.assertEqual(delivery.git(record['remote'], 'rev-parse', 'HEAD'), receipt['sha256'])
            preserved(record, record['root'])
            rolled = self.base / (name + '-rolled')
            rollback = lifecycle.rollback(candidate, rolled)
            self.assertEqual(rollback['status'], 'verified-candidate', rollback)
            self.assertEqual(load(rolled / 'release.yaml')['version'], initial_version)
            preserved(record, rolled)
            backup = self.base / (name + '-backup')
            lifecycle.backup(rolled, backup)
            restored = self.base / (name + '-restored')
            self.assertEqual(lifecycle.restore(backup, restored)['status'], 'restored-and-verified')
            preserved(record, restored)
            # The other company's accepted value must fail under this company's own rule.
            document = restored / record['consumer']
            original = document.read_bytes()
            wrong = 24 if record['value'] == 8 else 8
            document.write_bytes(original.replace(('Limit ' + str(record['value']) + '.').encode(),
                                                  ('Limit ' + str(wrong) + '.').encode()))
            self.assertNotEqual(document.read_bytes(), original)
            with self.assertRaisesRegex(Rejected, 'documentation'):
                validation.repository(restored)
            document.write_bytes(original)
            preserved(record, restored)
