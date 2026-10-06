"""Prepared local operations; the common task cycle owns their state."""
from __future__ import annotations
import datetime as dt
from pathlib import Path
from core import changed_bindings, config, digest, event, ident, load, lock, method_bindings, now, object_hash, path, require, task_file, tasks, write
import validation


def context(root, task_id=None):
    cfg = config(root)
    result = {"company_id": cfg["id"], "release": load(path(root, "release.yaml")), "map": "company/README.md", "active_standards": cfg.get("bindings", {}), "native_memory": "not-used", "tasks": []}
    for file in tasks(root):
        t = load(file)
        if task_id and t["id"] != task_id:
            continue
        result["tasks"].append({"path": str(file.relative_to(root)), "id": t["id"], "status": t["status"], "revision": t["revision"], "next_action": t["next_action"], "blocker": t.get("blocker"), "changed_dependencies": changed_bindings(root, t["bindings"])})
    if task_id:
        require(result["tasks"], "task not found")
    return result


def intake(root, task_id, request, owner, acceptance, confirmed=False, unknowns=None, inputs=None, independent_required=False):
    with lock(root):
        cfg = config(root, True)
        file = task_file(root, task_id)
        require(request.strip() and owner.strip() and acceptance.get("kind"), "intake contract missing")
        if file.exists():
            old = load(file)
            require(old["request"] == request and old["acceptance"] == acceptance and old["owner"] == owner, "task ID conflict; both requests must be preserved")
            return old
        inputs = inputs or []
        bindings = method_bindings(root, inputs)
        unknowns = unknowns or []
        t = {"schema_version": 1, "id": task_id, "company_id": cfg["id"], "owner": owner, "request": request, "acceptance": acceptance, "acceptance_hash": object_hash(acceptance), "inputs": inputs, "bindings": bindings, "confirmed": confirmed, "unknowns": unknowns, "status": "active" if confirmed and not unknowns else "waiting", "blocker": None if confirmed and not unknowns else "Confirm request/resolve unknowns with owner", "next_action": "Execute accepted operation" if confirmed and not unknowns else "Obtain missing decision", "revision": 0, "history": [], "errors": [], "output": None, "critical": None, "evidence": None, "independent_required": independent_required, "independent_review": None, "delivery": {"status": "local"}, "application": {"status": "unknown"}, "effect": {"status": "unknown"}}
        event(t, "intake", {"confirmed": confirmed, "unknowns": unknowns})
        validation.task(root, t)
        write(file, t)
        return t


def decide(root, task_id, revision, action, reason, actor):
    with lock(root):
        cfg = config(root, True)
        require(actor == cfg["owner"] and reason.strip(), "company owner decision required")
        file = task_file(root, task_id)
        t = load(file)
        require(t["revision"] == revision, "stale task revision")
        require(action in {"confirm", "cancel", "resume"}, "unknown decision")
        require(t["status"] != "cancelled" or action == "cancel", "cancelled task is terminal")
        previous_verification = {"evidence": t.get("evidence"), "critical": t.get("critical"), "output": t.get("output")} if action == "resume" and t.get("evidence") else None
        if action == "cancel":
            t.update(status="cancelled", next_action="None", blocker=None)
        else:
            if action == "resume":
                # Explicit version reconciliation; old evidence remains in history.
                t["bindings"] = method_bindings(root, t["inputs"])
                t["evidence"] = None
            t.update(status="active", confirmed=True, unknowns=[], blocker=None, next_action="Execute accepted operation")
        detail = {"actor": actor, "reason": reason}
        if previous_verification:
            detail["previous_verification"] = previous_verification
        event(t, action, detail)
        write(file, t)
        return t


def execute(root, task_id, artifact, critical, independent_review=None):
    with lock(root):
        config(root, True)
        file = task_file(root, task_id)
        t = load(file)
        require(t["confirmed"] and not t["unknowns"] and t["status"] not in {"waiting", "cancelled"}, "unconfirmed/cancelled request")
        changes = changed_bindings(root, t["bindings"])
        if changes:
            t.update(status="blocked", blocker="Pinned methods/inputs changed: " + ", ".join(changes), next_action="Owner reconciles exact versions; resume")
            event(t, "version-block", changes)
            write(file, t)
            raise ValueError(t["blocker"])
        try:
            validation.repository(root, freshness=False)
            candidate_hash = object_hash(artifact)
            recent = []
            for history in reversed(t["history"]):
                if history["action"] in {"resume", "verified"}:
                    break
                if history["action"] == "rejected":
                    recent.append(history)
            require(len(recent) < 2 or any(e.get("candidate_hash") != candidate_hash for e in t["errors"][-2:]), "same candidate failed twice; new evidence or owner reconciliation required")
            require(t["acceptance_hash"] == object_hash(t["acceptance"]), "original acceptance changed")
            admission = validation.source_admission(root, artifact) if artifact.get("kind") == "metrics" else None
            validation.result(root, artifact, t["acceptance"], admission)
            require(isinstance(critical, dict) and critical.get("request_alignment") and critical.get("counterexample") and isinstance(critical.get("limitations"), list) and critical.get("references"), "critical pass needs evidence and counterexample")
            for ref in critical["references"]:
                require(path(root, ref).is_file(), "critical reference missing")
            if t["independent_required"]:
                require(independent_review and independent_review.get("reviewer") != t["owner"] and independent_review.get("reviewer") and independent_review.get("verdict") == "pass" and independent_review.get("artifact_hash") == object_hash(artifact), "independent review required for exact artifact")
            if t["status"] == "verified" and t.get("output") and load(path(root, t["output"]["path"])) == artifact:
                validation.task(root, t)
                return t
            relative = f"work/{t['id']}/result-{t['revision'] + 1}.json"
            write(path(root, relative), artifact)
            if t.get("output"):
                event(t, "superseded-result", {"output": t["output"], "evidence": t.get("evidence"), "critical": t.get("critical"), "application": t["application"], "effect": t["effect"], "delivery": t["delivery"], "independent_review": t.get("independent_review")})
            t.update(application={"status": "unknown"}, effect={"status": "unknown"}, independent_review=None)
            t["output"] = {"path": relative, "sha256": digest(path(root, relative))}
            t["critical"] = critical
            t["evidence"] = {"command": "python3 scripts/system.py execute", "exit_code": 0, "output": "structure, provenance and selected result contract passed; semantic assessment recorded separately", "at": now(), "bindings": t["bindings"], "acceptance_sha256": t["acceptance_hash"], "result_sha256": t["output"]["sha256"], "critical_sha256": object_hash(critical)}
            if admission:
                t["evidence"].update(at=admission["at"], source_admission=admission, source_admission_sha256=object_hash(admission))
            if independent_review:
                t["independent_review"] = {**independent_review, "result_sha256": t["output"]["sha256"]}
            t.update(status="verified", blocker=None, next_action=artifact["next_action"], delivery={"status": "pending"})
            event(t, "verified", {"output": t["output"], "semantic": "author-assessment", "application": "unknown"})
            validation.task(root, t)
            write(file, t)
            return t
        except Exception as exc:
            t["errors"].append({"at": now(), "observed": str(exc), "first_false_assumption": "Candidate satisfies original contract", "candidate_hash": object_hash(artifact)})
            t.update(status="blocked", blocker=str(exc), next_action="Challenge assumption, repair candidate, repeat failing check")
            event(t, "rejected", str(exc))
            write(file, t)
            raise


def observe(root, task_id, layer, evidence, actor):
    require(layer in {"application", "effect"}, "observation layer")
    with lock(root):
        cfg = config(root, True)
        require(actor == cfg["owner"] and evidence.strip(), "observation needs owner evidence")
        file = task_file(root, task_id)
        t = load(file)
        require(t["status"] == "verified", "unverified output cannot establish application")
        validation.task(root, t)
        t[layer] = {"status": "observed", "evidence": evidence, "actor": actor, "at": now(), "result_sha256": t["output"]["sha256"]}
        event(t, layer, t[layer])
        validation.task(root, t)
        write(file, t)
        return t


def summary(root, exclude=None):
    if exclude:
        ident(exclude)
        with lock(root):
            config(root, True)
            require(task_file(root, exclude).is_file(), "summary owning task missing")
            return _summary(root, exclude)
    return _summary(root)


def _summary(root, exclude=None):
    entries, refs = [], []
    for file in tasks(root):
        t = load(file)
        if t["id"] == exclude:
            continue
        refs.append({"path": str(file.relative_to(root)), "sha256": digest(file)})
        entries.append({"id": t["id"], "owner": t["owner"], "status": t["status"], "next_action": t["next_action"], "waiting_for": t.get("blocker"), "since": t["history"][-1]["at"], "revision": t["revision"]})
    counts = {status: sum(t["status"] == status for t in entries) for status in ["draft", "active", "waiting", "blocked", "verified", "cancelled"]}
    company_id = config(root)["id"]
    if exclude:
        snapshot = {"schema_version": 1, "company_id": company_id, "kind": "action-summary-input", "entries": entries, "counts": counts, "observed_sources": refs, "excluded_task": exclude}
        relative = f"work/{exclude}/inputs/summary-{object_hash(snapshot)}.json"
        target = path(root, relative)
        if target.exists():
            require(load(target) == snapshot, "summary snapshot changed")
        else:
            write(target, snapshot)
        refs = [{"path": relative, "sha256": digest(target)}]
    return {"schema_version": 1, "company_id": company_id, "kind": "action-summary", "summary": "Current actions and waiting owners", "entries": entries, "counts": counts, "sources": refs or [{"path": "company/config.yaml", "sha256": digest(path(root, "company/config.yaml"))}], "limitations": ["Priorities/WIP limits and team use are not inferred", "History belongs to each task", "Snapshot reflects recorded revisions; later changes need a new summary"], "next_action": "Each listed owner can follow the referenced task"}


def reconcile(left, right):
    for envelope in (left, right):
        require(isinstance(envelope, dict) and envelope.get("complete") is True, "incomplete reconciliation source")
        require(all(envelope.get(k) for k in ["company_id", "account", "scope", "period", "unit"]), "reconciliation metadata missing")
        require(isinstance(envelope.get("records"), list) and all(isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"] for r in envelope["records"]), "reconciliation record ID missing")
    a, b = {r["id"]: r for r in left["records"]}, {r["id"]: r for r in right["records"]}
    require(len(a) == len(left["records"]) and len(b) == len(right["records"]), "duplicate reconciliation ID")
    require(all(left.get(k) == right.get(k) for k in ["company_id", "scope", "period", "unit"]), "incomparable sources")
    return {"only_left": sorted(a.keys() - b.keys()), "only_right": sorted(b.keys() - a.keys()), "different": [key for key in sorted(a.keys() & b.keys()) if a[key] != b[key]], "matched": sum(a[k] == b[k] for k in a.keys() & b.keys()), "limitations": ["A recorded error is not automatically a lost outcome", "Different accounts require an accepted common record-ID/scope definition; matching alone does not prove business compatibility"]}


def episodes(envelope, denominator):
    require(type(denominator) is int and denominator >= len(envelope["records"]), "unknown/invalid denominator")
    counts = {}
    for r in envelope["records"]:
        require(r.get("scenario") and r.get("outcome") in {"completed", "waiting", "unknown"}, "episode definition missing")
        key = r["scenario"] + ":" + r["outcome"]
        counts[key] = counts.get(key, 0) + 1
    return {"period": envelope["period"], "denominator": denominator, "observed": len(envelope["records"]), "missing": denominator - len(envelope["records"]), "scenarios": counts, "limitations": ["Unobserved episodes retain unknown outcomes; no causal inference"]}


def tick(root):
    with lock(root):
        cfg = config(root, True)
        if not cfg["features"]["proactive"]:
            return {"status": "disabled", "notify": False}
        specification = cfg["proactive"]
        require(specification.get("authorization") and type(specification.get("max_runs")) is int and specification["max_runs"] > 0 and type(specification.get("interval_seconds")) is int and specification["interval_seconds"] > 0, "proactive scope/limits not accepted")
        file = task_file(root, specification["task_id"])
        t = load(file)
        require(t["confirmed"] and t["status"] not in {"cancelled", "waiting"}, "proactive task not authorized")
        require(not changed_bindings(root, t["bindings"]), "proactive versions changed")
        state = t.setdefault("automation", {"runs": 0, "fingerprint": None, "last_at": None})
        if state["runs"] >= specification["max_runs"]:
            return {"status": "limit", "notify": False}
        if state["last_at"] and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(state["last_at"])).total_seconds() < specification["interval_seconds"]:
            return {"status": "too-early", "notify": False}
        output = _summary(root, t["id"])
        fingerprint = object_hash(output)
        changed = fingerprint != state["fingerprint"]
        state.update(runs=state["runs"] + 1, fingerprint=fingerprint, last_at=now())
        if changed:
            relative = f"work/{t['id']}/signal-{state['runs']}.json"
            write(path(root, relative), output)
            t["signal"] = {"path": relative, "sha256": digest(path(root, relative)), "acknowledged": False}
            event(t, "signal", t["signal"])
        write(file, t)
        return {"status": "changed" if changed else "unchanged", "notify": changed, "signal": t.get("signal") if changed else None}


def incident(root, task_id, observed, assumption, proposal):
    with lock(root):
        config(root, True)
        file = task_file(root, task_id)
        t = load(file)
        require(t["status"] != "cancelled", "cancelled task is terminal; create a new confirmed request")
        require(observed and assumption and proposal, "incident evidence missing")
        t["errors"].append({"at": now(), "observed": observed, "first_false_assumption": assumption, "proposal": proposal, "status": "proposal"})
        t.update(status="blocked", blocker=observed, next_action="Evaluate existing solution; test bounded repair against original criterion")
        event(t, "incident", t["errors"][-1])
        write(file, t)
        return t
