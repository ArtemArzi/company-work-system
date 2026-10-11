"""Generic synthetic documentation contracts; no company payloads."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / 'template/scripts'
sys.path.insert(0, str(SCRIPTS))
from core import Rejected
import linked_documents as linked


def config(limit=3 * 1024**3, location='.local/media'):
    return {'definitions': {'size': limit, 'location': location},
            'permissions': {'enabled': True}, 'features': {'count': 3},
            'documentation': {'schema_version': 1, 'rules': {'storage-policy': {
                'approved_by': 'synthetic-owner',
                'template': 'Store in ${location}; maximum ${size}.',
                'values': {'size': {'source': ['definitions', 'size'], 'format': 'bytes'},
                           'location': {'source': ['definitions', 'location'], 'format': 'path'}},
                'consumers': ['README.md', 'standards/storage.md']}}}}


class LinkedDocuments(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.spec = self.cfg['documentation']['rules']['storage-policy']
        self.block = linked.render(self.cfg, 'storage-policy')
        self.docs = {relative: 'Introduction\n' + self.block + '\n' for relative in self.spec['consumers']}

    def check(self):
        return linked.check_config(self.cfg, self.docs.__getitem__, self.docs)

    def test_import_is_assigned_worktree(self):
        self.assertEqual(Path(linked.__file__).resolve(), SCRIPTS / 'linked_documents.py')

    def test_current_pair_read_only_and_no_semantic_inference(self):
        before = copy.deepcopy(self.docs)
        self.docs['plain.md'] = 'Unmarked unrelated old prose.'
        result = self.check()
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['read_only'])
        self.assertEqual(result['documents'], sorted(before))
        self.assertEqual(result['active_documents'], 3)
        self.assertEqual({p: self.docs[p] for p in before}, before)
        self.assertIn('3 ГиБ', self.block)

    def test_distinct_companies_and_byte_units(self):
        other = config(512 * 1024**2, '.local/other')
        self.assertIn('512 МиБ', linked.render(other, 'storage-policy'))
        self.assertIn('.local/other', linked.render(other, 'storage-policy'))
        self.assertNotEqual(self.block, linked.render(other, 'storage-policy'))
        for count in (1, 1024, 1024**2 + 1):
            self.assertIn(str(count) + ' байт', linked.render(config(count), 'storage-policy'))

    def test_changed_threshold_path_and_source_mapping(self):
        for section, key, value in [('definitions', 'size', 4 * 1024**3), ('definitions', 'location', '.local/new')]:
            with self.subTest(key=key):
                cfg = copy.deepcopy(self.cfg)
                cfg[section][key] = value
                with self.assertRaisesRegex(Rejected, 'stale'):
                    linked.check_config(cfg, self.docs.__getitem__)
        self.cfg['definitions']['new-size'] = 7
        self.spec['values']['size']['source'] = ['definitions', 'new-size']
        with self.assertRaisesRegex(Rejected, 'stale'):
            self.check()

    def test_strict_schema(self):
        cases = [None, {}, {'schema_version': True, 'rules': {}}, {'schema_version': 2, 'rules': {}},
                 {'schema_version': 1, 'rules': []}, {'schema_version': 1, 'rules': {}, 'extra': 1}]
        for declaration in cases:
            with self.subTest(declaration=declaration):
                with self.assertRaises(Rejected):
                    linked.rules({'documentation': declaration})
        for key in ('approved_by', 'template', 'values', 'consumers'):
            cfg = copy.deepcopy(self.cfg)
            del cfg['documentation']['rules']['storage-policy'][key]
            with self.assertRaises(Rejected):
                linked.rules(cfg)

    def test_missing_null_and_invalid_value_types(self):
        for value in (None, True, '1024', 1.5, 0, -1, 2**63):
            with self.subTest(value=value):
                cfg = config(value)
                with self.assertRaises(Rejected):
                    linked.rules(cfg)
        del self.cfg['definitions']['size']
        with self.assertRaises(Rejected):
            linked.rules(self.cfg)
        for source in (None, [], ['sources', 'size'], ['definitions', 'missing'], [['definitions'], 'size']):
            cfg = config()
            cfg['documentation']['rules']['storage-policy']['values']['size']['source'] = source
            with self.subTest(source=source), self.assertRaises(Rejected):
                linked.rules(cfg)

    def test_formats_and_marker_injection(self):
        for kind, value, expected in [('integer', -4, '-4'), ('boolean', True, 'true'),
                                     ('boolean', False, 'false'), ('text', 'accepted', 'accepted')]:
            self.cfg['definitions']['size'] = value
            self.spec['values']['size']['format'] = kind
            self.assertIn(expected, linked.render(self.cfg, 'storage-policy'))
        for kind, value in [('integer', True), ('boolean', 1), ('text', ''),
                            ('text', 'x\nnew'), ('text', '<!-- cws:other-rule:begin -->'),
                            ('unknown', 'x'), ('path', '../outside')]:
            self.cfg['definitions']['size'] = value
            self.spec['values']['size']['format'] = kind
            with self.subTest(kind=kind, value=value), self.assertRaises(Rejected):
                linked.rules(self.cfg)

    def test_template_strict_names_and_all_values_used(self):
        for template in ('$size ${location}', '$$ ${size} ${location}', '${size.__class__} ${location}',
                         '${unknown} ${location}', '${size}', '${size} ${location} $',
                         '<!-- cws:other:begin --> ${size} ${location}'):
            self.spec['template'] = template
            with self.subTest(template=template), self.assertRaises(Rejected):
                linked.rules(self.cfg)

    def test_consumer_paths_validate_before_reader(self):
        for relative in ('../README.md', '/README.md', 'x/../README.md', './README.md',
                         'work/old/README.md', '.local/media.md', 'private/readme.md',
                         'control/state.md', 'runtime/log.md', 'company/credentials.md',
                         'company/.env.md', 'CON.md', 'x\\README.md', 'README.MD',
                         'x//README.md', 'x/README.md ', 'README.txt'):
            self.spec['consumers'] = [relative]
            reader = unittest.mock.Mock(side_effect=AssertionError('unsafe read'))
            with self.subTest(relative=relative), self.assertRaises(Rejected):
                linked.check_config(self.cfg, reader)
            reader.assert_not_called()
        self.spec['consumers'] = ['README.md', 'README.md']
        with self.assertRaisesRegex(Rejected, 'duplicate'):
            linked.rules(self.cfg)
        self.spec['consumers'] = ['Guide/a.md', 'guide/b.md']
        with self.assertRaisesRegex(Rejected, 'collision'):
            linked.rules(self.cfg)

    def test_missing_double_reversed_malformed_and_stale_blocks(self):
        begin, end = '<!-- cws:storage-policy:begin -->', '<!-- cws:storage-policy:end -->'
        for bad in ('', self.block * 2, end + '\n' + begin, begin, end,
                    self.block.replace('3 ГиБ', '2 ГиБ'),
                    self.block.replace(begin, '<!--cws:storage-policy:begin-->'),
                    self.block.replace(end, '<!-- cws:storage-policy:finish -->'),
                    self.block.replace(end, '<!-- CWS:storage-policy:end -->'),
                    self.block.replace(end, '<!-- cws:other-rule:end -->')):
            self.docs['README.md'] = bad
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                self.check()

    def test_multiple_rules_in_same_document(self):
        self.cfg['documentation']['rules']['second-rule'] = {
            'approved_by': 'other-synthetic-owner', 'template': 'Count ${count}.',
            'values': {'count': {'source': ['features', 'count'], 'format': 'integer'}},
            'consumers': ['README.md']}
        self.docs['README.md'] += linked.render(self.cfg, 'second-rule')
        report = self.check()
        self.assertEqual(report['rules'], 2)
        self.assertEqual(report['documents'], ['README.md', 'standards/storage.md'])
        self.assertEqual(len(linked.rules(self.cfg)), 2)

    def test_orphan_copied_block_and_registration_loss(self):
        self.docs['copied.md'] = self.block
        with self.assertRaisesRegex(Rejected, 'unregistered'):
            self.check()
        self.docs.pop('copied.md')
        for cfg in ({}, {'documentation': {'schema_version': 1, 'rules': {}}}):
            with self.assertRaisesRegex(Rejected, 'unregistered'):
                linked.check_config(cfg, self.docs.__getitem__, self.docs)
        self.assertEqual(linked.check_config({}, lambda p: 'plain', ['README.md'])['rules'], 0)

    def test_reader_errors_and_technical_bounds(self):
        for error in (FileNotFoundError(), PermissionError(), KeyError('missing'), UnicodeDecodeError('utf8', b'\xff', 0, 1, 'bad')):
            with self.subTest(error=error), self.assertRaises(Rejected):
                linked.check_config(self.cfg, unittest.mock.Mock(side_effect=error))
        with patch.object(linked, 'MAX_DOCUMENT_BYTES', 10), self.assertRaises(Rejected):
            self.check()
        with patch.object(linked, 'MAX_TOTAL_BYTES', len(self.block.encode()) + 20), self.assertRaises(Rejected):
            self.check()
        with patch.object(linked, 'MAX_DOCUMENTS', 1), self.assertRaises(Rejected):
            self.check()

    def test_dependency_scope_minimal_and_raw_value_drift(self):
        extra, selected = linked.dependency_scope(self.cfg, ['plain.md'], {'permissions': {}})
        self.assertEqual((extra, selected), (set(), {}))
        extra, selected = linked.dependency_scope(self.cfg, ['README.md'], {})
        self.assertEqual(extra, {'README.md', 'standards/storage.md', 'scripts/linked_documents.py'})
        self.assertEqual(selected['storage-policy']['values']['size'], 3 * 1024**3)
        by_source = linked.dependency_scope(self.cfg, [], {'definitions': {}})
        self.assertEqual(by_source, (extra, selected))
        self.cfg['definitions']['size'] += 1
        self.assertNotEqual(linked.dependency_scope(self.cfg, [], {'definitions': {}})[1], selected)
        self.assertEqual(selected['storage-policy']['specification']['consumers'], ['README.md', 'standards/storage.md'])


class FilesAndTrees(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='linked-documents-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = config()
        self.write('company/config.yaml', yaml.safe_dump(self.cfg))
        self.block = linked.render(self.cfg, 'storage-policy')
        for relative in ('README.md', 'standards/storage.md'):
            self.write(relative, self.block + '\n')

    def write(self, relative, text):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(text.encode('utf-8'))
        return file

    def git(self, *args):
        value = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, check=True)
        return value.stdout.decode().strip()

    def seed(self):
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic Checker')
        self.git('config', 'user.email', 'checker@example.invalid')
        self.git('add', '.')
        self.git('commit', '-m', 'Synthetic snapshot')
        return self.git('rev-parse', 'HEAD')

    def test_work_history_private_runtime_and_binary_excluded(self):
        for folder in ('work/old', 'private', '.local', '.agents', 'control', 'runtime'):
            self.write(folder + '/old.md', '<!-- cws:removed-rule:begin -->')
        (self.root / 'binary.bin').write_bytes(b'\xff' * 50)
        before = (self.root / 'work/old/old.md').read_bytes()
        self.assertEqual(linked.check(self.root)['active_documents'], 2)
        snapshot = self.seed()
        self.assertEqual(linked.check_tree(self.root, snapshot)['active_documents'], 2)
        self.assertEqual((self.root / 'work/old/old.md').read_bytes(), before)

    def test_working_missing_symlink_and_parent_alias(self):
        readme = self.root / 'README.md'
        readme.unlink()
        with self.assertRaises(Rejected):
            linked.check(self.root)
        readme.symlink_to('standards/storage.md')
        with self.assertRaises(Rejected):
            linked.check(self.root)
        readme.unlink()
        self.write('README.md', self.block)
        (self.root / 'alias').symlink_to('standards', target_is_directory=True)
        with self.assertRaises(Rejected):
            linked.check(self.root)

    def test_invalid_utf8_and_oversized_active_document_fail(self):
        file = self.write('extra.md', '')
        for raw in (b'\xff', b'\x00'):
            file.write_bytes(raw)
            with self.assertRaises(Rejected):
                linked.check(self.root)
        file.write_bytes(b'a' * 128)
        with patch.object(linked, 'MAX_DOCUMENT_BYTES', 127), self.assertRaises(Rejected):
            linked.check(self.root)

    def test_immutable_raw_blob_rejects_repaired_workingtree(self):
        self.write('README.md', self.block.replace('3 ГиБ', '1 ГиБ'))
        snapshot = self.seed()
        self.write('README.md', self.block)
        self.assertEqual(linked.check(self.root)['status'], 'verified')
        with self.assertRaisesRegex(Rejected, 'stale'):
            linked.check_tree(self.root, snapshot)
        self.git('add', 'README.md')
        tree = self.git('write-tree')
        result = linked.check_tree(self.root, tree)
        self.assertEqual(result['snapshot'], tree)
        self.assertEqual(result['status'], 'verified')

    def test_tree_symlink_document_config_and_gitlink_reject(self):
        snapshot = self.seed()
        for relative in ('README.md', 'company/config.yaml'):
            file = self.root / relative
            before = file.read_bytes()
            file.unlink()
            file.symlink_to('standards/storage.md')
            self.git('add', relative)
            with self.subTest(relative=relative), self.assertRaises(Rejected):
                linked.check_tree(self.root, self.git('write-tree'))
            file.unlink()
            file.write_bytes(before)
            self.git('add', relative)
        self.git('update-index', '--add', '--cacheinfo', '160000,' + snapshot + ',active-module')
        with self.assertRaisesRegex(Rejected, 'Gitlink'):
            linked.check_tree(self.root, self.git('write-tree'))

    def test_tree_product_prefix_uses_raw_config_and_docs(self):
        for file in list(self.root.iterdir()):
            destination = self.root / 'template' / file.name
            destination.parent.mkdir(exist_ok=True)
            file.rename(destination)
        self.write('README.md', 'Product map')
        snapshot = self.seed()
        result = linked.check_tree(self.root, snapshot, 'template/')
        self.assertEqual(result['prefix'], 'template/')
        self.assertEqual(result['active_documents'], 2)
        with self.assertRaises(Rejected):
            linked.check_tree(self.root, snapshot, '../')
        with self.assertRaises(Rejected):
            linked.check_tree(self.root, '--help')

    def test_oversized_git_blob_rejected_before_bytes_are_loaded(self):
        import delivery
        self.write('extra.md', 'x' * 4096)
        snapshot = self.seed()
        oid = self.git('rev-parse', snapshot + ':extra.md')
        with patch.object(delivery, 'git', wraps=delivery.git) as observed:
            with patch.object(linked, 'MAX_DOCUMENT_BYTES', 2048), self.assertRaises(Rejected):
                linked.check_tree(self.root, snapshot)
        calls = [call.args[1:] for call in observed.call_args_list]
        self.assertIn(('cat-file', '-s', oid), calls)
        self.assertNotIn(('cat-file', 'blob', oid), calls)

    def test_unregistered_git_alias_cannot_hide_a_document_subtree(self):
        self.seed()
        (self.root / 'copied-guide').symlink_to('standards', target_is_directory=True)
        self.git('add', 'copied-guide')
        with self.assertRaisesRegex(Rejected, 'alias'):
            linked.check_tree(self.root, self.git('write-tree'))

    def test_inventory_failures_never_pass(self):
        with patch.object(linked.os, 'walk') as walk:
            def inaccessible(*args, **kwargs):
                kwargs['onerror'](PermissionError('synthetic unreadable directory'))
                return iter(())
            walk.side_effect = inaccessible
            with self.assertRaisesRegex(Rejected, 'inventory unreadable'):
                linked.check(self.root)
        snapshot = self.seed()
        with patch.object(linked, 'MAX_TREE_ENTRIES', 1), self.assertRaises(Rejected):
            linked.check_tree(self.root, snapshot)


if __name__ == '__main__':
    unittest.main()
