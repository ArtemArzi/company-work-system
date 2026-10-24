"""The single Git delivery path. Updates and proposals prepare different candidates."""
from __future__ import annotations
from pathlib import Path
import os
import json
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from core import Rejected, config, digest, ident, load, lock, object_hash, path, require, write
import validation
import candidate_storage
from platform_runtime import atomic_text


def diagnostic(stage, error="", *, code=None, timed_out=False):
    """Classify evidence, never include subprocess output or credentials."""
    text = error.decode(errors="replace") if isinstance(error, bytes) else str(error)
    text = text.lower()
    if code is None:
        if timed_out:
            code = "timeout"
        elif any(s in text for s in ("could not resolve host", "could not resolve hostname", "name or service not known")):
            code = "network_dns"
        elif any(s in text for s in ("failed to connect", "connection refused", "network is unreachable", "connection timed out")):
            code = "network_connection"
        elif any(s in text for s in ("authentication failed", "could not read username", "terminal prompts disabled", "permission denied (publickey)")):
            code = "authentication"
        elif any(s in text for s in ("http 403", "error: 403", "write access to repository not granted")):
            code = "authorization"
        elif ".lock" in text and ("file exists" in text or "another git process" in text):
            code = "git_locked"
        elif (".git" in text or "index.lock" in text) and ("permission denied" in text or "read-only file system" in text):
            code = "git_metadata_write"
        else:
            code = "unknown"
    actions = {
        "network_dns": "Preserve the candidate; verify DNS/network in this execution scope, then retry with authorized network access.",
        "network_connection": "Preserve the candidate; restore the connection before retrying.",
        "authentication": "Use the approved Git identity and credential mechanism; never put credentials in commands or files.",
        "authorization": "Verify repository and branch permission with the owner; preserve the candidate.",
        "git_locked": "Preserve existing Git locks; identify their owner before resuming the same candidate.",
        "git_metadata_write": "Verify write permission for this repository's Git metadata in the current execution scope.",
        "timeout": "Outcome may be unknown; inspect the remote before repeating an external write.",
        "local_pending": "Preserve local edits and the delivered candidate; reconcile the original checkout before claiming complete saving.",
        "source_changed": "Preserve both versions; reconcile the captured source branch, index and candidate before resuming.",
        "candidate_not_ignored": "Choose the default ignored candidate directory or an explicit directory outside this checkout.",
        "source_witness_missing": "Preserve this candidate; use a witnessed prepare candidate for automatic local completion.",
        "wrong_identity": "Check the accepted company, repository and branch; do not retarget this candidate.",
        "work_route_required": "Use the installed Work system.py sync route; ordinary checkout completion cannot replace its owned Git route.",
        "remote_diverged": "Preserve both histories and prepare a separately checked reconciliation candidate.",
        "unknown": "Preserve the candidate; inspect the failed stage in the authorized environment before retrying.",
    }
    result = {"stage": stage, "reason_code": code, "next_action": actions.get(code, actions["unknown"])}
    if stage == "readback":
        result["next_action"] = "Delivery is not confirmed; read the remote before repeating the push. " + result["next_action"]
    return result


class GitFailure(Rejected):
    def __init__(self, stage, error="", *, timed_out=False, code=None):
        self.diagnostic = diagnostic(stage, error, timed_out=timed_out, code=code)
        suffix = " (concurrent Git operation; preserve existing locks)" if self.diagnostic["reason_code"] == "git_locked" else ""
        super().__init__("Git operation failed at " + stage + ": " + self.diagnostic["reason_code"] + suffix)


def metadata_error(stage, error):
    import errno
    denied = isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    return GitFailure(stage, code="git_metadata_write" if denied else "unknown")


def write_delivery_record(file, value):
    """Keep confirmed delivery available when private Git metadata is read-only."""
    try:
        file = Path(file)
        expected = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if file.suffix == '.json' and file.is_file() and not file.is_symlink() and file.read_bytes() == expected:
            return
        write(file, value)
    except OSError as exc:
        raise metadata_error("delivery_record", exc) from None


def scope_root(root):
    root = Path(root)
    return root / "template" if not (root / "company/config.yaml").exists() and (root / "template/company/config.yaml").exists() else root


def git_environment():
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    # Never inherit a caller's index/repository/config redirection. Auth remains.
    selectors = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM"}
    for key in list(env):
        if key in selectors or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            env.pop(key)
    return env


@candidate_storage.guarded
def git(root, *arguments, check=True, timeout=None, noninteractive=False, binary=False, private_index=None):
    command = ["git", "-C", str(root), *arguments]
    environment = git_environment()
    if private_index is not None:
        private_index = Path(private_index)
        require(private_index.is_absolute() and private_index.parent.name.startswith("company-export-index-") and private_index.name == "index" and not private_index.is_symlink(), "owned temporary export index required")
        environment["GIT_INDEX_FILE"] = str(private_index)
    stage = next((a for a in arguments if a in {"clone", "fetch", "push", "commit", "merge", "read-tree", "update-ref"}), "git")
    if stage in {"clone", "fetch", "push"}:
        timeout = (120 if stage == "clone" else 30) if timeout is None else timeout
        noninteractive = True
    if noninteractive:
        environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        value = subprocess.run(command, capture_output=True, text=not binary, encoding=None if binary else "utf-8", errors=None if binary else "replace", timeout=timeout, env=environment)
    except subprocess.TimeoutExpired:
        if not check:
            raise
        raise GitFailure(stage, timed_out=True) from None
    if check:
        if value.returncode:
            raise GitFailure(stage, value.stderr)
    return (value.stdout if binary else value.stdout.strip()) if check else value


def identity(root):
    # Only newly created repositories; no global identity/config changes.
    git(root, "config", "user.name", "Company Work System")
    git(root, "config", "user.email", "local-system@example.invalid")


def clean(root):
    require(not git(root, "status", "--porcelain", "--untracked-files=all"), "dirty candidate; preserve local work and prepare separately")


def commit_path(root, relative):
    """Data paths stay strict; only declared skill projection metadata may be staged."""
    try:
        return path(root, relative)
    except Rejected:
        root = Path(root).resolve()
        base = scope_root(root)
        prefix = 'template/' if base != root else ''
        match = re.fullmatch(re.escape(prefix)+r'\.(?:agents|claude)/skills/([a-z][a-z0-9-]{1,63})', relative)
        require(match is not None, 'symlink source is not canonical')
        file = root / relative
        require(file.is_symlink() and file.readlink().as_posix() == '../../skills/'+match[1], 'projection target is not canonical')
        require(not any(p.is_symlink() for p in file.parents if p != root), 'projection parent is not canonical')
        require(file.resolve() == base / 'skills' / match[1] and path(base, 'skills/'+match[1]+'/SKILL.md').is_file(), 'projection source missing')
        return file


def remote_allowed(root, remote):
    import urllib.parse
    cfg = config(scope_root(root))
    require(remote and "\n" not in remote and not remote.startswith("-"), "invalid remote")
    parsed = urllib.parse.urlparse(remote)
    require(not parsed.password and not (parsed.scheme in {"http", "https"} and parsed.username), "credential-bearing remote prohibited")
    local = Path(remote).is_absolute() and Path(remote).exists()
    if not local:
        require(cfg["permissions"]["external_delivery"] and remote in cfg["permissions"]["remotes"], "external remote not authorized")
    return local


def prepare_witness(root, remote, branch):
    """Capture the unchanged source contract before creating a disposable copy."""
    import history
    root = Path(root).resolve()
    if git(root, "rev-parse", "--show-toplevel", check=False).returncode:
        return None
    location = history.repository_location(root)
    index = Path(location["gitdir"]) / "index"
    return {"source": str(root), "location": object_hash(location), "head": git(root, "rev-parse", "HEAD"),
            "head_bytes": (Path(location["gitdir"]) / "HEAD").read_bytes().hex(),
            "index": digest(index) if index.is_file() else None,
            "branch": git(root, "symbolic-ref", "--quiet", "HEAD", check=False).stdout.strip(),
            "company": config(scope_root(root))["id"], "remote": remote, "remote_branch": branch,
            "native": native_fingerprint(root)}


def record_prepare(root, destination, witness, reservation):
    import history
    base = git(destination, "rev-parse", "HEAD")
    if witness:
        witness.update(candidate=str(destination), candidate_location=object_hash(history.repository_location(destination)),
                       candidate_branch=git(destination, "symbolic-ref", "HEAD"), base=base)
        write_delivery_record(destination / ".git/cws-prepare.json", witness)
    candidate_storage.register(root, destination, reservation)
    return base


@candidate_storage.guarded
def prepare(root, remote, branch, destination=None, task_id=None, keep_candidate=False):
    import uuid
    import platform_runtime
    import history
    root = Path(root).resolve()
    remote_allowed(root, remote)
    ident(branch)
    if destination is None:
        parent = root / "work" / ident(task_id) if task_id else root
        if task_id:
            require(path(root, f"work/{task_id}/task.json").is_file(), "owning task missing")
        destination = parent / ".local/delivery" / uuid.uuid4().hex
    destination = Path(destination).absolute()
    platform_runtime.no_alias_parents(Path(destination.anchor), destination)
    destination = destination.resolve()
    require(not destination.exists() and not destination.is_symlink(), "candidate destination already exists")
    if destination.is_relative_to(root):
        if git(root, "check-ignore", "--quiet", "--", destination.relative_to(root).as_posix(), check=False).returncode != 0:
            raise GitFailure("prepare", code="candidate_not_ignored")
    reservation = candidate_storage.reserve(root, destination, keep=keep_candidate,
                                            intent={"remote": remote, "branch": branch})
    with candidate_storage.preparation(root, reservation) as guard:
        witness = prepare_witness(root, remote, branch)
        git(root, "clone", "--no-local", "--single-branch", "--branch", branch, remote, str(destination))
        guard.check()
        identity(destination)
        base = record_prepare(root, destination, witness, reservation)
    return {"path": str(destination), "base": base, "branch": branch, "source_witness": bool(witness)}


@candidate_storage.guarded
def preflight(root, remote=None, branch="main", authorize=remote_allowed):
    """Receive shared state without staging, publishing or discarding local work."""
    root = Path(root).resolve()
    ident(branch)
    require(Path(git(root, "rev-parse", "--show-toplevel")).resolve() == root, "preflight requires the repository root")
    with lock(scope_root(root)):
        head = git(root, "rev-parse", "HEAD")
        def blocked(reason, **extra):
            return {"status": "blocked", "reason": reason, "local_head": head, "fresh": False, "next_action": "Preserve local work; resolve the stated prerequisite before dependent work", **extra}
        if remote is None:
            configured = git(root, "remote", "get-url", "origin", check=False)
            if configured.returncode:
                return blocked("Shared repository is not configured")
            remote = configured.stdout.strip()
        authorize(root, remote)
        dirty = git(root, "status", "--porcelain", "--untracked-files=all")
        for marker in ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"]:
            location = Path(git(root, "rev-parse", "--git-path", marker))
            if not location.is_absolute():
                location = root / location
            if location.exists():
                return blocked("An unfinished Git operation requires reconciliation", operation=marker)
        if git(root, "symbolic-ref", "--quiet", "HEAD", check=False).returncode:
            return blocked("Detached HEAD; choose the accepted working branch")
        try:
            fetched = git(root, "-c", "credential.interactive=false", "fetch", "--no-tags", remote, f"refs/heads/{branch}", check=False, timeout=30, noninteractive=True)
        except subprocess.TimeoutExpired:
            return blocked("Shared version fetch timed out; local snapshot is not confirmed fresh", **diagnostic("fetch", timed_out=True))
        if fetched.returncode:
            return blocked("Shared version unavailable; local snapshot is not confirmed fresh", **diagnostic("fetch", fetched.stderr))
        actual = git(root, "rev-parse", "FETCH_HEAD")
        relative = "template/company/config.yaml" if scope_root(root) != root else "company/config.yaml"
        observed = git(root, "show", f"{actual}:{relative}", check=False)
        import yaml
        try:
            other = yaml.safe_load(observed.stdout) if observed.returncode == 0 else None
        except yaml.YAMLError:
            return blocked("Shared repository identity is unreadable", remote_head=actual)
        if not isinstance(other, dict) or other.get("id") != config(scope_root(root))["id"]:
            return blocked("Shared repository belongs to a different company/product scope", remote_head=actual)
        if dirty:
            return blocked("Uncommitted local work; do not auto-commit, stash or overwrite it", remote_head=actual, **diagnostic("preflight", code="local_pending"))
        # A Git process outside our lock may have changed the checkout during fetch.
        if git(root, "rev-parse", "HEAD") != head or git(root, "status", "--porcelain", "--untracked-files=all"):
            return blocked("Checkout changed during preflight; repeat against the preserved current state", remote_head=actual)
        if head == actual:
            mode = "current"
        elif git(root, "merge-base", "--is-ancestor", actual, head, check=False).returncode == 0:
            mode = "local-ahead"
        elif git(root, "merge-base", "--is-ancestor", head, actual, check=False).returncode == 0:
            merged = git(root, "merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", actual, check=False)
            if merged.returncode:
                return blocked("Fast-forward refused; preserve checkout and reconcile", remote_head=actual)
            mode = "updated"
        else:
            return blocked("Local and shared histories diverged; prepare a reconciliation candidate", remote_head=actual, **diagnostic("preflight", code="remote_diverged"))
        if mode == "updated":
            actual_root = scope_root(root)
            try:
                validated = subprocess.run([sys.executable, str(actual_root / "scripts/system.py"), "--root", str(actual_root), "validate"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            except subprocess.TimeoutExpired:
                return blocked("Received version validator timed out; dependent work is blocked", local_head=git(root, "rev-parse", "HEAD"), remote_head=actual)
            if validated.returncode:
                return blocked("Received version failed its own validator; dependent work is blocked", local_head=git(root, "rev-parse", "HEAD"), remote_head=actual)
        else:
            validation.repository(scope_root(root))
        return {"status": "ready", "mode": mode, "fresh": True, "local_head": git(root, "rev-parse", "HEAD"), "remote_head": actual, "branch": branch, "pending_delivery": mode == "local-ahead", "next_action": "Read context from disk; deliver only authorized local commits through the common route"}


def commit_context(root):
    """Pin effective Git configuration and executable native commit hooks."""
    settings = git(root, "config", "--null", "--list", binary=True)
    for key in ("core.splitIndex", "index.sparse"):
        value = git(root, "config", "--bool", "--get", key, check=False)
        require(value.returncode in {0, 1} and value.stdout.strip() != "true", "split/sparse index requires a separate supported candidate")
    hooks_path = git(root, "config", "--get", "core.hooksPath", check=False)
    require(hooks_path.returncode in {0, 1}, "unreadable native hook configuration")
    require(not hooks_path.stdout.strip() or Path(hooks_path.stdout.strip()).is_absolute(), "relative native hooksPath cannot be preserved in an isolated commit")
    hookdir = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks")).resolve()
    fingerprints = {}
    for name in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "reference-transaction", "post-index-change", "pre-merge-commit", "post-merge", "post-checkout"):
        file = hookdir / name
        if file.is_file() and os.access(file, os.X_OK):
            fingerprints[name] = (str(file.resolve()), digest(file))
    return (settings, str(hookdir), fingerprints)


def native_fingerprint(root):
    import hashlib
    settings, hookdir, hooks = commit_context(root)
    return object_hash({"config": hashlib.sha256(settings).hexdigest(), "hookdir": hookdir, "hooks": hooks})


@contextmanager
def commit_locks(gitdir):
    """Own only locks created here; existing Git locks are never removed."""
    owned = []
    try:
        for name in ("index.lock", "HEAD.lock"):
            file = Path(gitdir) / name
            try:
                descriptor = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise GitFailure("git_lock", code="git_locked") from None
            except OSError as exc:
                raise metadata_error("git_lock", exc) from None
            stat = os.fstat(descriptor)
            os.close(descriptor)
            owned.append((file, (stat.st_dev, stat.st_ino)))
        yield owned[0][0]
    finally:
        for file, identity in reversed(owned):
            try:
                stat = file.lstat()
            except FileNotFoundError:
                continue
            if (stat.st_dev, stat.st_ino) == identity:
                file.unlink()


def lock_identity(file):
    import stat
    value = Path(file).lstat()
    require(stat.S_ISREG(value.st_mode), "Git lock is no longer an owned regular file")
    return value.st_dev, value.st_ino


def write_owned_index(index_lock, data, identity):
    try:
        descriptor = os.open(index_lock, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise metadata_error("git_index", exc) from None
    try:
        value = os.fstat(descriptor)
        require((value.st_dev, value.st_ino) == identity, "Git lock changed; preserve foreign lock")
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except OSError as exc:
        raise metadata_error("git_index", exc) from None
    finally:
        os.close(descriptor)


def install_commit_index(index_lock, index, identity=None):
    if identity is not None:
        require(lock_identity(index_lock) == identity, "Git lock changed; preserve foreign lock")
    try:
        os.replace(index_lock, index)
    except OSError as exc:
        raise metadata_error("git_index", exc) from None


@contextmanager
def native_admin(root, head, worktree=None):
    """Use native Git/config/hooks with private HEAD/index and the same store."""
    import history
    location = history.repository_location(root)
    with tempfile.TemporaryDirectory(prefix="company-native-admin-") as temporary:
        scratch, admin = Path(temporary) / "work", Path(temporary) / "admin"
        scratch.mkdir(); admin.mkdir()
        atomic_text(scratch / ".git", "gitdir: " + str(admin) + "\n")
        atomic_text(admin / "commondir", location["common"] + "\n")
        atomic_text(admin / "gitdir", str(scratch / ".git") + "\n")
        atomic_text(admin / "HEAD", (head or "ref: refs/worktree/company-guarded") + "\n")
        worktree_config = Path(location["gitdir"]) / "config.worktree"
        if worktree_config.exists():
            (admin / "config.worktree").write_bytes(worktree_config.read_bytes())
        def private_git(*arguments, **options):
            return git(scratch, "--git-dir", str(admin), "--work-tree", str(worktree or scratch), *arguments, **options)
        yield scratch, admin, private_git


@candidate_storage.guarded
def commit(root, relative_paths, message):
    require(relative_paths and message.strip(), "explicit changed paths and message required")
    root = Path(root).resolve()
    require(not git(root, "diff", "--cached", "--name-only"), "foreign staged work present")
    for relative in relative_paths:
        file = commit_path(root, relative)
        require(file.is_file() or file.is_symlink() or git(root, "ls-files", "--error-unmatch", "--", relative, check=False).returncode == 0, "commit requires exact file paths")
        require(not any(p in {".git", ".env", ".local"} for p in Path(relative).parts), "private/control path cannot be delivered")
    validation.repository(scope_root(root))
    import history
    snapshot = history.company_policy(scope_root(root))
    location = history.repository_location(root)
    gitdir = Path(location["gitdir"])
    index = gitdir / "index"
    index_before = index.read_bytes() if index.exists() else None
    head_before = (gitdir / "HEAD").read_bytes()
    branch = git(root, "symbolic-ref", "--quiet", "HEAD", check=False)
    require(branch.returncode == 0 and branch.stdout.strip().startswith("refs/heads/"), "guarded commit requires an attached working branch")
    branch = branch.stdout.strip()
    require(git(root, "symbolic-ref", "--quiet", branch, check=False).returncode == 1, "symbolic branch alias cannot be published")
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        require(not (gitdir / marker).exists(), "unfinished Git operation requires explicit reconciliation")
    old_head = git(root, "rev-parse", "--verify", "HEAD", check=False)
    old_head = old_head.stdout.strip() if old_head.returncode == 0 else None
    native_context = commit_context(root)
    history.working(root, relative_paths, snapshot)
    candidate_tree = history.preview_tree(root, relative_paths, old_head)
    import linked_documents
    linked_documents.check_tree(root, candidate_tree, prefix='template/' if scope_root(root) != root else '')
    if snapshot:
        history.scan(root, snapshot["policy"], tips=(old_head,) if old_head else (), baseline=snapshot["baseline"], trees=(candidate_tree,))
        history.unchanged(scope_root(root), snapshot)

    def unchanged():
        require(history.repository_location(root) == location, "repository identity changed during guarded commit")
        require((git(root, "rev-parse", "--verify", "HEAD", check=False).stdout.strip() or None) == old_head, "HEAD changed during guarded commit")
        require((gitdir / "HEAD").read_bytes() == head_before, "HEAD association changed during guarded commit")
        require((index.read_bytes() if index.exists() else None) == index_before, "index changed during guarded commit; preserve foreign staged work")
        require(commit_context(root) == native_context, "native config/hooks changed during guarded commit")
        history.unchanged(scope_root(root), snapshot)

    unchanged()
    if old_head and git(root, "rev-parse", old_head + "^{tree}") == candidate_tree:
        return old_head
    with native_admin(root, old_head) as (scratch, admin, private_git):
        require(commit_context(scratch) == native_context, "isolated native config/hooks differ; preserve original work")
        private_git("read-tree", candidate_tree)
        private_git("checkout-index", "--all")
        unchanged()
        require(commit_context(scratch) == native_context, "isolated native config/hooks changed")
        private_git("commit", "-m", message)
        new_head = private_git("rev-parse", "HEAD")
        require(private_git("rev-parse", new_head + "^{tree}") == candidate_tree, "native hook changed checked commit tree; original branch preserved")
        require(private_git("show", "--format=%P", "--no-patch", new_head) == (old_head or ""), "isolated commit parent changed; original branch preserved")
        require(private_git("write-tree") == candidate_tree, "native hook changed candidate index; original branch preserved")
        with commit_locks(gitdir) as index_lock:
            owned_index = lock_identity(index_lock)
            unchanged()
            require(commit_context(scratch) == native_context, "isolated native config/hooks changed before publication")
            write_owned_index(index_lock, (admin / "index").read_bytes(), owned_index)
            # A captured branch and old object ID make publication atomic. No hook is bypassed.
            private_git("update-ref", "--no-deref", "-m", "guarded commit: " + message, branch, new_head, old_head or "0" * len(new_head))
            require(git(root, "rev-parse", branch) == new_head, "published branch changed; preserve candidate and reconcile")
            require((gitdir / "HEAD").read_bytes() == head_before, "HEAD association changed after publication; reconcile")
            install_commit_index(index_lock, index, owned_index)
            require(git(root, "write-tree") == candidate_tree and git(root, "rev-parse", "HEAD") == new_head, "published commit/index readback mismatch; preserve and reconcile")
    # No index backing file may depend on the now-removed scratch admin.
    require(git(root, "write-tree") == candidate_tree and git(root, "rev-parse", "HEAD") == new_head, "commit changed after isolated cleanup; preserve and reconcile")
    return new_head


def completion_guard(condition, code="source_changed"):
    if not condition:
        raise GitFailure("local_completion", code=code)


def complete_source(witness, target, state, state_file):
    """Native checkout with private admin; publish captured branch under real locks."""
    import history
    source = Path(witness["source"])
    completion_guard(config(source, True).get("workspace", {}).get("profile") != "work", "work_route_required")
    with lock(source):
        location = history.repository_location(source)
        gitdir = Path(location["gitdir"])
        index = gitdir / "index"
        head_file = gitdir / "HEAD"
        completion_guard(object_hash(location) == witness["location"], "wrong_identity")
        completion_guard(config(source)["id"] == witness["company"], "wrong_identity")
        # Fetch objects locally only. Publication/readback has already completed.
        git(source, "fetch", "--no-tags", witness["candidate"], target)
        with commit_locks(gitdir) as index_lock:
            locks = {file: lock_identity(file) for file in (index_lock, gitdir / "HEAD.lock")}
            def unchanged():
                completion_guard(all(lock_identity(file) == identity for file, identity in locks.items()))
                completion_guard(object_hash(history.repository_location(source)) == witness["location"])
                completion_guard(head_file.read_bytes().hex() == witness["head_bytes"])
                completion_guard(git(source, "symbolic-ref", "--quiet", "HEAD") == witness["branch"])
                completion_guard(native_fingerprint(source) == witness["native"])
                completion_guard(not any((gitdir / name).exists() for name in
                                         ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")))
            unchanged()
            current = git(source, "rev-parse", "HEAD")
            completion_guard(current in {witness["head"], target})
            original_index = digest(index) == witness["index"]
            target_index = current == target and git(source, "diff", "--cached", "--quiet", target, check=False).returncode == 0
            completion_guard(original_index or target_index)
            completion_guard(git(source, "merge-base", "--is-ancestor", witness["head"], target, check=False).returncode == 0)
            if state["phase"] == "complete" and target_index:
                completion_guard(not git(source, "status", "--porcelain", "--untracked-files=all"), "local_pending")
                validation.repository(source)
                unchanged()
                return {"workspace_reconciled": True, "local_head": target, "source": str(source)}
            context = commit_context(source)
            with native_admin(source, witness["head"], source) as (scratch, admin, private_git):
                completion_guard(commit_context(scratch) == context)
                def exact_worktree():
                    return (private_git("update-index", "--refresh", check=False).returncode == 0 and
                            private_git("diff-files", "--quiet", check=False).returncode == 0 and
                            not private_git("ls-files", "--others", "--exclude-standard"))
                if state["phase"] == "readback-confirmed" and current != target:
                    completion_guard(not git(source, "status", "--porcelain", "--untracked-files=all"), "local_pending")
                    private_git("read-tree", witness["head"])
                    state["phase"] = "checkout-started"
                    write_delivery_record(state_file, state)
                    unchanged()
                    private_git("merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", "--no-edit", "--no-stat", target)
                    completion_guard(private_git("rev-parse", "HEAD") == target)
                else:
                    # After an interruption, never replay the checkout over edits.
                    private_git("read-tree", target)
                completion_guard(private_git("write-tree") == private_git("rev-parse", target + "^{tree}"))
                completion_guard(exact_worktree(), "local_pending")
                unchanged()
                completion_guard(digest(index) == witness["index"] or target_index)
                state["phase"] = "worktree-updated"
                write_delivery_record(state_file, state)
                if current != target:
                    private_git("update-ref", "--no-deref", "-m", "Complete confirmed delivery", witness["branch"], target, witness["head"])
                state["phase"] = "ref-published"
                write_delivery_record(state_file, state)
                unchanged()
                completion_guard(git(source, "rev-parse", witness["branch"]) == target)
                completion_guard(exact_worktree(), "local_pending")
                write_owned_index(index_lock, (admin / "index").read_bytes(), locks[index_lock])
                unchanged()
                install_commit_index(index_lock, index, locks[index_lock])
            completion_guard(git(source, "rev-parse", "HEAD") == target)
            completion_guard(git(source, "write-tree") == git(source, "rev-parse", target + "^{tree}"))
            completion_guard(not git(source, "status", "--porcelain", "--untracked-files=all"), "local_pending")
            validation.repository(source)
        state["phase"] = "complete"
        state.pop("diagnostic", None)
        write_delivery_record(state_file, state)
    return {"workspace_reconciled": True, "local_head": target, "source": str(source)}


@candidate_storage.guarded
def finish_delivery(root, remote, branch, expected_base, task_id):
    import history
    root = Path(root).resolve()
    gitdir = Path(history.repository_location(root)["gitdir"])
    witness_file = gitdir / "cws-prepare.json"
    completion_guard(witness_file.is_file() and not witness_file.is_symlink(), "source_witness_missing")
    witness = load(witness_file)
    completion_guard(witness["candidate"] == str(root) and witness["candidate_location"] == object_hash(history.repository_location(root)), "wrong_identity")
    completion_guard(git(root, "symbolic-ref", "HEAD") == witness["candidate_branch"], "wrong_identity")
    completion_guard(witness["branch"].startswith("refs/heads/"), "wrong_identity")
    completion_guard(remote == witness["remote"] and branch == witness["remote_branch"], "wrong_identity")
    source = Path(witness["source"])
    completion_guard(config(source).get("workspace", {}).get("profile") != "work", "work_route_required")
    remote_allowed(source, remote)
    expected_base = expected_base or witness["base"]
    state_file = gitdir / "cws-finish.json"
    with lock(gitdir / "cws-finish-lock"):
        if state_file.exists():
            completion_guard(not state_file.is_symlink())
            state = load(state_file)
            completion_guard(state["target"] == git(root, "rev-parse", "HEAD") and state["task_id"] == task_id)
            completion_guard(state["witness"] == object_hash(witness))
            receipt = state["receipt"]
        else:
            # Reject a retargeted source before an external write, even if clean.
            completion_guard(object_hash(history.repository_location(source)) == witness["location"], "wrong_identity")
            completion_guard(git(source, "symbolic-ref", "HEAD", check=False).stdout.strip() == witness["branch"], "wrong_identity")
            completion_guard(config(source, True)["id"] == witness["company"], "wrong_identity")
            completion_guard(native_fingerprint(source) == witness["native"])
            completion_guard(not git(source, "diff", "--cached", "--name-only"))
            completion_guard(git(source, "rev-parse", "HEAD") in {witness["head"], git(root, "rev-parse", "HEAD")})
            completion_guard(git(root, "merge-base", "--is-ancestor", witness["head"], "HEAD", check=False).returncode == 0)
            receipt = deliver(root, remote, branch, expected_base, task_id)
            state = {"witness": object_hash(witness), "target": git(root, "rev-parse", "HEAD"),
                     "task_id": task_id, "receipt": receipt, "phase": "readback-confirmed"}
        try:
            completion_guard(not git(root, "status", "--porcelain", "--untracked-files=all"), "local_pending")
            write_delivery_record(state_file, state)
            local = complete_source(witness, state["target"], state, state_file)
            result = {**receipt, **local, "completion_phase": state["phase"]}
        except Exception as exc:
            detail = exc.diagnostic if isinstance(exc, GitFailure) else diagnostic("local_completion", code="source_changed")
            state["diagnostic"] = detail
            try:
                write_delivery_record(state_file, state)
            except (OSError, GitFailure):
                pass  # Return the confirmed receipt even when the journal cannot be written.
            return {**receipt, "status": "delivered-local-pending", "workspace_reconciled": False,
                    "completion_phase": state["phase"], **detail}
    return {**result, **candidate_storage.seal(root, result)}


@candidate_storage.guarded
def deliver(root, remote, branch="main", expected_base=None, task_id=None, finish=False):
    """Fail on stale base. Never force/rebase/reset; retry first reconciles remote."""
    if finish:
        return finish_delivery(root, remote, branch, expected_base, task_id)
    remote_allowed(root, remote)
    ident(branch)
    clean(root)
    validation.repository(scope_root(root))
    head = git(root, "rev-parse", "HEAD")
    import history
    location = history.repository_location(root)
    local_remote = Path(remote).is_absolute() and Path(remote).exists()
    snapshot = history.company_policy(scope_root(root), required=not local_remote and scope_root(root) == Path(root))
    git(root, "fetch", remote, f"refs/heads/{branch}")
    current = git(root, "rev-parse", "FETCH_HEAD")
    import linked_documents
    linked_documents.check_tree(root, head, prefix='template/' if scope_root(root) != Path(root) else '')
    import yaml
    config_path = "template/company/config.yaml" if scope_root(root) != Path(root) else "company/config.yaml"
    remote_company = yaml.safe_load(git(root, "show", f"FETCH_HEAD:{config_path}"))
    require(remote_company["id"] == config(scope_root(root))["id"], "remote belongs to another company/product scope")
    require(history.repository_location(root)==location, 'repository identity changed during delivery')
    if snapshot:
        history.scan(root, snapshot["policy"], tips=(head,), baseline=snapshot["baseline"])
        history.unchanged(scope_root(root), snapshot)
        require(git(root, "rev-parse", "HEAD") == head, "HEAD changed during guarded delivery")
        clean(root)
    if git(root, "merge-base", "--is-ancestor", head, current, check=False).returncode == 0:
        receipt = {"status": "readback-confirmed", "sha256": head, "remote_head": current, "newer_remote": current != head, "repeated": True}
        return acknowledge(root, remote, branch, task_id, receipt) if task_id else receipt
    require(not expected_base or current == expected_base, "stale candidate; latest common version changed")
    require(git(root, "merge-base", "--is-ancestor", current, head, check=False).returncode == 0, "candidate conflicts with current common version; preserve both")
    # A normal push checks fast-forward atomically at the server.
    history.unchanged(scope_root(root), snapshot)
    require(history.repository_location(root)==location, 'repository identity changed before push')
    require(git(root, "rev-parse", "HEAD") == head, "HEAD changed before push")
    try:
        pushed = git(root, "push", remote, f"{head}:refs/heads/{branch}", check=False)
    except subprocess.TimeoutExpired:
        pushed = subprocess.CompletedProcess([], 124, "", "")
    # Success is not inferred from push text/exit code; unknown outcomes read back.
    try:
        fetched = git(root, "fetch", remote, f"refs/heads/{branch}", check=False)
    except subprocess.TimeoutExpired:
        raise GitFailure("readback", timed_out=True) from None
    if fetched.returncode:
        raise GitFailure("readback", fetched.stderr)
    actual = git(root, "rev-parse", "FETCH_HEAD")
    require(git(root, "merge-base", "--is-ancestor", head, actual, check=False).returncode == 0, "candidate not included; preserve it and refresh common base")
    files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", head).splitlines()
    # Read inclusion at delivered commit. Later edits are reported as newer, never lost.
    for relative in files:
        if git(root, "cat-file", "-e", f"{head}:{relative}", check=False).returncode == 0:
            require(git(root, "rev-parse", f"{head}:{relative}") == git(root, "rev-parse", f"FETCH_HEAD~0:{relative}") or actual != head, "remote content mismatch")
    receipt = {"status": "readback-confirmed", "sha256": head, "remote_head": actual, "newer_remote": actual != head, "push_exit_code": pushed.returncode, "files": files, "repeated": False}
    return acknowledge(root, remote, branch, task_id, receipt) if task_id else receipt


def acknowledge(root, remote, branch, task_id, receipt):
    root = Path(root).resolve()
    from core import event, load, lock, task_file, write
    with lock(root):
        file = task_file(root, task_id)
        t = load(file)
        require(t["status"] == "verified", "only verified result can be acknowledged")
        require(git(root, "show", f"{receipt['sha256']}:{t['output']['path']}") == Path(root, t["output"]["path"]).read_text(encoding="utf-8").strip(), "delivered commit lacks exact result")
        if t["delivery"]["status"] == "delivered" and t["delivery"].get("result_sha256") == t["output"]["sha256"]:
            return receipt
        t["delivery"] = {"status": "delivered", "commit": receipt["sha256"], "readback": True, "result_sha256": t["output"]["sha256"]}
        event(t, "delivery", t["delivery"])
        write(file, t)
        commit(root, [file.relative_to(root).as_posix()], "Record result delivery readback")
    # Same delivery path for the acknowledgment; no independent sync implementation.
    acknowledged = deliver(root, remote, branch, expected_base=receipt.get("remote_head", receipt["sha256"]))
    return {**receipt, "acknowledgment_commit": acknowledged["sha256"]}
