"""Reproducible declared documentation, read-only and independent of candidate code.

Coverage is active ordinary Markdown, excluding historical/runtime/private trees.
Limits fail closed: 4096 documents, 2 MiB each, 32 MiB total; Git inventory at
most 100000 entries. Unmarked prose still requires human consumer discovery.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import stat
from string import Template

import yaml
from core import Rejected, config_snapshot, path, require
import platform_runtime as platform

MAX_DOCUMENTS = 4096
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_TREE_ENTRIES = 100000
MAX_RULES = 128
MAX_VALUES = 128
MAX_CONSUMERS = 256
MAX_TEXT = 32768
MAX_TEMPLATE = 65536
EXCLUDED = frozenset({'work', '.git', '.local', '.venv', '.system', '.agents',
                      '.claude', '.codex', '__pycache__', 'private', 'control',
                      'runtime', 'node_modules'})
SENSITIVE = re.compile(r'(^|[._-])(credentials?|secrets?|tokens?|passwords?|private|control)([._-]|$)', re.I)
RULE_ID = re.compile(r'[a-z][a-z0-9-]{1,63}')
VARIABLE = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]{0,63}')
MARKER = re.compile(r'<!-- cws:([a-z][a-z0-9-]{1,63}):(begin|end) -->')
MARKER_START = re.compile(r'<!--\s*cws\b', re.I)
PLACEHOLDER = re.compile(r'\$\{([a-zA-Z_][a-zA-Z0-9_]{0,63})\}')


def _plain(value, maximum, label, multiline=False):
    require(isinstance(value, str) and 0 < len(value) <= maximum and value.strip(), label + ' must be bounded nonempty text')
    require(not any(ord(c) < 32 and not (multiline and c in '\n\t') or ord(c) == 127 for c in value), label + ' contains controls')
    require(not MARKER_START.search(value), label + ' contains a documentation marker')
    return value


def _canonical(relative):
    require(isinstance(relative, str) and 0 < len(relative) <= 1024 and not relative.startswith('/'), 'canonical relative path required')
    try:
        platform.portable_path(relative)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc
    require(len(relative.split('/')) <= 32, 'documentation path depth exceeded')
    return relative


def _excluded(relative):
    return any(part.casefold() in EXCLUDED or SENSITIVE.search(part) or part.casefold().startswith('.env') for part in relative.split('/'))


def document_path(relative):
    """Shared consumer/inventory path contract; never access the target here."""
    _canonical(relative)
    require(relative.endswith('.md') and not _excluded(relative), 'active ordinary Markdown path required')
    return relative


def _source(cfg, source):
    require(isinstance(source, list) and 2 <= len(source) <= 16 and isinstance(source[0], str) and source[0] in {'definitions', 'permissions', 'features'}, 'invalid documentation source')
    value = cfg
    for key in source:
        require(isinstance(key, str) and 0 < len(key) <= 128 and not any(ord(c) < 32 for c in key), 'invalid documentation source key')
        require(isinstance(value, dict) and key in value, 'documentation source missing')
        value = value[key]
    require(value is not None, 'documentation source is null')
    return value


def _formatted(value, kind):
    require(isinstance(kind, str) and kind in {'text', 'integer', 'bytes', 'path', 'boolean'}, 'unsupported documentation format')
    if kind == 'text':
        return _plain(value, MAX_TEXT, 'documentation value')
    if kind == 'path':
        return _canonical(value)
    if kind == 'boolean':
        require(type(value) is bool, 'boolean documentation value required')
        return 'true' if value else 'false'
    require(type(value) is int and abs(value) <= 2**63 - 1, 'bounded integer documentation value required')
    if kind == 'integer':
        return str(value)
    require(value > 0, 'positive bytes documentation value required')
    for size, unit in ((1024**3, 'GiB'), (1024**2, 'MiB')):
        if value % size == 0:
            return str(value // size) + ' ' + unit
    return str(value) + ' байт'


def rules(cfg):
    """Return the validated rule mapping; absent optional config is legacy."""
    require(isinstance(cfg, dict), 'company config must be an object')
    if 'documentation' not in cfg:
        return {}
    declaration = cfg['documentation']
    require(isinstance(declaration, dict) and set(declaration) == {'schema_version', 'rules'}, 'documentation schema fields')
    require(type(declaration['schema_version']) is int and declaration['schema_version'] == 1, 'unsupported documentation schema')
    specs = declaration['rules']
    require(isinstance(specs, dict) and len(specs) <= MAX_RULES, 'bounded documentation rules required')
    consumers = []
    for name, spec in specs.items():
        require(isinstance(name, str) and RULE_ID.fullmatch(name), 'invalid documentation rule ID')
        require(isinstance(spec, dict) and set(spec) == {'approved_by', 'template', 'values', 'consumers'}, 'documentation rule fields')
        _plain(spec['approved_by'], 256, 'documentation approval')
        template = _plain(spec['template'], MAX_TEMPLATE, 'documentation template', multiline=True)
        variables = spec['values']
        require(isinstance(variables, dict) and len(variables) <= MAX_VALUES, 'bounded documentation values required')
        # string.Template accepts $name and $$ too; this contract deliberately does not.
        used = PLACEHOLDER.findall(template)
        require('$' not in PLACEHOLDER.sub('', template), 'only ${name} template placeholders allowed')
        require(set(used) == set(variables), 'documentation variables must all be declared and used')
        for variable, value_spec in variables.items():
            require(isinstance(variable, str) and VARIABLE.fullmatch(variable), 'invalid documentation variable')
            require(isinstance(value_spec, dict) and set(value_spec) == {'source', 'format'}, 'documentation value fields')
            _formatted(_source(cfg, value_spec['source']), value_spec['format'])
        paths = spec['consumers']
        require(isinstance(paths, list) and 0 < len(paths) <= MAX_CONSUMERS, 'bounded nonempty documentation consumers required')
        for relative in paths:
            document_path(relative)
        require(len(set(paths)) == len(paths), 'duplicate documentation consumer')
        consumers.extend(paths)
    _collisions(consumers)
    return specs


def _collisions(paths):
    try:
        platform.path_collisions(paths)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc


def _render(cfg, name, spec):
    values = {key: _formatted(_source(cfg, item['source']), item['format']) for key, item in spec['values'].items()}
    body = Template(spec['template']).substitute(values)
    return f'<!-- cws:{name}:begin -->\n{body}\n<!-- cws:{name}:end -->'


def render(cfg, rule_id):
    specs = rules(cfg)
    require(isinstance(rule_id, str) and rule_id in specs, 'unknown documentation rule')
    return _render(cfg, rule_id, specs[rule_id])


def check_config(cfg, read_document, document_paths=None):
    specs = rules(cfg)
    require(callable(read_document), 'documentation reader required')
    consumers = {p for spec in specs.values() for p in spec['consumers']}
    active = set(consumers)
    if document_paths is not None:
        for relative in document_paths:
            active.add(document_path(relative))
            require(len(active) <= MAX_DOCUMENTS, 'active Markdown inventory limit exceeded')
    require(len(active) <= MAX_DOCUMENTS, 'active Markdown inventory limit exceeded')
    _collisions(active)
    total = 0
    expected = {name: _render(cfg, name, spec) for name, spec in specs.items()}
    for relative in sorted(active):
        try:
            text = read_document(relative)
        except (OSError, UnicodeError, ValueError, KeyError) as exc:
            raise Rejected('documentation unreadable: ' + relative) from exc
        require(isinstance(text, str), 'documentation reader must return text')
        try:
            size = len(text.encode('utf-8'))
        except UnicodeError as exc:
            raise Rejected('documentation is not UTF-8: ' + relative) from exc
        total += size
        require(size <= MAX_DOCUMENT_BYTES and total <= MAX_TOTAL_BYTES, 'active Markdown byte limit exceeded')
        require('\x00' not in text, 'active Markdown contains binary data')
        starts = list(MARKER_START.finditer(text))
        tokens = []
        for start in starts:
            match = MARKER.match(text, start.start())
            require(match is not None, 'malformed documentation marker: ' + relative)
            tokens.append(match)
        opened, found = None, set()
        for token in tokens:
            name, direction = token.groups()
            require(name in specs and relative in specs[name]['consumers'], 'unregistered documentation marker: ' + relative)
            if direction == 'begin':
                require(opened is None and name not in found, 'duplicate/nested documentation block: ' + relative)
                opened = token
            else:
                require(opened is not None and opened[1] == name, 'reversed/mismatched documentation block: ' + relative)
                require(text[opened.start():token.end()] == expected[name], 'stale documentation block: ' + relative)
                found.add(name)
                opened = None
        require(opened is None, 'unclosed documentation block: ' + relative)
        required = {name for name, spec in specs.items() if relative in spec['consumers']}
        require(found == required, 'missing documentation block: ' + relative)
    return {'status': 'verified', 'documents': sorted(consumers), 'rules': len(specs),
            'read_only': True, 'active_documents': len(active), 'active_bytes': total,
            'coverage': 'declared consumers and supplied active Markdown; unmarked prose requires review'}


def _read_file(root, relative):
    file = path(root, relative)
    require(stat.S_ISREG(file.stat().st_mode), 'ordinary documentation file required')
    with file.open('rb') as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    require(len(raw) <= MAX_DOCUMENT_BYTES, 'documentation byte limit exceeded')
    return raw.decode('utf-8')


def _inventory(root):
    documents, count = [], 0
    def failed(exc):
        raise Rejected('active Markdown inventory unreadable') from exc
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        base = Path(directory).relative_to(root)
        retained = []
        for name in dirs:
            relative = (base / name).as_posix()
            if not _excluded(relative):
                path(root, relative)  # Refuse alias directories rather than hide consumers.
                retained.append(name)
        dirs[:] = retained
        count += len(dirs) + len(files)
        require(count <= MAX_TREE_ENTRIES, 'active inventory entry limit exceeded')
        for name in files:
            relative = (base / name).as_posix()
            if name.lower().endswith('.md') and not _excluded(relative):
                documents.append(document_path(relative))
                require(len(documents) <= MAX_DOCUMENTS, 'active Markdown inventory limit exceeded')
    return documents


def check(root):
    root = Path(root).resolve()
    try:
        config_file = path(root, 'company/config.yaml')
        require(config_file.stat().st_size <= MAX_DOCUMENT_BYTES and stat.S_ISREG(config_file.stat().st_mode), 'bounded ordinary documentation config required')
        cfg, sha = config_snapshot(root)
        report = check_config(cfg, lambda relative: _read_file(root, relative), _inventory(root))
        require(config_snapshot(root)[1] == sha, 'documentation config changed during check')
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise Rejected('documentation snapshot unreadable') from exc
    report['config_sha256'] = sha
    return report


def check_tree(root, ref, prefix=''):
    """Check raw blobs from a fixed tree, without checkout, filters or imports."""
    from delivery import git  # Trusted running code, lazy to avoid validation cycles.
    require(prefix in {'', 'template/'}, 'unsupported documentation tree prefix')
    require(isinstance(ref, str) and ref and not ref.startswith('-') and not any(ord(c) < 32 for c in ref), 'invalid documentation Git ref')
    snapshot = git(root, 'rev-parse', '--verify', '--end-of-options', ref + '^{tree}')
    require(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', snapshot), 'invalid resolved documentation tree')
    raw = git(root, 'ls-tree', '-r', '-z', '--full-tree', snapshot, binary=True)
    entries = raw.split(b'\0')
    require(len(entries) <= MAX_TREE_ENTRIES + 1, 'Git documentation inventory limit exceeded')
    blobs, active = {}, []
    try:
        for entry in entries:
            if not entry:
                continue
            metadata, encoded = entry.split(b'\t', 1)
            relative = encoded.decode('utf-8')
            if not relative.startswith(prefix):
                continue
            relative = relative[len(prefix):]
            if _excluded(relative):
                continue
            mode, kind, oid = metadata.decode('ascii').split()
            _canonical(relative)
            # Gitlinks can hide entire active document directories.
            require(kind != 'commit', 'active Gitlink prevents complete Markdown coverage')
            require(mode != '120000', 'active Git alias prevents complete Markdown coverage')
            blobs[relative] = (mode, kind, oid)
            if relative.lower().endswith('.md'):
                active.append(document_path(relative))
                require(len(active) <= MAX_DOCUMENTS, 'active Markdown inventory limit exceeded')
        _collisions(blobs)
        def read(relative):
            _canonical(relative)
            require(relative in blobs, 'documentation blob missing: ' + relative)
            mode, kind, oid = blobs[relative]
            require(mode in {'100644', '100755'} and kind == 'blob', 'ordinary documentation blob required')
            require(int(git(root, 'cat-file', '-s', oid)) <= MAX_DOCUMENT_BYTES, 'documentation blob byte limit exceeded')
            data = git(root, 'cat-file', 'blob', oid, binary=True)
            require(len(data) <= MAX_DOCUMENT_BYTES, 'documentation blob byte limit exceeded')
            return data.decode('utf-8')
        cfg = yaml.safe_load(read('company/config.yaml'))
        report = check_config(cfg, read, active)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise Rejected('Git documentation snapshot unreadable') from exc
    return {**report, 'snapshot': snapshot, 'prefix': prefix}


def dependency_scope(cfg, paths, facets):
    """Return additional consumer paths and only selected rules with raw values."""
    specs, selected, additional = rules(cfg), {}, set()
    paths = set(paths)
    for name, spec in specs.items():
        if not (paths.intersection(spec['consumers']) or any(value['source'][0] in facets for value in spec['values'].values())):
            continue
        selected[name] = {'specification': copy.deepcopy(spec),
                          'values': {key: copy.deepcopy(_source(cfg, value['source'])) for key, value in spec['values'].items()}}
        additional.update(spec['consumers'])
    if selected:
        additional.add('scripts/linked_documents.py')
    return additional, selected
