"""Fixed local hook handlers and conservative project-local projections.

Native output is a provider protocol, never a substitute for CLI admission.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import shlex
import os
import threading
import sys
import time

from core import Rejected, changed_bindings, config, config_snapshot, digest, ident, load, lock, object_hash, path, require, write
import validation
import platform_runtime as platform

NAMESPACE = "company-work-system/hooks/v1"
MAX_INPUT = 262144
MAX_FILE = 1048576
EVENTS = {"SessionStart": ["context-entry"], "PreToolUse": ["entity-before-write", "method-impact", "publication-check"], "PostToolUse": ["entity-after-write"], "PreCompact": ["task-continuation"], "Stop": ["task-continuation"]}
SCENARIOS = {name for names in EVENTS.values() for name in names}
TARGETS = {"codex": ".codex/hooks.json", "claude": ".claude/settings.json"}


def roots(root):
    root = Path(root)
    require(root.is_absolute(), "absolute hook root required")
    root = root.resolve()
    if (root / "company/config.yaml").is_file():
        config(root)
        return root, root, "company"
    require((root / "template/company/config.yaml").is_file() and (root / "docs/development/PLAN.md").is_file() and (root / "AGENTS.md").is_file() and (root / "scripts/product.py").is_file() and (root / ".git").is_dir(), "unrecognized hook root; use explicit company/product root")
    config(root / "template")
    return root, root / "template", "product"


def bounded_load(file):
    require(file.is_file() and file.stat().st_size <= MAX_FILE, "missing/oversize hook file: " + str(file))
    return load(file)


def manifest(root):
    _, base, _ = roots(root)
    value = bounded_load(path(base, "hooks/manifest.yaml"))
    require(isinstance(value, dict) and set(value) == {"schema_version", "id", "version", "owner", "hooks"}, "invalid hook manifest fields")
    require(value["schema_version"] == 1 and value["id"] == "company-hooks" and value["version"] == 1 and value["owner"], "unsupported hook manifest")
    require(isinstance(value["hooks"], list), "hook list required")
    seen = set()
    fields = {"id", "version", "owner", "purpose", "failure", "events", "tools", "paths", "handler", "dependencies", "severity", "enabled", "timeout", "fallback", "positive", "negative"}
    for item in value["hooks"]:
        require(isinstance(item, dict) and set(item) == fields, "invalid hook declaration fields")
        name = item["id"]
        require(name in SCENARIOS and name not in seen and item["handler"] == name, "unknown/duplicate fixed handler")
        require(item["events"] == [event for event, names in EVENTS.items() if name in names], "unsupported event mapping")
        require(item["version"] == 1 and item["owner"] and item["enabled"] == "explicit-company-opt-in", "hook policy missing")
        require(item["severity"] == ("proved-violation" if name == "entity-before-write" else "advisory"), "unsupported severity")
        require(type(item["timeout"]) is int and 0 < item["timeout"] <= 3, "hook timeout exceeds budget")
        for field in ["tools", "paths", "dependencies"]:
            require(isinstance(item[field], list) and all(isinstance(x, str) for x in item[field]), "hook selectors/dependencies malformed")
        require(set(item["tools"]) <= {"Write", "Edit", "apply_patch", "Bash"}, "unsupported tool selector")
        require(all(selector and ".." not in selector.split("/") and not selector.startswith("/") for selector in item["paths"]), "unsupported path selector")
        for field in ["purpose", "failure", "fallback", "positive", "negative"]:
            require(isinstance(item[field], str) and item[field], "hook contract incomplete")
        for relative in item["dependencies"]:
            require(path(base, relative).is_file(), "missing hook dependency: " + relative)
        seen.add(name)
    require(seen == SCENARIOS, "six canonical scenarios required")
    return value


def definition_hash(root, harness):
    require(harness in TARGETS or harness == "common", "unsupported harness")
    _, base, _ = roots(root)
    declaration = manifest(root)
    files = {"hooks/manifest.yaml", "scripts/hooks.py", "scripts/core.py", "scripts/platform_runtime.py", "scripts/validation.py", "scripts/dependencies.py"}
    files.update(relative for item in declaration["hooks"] for relative in item["dependencies"])
    if harness != "common":
        files.add(f"adapters/{harness}/profile.yaml")
    return object_hash({relative: digest(path(base, relative)) for relative in sorted(files)})


def _relative(root, cwd, value):
    require(isinstance(value, str) and value and "\x00" not in value, "tool path required")
    candidate = Path(value)
    # Reject lexical traversal even when resolution would land back inside root.
    require(".." not in candidate.parts, "tool path traversal")
    candidate = candidate if candidate.is_absolute() else cwd / candidate
    require(candidate.resolve().is_relative_to(root), "tool path outside explicit root")
    return path(root, candidate.relative_to(root).as_posix()).relative_to(root).as_posix()


def _changes(root, payload):
    cwd = Path(payload.get("cwd", str(root))).resolve()
    require(cwd.is_relative_to(root) and cwd.is_dir(), "hook cwd outside explicit root")
    tool = payload.get("tool_name")
    data = payload.get("tool_input", {})
    require(isinstance(data, dict), "tool_input object required")
    if tool == "Write":
        require(isinstance(data.get("content"), str), "Write content required")
        return [{"path": _relative(root, cwd, data.get("file_path")), "content": data["content"]}]
    if tool == "Edit":
        relative = _relative(root, cwd, data.get("file_path"))
        require(isinstance(data.get("old_string"), str) and isinstance(data.get("new_string"), str), "Edit strings required")
        file = path(root, relative)
        require(file.is_file() and file.stat().st_size <= MAX_FILE, "Edit target missing/oversize")
        text = file.read_text(encoding="utf-8")
        old = data["old_string"]
        if not old or (not data.get("replace_all") and text.count(old) != 1) or old not in text:
            return [{"path": relative, "content": None}]
        return [{"path": relative, "content": text.replace(old, data["new_string"], -1 if data.get("replace_all") else 1)}]
    if tool != "apply_patch":
        return []
    patch = data.get("command")
    require(isinstance(patch, str), "apply_patch command required")
    lines = patch.splitlines()
    require(lines and lines[0] == "*** Begin Patch" and lines[-1] == "*** End Patch", "unrecognized patch framing")
    changes = []
    i = 1
    while i < len(lines) - 1:
        header = lines[i]
        require(header.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ")), "unrecognized patch action")
        relative = _relative(root, cwd, header.split(": ", 1)[1])
        i += 1
        body = []
        while i < len(lines) - 1 and not lines[i].startswith("*** "):
            body.append(lines[i]); i += 1
        if header.startswith("*** Add File: "):
            require(all(line.startswith("+") for line in body), "unrecognized Add File patch")
            content = "\n".join(line[1:] for line in body) + "\n"
        else:
            content = None  # Partial update hunks cannot prove replacement metadata.
        changes.append({"path": relative, "content": content})
    return changes


def _task(root, base, kind, task_id):
    if not task_id:
        return None
    if kind == "product":
        require(isinstance(task_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{1,127}", task_id), "invalid development task slug")
        file = path(root, f"docs/development/work/{task_id}/task.md")
        return {"path": file.relative_to(root).as_posix(), "development": True} if file.is_file() else None
    ident(task_id)
    file = path(base, f"work/{task_id}/task.json")
    if not file.is_file():
        return None
    value = bounded_load(file)
    require(isinstance(value, dict) and value.get("id") == task_id and value.get("company_id") == config(base)["id"], "task identity mismatch")
    return {**value, "path": file.relative_to(root).as_posix()}


def _navigation(kind, task):
    routes = ["AGENTS.md", "docs/development/README.md", "docs/development/PLAN.md"] if kind == "product" else ["AGENTS.md", "company/config.yaml", "company/README.md"]
    if task:
        routes.append(task["path"])
    return "Read " + ", ".join(routes) + "; independent task entry still requires the shared preflight."


def _publication_invocation(root, payload):
    if payload.get("tool_name") != "Bash":
        return False
    data = payload.get("tool_input", {})
    command = data.get("command") if isinstance(data, dict) else None
    if not isinstance(command, str):
        return False
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if len(args) < 3 or Path(args[0]).name not in {"python", "python3", Path(sys.executable).name}:
        return False
    cwd = Path(payload.get("cwd", root))
    script = Path(args[1]); script = script if script.is_absolute() else cwd / script
    if script.resolve() not in {root / "scripts/system.py", root / "scripts/product.py"}:
        return False
    return any(arg in {"commit", "deliver"} for arg in args[2:])


def _impact(base, changes):
    selected = {item["path"] for item in changes if item["path"].startswith(("standards/", "skills/", "workflows/", "scripts/", "hooks/", "adapters/", "company/standards/"))}
    impacted = []
    if not selected:
        return impacted
    # Only explicit task file dependencies are evidence of impact.
    for file in sorted(base.glob("work/*/task.json")):
        value = bounded_load(file)
        changed = changed_bindings(base, value.get("bindings", {}), scope=value.get("binding_scope"))
        matches = sorted(selected & (set(value.get("bindings", {})) | set(changed)))
        if matches:
            impacted.append({"task": value.get("id"), "paths": matches, "changed": changed})
    return impacted


def _method_consumers(base, changes):
    from dependencies import closure
    selected = {item["path"] for item in changes}
    consumers = []
    for recipe in sorted(base.glob("skills/*/workflow.yaml")):
        result = closure(base, recipe.parent.name)
        if result is None:
            consumers.append({"skill": recipe.parent.name, "status": "open", "reason": "undeclared dependencies; conservative CLI check required"})
        else:
            files, _ = result
            matches = sorted(selected & files)
            if matches:
                consumers.append({"skill": recipe.parent.name, "status": "explicit", "paths": matches})
    return consumers


def _once(root, task, fingerprint, dependency_changes, message):
    key = object_hash({"task": task["id"], "revision": task.get("revision"), "definition": fingerprint, "dependencies": dependency_changes, "next": task.get("next_action"), "evidence": task.get("evidence"), "errors": task.get("errors")})
    receipt = path(root, ".system/cache/hooks-continuation.json")
    with lock(root):
        value = bounded_load(receipt) if receipt.exists() else {"schema_version": 1, "tasks": {}}
        require(value.get("schema_version") == 1 and isinstance(value.get("tasks"), dict), "invalid hook dedup cache")
        if value["tasks"].get(task["id"]) == key:
            return None
        value["tasks"][task["id"]] = key
        write(receipt, value)
    return message


def dispatch(root, harness, event, payload, task_id=None, *, definition_hash_expected=None, native=False):
    start = time.monotonic()
    require(harness in {*TARGETS, "common"}, "unsupported harness")
    require(isinstance(payload, dict) and len(json.dumps(payload).encode()) <= MAX_INPUT, "invalid/oversize hook payload")
    root, base, kind = roots(root)
    require(event in EVENTS or event in SCENARIOS or event == "Interrupt", "unsupported hook event")
    require(payload.get("hook_event_name", event) == event, "hook event mismatch")
    if "cwd" in payload:
        require(isinstance(payload["cwd"], str) and Path(payload["cwd"]).is_absolute() and Path(payload["cwd"]).resolve().is_relative_to(root), "hook cwd outside explicit root")
    fingerprint = definition_hash(root, harness)
    declaration = {item["id"]: item for item in manifest(root)["hooks"]}
    if definition_hash_expected is not None:
        require(definition_hash_expected == fingerprint, "hook definition/dependency fingerprint changed; regenerate, review native trust, use CLI fallback")
    policy = config(base).get("hooks", {})
    require(isinstance(policy, dict) and isinstance(policy.get("disabled", []), list) and set(policy.get("disabled", [])) <= SCENARIOS, "invalid company hooks policy")
    if native and policy.get("enabled") is not True:
        return {"status": "disabled", "messages": ["Native hooks disabled; shared CLI checks remain required."], "definition_hash": fingerprint}
    if native and payload.get("hook_trusted") is False:
        return {"status": "untrusted", "messages": ["Provider hook trust missing; use shared CLI fallback."], "definition_hash": fingerprint}
    if native:
        require(definition_hash_expected is not None, "native definition fingerprint required; use shared CLI fallback")
    if event == "Interrupt" or payload.get("interrupted") or payload.get("stop_hook_active") or payload.get("subagent_id") or payload.get("agent_type") not in {None, "main"}:
        return {"status": "skipped", "messages": [], "reason": "interrupt/subagent/recursive-stop", "definition_hash": fingerprint}
    selected = EVENTS.get(event, [event])
    selected = [name for name in selected if name not in policy.get("disabled", [])]
    task = _task(root, base, kind, task_id)
    messages, violations, impact, consumers = [], [], [], []
    changes = _changes(root, payload) if event in {"PreToolUse", "PostToolUse", "entity-before-write", "entity-after-write", "method-impact"} else []
    if event == "method-impact" and "paths" in payload:
        require(isinstance(payload["paths"], list) and 0 < len(payload["paths"]) <= 128, "method-impact paths list required")
        changes = [{"path": _relative(root, root, value), "content": None} for value in payload["paths"]]
    if kind == "product":
        changes = [{**item, "path": item["path"][len("template/"):]} for item in changes if item["path"].startswith("template/")]
    for name in selected:
        item = declaration[name]
        if item["tools"] and payload.get("tool_name") not in item["tools"] and event not in SCENARIOS:
            continue
        relevant = [change for change in changes if not item["paths"] or any(change["path"].startswith(selector) if selector.endswith("/") else Path(change["path"]).match(selector) for selector in item["paths"])]
        if name == "context-entry":
            messages.append(_navigation(kind, task))
            if task and not task.get("development"):
                messages.append(f"Task revision {task.get('revision')}; next_action: {str(task.get('next_action', 'missing'))[:500]}")
        elif name in {"entity-before-write", "entity-after-write"} and relevant:
            entities = [change for change in relevant if validation.entity_kind(change["path"]) is not None]
            if not entities:
                continue
            if name == "entity-after-write":
                entities = [{**change, "content": None} for change in entities]
            checked = validation.entity_changes(base, entities, before=name == "entity-before-write")
            violations.extend(checked.get("violations", []) if name == "entity-before-write" else [])
            messages.extend(checked.get("advisories", []))
            if name == "entity-after-write":
                messages.extend(checked.get("violations", []))
        elif name == "method-impact" and relevant:
            impact = _impact(base, relevant)
            consumers = _method_consumers(base, relevant)
            if impact:
                messages.append("Explicit method dependencies affect: " + ", ".join(str(item["task"]) for item in impact) + "; reconcile each task before dependent execution.")
            else:
                messages.append("No explicit task consumer recorded; wider method/process/block impact remains open. Use the shared CLI validation before delivery.")
            if consumers:
                messages.append("Declared recipe consumers: " + ", ".join(item["skill"] + (" (open)" if item["status"] == "open" else "") for item in consumers) + ".")
        elif name == "task-continuation":
            if not task or task.get("development"):
                messages.append(_navigation(kind, task))
            elif task.get("status") != "cancelled":
                if payload.get("task_revision") is not None:
                    require(payload["task_revision"] == task.get("revision"), "stale hook task revision; read current task")
                changed = changed_bindings(base, task.get("bindings", {}), scope=task.get("binding_scope"))
                missing = [field for field in ["next_action", "evidence"] if not task.get(field)]
                message = f"Read {task['path']} revision {task.get('revision')}; "
                message += "save current next_action/evidence and observed failure before handoff." if missing or changed else "continue from recorded next_action; delivered/application state remains separate."
                once = _once(root, task, fingerprint, changed, message)
                if once:
                    messages.append(once)
        elif name == "publication-check":
            # A hint only: arbitrary Bash/MCP writes are outside interception.
            if event == "publication-check" or _publication_invocation(root, payload):
                messages.append("Publication requires the shared CLI history/path/policy guard on the exact candidate; this hook does not scan Git history or authorize a push.")
    require(time.monotonic() - start <= 3, "hook local budget exceeded; CLI fallback required")
    status = "proved-violation" if violations else "advisory" if messages else "disabled" if not selected else "skipped" if event in {"PreToolUse", "PostToolUse"} and not changes else "pass"
    return {"status": status, "messages": messages, "violations": violations, "impact": impact, "method_consumers": consumers, "definition_hash": fingerprint, "coverage": "recognized file inputs and explicit CLI hints only; required CLI guards remain authoritative", "elapsed_ms": round((time.monotonic() - start) * 1000, 3)}


def native_output(event, result):
    messages = result.get("violations", []) + result.get("messages", [])
    text = " ".join(messages)[:4000]
    if event == "PreToolUse" and result["status"] == "proved-violation":
        return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny", "permissionDecisionReason": text}}
    if not text:
        return {}
    if event in {"SessionStart", "PreToolUse", "PostToolUse"}:
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    return {"systemMessage": text}  # No Stop decision/block/continue=false.


def _projection(root, harness, fingerprint, enabled):
    root, base, _ = roots(root)
    if not enabled:
        return []
    declaration = manifest(root)
    result = []
    for event in EVENTS:
        args = [sys.executable, str(base / "scripts/hooks.py"), "--root", str(root), "--namespace", NAMESPACE, "--harness", harness, "--event", event, "--definition-hash", fingerprint]
        handler = {"type": "command", "command": platform.shell_command(args), "timeout": 3}
        if platform.WINDOWS:
            windows = platform.shell_command(args, windows=True)
            if harness == "claude":
                handler.update(command=windows, shell="powershell")
            else:
                # Explicit shell inside the Windows override; no assumption about
                # the provider's outer shell. EncodedCommand is UTF-16LE PowerShell.
                encoded = base64.b64encode(windows.encode("utf-16le")).decode("ascii")
                handler["commandWindows"] = "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded
        group = {"hooks": [handler]}
        if event in {"PreToolUse", "PostToolUse"}:
            tools = sorted({tool for item in declaration["hooks"] if event in item["events"] for tool in item["tools"]})
            group["matcher"] = "^(" + "|".join(re.escape(tool) for tool in tools) + ")$"
        result.append({"event": event, "group": group})
    return result


def project(root, harness, apply=False, enabled=False):
    require(harness in TARGETS, "unsupported native harness")
    root, base, kind = roots(root)
    if apply:
        require(kind == "company", "product hook projection is preview-only; apply only to an authorized adapted company")
        config(base, writable=True)
    target = path(root, TARGETS[harness])
    receipt = path(root, f".system/hooks-project-{harness}.json")
    legacy = path(root, f".system/cache/hooks-project-{harness}.json")
    def prepare():
        fingerprint = definition_hash(root, harness)
        desired = _projection(root, harness, fingerprint, enabled)
        def conflict(reason):
            return {"status": "conflict", "harness": harness, "target": TARGETS[harness], "reason": reason, "definition_hash": fingerprint, "projection": desired}, None, None
        try:
            existing = bounded_load(target) if target.exists() else {}
            # New tracked proof is the only authority. Legacy is considered only
            # when no new proof exists, and only after exact native comparison.
            previous_file = receipt if receipt.exists() else legacy if legacy.exists() else None
            previous = bounded_load(previous_file) if previous_file else None
        except (OSError, ValueError, Rejected):
            return conflict("native settings/receipt unreadable or malformed; preserved")
        if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
            return conflict("native settings malformed; preserved")
        if previous is not None:
            receipt_fields = {"schema_version", "namespace", "harness", "target", "definition_hash", "owned", "enabled"}
            if not isinstance(previous, dict) or set(previous) != receipt_fields or not (previous.get("schema_version") == 1 and previous.get("namespace") == NAMESPACE and previous.get("harness") == harness and previous.get("target") == TARGETS[harness]) or not isinstance(previous.get("definition_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", previous["definition_hash"]) or not isinstance(previous.get("owned"), list) or type(previous.get("enabled")) is not bool:
                return conflict("unknown projection receipt; preserved")
        found, positions = [], []
        for event, groups in existing.get("hooks", {}).items():
            if not isinstance(groups, list):
                return conflict("native hook groups malformed; preserved")
            for i, group in enumerate(groups):
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    return conflict("native hook group malformed; preserved")
                if any(NAMESPACE in str(handler.get("command", "")) for handler in group["hooks"] if isinstance(handler, dict)):
                    found.append({"event": event, "group": group}); positions.append((event, i))
        expected = previous.get("owned", []) if previous else []
        if found != expected:
            return conflict("owned native definitions differ from exact previous projection; settings preserved")
        candidate = copy.deepcopy(existing)
        hooks = candidate.setdefault("hooks", {}) if desired or found else candidate.get("hooks", {})
        replacements = {item["event"]: item["group"] for item in desired}
        for event, i in reversed(positions):
            if event in replacements:
                hooks[event][i] = replacements.pop(event)
            else:
                del hooks[event][i]
        for event, group in replacements.items():
            hooks.setdefault(event, []).append(group)
        if not desired:
            for event, _ in positions:
                if not hooks[event]:
                    del hooks[event]
        owned = [{"event": event, "group": group} for event, groups in hooks.items() for group in groups if any(NAMESPACE in str(handler.get("command", "")) for handler in group["hooks"] if isinstance(handler, dict))]
        record = {"schema_version": 1, "namespace": NAMESPACE, "harness": harness, "target": TARGETS[harness], "definition_hash": fingerprint, "owned": owned, "enabled": enabled}
        result = {"status": "preview", "harness": harness, "target": TARGETS[harness], "definition_hash": fingerprint, "projection": desired, "changed": candidate != existing, "enabled": enabled, "native": "unverified; provider trust/access required", "preserved_groups": sum(len(g) for g in existing.get("hooks", {}).values()) - len(found), "legacy_migration": previous_file == legacy}
        return result, candidate, record
    if not apply:
        return prepare()[0]
    with lock(root):
        config_file = path(base, "company/config.yaml")
        try:
            pinned_config, config_sha = config_snapshot(base)
            # Reuse the shared writable contract. Its value must still describe
            # exactly the bytes pinned under this same company lock.
            authorized_config = config(base, writable=True)
            require(authorized_config == pinned_config and digest(config_file) == config_sha, "company authorization changed during projection entry")
        except Exception:
            return {"status": "conflict", "harness": harness, "target": TARGETS[harness], "reason": "company identity/owner/authorization changed or became unreadable; preserve and preview again"}
        guarded = [target, receipt] + ([legacy] if not receipt.exists() else [])
        snapshots = {file: digest(file) if file.exists() else None for file in guarded}
        snapshots[config_file] = config_sha
        result, candidate, record = prepare()
        if result["status"] == "conflict":
            return result
        def unchanged():
            try:
                # Fingerprinting itself reads mutable dependencies. Always do
                # the final byte CAS after that call, immediately before writing.
                current_definition = definition_hash(root, harness)
                return current_definition == result["definition_hash"] and all((digest(file) if file.exists() else None) == sha for file, sha in snapshots.items())
            except Exception:
                return False
        def conflict():
            return {**result, "status": "conflict", "reason": "company authorization/native settings/proof/method bytes changed during projection; preserve and preview again"}
        proof_needed = (result["changed"] or receipt.exists() or enabled or result["legacy_migration"]) and (not receipt.exists() or bounded_load(receipt) != record)
        if not unchanged():
            return conflict()
        if result["changed"]:
            write(target, candidate)
            # Pin the exact JSON bytes produced by core.write, rather than
            # adopting whatever a concurrent editor may have written afterward.
            snapshots[target] = hashlib.sha256((json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
        if proof_needed:
            if not unchanged():
                return conflict()
            write(receipt, record)
        result["status"] = "projected" if result["changed"] else "unchanged"
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--harness", choices=sorted(TARGETS), required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--event", choices=sorted(EVENTS), required=True)
    parser.add_argument("--definition-hash", required=True)
    parser.add_argument("--task")
    args = parser.parse_args(argv)
    finished = threading.Event()
    def deadline():
        if not finished.wait(3):
            message = {"systemMessage": "Hook handler failure; use shared CLI fallback: TimeoutError: hook timeout; required CLI checks remain pending"}
            os.write(sys.stdout.fileno(), (json.dumps(message) + "\n").encode("utf-8"))
            os._exit(1)  # Only this owned hook process; no worker can write later.
    threading.Thread(target=deadline, daemon=True).start()
    try:
        require(args.namespace == NAMESPACE, "unknown hook namespace")
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        require(len(raw) <= MAX_INPUT, "oversize hook payload")
        payload = json.loads(raw)
        result = dispatch(args.root, args.harness, args.event, payload, args.task, definition_hash_expected=args.definition_hash, native=True)
        print(json.dumps(native_output(args.event, result), ensure_ascii=True), flush=True)
        return 0
    except Exception as exc:
        # Exit 1 is failure, never provider denial/automatic continuation.
        print(json.dumps({"systemMessage": "Hook handler failure; use shared CLI fallback: " + type(exc).__name__ + ": " + str(exc)[:500]}), flush=True)
        return 1
    finally:
        finished.set()


if __name__ == "__main__":
    raise SystemExit(main())
