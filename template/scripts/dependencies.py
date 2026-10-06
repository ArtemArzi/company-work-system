"""Declared method closures and config facets; legacy tasks keep full bindings."""
from __future__ import annotations
from pathlib import Path
from core import Rejected, config_snapshot, digest, ident, load, method_bindings, object_hash, path, require

KINDS = {'metrics': 'company-source-read', 'marketing': 'company-marketing',
         'research': 'company-research', 'action-summary': 'company-summary'}
KERNEL = {'.gitattributes', 'requirements.txt', 'scripts/bootstrap.py', 'scripts/bootstrap.lock', 'scripts/run.sh', 'scripts/run.ps1', 'scripts/platform_runtime.py', 'scripts/core.py', 'scripts/dependencies.py', 'scripts/operations.py',
          'scripts/validation.py', 'scripts/system.py', 'scripts/hooks.py',
          'hooks/manifest.yaml', 'standards/task-validation.md', 'standards/runtime.md'}
ADMISSION_FIELDS = ('type', 'approved_by', 'account', 'scope', 'period', 'unit',
                    'max_age_seconds', 'max_pages', 'timeout_seconds')


def closure(root, skill):
    """None means an undeclared method: conservative full-set fallback."""
    files, operations, visiting, visited = set(KERNEL), {'intake', 'context', 'execute'}, set(), set()
    def visit(name):
        ident(name)
        require(name not in visiting, 'cyclic skill dependency')
        if name in visited:
            return True
        visiting.add(name)
        base = 'skills/' + name + '/'
        for relative in (base+'SKILL.md', base+'workflow.yaml'):
            require(path(root, relative).is_file(), 'missing declared skill: '+relative)
            files.add(relative)
        recipe = load(path(root, base+'workflow.yaml'))
        if 'dependency_paths' not in recipe:
            return False
        declared = recipe['dependency_paths']
        require(isinstance(declared, list) and all(isinstance(p, str) for p in declared), 'dependency_paths must be paths')
        for relative in declared + recipe.get('standards', []):
            require(path(root, relative).is_file(), 'missing declared dependency: '+relative)
            files.add(relative)
        for step in recipe.get('steps', []):
            if step.get('operation'):
                operations.add(step['operation'])
            if step.get('skill') and not visit(step['skill']):
                return False
        visiting.remove(name); visited.add(name)
        return True
    return (files, operations) if visit(skill) else None


def selected_sources(root, cfg, inputs, source_ids, metrics):
    if source_ids:
        for source in source_ids:
            ident(source)
            require(isinstance(cfg.get('sources', {}).get(source), dict), 'selected source missing: '+source)
        return sorted(set(source_ids))
    if not metrics:
        return []
    matches = [name for name, spec in cfg.get('sources', {}).items()
               if isinstance(spec, dict) and spec.get('type') == 'export' and spec.get('path') in inputs]
    return matches if len(matches) == 1 else None


def projected(root, cfg, selection):
    result = closure(root, selection['skill'])
    require(result is not None, 'selected method no longer declares dependencies')
    files, operations = result
    files.update(selection['inputs'])
    sources = {name: cfg.get('sources', {}).get(name) for name in selection['source_ids']}
    require(all(isinstance(spec, dict) for spec in sources.values()), 'selected source removed')
    facets = {'identity': {key: cfg.get(key) for key in ('schema_version', 'id', 'owner')},
              'permissions': cfg.get('permissions'), 'sources': sources,
              'bindings': {key: value for key, value in cfg.get('bindings', {}).items() if key in operations}}
    for binding in facets['bindings'].values():
        files.update([binding['base'], binding['local']])
        files.update(binding.get('dependency_paths', []))
    if 'marketing' in operations:
        facets.update(definitions=cfg.get('definitions'), decisions=cfg.get('decisions'))
    if 'knowledge' in operations:
        files.add('scripts/connectors.py')
        for source_file in sorted(Path(root, 'company/sources').glob('*/source.yaml')):
            relative = source_file.relative_to(root).as_posix()
            files.add(relative)
            meta = load(source_file)
            if meta.get('material'):
                files.add(meta['material'])
        facets.update(knowledge_base=cfg.get('knowledge_base'),
                      features={key: cfg.get('features', {}).get(key) for key in ('graph', 'knowledge_base')})
    if 'tick' in operations:
        facets.update(proactive=cfg.get('proactive'), proactive_enabled=cfg.get('features', {}).get('proactive'))
    if 'deliver' in operations:
        facets['export'] = cfg.get('export')
    if 'author' in operations:
        facets['hooks'] = cfg.get('hooks')
    harness = selection['harness']
    if harness:
        require(harness in {'codex', 'claude'}, 'unknown selected harness')
        facets['profile'] = cfg.get('profiles', {}).get(harness)
        files.update({'adapters/'+harness+'/profile.yaml', 'adapters/'+harness+'/README.md'})
    release = load(path(root, 'release.yaml'))
    facets['release'] = {key: release.get(key) for key in ('schema_version', 'state_format', 'workflow_format')}
    return sorted(files), facets


def seal(scope):
    return object_hash({key: value for key, value in scope.items() if key != 'sha256'})


def contract(scope):
    require(isinstance(scope, dict) and scope.get('schema_version') == 1, 'unsupported binding scope')
    require(scope.get('sha256') == seal(scope), 'binding scope changed')
    selection = scope.get('selection', {})
    require(set(selection) == {'skill', 'harness', 'inputs', 'source_ids'}, 'binding selection format')
    ident(selection['skill'])
    require(selection['harness'] in {None, 'codex', 'claude'}, 'binding harness format')
    require(isinstance(selection['inputs'], list) and isinstance(selection['source_ids'], list), 'binding selection lists')
    require(isinstance(scope.get('paths'), list) and isinstance(scope.get('facets'), dict), 'binding closure format')
    require(scope.get('source_snapshots') == scope['facets'].get('sources'), 'source snapshot not bound to facets')
    return scope


def capture(root, inputs, acceptance, skill=None, harness=None, source_ids=None):
    cfg, sha = config_snapshot(root)
    selected = skill or KINDS.get(acceptance['kind'])
    if not selected:
        return method_bindings(root, inputs), None
    ids = selected_sources(root, cfg, inputs, source_ids, acceptance['kind'] == 'metrics')
    declared = closure(root, selected)
    if ids is None or declared is None:
        return method_bindings(root, inputs), None
    if 'source-read' in declared[1] and not ids:
        return method_bindings(root, inputs), None
    if 'knowledge' in declared[1] and cfg.get('features', {}).get('knowledge_base') and not ids:
        return method_bindings(root, inputs), None
    selection = {'skill': selected, 'harness': harness, 'inputs': list(inputs), 'source_ids': ids}
    files, facets = projected(root, cfg, selection)
    if any('dependency_paths' not in binding for binding in facets['bindings'].values()):
        return method_bindings(root, inputs), None
    bindings = {relative: digest(path(root, relative)) for relative in files}
    # These pins preserve conservative behavior for pre-scope readers.
    bindings.update({'company/config.yaml': sha, 'release.yaml': digest(path(root, 'release.yaml'))})
    scope = {'schema_version': 1, 'selection': selection, 'paths': files, 'facets': facets, 'source_snapshots': facets['sources']}
    scope['sha256'] = seal(scope)
    require(config_snapshot(root)[1] == sha, 'config changed during dependency capture')
    return bindings, scope


def changed(root, bindings, scope):
    contract(scope)
    require(set(bindings) == set(scope['paths']) | {'company/config.yaml', 'release.yaml'}, 'flat bindings do not match scoped closure')
    differences = [relative for relative in scope['paths']
                   if not path(root, relative).is_file() or digest(path(root, relative)) != bindings[relative]]
    cfg, _ = config_snapshot(root)
    try:
        files, facets = projected(root, cfg, scope['selection'])
        differences.extend(sorted(set(files) ^ set(scope['paths'])))
        differences.extend('config:'+key for key in set(facets) | set(scope['facets'])
                           if facets.get(key) != scope['facets'].get(key))
    except (Rejected, KeyError, ValueError, OSError) as exc:
        differences.append('dependency-contract:'+str(exc))
    return sorted(set(differences))


def admission_contract(scope, admission):
    contract(scope)
    spec = scope['source_snapshots'].get(admission['source_id'])
    require(isinstance(spec, dict), 'admitted source was not selected by the task')
    require(admission['specification'] == {key: spec.get(key) for key in ADMISSION_FIELDS},
            'admitted specification differs from accepted source snapshot')
