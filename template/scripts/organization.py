"""Read-only organization evidence for exact repository paths.

The scanner reports structural facts.  It never chooses a semantic owner, moves
files, or rewrites consumers; those decisions belong to the organization skill.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

import yaml

from core import Rejected, config, digest, ident, object_hash, path, require
import validation
import platform_runtime as platform


MAX_PATHS = 128
MAX_READ_BYTES = 1_048_576
READABLE_SUFFIXES = {".md", ".yaml", ".yml", ".json"}
LINKS = re.compile(r"\]\(([^)]+)\)")


def _relative(root, file):
    return Path(file).relative_to(root).as_posix()


def _safe_file(root, relative, *, required=False):
    """Resolve a regular in-company file without following an alias."""
    try:
        file = path(root, relative)
    except (Rejected, OSError, ValueError):
        if required:
            raise
        return None, "alias"
    if not file.exists():
        return None, "absent"
    require(not required or file.is_file(), "regular file required")
    if not file.is_file():
        return None, "not-file"
    try:
        size = file.stat().st_size
    except OSError:
        if required:
            raise
        return None, "unreadable"
    if size > MAX_READ_BYTES:
        if required:
            raise Rejected("organization input too large")
        return None, "oversize"
    return file, None


def _input_paths(root, values):
    require(isinstance(values, list) and 0 < len(values) <= MAX_PATHS,
            "organization paths must be a non-empty bounded list")
    require(all(isinstance(value, str) for value in values),
            "organization paths must be strings")
    require(len(values) == len(set(values)), "duplicate organization path")
    try:
        platform.path_collisions(values)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc
    result = []
    for relative in values:
        file = path(root, relative)
        require(not file.exists() or file.is_file(), "organization path must be a file")
        result.append(relative)
    return sorted(result)


def load_manifest(root, file, expected):
    """Read one small JSON manifest inside the company without alias traversal."""
    root = Path(root).resolve()
    candidate = Path(file)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise Rejected("manifest outside company") from exc
    safe, reason = _safe_file(root, relative, required=True)
    require(reason is None and safe.suffix == ".json", "JSON manifest required")
    value = json.loads(safe.read_text(encoding="utf-8"))
    require(isinstance(value, expected), "unexpected manifest format")
    return value


def _read(file):
    return file.read_text(encoding="utf-8")


def _walk_values(value, target):
    if isinstance(value, str):
        return value == target
    if isinstance(value, list):
        return any(_walk_values(item, target) for item in value)
    if isinstance(value, dict):
        return any(key == target or _walk_values(item, target)
                   for key, item in value.items())
    return False


def _link_target(root, source, raw):
    if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*://", raw) or raw.startswith("#"):
        return None
    cleaned = raw.strip("<>").split("#", 1)[0]
    if not cleaned:
        return None
    resolved = (source.parent / cleaned).resolve()
    if not resolved.is_relative_to(root):
        return "outside"
    return resolved.relative_to(root).as_posix()


def _entity(root, relative, file):
    kind = validation.entity_kind(relative)
    if kind:
        try:
            meta = validation.frontmatter(file) if file.suffix == ".md" else yaml.safe_load(_read(file))
            entity = meta.get("id", meta.get("name")) if isinstance(meta, dict) else None
            if entity:
                expected = {
                    "skill": f"skills/{entity}/SKILL.md",
                    "skill-recipe": f"skills/{entity}/workflow.yaml",
                    "standard": (f"company/standards/{entity}.md"
                                 if relative.startswith("company/standards/")
                                 else f"standards/{entity}.md"),
                    "source": f"company/sources/{entity}/source.yaml",
                    "workflow": f"workflows/{entity}.yaml",
                }[kind]
                return kind, "canonical" if relative == expected else "wrong-place", expected
        except (OSError, UnicodeError, yaml.YAMLError, AttributeError):
            return kind, "unknown", None
        return kind, "unknown", None
    if file.suffix == ".md":
        return "document", "semantic-required", None
    return "file", "unknown", None


def _required_map(relative, kind):
    if kind == "skill":
        return "skills/README.md"
    if kind == "standard":
        return "standards/README.md" if relative.startswith("standards/") else None
    if kind == "source":
        return "company/sources/README.md"
    return None


def _scan(root, task_id):
    """Return safely decoded repository documents plus explicit coverage gaps."""
    documents, skipped_aliases, skipped_oversize, skipped_unreadable = {}, [], [], []
    own_snapshot = (f"work/{task_id}/inputs/organization-snapshot.json"
                    if task_id else None)
    excluded = {'.git', '.local', '.venv', '.system', '.agents', '.claude',
                '.codex', '__pycache__'}
    def walk_error(exc):
        try:
            failed = Path(exc.filename)
            skipped_unreadable.append(failed.relative_to(root).as_posix())
        except (TypeError, ValueError):
            skipped_unreadable.append("<repository-scan>")
    for directory, dirs, _ in os.walk(root, followlinks=False, onerror=walk_error):
        kept = []
        for name in dirs:
            candidate = Path(directory) / name
            if name in excluded:
                continue
            if platform.alias(candidate):
                skipped_aliases.append(_relative(root, candidate))
            else:
                kept.append(name)
        dirs[:] = kept
    for found in validation.documents(root, "*"):
        if found.suffix.lower() not in READABLE_SUFFIXES:
            continue
        relative = _relative(root, found)
        if relative == own_snapshot:
            continue
        safe, reason = _safe_file(root, relative)
        if reason:
            bucket = {"alias": skipped_aliases, "oversize": skipped_oversize,
                      "unreadable": skipped_unreadable}.get(reason)
            if bucket is not None:
                bucket.append(relative)
            continue
        try:
            text = _read(safe)
            parsed = None
            if safe.suffix in {".yaml", ".yml"}:
                parsed = yaml.safe_load(text)
            elif safe.suffix == ".json":
                parsed = json.loads(text)
            documents[relative] = {"file": safe, "text": text, "parsed": parsed}
        except (OSError, UnicodeError, yaml.YAMLError, json.JSONDecodeError):
            skipped_unreadable.append(relative)
    coverage = {
        "complete": not (skipped_aliases or skipped_oversize or skipped_unreadable),
        "skipped_aliases": sorted(set(skipped_aliases)),
        "skipped_oversize": sorted(set(skipped_oversize)),
        "skipped_unreadable": sorted(set(skipped_unreadable)),
    }
    return documents, coverage


def inspect(root, paths, task_id=None):
    """Inspect exact paths and their repository consumers without writing."""
    root = Path(root).resolve()
    selected = _input_paths(root, paths)
    if task_id is not None:
        ident(task_id)
    company = config(root)["id"]
    documents, coverage = _scan(root, task_id)
    preimage_paths = {"company/config.yaml", *selected}
    items = []
    for relative in selected:
        selected_file, reason = _safe_file(root, relative)
        require(reason != "alias", "organization path uses an alias")
        state = "file" if selected_file else "absent"
        kind, placement, destination = ("file", "unknown", None)
        broken = []
        if selected_file:
            kind, placement, destination = _entity(root, relative, selected_file)
            if selected_file.suffix == ".md":
                for raw in LINKS.findall(_read(selected_file)):
                    target = _link_target(root, selected_file, raw)
                    if target == "outside" or (target is not None and not (root / target).exists()):
                        broken.append({"target": raw, "status": "outside" if target == "outside" else "missing"})
        required_map = _required_map(relative, kind)
        mapped = None
        nearest_map = None
        current = (selected_file or (root / relative)).parent
        while current.is_relative_to(root):
            candidate = current / "README.md"
            candidate_relative = candidate.relative_to(root).as_posix()
            safe_map, _ = _safe_file(root, candidate_relative)
            if safe_map:
                nearest_map = candidate_relative
                break
            if current == root:
                break
            current = current.parent
        if required_map:
            preimage_paths.add(required_map)
            map_document = documents.get(required_map)
            mapped = False
            if map_document:
                for raw in LINKS.findall(map_document["text"]):
                    if _link_target(root, map_document["file"], raw) == relative:
                        mapped = True
                        break

        inbound, structured, immutable = [], [], []
        for source_relative, document in documents.items():
            if source_relative == relative:
                continue
            if document["file"].suffix == ".md":
                matching = [raw for raw in LINKS.findall(document["text"])
                            if _link_target(root, document["file"], raw) == relative]
                if matching:
                    inbound.append({"path": source_relative, "targets": sorted(set(matching))})
                    preimage_paths.add(source_relative)
            if document["parsed"] is not None and _walk_values(document["parsed"], relative):
                structured.append({"path": source_relative})
                preimage_paths.add(source_relative)
                if (source_relative.startswith("work/") and
                        source_relative.endswith("/task.json")):
                    immutable.append({"path": source_relative})
        action = "leave-open" if (placement in {"semantic-required", "wrong-place", "unknown"}
                                  or immutable or not coverage["complete"] or state == "absent") else "inspect-owner"
        items.append({
            "path": relative,
            "state": state,
            "sha256": digest(selected_file) if selected_file else None,
            "kind": kind,
            "placement": placement,
            "canonical_destination": destination,
            "required_map": required_map,
            "mapped": mapped,
            "nearest_map": nearest_map,
            "broken_links": broken,
            "inbound_links": sorted(inbound, key=lambda value: value["path"]),
            "structured_consumers": sorted(structured, key=lambda value: value["path"]),
            "immutable_pins": sorted(immutable, key=lambda value: value["path"]),
            "recommended_action": action,
        })
    preimages = {}
    for relative in sorted(preimage_paths):
        file, reason = _safe_file(root, relative)
        require(reason != "alias", "organization evidence path uses an alias")
        preimages[relative] = digest(file) if file else None
    result = {
        "schema_version": 1,
        "status": "inspection",
        "company_id": company,
        "task_id": task_id,
        "paths": selected,
        "items": items,
        "preimages": preimages,
        "coverage": coverage,
        "read_only": True,
    }
    result["snapshot_sha256"] = object_hash(result)
    return result


def compare(root, snapshot):
    """Fail closed if any selected path, map, or observed consumer drifted."""
    require(isinstance(snapshot, dict) and snapshot.get("schema_version") == 1,
            "organization snapshot format")
    sealed = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    require(snapshot.get("snapshot_sha256") == object_hash(sealed),
            "organization snapshot changed")
    current = inspect(root, snapshot.get("paths"), snapshot.get("task_id"))
    expected_coverage = snapshot.get("coverage")
    require(isinstance(expected_coverage, dict) and expected_coverage.get("complete") is True,
            "organization expected snapshot has incomplete coverage")
    require(current["coverage"] == expected_coverage,
            "organization inspection coverage changed")
    expected = snapshot.get("preimages")
    require(isinstance(expected, dict), "organization snapshot preimages missing")
    differences = sorted(relative for relative in set(expected) | set(current["preimages"])
                         if expected.get(relative) != current["preimages"].get(relative))
    require(not differences, "organization preimage changed: " + ", ".join(differences))
    current["status"] = "unchanged"
    return current
