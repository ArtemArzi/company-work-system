"""Shared file contracts; no company state outside its owning object."""
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import yaml
import platform_runtime as platform


class Rejected(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def ident(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{1,63}", value), "invalid ID")
    return value


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def path(root, relative):
    root = Path(root).resolve()
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute(), "relative path required")
    require(relative == Path(relative).as_posix() and not any(part in {"", ".", ".."} for part in relative.split("/")), "canonical relative path required")
    try:
        platform.portable_path(relative)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc
    result = root / relative
    require(result.resolve().is_relative_to(root), "path outside company")
    try:
        platform.no_alias_parents(root, result)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc
    return result


def load(file):
    file = Path(file)
    if file.suffix == ".json":
        return json.loads(file.read_text(encoding="utf-8"))
    return yaml.safe_load(file.read_text(encoding="utf-8"))


def digest(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write(file, data):
    """Atomic replacement + fsync; callers hold the company lock for state changes."""
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    value = json.dumps(data, ensure_ascii=False, indent=2) + "\n" if file.suffix == ".json" else yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    platform.atomic_text(file, value)


@contextlib.contextmanager
def lock(root):
    file = Path(root).resolve() / ".system/lock"
    try:
        platform.no_alias_parents(Path(root).resolve(), file)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc
    try:
        with platform.process_lock(file):
            yield
    except BlockingIOError as exc:
        raise Rejected("company operation already running") from exc


def config_snapshot(root):
    """Parse and hash the same immutable bytes, never two reads of mutable config."""
    raw = path(root, "company/config.yaml").read_bytes()
    value = yaml.safe_load(raw)
    require(isinstance(value, dict), "company config must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def config(root, writable=False):
    value = config_snapshot(root)[0]
    require(value.get("schema_version") == 1, "unsupported company format")
    ident(value.get("id"))
    if writable:
        require(value["id"] != "unconfigured" and value.get("owner") and value["permissions"]["local_work"] is True, "company not adapted/authorized")
    return value


def task_file(root, task_id):
    return path(root, f"work/{ident(task_id)}/task.json")


def tasks(root):
    return sorted(Path(root).glob("work/*/task.json"))


def event(task, action, detail):
    task["revision"] += 1
    task["history"].append({"at": now(), "revision": task["revision"], "action": action, "detail": detail})


def method_bindings(root, inputs=()):
    relatives = ["company/config.yaml", "release.yaml"]
    for directory in ["standards", "skills", "workflows", "scripts", "adapters", "hooks", "company/standards"]:
        relatives += [p.relative_to(root).as_posix() for p in sorted(Path(root, directory).rglob("*")) if p.is_file() and p.suffix in {".py", ".md", ".yaml"}]
    relatives += list(inputs)
    return {p: digest(path(root, p)) for p in sorted(set(relatives))}


def changed_bindings(root, bindings, scope=None):
    if scope is not None:
        from dependencies import changed
        return changed(root, bindings, scope)
    return [p for p, sha in bindings.items() if not path(root, p).is_file() or digest(path(root, p)) != sha]
