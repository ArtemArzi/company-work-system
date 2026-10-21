"""Read-only maintenance of declared current routes, never historical evidence."""
from pathlib import Path
import os
import re
from urllib.parse import unquote, urlsplit

from core import config, digest, load, path, require
import platform_runtime as platform

MAX_CONSUMERS = 128
MAX_DOCUMENT_BYTES = 1024 * 1024


def declaration(root):
    cfg = config(root)
    value = cfg.get("workspace", {}).get("navigation", {})
    require(isinstance(value, dict), "navigation must be an object")
    redirects, consumers = value.get("redirects", {}), value.get("consumers", [])
    local_only = value.get("local_only", [])
    require(isinstance(redirects, dict) and len(redirects) <= MAX_CONSUMERS, "invalid route redirects")
    require(isinstance(consumers, list) and len(consumers) <= MAX_CONSUMERS, "invalid current route consumers")
    require(all(isinstance(p, str) for p in consumers) and len(set(consumers)) == len(consumers), "duplicate/invalid route consumer")
    require(isinstance(local_only, list) and all(isinstance(p, str) for p in local_only), "invalid local-only routes")
    for relative in [*redirects, *redirects.values(), *consumers, *local_only]:
        path(root, relative)
    platform.path_collisions([*redirects, *redirects.values(), *consumers, *local_only])
    for old in redirects:
        seen, current = set(), old
        while current in redirects:
            require(current not in seen, "route redirect cycle")
            seen.add(current)
            current = redirects[current]
    return cfg, redirects, consumers, set(local_only)


def _resolve(root, relative, redirects, local_only):
    path(root, relative)
    chain, current = [relative], relative
    while current in redirects:
        current = redirects[current]
        chain.append(current)
    file = path(root, current)
    return {"requested": relative, "canonical": current, "chain": chain,
            "available": file.is_file(), "local_only": current in local_only,
            "action": "read-current-owner" if file.is_file() else "locate-owner-without-guessing"}


def resolve(root, relative):
    _, redirects, _, local_only = declaration(root)
    return _resolve(root, relative, redirects, local_only)


def _historical(relative):
    return (relative.startswith(("work/", ".system/", ".local/")) or
            any(part in {"history", "evidence", "snapshots"} for part in Path(relative).parts))


def _scalars(value, location=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"history", "evidence", "source_admission", "snapshots", "decisions", "origin", "provenance"}:
                continue  # Retained observations/decisions are not current routing declarations.
            if location == ("workspace",) and key == "navigation":
                continue  # Old registry keys are intentional declarations.
            yield from _scalars(child, (*location, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalars(child, (*location, str(index)))
    elif isinstance(value, str):
        yield ".".join(location), value


def _references(root, relative, file):
    if file.suffix in {".yaml", ".yml", ".json"}:
        yield from _scalars(load(file))
    elif file.suffix == ".md":
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"\[[^\]\n]*\]\(([^)\n]+)\)", line):
                target = match.group(1).strip().strip("<>")
                if urlsplit(target).scheme or target.startswith(("#", "/")):
                    continue
                target = unquote(target.split("#", 1)[0])
                candidate = Path(os.path.abspath(file.parent / target))
                require(candidate.is_relative_to(Path(root).resolve()), "current route escapes company")
                canonical = candidate.relative_to(Path(root).resolve()).as_posix()
                path(root, canonical)
                yield str(number), canonical


def check(root, relatives=None):
    root = Path(root).resolve()
    cfg, redirects, consumers, local_only = declaration(root)
    selected = consumers if relatives is None else relatives
    require(isinstance(selected, list) and len(selected) <= MAX_CONSUMERS, "exact route path list required")
    require(all(isinstance(p, str) for p in selected) and len(selected) == len(set(selected)), "duplicate/invalid route paths")
    stale, issues, observed, skipped = [], [], {}, []
    for relative in selected:
        file = path(root, relative)
        if _historical(relative):
            skipped.append(relative)
            continue
        if not file.is_file():
            issues.append({"path": relative, "reason": "current-consumer-missing"})
            continue
        require(file.stat().st_size <= MAX_DOCUMENT_BYTES, "current route document exceeds read bound")
        observed[relative] = digest(file)
        for location, target in _references(root, relative, file):
            if target in redirects:
                resolved = _resolve(root, target, redirects, local_only)
                stale.append({"consumer": relative, "location": location, **resolved,
                              "action": "repair-proven-current-reference" if resolved["available"] else "leave-dependent-action-pending"})
        require(digest(file) == observed[relative], "route consumer changed during inspection")
    resolutions = [_resolve(root, old, redirects, local_only) for old in redirects]
    for item in resolutions:
        if not item["available"] and not item["local_only"]:
            issues.append({"path": item["canonical"], "reason": "redirect-target-missing"})
    owners = []
    for relative in cfg.get("workspace", {}).get("state_owners", []):
        require(isinstance(relative, str), "state owner path must be a string")
        owners.append(_resolve(root, relative, redirects, local_only))
    return {"redirects": resolutions, "owners": owners, "stale": stale, "issues": issues,
            "observed": observed, "skipped_historical": skipped,
            "boundary": "declared current consumers or exact selected paths; history is never rewritten"}


def validate(root):
    report = check(root)
    require(not report["issues"], "current navigation has unavailable required routes")
    require(not report["stale"], "current navigation still uses a declared obsolete route")
    return True
