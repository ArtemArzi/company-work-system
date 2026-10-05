"""One deterministic validation library used by workflow, CLI and delivery."""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
import json
import math
from pathlib import Path
import re
from core import config, digest, ident, load, object_hash, path, require, tasks, changed_bindings

CAPABILITIES = {"files", "validation", "git", "source-read", "critical", "tick", "graph", "independent-review"}
OPERATIONS = {"context", "intake", "execute", "summary", "episodes", "research", "marketing", "source-read", "deliver", "author", "incident", "knowledge"}


def frontmatter(file):
    text = Path(file).read_text()
    require(text.startswith("---\n"), f"missing frontmatter: {file}")
    import yaml
    return yaml.safe_load(text.split("---", 2)[1])


def recipe(root, file):
    value = load(file)
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
    return value


def source_envelope(value, company, specification):
    require(isinstance(value, dict) and value.get("schema_version") == 1, "source format")
    for key, expected in [("company_id", company), ("account", specification["account"]), ("scope", specification["scope"]), ("period", specification["period"]), ("unit", specification["unit"])]:
        require(value.get(key) == expected, f"wrong source {key}")
    require(value.get("complete") is True and isinstance(value.get("records"), list), "partial source")
    observed = dt.datetime.fromisoformat(value["observed_at"])
    require(observed.tzinfo is not None, "source timezone missing")
    age = (dt.datetime.now(dt.timezone.utc) - observed).total_seconds()
    require(0 <= age <= specification["max_age_seconds"], "stale/future source")
    seen = set()
    for record in value["records"]:
        require(isinstance(record, dict) and isinstance(record.get("id"), str) and record["id"], "record ID missing")
        require(record["id"] not in seen, "duplicate source record")
        seen.add(record["id"])


def result(root, value, acceptance):
    require(isinstance(value, dict) and value.get("schema_version") == 1, "result format")
    require(value.get("company_id") == config(root)["id"], "wrong result company")
    require(value.get("kind") == acceptance["kind"], "unexpected result kind")
    require(value.get("summary") and value.get("next_action") and isinstance(value.get("limitations"), list), "unusable output")
    refs = value.get("sources", [])
    require(refs, "result without primary evidence")
    for source in refs:
        require(path(root, source["path"]).is_file() and digest(path(root, source["path"])) == source["sha256"], "result source changed/missing")
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
        source_records = load(path(root, refs[0]["path"])).get("records")
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
    if value["status"] in {"blocked", "waiting"}:
        require(value.get("blocker"), "missing waiting reason")
    if value["status"] == "verified":
        require(value.get("evidence") and value.get("critical"), "text PASS is not evidence")
        artifact = value["output"]
        require(digest(path(root, artifact["path"])) == artifact["sha256"], "output changed")
        result(root, load(path(root, artifact["path"])), value["acceptance"])
        evidence = value["evidence"]
        require(evidence.get("exit_code") == 0 and evidence.get("critical_sha256") == object_hash(value["critical"]) and evidence.get("result_sha256") == artifact["sha256"], "unbound evidence")
        require(evidence.get("acceptance_sha256") == value["acceptance_hash"], "evidence criterion mismatch")
        if freshness and value["delivery"]["status"] != "delivered":
            require(not changed_bindings(root, evidence["bindings"]), "verification dependencies changed")
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
    identities = {}
    canonical_skills = set()
    for directory, pattern, kind in [("standards", "*.md", "standard"), ("company/standards", "*.md", "standard"), ("skills", "*/SKILL.md", "skill"), ("workflows", "*.yaml", "workflow"), ("company/sources", "*/source.yaml", "source")]:
        for file in sorted((root / directory).glob(pattern)):
            if file.name == "README.md":
                continue
            def inspect(file=file, kind=kind):
                meta = frontmatter(file) if file.suffix == ".md" else load(file)
                entity = meta.get("id", meta.get("name"))
                ident(entity)
                require(entity not in identities, f"duplicate entity ID {entity}")
                identities[entity] = str(file.relative_to(root))
                require(meta.get("owner") or kind == "skill", "entity owner missing")
                if kind == "skill":
                    require(file.parent.name == entity and meta.get("description"), "skill in wrong place")
                    paired = recipe(root, file.with_name("workflow.yaml"))
                    require(paired["id"] == entity, "skill/recipe disagreement")
                    canonical_skills.add(entity)
                elif kind == "workflow":
                    require(file.stem == entity, "workflow in wrong place")
                    recipe(root, file)
                elif kind == "source":
                    require(file.parent.name == entity and meta.get("origin") and meta.get("status"), "source provenance/place")
                else:
                    require(file.stem == entity and meta.get("version"), "standard place/version")
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
        write(root / "company/config.yaml", {"schema_version": 1, "id": "selftest-company"})
        source = root / "source.json"
        records = [{"id": "one", "value": 2}, {"id": "two", "value": 3}]
        write(source, {"records": records})
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
