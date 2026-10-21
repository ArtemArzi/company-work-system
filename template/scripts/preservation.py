"""Observed saving status and explicit local snapshots of selected regular files."""
from pathlib import Path
import hashlib
import os
import re
import stat

from core import Rejected, digest, load, now, path, require, task_file, write
import platform_runtime as platform

MAX_FILES = 128
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
DOCUMENT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".txt"}
PRIVATE_NAMES = {"auth.json", "credentials.json", "cookies.json", "storage-state.json", "session.json"}


def _owned_head(root):
    """Never discover a parent repository for a company without its own Git."""
    import delivery
    try:
        resolver = getattr(delivery, "git_source", None)
        declared = resolver(root) if resolver else None
        marker = root / ".git"
        if declared is None and (platform.alias(marker) or not (marker.is_dir() or marker.is_file())):
            return None
        top = delivery.git(root, "rev-parse", "--show-toplevel", check=False)
        if top.returncode or Path(top.stdout.strip()).resolve() != root:
            return None
        checked = delivery.git(root, "rev-parse", "--verify", "HEAD", check=False)
        return checked.stdout.strip() if checked.returncode == 0 else None
    except (Rejected, OSError):
        return None


def inspect(root, relatives, task_id=None):
    """Report current bytes and recorded delivery separately; never fetch or write."""
    from delivery import git
    root = Path(root).resolve()
    require(isinstance(relatives, list) and 0 < len(relatives) <= MAX_FILES, "exact preservation path list required")
    require(all(isinstance(p, str) for p in relatives) and len(set(relatives)) == len(relatives), "duplicate/invalid preservation paths")
    task = load(task_file(root, task_id)) if task_id else None
    head = _owned_head(root)
    files = []
    for relative in relatives:
        file = path(root, relative)
        local_hash = digest(file) if file.is_file() else None
        item = {"path": relative, "local": {"present": local_hash is not None, "sha256": local_hash},
                "git": {"head": head, "exact_bytes_in_head": None},
                "delivery": {"status": "unknown"}, "backup": "coverage-not-checked"}
        if head:
            blob = git(root, "show", f"{head}:{relative}", check=False, binary=True)
            item["git"]["exact_bytes_in_head"] = bool(local_hash and blob.returncode == 0 and hashlib.sha256(blob.stdout).hexdigest() == local_hash)
        if task and (task.get("output") or {}).get("path") == relative:
            recorded = task.get("delivery", {"status": "local"})
            exact_output = local_hash == task["output"].get("sha256")
            exact_receipt = (recorded.get("status") == "delivered" and recorded.get("readback") is True and
                             recorded.get("result_sha256") == local_hash and exact_output)
            if exact_receipt:
                item["delivery"] = {"status": "recorded-readback", "commit": recorded.get("commit"),
                                    "fresh_remote_check": False}
            else:
                status = "recorded-" + recorded.get("status", "unknown")
                if recorded.get("status") == "delivered":
                    status = "recorded-delivery-incomplete"
                item["delivery"] = {"status": status if exact_output else "current-bytes-not-acknowledged",
                                    "fresh_remote_check": False}
        files.append(item)
    return {"files": files, "boundary": "local bytes, Git HEAD and recorded receipt are separate; off-device backup and remote freshness are not inferred"}


def _selection(root, relatives):
    require(isinstance(relatives, list) and 0 < len(relatives) <= MAX_FILES, "bounded exact snapshot file list required")
    require(all(isinstance(p, str) for p in relatives) and len(set(relatives)) == len(relatives), "duplicate/invalid snapshot paths")
    for relative in relatives:
        path(root, relative)
    platform.path_collisions(relatives)
    total, rows = 0, []
    for relative in relatives:
        file = path(root, relative)
        require(len(Path(relative).parts) <= 24 and file.suffix.lower() in DOCUMENT_SUFFIXES, "snapshot accepts selected document files only")
        require(not any(part.startswith(".") or part in {"node_modules", "__pycache__"} for part in Path(relative).parts), "snapshot private/runtime path excluded")
        require(file.name.lower() not in PRIVATE_NAMES and not re.search(r"(?:credential|token|password|secret|id_rsa|id_ed25519)", file.name, re.I), "snapshot credential filename excluded")
        metadata = file.lstat()
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1, "snapshot needs non-aliased regular files")
        require(metadata.st_size <= MAX_FILE_BYTES, "snapshot file exceeds bound")
        total += metadata.st_size
        require(total <= MAX_TOTAL_BYTES, "snapshot total exceeds bound")
        rows.append((relative, file, metadata))
    return rows


def _new_destination(root, destination):
    destination = Path(os.path.abspath(destination))
    platform.no_alias_parents(Path(destination.anchor), destination)
    require(not destination.exists() and not destination.is_symlink(), "snapshot destination must be new; no overwrite")
    require(not destination.resolve().is_relative_to(Path(root).resolve()), "snapshot destination must be outside source root")
    require(destination.parent.is_dir(), "snapshot destination parent must already exist")
    return destination


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_nlink


def _copy(source, target, initial, expected_hash=None):
    """Hash the bytes copied; reject source replacement and edits during copying."""
    require(_identity(source.lstat()) == _identity(initial), "snapshot source changed before copy")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    platform.no_alias_parents(Path(target.anchor), target)
    measured, count = hashlib.sha256(), 0
    with source.open("rb") as incoming:
        require(_identity(os.fstat(incoming.fileno())) == _identity(initial), "snapshot source identity changed")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as outgoing:
            while chunk := incoming.read(64 * 1024):
                count += len(chunk)
                require(count <= MAX_FILE_BYTES and count <= initial.st_size, "snapshot source grew during copy")
                measured.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        require(_identity(os.fstat(incoming.fileno())) == _identity(initial), "snapshot source edited during copy")
    require(_identity(source.lstat()) == _identity(initial) and count == initial.st_size, "snapshot source replaced during copy")
    sha = measured.hexdigest()
    require(expected_hash is None or sha == expected_hash, "snapshot payload changed during restore")
    platform.no_alias_parents(Path(target.anchor), target)
    require(digest(target) == sha, "snapshot copied bytes differ")
    return {"sha256": sha, "size": count}


def snapshot(root, relatives, destination):
    """New local copy of explicit documents; unchanged tracked Git backup is separate."""
    root = Path(root).resolve()
    rows = _selection(root, relatives)
    destination = _new_destination(root, destination)
    destination.mkdir(mode=0o700)
    (destination / "files").mkdir(mode=0o700)
    files = {}
    for relative, source, initial in rows:
        files[relative] = _copy(source, path(destination / "files", relative), initial)
    manifest = {"schema_version": 1, "kind": "selected-files-snapshot", "at": now(), "files": files,
                "boundary": "explicit selected documents, local copy only; no Git history, raw/runtime files or off-device guarantee"}
    write(destination / "manifest.json", manifest)
    return {"path": str(destination), "files": files, "manifest_sha256": digest(destination / "manifest.json"), "boundary": manifest["boundary"]}


def restore(snapshot_path, destination):
    snapshot_path = Path(os.path.abspath(snapshot_path))
    platform.no_alias_parents(Path(snapshot_path.anchor), snapshot_path)
    manifest_file = path(snapshot_path, "manifest.json")
    require(manifest_file.is_file() and manifest_file.stat().st_size <= 1024 * 1024, "snapshot manifest missing/oversize")
    manifest_hash = digest(manifest_file)
    manifest = load(manifest_file)
    require(isinstance(manifest, dict) and manifest.get("schema_version") == 1 and manifest.get("kind") == "selected-files-snapshot", "unsupported selected snapshot")
    files = manifest.get("files")
    require(isinstance(files, dict), "snapshot file manifest missing")
    payload = path(snapshot_path, "files")
    require(payload.is_dir(), "snapshot payload missing")
    rows = _selection(payload, list(files))
    require({p.name for p in snapshot_path.iterdir()} == {"files", "manifest.json"}, "unexpected snapshot root payload")
    expected_dirs, actual_dirs, actual_files = set(), set(), set()
    for relative in files:
        for parent in Path(relative).parents:
            if str(parent) != ".":
                expected_dirs.add(parent.as_posix())
    entries = 0
    for current, directories, names in os.walk(payload, followlinks=False):
        for name in [*directories, *names]:
            entries += 1
            require(entries <= MAX_FILES * 25, "snapshot inventory exceeds bound")
            relative = (Path(current) / name).relative_to(payload).as_posix()
            file = path(payload, relative)
            require(not platform.alias(file), "snapshot aliases rejected")
            if name in directories:
                actual_dirs.add(relative)
            else:
                actual_files.add(relative)
    require(actual_files == set(files) and actual_dirs == expected_dirs, "snapshot payload inventory differs")
    for relative, source, initial in rows:
        record = files[relative]
        require(isinstance(record, dict) and type(record.get("size")) is int and record["size"] == initial.st_size, "snapshot size mismatch")
        require(isinstance(record.get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", record["sha256"]), "invalid snapshot hash")
        require(digest(source) == record["sha256"], "snapshot payload hash mismatch")
    require(digest(manifest_file) == manifest_hash, "snapshot manifest changed during verification")
    destination = _new_destination(snapshot_path, destination)
    destination.mkdir(mode=0o700)
    for relative, source, initial in rows:
        _copy(source, path(destination, relative), initial, files[relative]["sha256"])
    require(digest(manifest_file) == manifest_hash, "snapshot manifest changed during restore")
    return {"path": str(destination), "files": files, "verified": True, "boundary": "selected document bytes restored locally; no Git history or provider state"}
