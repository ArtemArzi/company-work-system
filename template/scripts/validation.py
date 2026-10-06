"""One deterministic validation library used by workflow, CLI and delivery."""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
import json
import math
from pathlib import Path
import re
from core import config, config_snapshot, digest, ident, load, object_hash, path, require, tasks, changed_bindings

CAPABILITIES = {"files", "validation", "git", "source-read", "critical", "tick", "graph", "independent-review"}
OPERATIONS = {"preflight", "context", "intake", "execute", "summary", "episodes", "research", "marketing", "source-read", "deliver", "author", "incident", "knowledge"}


def frontmatter(file):
    return frontmatter_text(Path(file).read_text())


def frontmatter_text(text):
    require(text.startswith("---\n"), "missing frontmatter")
    import yaml
    value = yaml.safe_load(text.split("---", 2)[1])
    require(isinstance(value, dict), "frontmatter must be an object")
    return value


def recipe(root, file):
    value = load(file)
    return recipe_contract(root, value)


def recipe_contract(root, value):
    require(value.get("schema_version") == 1, "unsupported workflow format")
    ident(value.get("id"))
    require(isinstance(value.get("version"), int) and value["version"] > 0, "workflow version")
    require(value.get("owner") and value.get("inputs") and value.get("output") and value.get("on_error") and value.get("stop"), "workflow contract incomplete")
    require(set(value.get("capabilities", [])) <= CAPABILITIES, "unknown capability")
    steps = value.get("steps", [])
    require(steps, "empty recipe")
    seen = set()
    for step in steps:
        step_id = ident(step.get("id"))
        require(step_id not in seen, "duplicate step")
        require(set(step.get("depends_on", [])) <= seen, "cycle/unknown/forward dependency")
        require(step.get("operation") in OPERATIONS or (isinstance(step.get("skill"), str) and path(root, "skills/" + step["skill"] + "/SKILL.md").is_file()), "unknown operation/skill")
        seen.add(step_id)
    for relative in value.get("standards", []):
        require(path(root, relative).is_file(), f"missing standard: {relative}")
    if 'dependency_paths' in value:
        require(isinstance(value['dependency_paths'], list), 'dependency_paths must be paths')
        for relative in value['dependency_paths']:
            require(path(root, relative).is_file(), 'missing declared dependency: '+relative)
    return value


def entity_place(relative, kind, entity):
    """The same canonical placement predicate for repository and write hooks."""
    file = Path(relative)
    if kind == 'skill':
        return relative == f'skills/{entity}/SKILL.md'
    if kind == 'skill-recipe':
        return relative == f'skills/{entity}/workflow.yaml'
    if kind == 'standard':
        return relative in {f'standards/{entity}.md', f'company/standards/{entity}.md'}
    if kind == 'source':
        return relative == f'company/sources/{entity}/source.yaml'
    return relative == f'workflows/{entity}.yaml'


def entity_contract(root, relative, kind, meta):
    entity = meta.get('id', meta.get('name'))
    ident(entity)
    require(entity_place(relative, kind, entity), kind+' in wrong place')
    require(meta.get('owner') or kind == 'skill', 'entity owner missing')
    if kind == 'skill':
        require(meta.get('description'), 'skill description missing')
        paired = recipe(root, path(root, relative).with_name('workflow.yaml'))
        require(paired['id'] == entity, 'skill/recipe disagreement')
    elif kind in {'workflow', 'skill-recipe'}:
        recipe_contract(root, meta)
    elif kind == 'source':
        require(meta.get('origin') and meta.get('status'), 'source provenance missing')
    else:
        require(meta.get('version'), 'standard version missing')
    return entity


def entity_kind(relative):
    file = Path(relative)
    if file.name == 'SKILL.md': return 'skill'
    if file.name == 'workflow.yaml' and relative.startswith('skills/'): return 'skill-recipe'
    if file.name == 'source.yaml' and relative.startswith('company/sources/'): return 'source'
    if relative.startswith('workflows/') and file.suffix == '.yaml': return 'workflow'
    if relative.startswith(('standards/', 'company/standards/')) and file.suffix == '.md' and file.name != 'README.md': return 'standard'
    return None


def entity_changes(root, changes, before=False):
    """Small write check. Incomplete writes advise; only proved placement/ID denies."""
    import yaml
    violations, advisories, checked = [], [], False
    existing = {}
    for directory, pattern in [('standards','*.md'), ('company/standards','*.md'), ('skills','*/SKILL.md'), ('workflows','*.yaml'), ('company/sources','*/source.yaml')]:
        for file in (Path(root)/directory).glob(pattern):
            if file.name == 'README.md': continue
            try:
                meta = frontmatter(file) if file.suffix == '.md' else load(file)
                name = meta.get('id', meta.get('name'))
                if name: existing.setdefault(name, []).append(str(file.relative_to(root)))
            except Exception:
                continue
    proposed = {}
    for change in changes:
        relative = change['path']; file = path(root, relative); kind = entity_kind(relative)
        if not kind: continue
        checked = True
        content = change.get('content')
        if content is None:
            if before:
                advisories.append(relative+': incomplete edit; check actual file after write'); continue
            if not file.is_file():
                advisories.append(relative+': file removed; update its map and consumers'); continue
            content = file.read_text()
        try:
            meta = frontmatter_text(content) if file.suffix == '.md' else yaml.safe_load(content)
            require(isinstance(meta, dict), 'entity metadata missing')
            name = meta.get('id', meta.get('name')); ident(name)
        except Exception as exc:
            advisories.append(relative+': '+str(exc)); continue
        if not entity_place(relative, kind, name):
            violations.append(relative+': '+kind+' in wrong place')
        others = [p for p in existing.get(name, []) if p != relative and not (kind == 'skill-recipe' and p == f'skills/{name}/SKILL.md')]
        if others or (name in proposed and proposed[name] != relative and kind != 'skill-recipe'):
            violations.append(relative+': duplicate entity ID '+name)
        proposed[name] = relative
        if before: continue
        try: entity_contract(root, relative, kind, meta)
        except Exception as exc: advisories.append(relative+': '+str(exc))
        index = {'standard': 'standards/README.md' if relative.startswith('standards/') else None,
                 'skill': 'skills/README.md', 'source': 'company/sources/README.md'}.get(kind)
        if index:
            map_file = path(root, index)
            targets = [(map_file.parent/t.strip('<>').split('#')[0]).resolve()
                       for t in re.findall(r'\]\(([^)]+)\)', map_file.read_text()) if not re.match(r'[a-z]+://', t)]
            if file.resolve() not in targets: advisories.append(relative+': entity missing from map '+index)
        if file.suffix == '.md':
            for target in re.findall(r'\]\(([^)]+)\)', content):
                if re.match(r'[a-z]+://', target) or target.startswith('#'): continue
                target = target.strip('<>').split('#')[0]
                if not (file.parent/target).resolve().is_relative_to(Path(root).resolve()) or not (file.parent/target).exists():
                    advisories.append(relative+': broken/outside link '+target)
    return {'checked': checked, 'violations': violations, 'advisories': advisories}


def source_specification(specification):
    require(isinstance(specification, dict) and specification.get("approved_by"), "source/account/scope not configured")
    require(specification.get("type") in {"export", "http", "mcp"}, "unknown source type")
    for field in ["account", "scope", "period", "unit", "max_age_seconds", "max_pages", "timeout_seconds"]:
        require(specification.get(field) is not None, f"source limit/definition missing: {field}")
    require(all(specification[field] for field in ["account", "scope", "period", "unit"]), "source identity/definition missing")
    require(type(specification["max_pages"]) is int and specification["max_pages"] > 0, "invalid source limits")
    require(all(type(specification[k]) in (int, float) and math.isfinite(specification[k]) and specification[k] > 0 for k in ["timeout_seconds", "max_age_seconds"]), "invalid source limits")
    return specification


def source_admission(root, value, snapshot=None):
    """Pin independent accepted definitions in existing task evidence, never infer them from data."""
    cfg, config_sha = snapshot if snapshot is not None else config_snapshot(root)
    refs = value.get("sources", [])
    require(refs, "metrics source missing")
    primary = refs[0]
    available = cfg.get("sources", {})
    source_id = primary.get("source_id")
    if source_id is None:
        matches = [key for key, spec in available.items() if isinstance(spec, dict) and spec.get("type") == "export" and spec.get("path") and path(root, spec["path"]) == path(root, primary["path"])]
        require(len(matches) == 1, "source admission missing or ambiguous; select an accepted source_id")
        source_id = matches[0]
    ident(source_id)
    spec = source_specification(available.get(source_id))
    if spec['type'] == 'export':
        require(spec.get('path') and path(root, spec['path']) == path(root, primary['path']), 'primary export differs from accepted source path')
    fields = ["type", "approved_by", "account", "scope", "period", "unit", "max_age_seconds", "max_pages", "timeout_seconds"]
    snapshot = {key: spec[key] for key in fields}
    from core import now
    return {"schema_version": 1, "source_id": source_id, "primary": {"path": primary["path"], "sha256": primary["sha256"]}, "specification": snapshot, "specification_sha256": object_hash(snapshot), "config_sha256": config_sha, "at": now()}


def source_envelope(value, company, specification, evaluation_time=None):
    require(isinstance(value, dict) and value.get("schema_version") == 1, "source format")
    for key, expected in [("company_id", company), ("account", specification["account"]), ("scope", specification["scope"]), ("period", specification["period"]), ("unit", specification["unit"])]:
        require(value.get(key) == expected, f"wrong source {key}")
    require(value.get("complete") is True and isinstance(value.get("records"), list), "partial source")
    observed = dt.datetime.fromisoformat(value["observed_at"])
    require(observed.tzinfo is not None, "source timezone missing")
    evaluation_time = evaluation_time or dt.datetime.now(dt.timezone.utc)
    require(evaluation_time.tzinfo is not None, "source evaluation timezone missing")
    age = (evaluation_time - observed).total_seconds()
    require(0 <= age <= specification["max_age_seconds"], "stale/future source")
    seen = set()
    for record in value["records"]:
        require(isinstance(record, dict) and isinstance(record.get("id"), str) and record["id"], "record ID missing")
        require(record["id"] not in seen, "duplicate source record")
        seen.add(record["id"])


def result(root, value, acceptance, admission=None, fresh=True):
    require(isinstance(value, dict) and value.get("schema_version") == 1, "result format")
    require(value.get("company_id") == config(root)["id"], "wrong result company")
    require(value.get("kind") == acceptance["kind"], "unexpected result kind")
    require(value.get("summary") and value.get("next_action") and isinstance(value.get("limitations"), list), "unusable output")
    refs = value.get("sources", [])
    require(refs, "result without primary evidence")
    for source in refs:
        require(path(root, source["path"]).is_file() and digest(path(root, source["path"])) == source["sha256"], "result source changed/missing")
    if value["kind"] == "action-summary":
        require(len(refs) == 1, "summary needs one immutable input snapshot; use summary --task")
        snapshot = load(path(root, refs[0]["path"]))
        require(snapshot.get("kind") == "action-summary-input" and snapshot.get("company_id") == value["company_id"], "summary input snapshot missing")
        require(snapshot.get("entries") == value.get("entries") and snapshot.get("counts") == value.get("counts"), "summary differs from input snapshot")
    if value["kind"] == "marketing":
        require(all(value.get(k) for k in ["audience", "recipient_action", "material", "measurement_source", "neighbor_handoff"]), "marketing output lacks recipient/context/handoff")
        require(value.get("proposal_status") == "draft" or value.get("decision_id") in {d.get("id") for d in config(root).get("decisions", [])}, "accepted marketing rule lacks company decision")
        require(value.get("budget_status") in {"unknown", "accepted"} and value.get("publication_status") in {"not-authorized", "authorized"}, "marketing authority/budget state missing")
        require(value.get("application_status") == "unknown" and value.get("effect_status") == "unknown", "artifact alone cannot prove application/effect")
    if value["kind"] == "research":
        require(value.get("question") and value.get("coverage") and isinstance(value.get("findings"), list), "research question/coverage missing")
        allowed_refs = {r["path"] for r in refs}
        require(all(f.get("claim") and f.get("source") in allowed_refs and f.get("level") in {"observation", "inference", "unknown"} for f in value["findings"]), "research claim without qualified source")
    if value["kind"] == "metrics":
        admission = admission if admission is not None else source_admission(root, value)
        require(admission.get("schema_version") == 1 and admission.get("source_id"), "source admission format")
        require(admission.get("primary") == {"path": refs[0]["path"], "sha256": refs[0]["sha256"]}, "source admission reference changed")
        require(not refs[0].get("source_id") or refs[0]["source_id"] == admission["source_id"], "source admission ID changed")
        specification = source_specification(admission.get("specification"))
        require(admission.get("specification_sha256") == object_hash(specification), "source admission specification changed")
        admitted_at = dt.datetime.fromisoformat(admission["at"])
        require(admitted_at.tzinfo is not None, "admission timezone missing")
        primary = load(path(root, refs[0]["path"]))
        source_envelope(primary, value["company_id"], specification, None if fresh else admitted_at)
        require(primary.get("period") == value.get("period") and primary.get("unit") == value.get("unit"), "metric result differs from admitted source scope")
        require(value.get("complete") is True and value.get("period") == acceptance.get("period") and value.get("unit") == acceptance.get("unit"), "metric scope/incomplete")
        records = value.get("records")
        require(isinstance(records, list) and len(records) == acceptance["expected_count"], "metric count")
        require(all(type(r.get("value")) in (int, float) and math.isfinite(r["value"]) for r in records), "missing/nonfinite value is not zero")
        require(len({r["id"] for r in records}) == len(records), "duplicate metric records")
        require(type(value.get("total")) in (int, float) and math.isfinite(value["total"]), "total must be a finite number")
        calculated = sum((Decimal(str(r["value"])) for r in records), Decimal(0))
        require(Decimal(str(value["total"])) == calculated, "incorrect total")
        if "expected_total" in acceptance:
            require(value["total"] == acceptance["expected_total"], "independent expected total mismatch")
        # Source-backed values, not a self-consistent invented sum.
        source_records = primary["records"]
        require(source_records == records, "metric records differ from primary source")
    return True


def task(root, value, freshness=True):
    require(value.get("schema_version") == 1 and value.get("company_id") == config(root)["id"], "task format/company")
    ident(value.get("id"))
    require(value.get("owner") and value.get("request") and value.get("next_action"), "task owner/request/continuation missing")
    require(value.get("status") in {"draft", "active", "waiting", "blocked", "verified", "cancelled"}, "task status")
    require(value.get("delivery", {}).get("status") in {"local", "pending", "delivered"}, "delivery status")
    require(value.get("application", {}).get("status") in {"unknown", "observed"} and value.get("effect", {}).get("status") in {"unknown", "observed"}, "observation status")
    require(value.get("acceptance_hash") == object_hash(value["acceptance"]), "accepted criterion was changed")
    scope = value.get('binding_scope')
    if scope is not None:
        from dependencies import contract
        contract(scope)
    for layer in ["application", "effect"]:
        observation = value[layer]
        if observation["status"] == "observed":
            require(value.get("output") and observation.get("result_sha256") == value["output"]["sha256"] and observation.get("evidence") and observation.get("actor") == config(root)["owner"] and observation.get("at"), "observation not bound to exact result/owner evidence")
    if value["status"] in {"blocked", "waiting"}:
        require(value.get("blocker"), "missing waiting reason")
    if value["status"] == "verified":
        require(value.get("evidence") and value.get("critical"), "text PASS is not evidence")
        artifact = value["output"]
        require(digest(path(root, artifact["path"])) == artifact["sha256"], "output changed")
        evidence = value["evidence"]
        if scope is not None:
            require(evidence.get('binding_scope') == scope and evidence.get('binding_scope_sha256') == scope['sha256'], 'evidence binding scope mismatch')
            require(evidence.get('bindings') == value['bindings'], 'evidence flat bindings mismatch')
        admission = evidence.get("source_admission")
        if value["acceptance"]["kind"] == "metrics":
            require(admission and evidence.get("source_admission_sha256") == object_hash(admission), "historical metrics source admission missing/changed; reconcile explicitly")
            require(admission.get("config_sha256") == evidence["bindings"].get("company/config.yaml") and admission.get("at") == evidence.get("at"), "source admission not bound to accepted config/time")
            if scope is not None:
                from dependencies import admission_contract
                admission_contract(scope, admission)
        result(root, load(path(root, artifact["path"])), value["acceptance"], admission, fresh=freshness and value["delivery"]["status"] != "delivered")
        require(evidence.get("exit_code") == 0 and evidence.get("critical_sha256") == object_hash(value["critical"]) and evidence.get("result_sha256") == artifact["sha256"], "unbound evidence")
        require(evidence.get("acceptance_sha256") == value["acceptance_hash"], "evidence criterion mismatch")
        if freshness and value["delivery"]["status"] != "delivered":
            require(not changed_bindings(root, evidence["bindings"], scope), "verification dependencies changed")
        if value["delivery"]["status"] == "delivered":
            require(value["delivery"].get("result_sha256") == artifact["sha256"] and value["delivery"].get("commit") and value["delivery"].get("readback") is True, "delivery not bound to result/readback")
        if value.get("independent_required"):
            review = value.get("independent_review") or {}
            require(review.get("reviewer") and review["reviewer"] != value["owner"] and review.get("verdict") == "pass" and review.get("result_sha256") == artifact["sha256"], "independent acceptance missing")
    return True


def repository(root, freshness=True):
    root = Path(root).resolve()
    errors = []
    def check(action, label):
        try:
            action()
        except (ValueError, KeyError, TypeError, OSError, Exception) as exc:
            errors.append(f"{label}: {exc}")
    check(lambda: config(root), "company")
    def release_contract():
        release = load(root / "release.yaml")
        require(release.get("schema_version") == 1 and release.get("state_format") == 1 and release.get("workflow_format") == 1, "unsupported release/state/workflow version")
        require(isinstance(release.get("version"), str) and re.fullmatch(r"\d+\.\d+\.\d+", release["version"]), "release version missing")
    check(release_contract, "release")
    if (root / 'hooks/manifest.yaml').is_file():
        def hooks_contract():
            from hooks import manifest
            manifest(root)
        check(hooks_contract, 'hooks')
    identities = {}
    canonical_skills = set()
    for directory, pattern, kind in [("standards", "*.md", "standard"), ("company/standards", "*.md", "standard"), ("skills", "*/SKILL.md", "skill"), ("workflows", "*.yaml", "workflow"), ("company/sources", "*/source.yaml", "source")]:
        for file in sorted((root / directory).glob(pattern)):
            if file.name == "README.md":
                continue
            def inspect(file=file, kind=kind):
                meta = frontmatter(file) if file.suffix == ".md" else load(file)
                entity = meta.get('id', meta.get('name'))
                ident(entity)
                require(entity not in identities, f"duplicate entity ID {entity}")
                identities[entity] = str(file.relative_to(root))
                entity_contract(root, str(file.relative_to(root)), kind, meta)
                if kind == "skill":
                    canonical_skills.add(entity)
            check(inspect, str(file.relative_to(root)))
    for file in root.rglob("SKILL.md"):
        if not file.is_relative_to(root / "skills") and not file.is_relative_to(root / ".agents") and not file.is_relative_to(root / ".claude") and not file.is_relative_to(root / ".git"):
            errors.append(f"skill outside canonical directory: {file.relative_to(root)}")
    for file in root.rglob("*.md"):
        if any(p in {".git", ".local", ".system", ".agents", ".claude"} for p in file.relative_to(root).parts):
            continue
        for target in re.findall(r"\]\(([^)]+)\)", file.read_text()):
            if re.match(r"[a-z]+://", target) or target.startswith("#"):
                continue
            target = target.strip("<>").split("#")[0]
            check(lambda file=file, target=target: require((file.parent / target).resolve().is_relative_to(root) and (file.parent / target).exists(), f"broken/outside link {target}"), str(file.relative_to(root)))
    for directory, pattern, index in [("standards", "*.md", "standards/README.md"), ("skills", "*/SKILL.md", "skills/README.md"), ("company/sources", "*/source.yaml", "company/sources/README.md")]:
        map_file = root / index
        if not map_file.exists():
            errors.append("missing entity map: " + index)
            continue
        targets = [(map_file.parent / t.split("#")[0]).resolve() for t in re.findall(r"\]\(([^)]+)\)", map_file.read_text()) if not re.match(r"[a-z]+://", t)]
        for entity in (root / directory).glob(pattern):
            if entity.name != "README.md" and entity.resolve() not in targets:
                errors.append("independent entity missing from map: " + str(entity.relative_to(root)))
    for file in tasks(root):
        check(lambda file=file: (require(file.parent.name == load(file)["id"], "task place"), task(root, load(file), freshness)), str(file.relative_to(root)))
    for harness, directory in [("codex", ".agents/skills"), ("claude", ".claude/skills")]:
        for name in canonical_skills:
            projected = root / directory / name
            check(lambda projected=projected, name=name: require(projected.is_symlink() and projected.resolve() == root / "skills" / name, "projection missing/changed"), f"{harness}:{name}")
    try:
        bindings = config(root).get("bindings", {})
        for operation, binding in bindings.items():
            require(operation in OPERATIONS, "unknown local binding")
            require(binding.get("approved_by") and binding.get("reason") and binding.get("checks"), "local rule not accepted/checked")
            require(digest(path(root, binding["base"])) == binding["base_sha256"], "local binding needs semantic reconciliation")
            require(path(root, binding["local"]).is_file(), "local rule missing")
    except Exception as exc:
        errors.append(str(exc))
    require(not errors, "\n".join(errors))
    return {"checked": True, "entities": len(identities), "skills": len(canonical_skills), "tasks": len(tasks(root))}


def self_test():
    """Prove this validator rejects a known bad result without reading client data."""
    import tempfile
    from core import write
    with tempfile.TemporaryDirectory(prefix="validator-self-test-") as directory:
        root = Path(directory)
        from core import now
        specification = {"type": "export", "path": "source.json", "approved_by": "synthetic-owner", "account": "synthetic", "scope": "synthetic", "period": "selftest", "unit": "selftest", "max_age_seconds": 3600, "max_pages": 1, "timeout_seconds": 1}
        write(root / "company/config.yaml", {"schema_version": 1, "id": "selftest-company", "sources": {"synthetic-source": specification}})
        source = root / "source.json"
        records = [{"id": "one", "value": 2}, {"id": "two", "value": 3}]
        write(source, {"schema_version": 1, "company_id": "selftest-company", "account": "synthetic", "scope": "synthetic", "period": "selftest", "unit": "selftest", "observed_at": now(), "complete": True, "records": records})
        criterion = {"kind": "metrics", "expected_count": 2, "expected_total": 5, "period": "selftest", "unit": "selftest"}
        artifact = {"schema_version": 1, "company_id": "selftest-company", "kind": "metrics", "summary": "Self-test", "next_action": "None", "limitations": [], "sources": [{"path": "source.json", "sha256": digest(source)}], "complete": True, "period": "selftest", "unit": "selftest", "records": records, "total": 5}
        result(root, artifact, criterion)
        rejected = []
        for field, bad in [("total", 6), ("complete", False), ("company_id", "another-company")]:
            candidate = {**artifact, field: bad}
            try:
                result(root, candidate, criterion)
            except Exception:
                rejected.append(field)
        require(len(rejected) == 3, "validator accepted a deliberately bad example")
        return {"status": "passed", "correct_example": True, "bad_examples_rejected": rejected, "boundary": "validator behavior only; no live/harness/business proof"}
