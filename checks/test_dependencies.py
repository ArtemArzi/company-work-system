"""Dependency invalidation, historical admission and explicit reconciliation."""
import copy
from pathlib import Path
from unittest.mock import patch
from checks import test_system as fixtures
from core import Rejected, changed_bindings, config, digest, load, method_bindings, object_hash, task_file, write
import dependencies
import operations
import validation


class Dependencies(fixtures.Fixture):
    def test_selected_sources_harness_and_unrelated_methods(self):
        task = self.task()
        self.assertIn('binding_scope', task)
        cfg = config(self.root)
        cfg['sources']['unrelated-source'] = {'type': 'export', 'path': 'elsewhere', 'account': 'other'}
        cfg['profiles']['claude'] = 'other-profile'
        write(self.root/'company/config.yaml', cfg)
        file = self.root/'adapters/claude/README.md'; file.write_text(file.read_text(encoding="utf-8")+'\nUnrelated change\n', encoding="utf-8")
        file = self.root/'skills/company-marketing/SKILL.md'; file.write_text(file.read_text(encoding="utf-8")+'\nUnrelated method\n', encoding="utf-8")
        self.assertEqual(changed_bindings(self.root, task['bindings'], task['binding_scope']), [])
        verified = operations.execute(self.root, task['id'], self.artifact(), self.critical())
        self.assertEqual(verified['status'], 'verified')
        self.assertEqual(verified['evidence']['source_admission']['config_sha256'], digest(self.root/'company/config.yaml'))
        evidence = copy.deepcopy(verified['evidence'])
        cfg['name'] = 'Unrelated display name'; write(self.root/'company/config.yaml', cfg)
        self.assertEqual(operations.execute(self.root, task['id'], self.artifact(), self.critical())['evidence'], evidence)

    def test_selected_source_same_id_change_and_rights_block(self):
        task = self.task(); cfg = config(self.root)
        cfg['sources']['test-data']['account'] = 'changed-account'; write(self.root/'company/config.yaml', cfg)
        self.assertIn('config:sources', changed_bindings(self.root, task['bindings'], task['binding_scope']))
        with self.assertRaisesRegex(ValueError, 'Pinned'): operations.execute(self.root, task['id'], self.artifact(), self.critical())
        self.assertEqual(load(task_file(self.root, task['id']))['status'], 'blocked')
        cfg['permissions']['external_delivery'] = True; write(self.root/'company/config.yaml', cfg)
        self.assertIn('config:permissions', changed_bindings(self.root, task['bindings'], task['binding_scope']))

    def test_config_race_before_evidence_never_verified(self):
        task = self.task(); original = validation.result
        def racing(*args, **kwargs):
            result = original(*args, **kwargs)
            cfg = config(self.root); cfg['name'] = 'external editor'; write(self.root/'company/config.yaml', cfg)
            return result
        with patch.object(validation, 'result', side_effect=racing):
            with self.assertRaisesRegex(Rejected, 'config changed'): operations.execute(self.root, task['id'], self.artifact(), self.critical())
        failed = load(task_file(self.root, task['id']))
        self.assertEqual(failed['status'], 'blocked'); self.assertIsNone(failed['evidence'])
        self.assertFalse(any(h['action'] == 'verified' for h in failed['history']))

    def test_delivered_admission_uses_historical_specification(self):
        self.task(); task = operations.execute(self.root, 'test-task', self.artifact(), self.critical())
        task['delivery'] = {'status': 'delivered', 'result_sha256': task['output']['sha256'], 'commit': 'synthetic-proof-only', 'readback': True}
        cfg = config(self.root); cfg['sources']['test-data']['account'] = 'next-account'; write(self.root/'company/config.yaml', cfg)
        self.assertTrue(validation.task(self.root, task))
        altered = copy.deepcopy(task)
        admission = altered['evidence']['source_admission']
        admission['specification']['max_pages'] += 1; admission['specification_sha256'] = object_hash(admission['specification'])
        altered['evidence']['source_admission_sha256'] = object_hash(admission)
        with self.assertRaisesRegex(Rejected, 'accepted source snapshot'): validation.task(self.root, altered)

    def test_declared_dependency_add_remove_and_kernel_change(self):
        task = self.task(); recipe = self.root/'skills/company-source-read/workflow.yaml'; value = load(recipe)
        value['dependency_paths'].append('scripts/knowledge.py'); write(recipe, value)
        changes = changed_bindings(self.root, task['bindings'], task['binding_scope'])
        self.assertIn('skills/company-source-read/workflow.yaml', changes)
        self.assertIn('scripts/knowledge.py', changes)
        value['dependency_paths'].append('scripts/nonexistent.py'); write(recipe, value)
        self.assertTrue(any('missing declared dependency' in item for item in changed_bindings(self.root, task['bindings'], task['binding_scope'])))
        recipe.unlink()
        self.assertIn('skills/company-source-read/workflow.yaml', changed_bindings(self.root, task['bindings'], task['binding_scope']))

    def test_explicit_harness_and_transitive_skill_selection(self):
        relative = self.input(); self.export_config()
        task = operations.intake(self.root, 'harness-task', 'Known source', 'test-owner', {'kind':'metrics'}, True, inputs=[relative], harness='codex')
        cfg = config(self.root); cfg['profiles']['codex'] = 'selected-change'; write(self.root/'company/config.yaml', cfg)
        self.assertIn('config:profile', changed_bindings(self.root, task['bindings'], task['binding_scope']))
        recipe = self.root/'skills/company-execute/workflow.yaml'; value = load(recipe)
        value['steps'].append({'id':'nested-method', 'skill':'company-source-read', 'depends_on':['execute-result']}); write(recipe,value)
        files, ops = dependencies.closure(self.root, 'company-execute')
        self.assertIn('scripts/connectors.py', files); self.assertIn('source-read', ops)
        value['steps'].append({'id':'cycle-method','skill':'company-execute','depends_on':['nested-method']});write(recipe,value)
        with self.assertRaisesRegex(Rejected,'cyclic'): dependencies.closure(self.root,'company-execute')

    def test_ambiguous_custom_and_undeclared_fallback(self):
        relative = self.input(); self.export_config(); cfg = config(self.root)
        cfg['sources']['second-data'] = copy.deepcopy(cfg['sources']['test-data']); write(self.root/'company/config.yaml',cfg)
        _, scope = dependencies.capture(self.root,[relative],{'kind':'metrics'})
        self.assertIsNone(scope)
        _, scope = dependencies.capture(self.root,[relative],{'kind':'custom'})
        self.assertIsNone(scope)
        recipe=self.root/'skills/company-execute/workflow.yaml';value=load(recipe);value.pop('dependency_paths');write(recipe,value)
        _,scope=dependencies.capture(self.root,[relative],{'kind':'note'},skill='company-execute')
        self.assertIsNone(scope)
        _,scope=dependencies.capture(self.root,[relative],{'kind':'metrics'},source_ids=['test-data'])
        self.assertEqual(scope['selection']['source_ids'],['test-data'])

    def test_legacy_read_and_resume_preserve_scope_history(self):
        task = self.task(); previous = copy.deepcopy(task)
        file=self.root/'standards/data-access.md';file.write_text(file.read_text(encoding="utf-8")+'\nChanged selected rule\n', encoding="utf-8")
        current=operations.decide(self.root,task['id'],task['revision'],'resume','Accept changed rule','test-owner')
        self.assertEqual(current['history'][-1]['detail']['previous_dependencies']['binding_scope'],previous['binding_scope'])
        self.assertNotEqual(current['bindings']['standards/data-access.md'],previous['bindings']['standards/data-access.md'])
        current.pop('binding_scope');current['bindings']=method_bindings(self.root,current['inputs']);write(task_file(self.root,task['id']),current)
        cfg=config(self.root);cfg['name']='Display update';write(self.root/'company/config.yaml',cfg)
        self.assertIn('company/config.yaml',changed_bindings(self.root,current['bindings']))
        resumed=operations.decide(self.root,task['id'],current['revision'],'resume','Legacy remains conservative','test-owner')
        self.assertNotIn('binding_scope',resumed)

    def test_scope_tampering_and_export_primary_path_rejected(self):
        task=self.task();bad=copy.deepcopy(task['binding_scope']);bad['source_snapshots']['test-data']['unit']='wrong'
        with self.assertRaisesRegex(Rejected,'scope changed'):changed_bindings(self.root,task['bindings'],bad)
        relative='company/sources/other.json';write(self.root/relative,self.source())
        value=self.artifact();value['sources']=[{'path':relative,'sha256':digest(self.root/relative),'source_id':'test-data'}]
        with self.assertRaisesRegex(Rejected,'accepted source path'):validation.source_admission(self.root,value)
