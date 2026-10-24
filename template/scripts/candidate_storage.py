"""Owned, bounded temporary candidates. Legacy directories are never discovered.

Only reserve sweeps sealed records. Confirmed delivery and storage housekeeping
have separate outcomes; inability to prove disposal always preserves bytes.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid

from core import Rejected, now, require, write
import platform_runtime as platform

MAX_CANDIDATES = 4
_FORMAT = 1
_MARKER = "cws-storage.json"
_MUTEX = threading.RLock()
_LEASES = {}


def _canonical(value):
    value = Path(value).absolute()
    try:
        platform.no_alias_parents(Path(value.anchor), value)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc
    require(value == value.resolve(), "candidate storage requires canonical paths")
    return value


def _node(file):
    value = Path(file).lstat()
    return [value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)]


def _parents(file):
    return [[str(p), _node(p)] for p in Path(file).parents]


def _check_parents(parents):
    for name, identity in parents:
        require(not platform.alias(name) and _node(name) == identity,
                "candidate storage parent identity changed")


def _git(root, *arguments, input=None):
    # Import lazily: delivery's public git may itself be guarded by this module.
    from delivery import git_environment
    environment = git_environment()
    environment.update(GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    value = subprocess.run(["git", "-C", str(root), *arguments], input=input,
                           capture_output=True, env=environment, timeout=60)
    require(value.returncode == 0, "candidate storage Git proof unavailable")
    return value.stdout


def _repository(root):
    root = _canonical(root)
    gitdir = root / ".git"
    require(gitdir.is_dir() and not platform.alias(gitdir),
            "candidate storage requires a standalone repository")
    require(Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()) == root,
            "candidate storage requires the repository root")
    require(Path(_git(root, "rev-parse", "--absolute-git-dir").decode().strip()) == gitdir,
            "candidate storage requires an owned .git directory")
    common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip())
    require(common == gitdir, "shared Git metadata cannot be disposed")
    return {"root": str(root), "gitdir": str(gitdir),
            "identity": [_node(root), _node(gitdir), _node(gitdir / "objects")]}


def _standalone(root):
    location = _repository(root)
    gitdir = root / ".git"
    for directory, names, files in os.walk(gitdir, followlinks=False):
        require(not any(platform.alias(Path(directory) / name) for name in names + files),
                "aliased Git metadata must be retained")
    for relative in ("commondir", "shallow", "info/grafts", "objects/info/alternates"):
        require(not (gitdir / relative).exists(), "shared/incomplete Git history must be retained")
    require(not (gitdir / "worktrees").exists() or not any((gitdir / "worktrees").iterdir()),
            "linked worktrees must be retained")
    worktrees = _git(root, "worktree", "list", "--porcelain").decode().splitlines()
    require([line for line in worktrees if line.startswith("worktree ")] == ["worktree " + str(root)],
            "dependent worktrees must be retained")
    settings = _git(root, "config", "--null", "--list").decode(errors="replace").lower()
    require(not re.search(r"(?:^|\x00)(?:extensions\.partialclone|remote\.[^\x00\n]+\.promisor)\n", settings),
            "partial Git object stores must be retained")
    require(not _git(root, "for-each-ref", "--format=%(refname)", "refs/replace").strip(),
            "replacement Git history must be retained")
    return location


def _registry(source, create=False):
    source = _canonical(source)
    base = _canonical(source / ".local/cws-candidates")
    if create:
        base.mkdir(parents=True, exist_ok=True)
        for part in ("records", "locks"):
            _canonical(base / part).mkdir(exist_ok=True)
    return base


class _Guard:
    def __init__(self, file, stream):
        self.file, self.stream = file, stream
        self.parents = _parents(file)
        self.identity = _node(file)
        self.check()

    def check(self):
        _check_parents(self.parents)
        metadata = self.file.lstat()
        opened = os.fstat(self.stream.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                [metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)] == self.identity and
                (metadata.st_dev, metadata.st_ino) == (opened.st_dev, opened.st_ino),
                "candidate storage lock replaced; preserve all data")
        return True

    __call__ = check


class _UnmanagedGuard:
    def check(self):
        return True
    __call__ = check


@contextmanager
def _lease(file):
    file = _canonical(file)
    key, owner = str(file), threading.get_ident()
    with _MUTEX:
        entry = _LEASES.get(key)
        if entry:
            require(entry[0] == owner, "candidate operation already running")
            entry[1] += 1
            guard = entry[2]
        else:
            context = platform.process_lock(file)
            try:
                stream = context.__enter__()
            except BlockingIOError as exc:
                raise Rejected("candidate operation already running") from exc
            try:
                guard = _Guard(file, stream)
            except BaseException:
                context.__exit__(*sys.exc_info())
                raise
            _LEASES[key] = [owner, 1, guard, context]
    try:
        guard.check()
        yield guard
        guard.check()
    finally:
        with _MUTEX:
            entry = _LEASES[key]
            entry[1] -= 1
            if entry[1] == 0:
                del _LEASES[key]
                # process_lock unlocks only its fd, never unlinks a replaced path.
                entry[3].__exit__(None, None, None)


def _read(file):
    require(file.is_file() and not platform.alias(file), "candidate storage record is not owned")
    value = json.loads(file.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and value.get("format") == _FORMAT,
            "candidate storage record format changed")
    return value


def _record_path(base, token):
    require(isinstance(token, str) and re.fullmatch(r"[0-9a-f]{32}", token), "invalid candidate storage ID")
    return _canonical(base / "records" / (token + ".json"))


def _records(base):
    if not base.exists():
        return []
    require(_canonical(base / "records").is_dir(), "candidate registry records missing")
    result = []
    for file in sorted((base / "records").iterdir()):
        require(re.fullmatch(r"[0-9a-f]{32}\.json", file.name), "unknown candidate registry entry")
        record = _read(file)
        require(record.get("id") == file.stem and record.get("source") == str(base.parent.parent),
                "candidate registry ownership changed")
        result.append(record)
    return result


def _save(base, record, guard):
    guard.check()
    record["updated_at"] = now()
    write(_record_path(base, record["id"]), record)
    guard.check()


def _managed(root):
    root = _canonical(root)
    marker = root / ".git" / _MARKER
    if not marker.exists() and not marker.is_symlink():
        return None
    marker = _canonical(marker)
    pointer = _read(marker)
    source = _canonical(pointer["source"])
    base = _registry(source)
    record = _read(_record_path(base, pointer.get("id")))
    require(pointer == _pointer(record) and record["candidate"] == str(root),
            "candidate storage marker ownership changed")
    return base, record


def _pointer(record):
    return {"format": _FORMAT, "id": record["id"], "source": record["source"],
            "candidate": record["candidate"], "source_identity": record["source_identity"],
            "candidate_identity": record["candidate_identity"]}


@contextmanager
def operation(root):
    """Yield a callable .check() guard; unmanaged roots keep their old behavior."""
    managed = _managed(root)
    if managed is None:
        yield _UnmanagedGuard()
        return
    base, record = managed
    with _lease(base / "locks" / (record["id"] + ".lock")) as guard:
        current = _managed(root)
        require(current and current[1]["id"] == record["id"], "candidate storage identity changed")
        require(current[1]["state"] not in {"closed", "retiring"}, "candidate is being retired")
        require(_repository(Path(root)) == record["candidate_identity"], "candidate repository identity changed")
        guard.check()
        yield guard


def guarded(function):
    """Keep a managed candidate leased for the entire public operation."""
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        with operation(root) as guard:
            guard.check()
            result = function(root, *args, **kwargs)
            guard.check()
            return result
    return wrapped


def _native(root):
    gitdir = root / ".git"
    settings = _git(root, "config", "--null", "--list", "--show-origin")
    hookdir = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks").decode().strip())
    hookdir = _canonical(hookdir)
    if hookdir.exists():
        for directory, names, files in os.walk(hookdir, followlinks=False):
            require(not any(platform.alias(Path(directory) / name) for name in names + files),
                    "aliased native hooks must be retained")
    data = {"settings": hashlib.sha256(settings).hexdigest(), "hookdir": str(hookdir), "files": {}}
    for file in [gitdir / "config", gitdir / "config.worktree"]:
        if file.exists():
            require(file.is_file() and not platform.alias(file), "aliased native config must be retained")
            data["files"][str(file)] = [stat.S_IMODE(file.stat().st_mode), hashlib.sha256(file.read_bytes()).hexdigest()]
    if hookdir.exists():
        data["hooks"] = _fingerprint(hookdir, caches=False)
    return data


def _cache(relative):
    parts = Path(relative).parts
    return (relative == ".system/cache" or relative.startswith(".system/cache/") or
            "__pycache__" in parts[:-1] and parts[-1].endswith(".pyc"))


def _fingerprint(root, caches=True):
    """Compact hash of names, metadata and content; never store private manifests."""
    digest = hashlib.sha256()
    count = size = 0
    def visit(directory):
        nonlocal count, size
        for file in sorted(directory.iterdir(), key=lambda p: p.name):
            relative = file.relative_to(root).as_posix()
            if caches and _cache(relative):
                continue
            value = file.lstat()
            require(stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode),
                    "special candidate file must be retained")
            # atime is changed by reads and is intentionally not an ownership proof.
            metadata = [relative, value.st_mode, value.st_size, value.st_mtime_ns,
                        value.st_ctime_ns, value.st_dev, value.st_ino, value.st_nlink,
                        getattr(value, "st_uid", None), getattr(value, "st_gid", None)]
            digest.update(json.dumps(metadata, ensure_ascii=True).encode() + b"\0")
            count += 1
            if stat.S_ISREG(value.st_mode):
                with file.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                require(_node(file) == [value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)] and
                        file.lstat().st_mtime_ns == value.st_mtime_ns and file.lstat().st_size == value.st_size and
                        file.lstat().st_ctime_ns == value.st_ctime_ns and file.lstat().st_mode == value.st_mode,
                        "candidate changed while fingerprinting")
            elif stat.S_ISLNK(value.st_mode):
                digest.update(os.fsencode(os.readlink(file)))
            else:
                visit(file)
    visit(root)
    return {"sha256": digest.hexdigest(), "files": count, "bytes": size}


def _known_files(root):
    require(not _git(root, "diff", "--name-only").strip() and
            not _git(root, "diff", "--cached", "--name-only").strip(),
            "tracked candidate changes must be retained")
    for arguments in (("ls-files", "--others", "--exclude-standard", "-z"),
                      ("ls-files", "--others", "--ignored", "--exclude-standard", "-z")):
        for raw in _git(root, *arguments).split(b"\0"):
            if not raw:
                continue
            relative = os.fsdecode(raw)
            file = root / relative
            if relative == ".system/lock":
                require(file.is_file() and not platform.alias(file) and file.stat().st_size == 0,
                        "changed CWS lock must be retained")
            else:
                require(_cache(relative) and not platform.alias(file),
                        "unknown untracked/ignored candidate data must be retained")
    # Empty private directories are data too; Git's file inventory omits them.
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        if current == root:
            names[:] = [name for name in names if name != ".git"]
        if current == root or names or files:
            continue
        relative = current.relative_to(root).as_posix()
        tracked = _git(root, "ls-files", "--", relative).strip()
        require(tracked or _cache(relative) or current.name == "__pycache__",
                "unknown empty candidate directory must be retained")


def _locks_clear(root):
    for directory, names, files in os.walk(root / ".git", followlinks=False):
        require(not any(name.endswith(".lock") for name in files + names),
                "Git lock present; candidate must be retained")
    # These are persistent process-lock files, not Git lock sentinels. Probe and
    # close them before rename so no Windows handle is held inside the tree.
    for file in (root / ".system/lock", root / ".git/cws-finish-lock/.system/lock"):
        if file.exists():
            require(file.is_file() and not platform.alias(file) and file.stat().st_size == 0,
                    "unknown native lock must be retained")
            with _lease(file) as guard:
                guard.check()


def _source_proof(record, candidate):
    source = _canonical(record["source"])
    require(_standalone(source) == record["source_identity"], "source repository identity changed")
    target = record["target"]
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", target), "storage target missing")
    _git(source, "merge-base", "--is-ancestor", target, "HEAD")
    _git(source, "merge-base", "--is-ancestor", record["source_head"], target)
    require(_git(candidate, "rev-parse", "HEAD").decode().strip() == target,
            "candidate target changed")
    # Every object, including abandoned blobs/trees/commits, needs an enduring
    # reference in the source; mere object existence would not survive source GC.
    inventory = set(_git(candidate, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)").splitlines())
    reachable = {line.split(b" ", 1)[0] for line in
                 _git(source, "rev-list", "--objects", "--all", "--reflog", target).splitlines()}
    require(inventory <= reachable, "candidate contains Git objects not preserved by source history")


def _safe_candidate(base, record, candidate, *, renamed=False):
    candidate = _canonical(candidate)
    _check_parents(record["parents"])
    expected = dict(record["candidate_identity"])
    if renamed:
        expected["root"] = str(candidate)
        expected["gitdir"] = str(candidate / ".git")
    require(_standalone(candidate) == expected, "candidate repository identity changed")
    marker = _read(candidate / ".git" / _MARKER)
    require(marker == _pointer(record), "candidate storage marker changed")
    require(not record.get("keep"), "candidate explicitly pinned")
    _locks_clear(candidate)
    _known_files(candidate)
    if not renamed:
        require(_native(candidate) == record["native"], "native config/hooks changed since preparation")
    _source_proof(record, candidate)
    if record.get("fingerprint"):
        require(_fingerprint(candidate) == record["fingerprint"], "candidate bytes/metadata changed since sealing")
    return candidate


def _not_running(candidate):
    paths = [Path.cwd(), Path(__file__).absolute()]
    for value in (getattr(sys.modules.get("__main__"), "__file__", None), sys.argv[0] if sys.argv else None):
        if value and value not in {"-", "-c"}:
            paths.append(Path(value).absolute())
    require(not any(path.resolve().is_relative_to(candidate) for path in paths),
            "candidate contains the current directory or running script")


def _rename(source, destination):
    """Atomic no-replace rename, or retain on unsupported native semantics."""
    require(not destination.exists() and not destination.is_symlink(), "quarantine path already exists")
    if os.name == "nt":
        os.rename(source, destination)  # Windows refuses replacement.
    elif sys.platform.startswith("linux"):
        native = ctypes.CDLL(None, use_errno=True)
        rename = getattr(native, "renameat2", None)
        require(rename is not None, "atomic no-replace rename unavailable; retain candidate")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
    else:
        raise Rejected("atomic no-replace rename unsupported; retain candidate")


def _sweep_one(base, record, registry_guard):
    try:
        with _lease(base / "locks" / (record["id"] + ".lock")) as guard:
            registry_guard.check(); guard.check()
            original = Path(record["candidate"])
            if record["state"] == "sealed":
                candidate = _safe_candidate(base, record, original)
                _not_running(candidate)
                quarantine = candidate.with_name(".cws-quarantine-" + record["id"] + "-" + uuid.uuid4().hex)
                record.update(state="retiring", phase="rename-planned", quarantine=str(quarantine))
                _save(base, record, registry_guard)
                registry_guard.check(); guard.check()
                _safe_candidate(base, record, candidate)
                _not_running(candidate)
                _rename(candidate, quarantine)
                record["phase"] = "quarantined"
                _save(base, record, registry_guard)
            else:
                quarantine = _canonical(record["quarantine"])
                require(quarantine.parent == original.parent and
                        quarantine.name.startswith(".cws-quarantine-" + record["id"] + "-"),
                        "quarantine ownership changed")
                if record.get("phase") == "rename-planned" and original.exists() and not quarantine.exists():
                    # The rename did not happen. Re-run the complete proof next time.
                    record.update(state="sealed", phase="sealed")
                    record.pop("quarantine", None)
                    _save(base, record, registry_guard)
                    return
                if record.get("phase") == "removing" and not original.exists() and not quarantine.exists():
                    record.update(state="closed", phase="closed", reason="owned quarantine removed")
                    _save(base, record, registry_guard)
                    return
                require(not original.exists(), "original candidate reappeared; preserve quarantine")
            _not_running(quarantine)
            _safe_candidate(base, record, quarantine, renamed=True)
            registry_guard.check(); guard.check()
            record["phase"] = "removing"
            _save(base, record, registry_guard)
            # The second full proof is immediately before destructive traversal.
            _safe_candidate(base, record, quarantine, renamed=True)
            registry_guard.check(); guard.check()
            shutil.rmtree(quarantine)
            registry_guard.check(); guard.check()
            record.update(state="closed", phase="closed", reason="owned quarantine removed")
            _save(base, record, registry_guard)
    except Exception as exc:
        # Busy records remain retryable; unknown drift/partial deletion is retained.
        if "already running" in str(exc):
            return
        registry_guard.check()
        retryable = isinstance(exc, OSError) and record.get("quarantine") and record.get("phase") in {"rename-planned", "quarantined", "removing"}
        record.update(state="retiring" if retryable else "retained", reason=str(exc))
        _save(base, record, registry_guard)


def reserve(source, destination, *, kind="prepare", keep=False):
    source, destination = _canonical(source), _canonical(destination)
    require(kind in {"prepare", "update", "rollback"}, "unknown candidate kind")
    require(not destination.exists() and not destination.is_symlink(), "candidate destination already exists")
    require(source != destination and not source.is_relative_to(destination), "candidate cannot contain its source")
    identity = _standalone(source)
    base = _registry(source, create=True)
    require(not destination.is_relative_to(base) and not destination.is_relative_to(source / ".git"),
            "candidate destination overlaps storage metadata")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = _canonical(destination)
    with _lease(base / "registry.lock") as guard:
        records = _records(base)
        require(not any(item["candidate"] == str(destination) and item["state"] != "closed" for item in records),
                "candidate path already reserved")
        for item in records:
            if item["state"] in {"sealed", "retiring"}:
                _sweep_one(base, item, guard)
        records = _records(base)
        active = [item for item in records if item["state"] != "closed"]
        require(len(active) < MAX_CANDIDATES, "candidate limit reached; preserve/reconcile existing candidates: " +
                ", ".join(item.get("quarantine", item["candidate"]) for item in active))
        require(_standalone(source) == identity, "source identity changed while reserving")
        require(not destination.exists() and not destination.is_symlink(), "candidate destination already exists")
        record = {"format": _FORMAT, "id": uuid.uuid4().hex, "source": str(source),
                  "candidate": str(destination), "kind": kind, "keep": bool(keep),
                  "state": "reserved", "phase": "reserved", "reason": "preparation not registered",
                  "source_identity": identity, "source_head": _git(source, "rev-parse", "HEAD").decode().strip(),
                  "parents": _parents(destination), "created_at": now()}
        _save(base, record, guard)
        return record


def register(source, destination, reservation):
    source, destination = _canonical(source), _canonical(destination)
    base = _registry(source)
    token = reservation["id"] if isinstance(reservation, dict) else reservation
    _record_path(base, token)  # Validate before deriving the operation lock path.
    with _lease(base / "locks" / (token + ".lock")) as operation_guard:
        with _lease(base / "registry.lock") as guard:
            record = _read(_record_path(base, token))
            require(record["source"] == str(source) and record["candidate"] == str(destination) and
                    record["state"] == "reserved", "candidate reservation changed")
            _check_parents(record["parents"])
            require(_standalone(source) == record["source_identity"], "source repository identity changed")
            record["candidate_identity"] = _standalone(destination)
            record["native"] = _native(destination)
            marker = destination / ".git" / _MARKER
            require(not marker.exists() and not marker.is_symlink(), "candidate marker already exists")
            operation_guard.check(); guard.check()
            # Save the identity before publishing the pointer, keeping interrupted
            # registration visible and counted even if pointer creation fails.
            _save(base, record, guard)
            write(marker, _pointer(record))
            operation_guard.check(); guard.check()
            record["prepared_footprint"] = _fingerprint(destination)
            record.update(state="prepared", phase="prepared", reason="not sealed")
            _save(base, record, guard)
            return record


def seal(candidate, result):
    """Return storage fields without invalidating an already confirmed delivery."""
    record = base = None
    try:
        managed = _managed(candidate)
        if managed is None:
            return {"storage_status": "unmanaged", "storage_reason": "legacy candidate has no ownership marker"}
        base, record = managed
        with operation(candidate) as operation_guard:
            with _lease(base / "registry.lock") as guard:
                record = _read(_record_path(base, record["id"]))
                require(result.get("workspace_reconciled") is True and result.get("source") == record["source"],
                        "source completion not confirmed")
                receipt = result.get("receipt", result)
                require(receipt.get("status") == "readback-confirmed", "confirmed delivery receipt missing")
                target = result.get("local_head")
                require(isinstance(target, str), "completed target missing")
                record["target"] = target
                record["receipt"] = {key: receipt[key] for key in
                    ("status", "sha256", "remote_head", "acknowledgment_commit", "newer_remote", "repeated") if key in receipt}
                require(receipt.get("acknowledgment_commit", receipt.get("sha256")) == target,
                        "receipt does not retain the completed target")
                # Repeated finish is valid until the next reserve; recapture only
                # after proving unchanged prior sealed contents, never bless drift.
                _safe_candidate(base, record, Path(candidate))
                operation_guard.check(); guard.check()
                record["fingerprint"] = _fingerprint(Path(candidate))
                record.update(state="sealed", phase="sealed", reason="completed; eligible on next preparation", sealed_at=now())
                _save(base, record, guard)
                return {"storage_status": "sealed", "storage_id": record["id"], "storage_reason": record["reason"]}
    except Exception as exc:
        if base is not None and record is not None:
            try:
                with operation(candidate) as operation_guard:
                    with _lease(base / "registry.lock") as guard:
                        current = _read(_record_path(base, record["id"]))
                        # Preserve compact completion evidence even when disposal
                        # is unsafe; never mutate a busy candidate's record.
                        for key in ("target", "receipt"):
                            if key in record:
                                current[key] = record[key]
                        current.update(state="retained", reason=str(exc))
                        operation_guard.check()
                        _save(base, current, guard)
            except Exception:
                pass
        return {"storage_status": "retained", "storage_reason": str(exc),
                **({"storage_id": record["id"]} if record is not None else {})}


def status(source):
    """Read only registered attempts. Never sweep or inspect legacy candidates."""
    source = _canonical(source)
    base = _registry(source)
    records = _records(base)
    return {"status": "ok", "source": str(source), "max_candidates": MAX_CANDIDATES,
            "active_candidates": sum(item["state"] != "closed" for item in records), "candidates": records}
