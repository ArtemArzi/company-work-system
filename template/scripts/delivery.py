"""The single Git delivery path. Updates and proposals prepare different candidates."""
from __future__ import annotations
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from core import Rejected, config, digest, ident, load, lock, path, require
import validation
from platform_runtime import atomic_text


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


def git(root, *arguments, check=True, timeout=None, noninteractive=False, binary=False, private_index=None):
    command = ["git", "-C", str(root), *arguments]
    environment = git_environment()
    if private_index is not None:
        private_index = Path(private_index)
        require(private_index.is_absolute() and private_index.parent.name.startswith("company-export-index-") and private_index.name == "index" and not private_index.is_symlink(), "owned temporary export index required")
        environment["GIT_INDEX_FILE"] = str(private_index)
    if noninteractive:
        environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    value = subprocess.run(command, capture_output=True, text=not binary, encoding=None if binary else "utf-8", errors=None if binary else "replace", timeout=timeout, env=environment)
    if check:
        require(value.returncode == 0, "Git operation failed: " + (value.stderr.decode(errors="replace") if binary else value.stderr).strip()[:1500])
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


def prepare(root, remote, branch, destination):
    remote_allowed(root, remote)
    ident(branch)
    destination = Path(destination).resolve()
    require(not destination.exists(), "candidate destination already exists")
    git(root, "clone", "--no-local", "--single-branch", "--branch", branch, remote, str(destination))
    identity(destination)
    return {"path": str(destination), "base": git(destination, "rev-parse", "HEAD"), "branch": branch}


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
            return blocked("Shared version fetch timed out; local snapshot is not confirmed fresh")
        if fetched.returncode:
            return blocked("Shared version unavailable; local snapshot is not confirmed fresh")
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
            return blocked("Uncommitted local work; do not auto-commit, stash or overwrite it", remote_head=actual)
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
            return blocked("Local and shared histories diverged; prepare a reconciliation candidate", remote_head=actual)
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
    for name in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "reference-transaction", "post-index-change"):
        file = hookdir / name
        if file.is_file() and os.access(file, os.X_OK):
            fingerprints[name] = (str(file.resolve()), digest(file))
    return (settings, str(hookdir), fingerprints)


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
                raise Rejected("concurrent Git operation; preserve existing locks and retry")
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


def install_commit_index(index_lock, index):
    os.replace(index_lock, index)


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
    with tempfile.TemporaryDirectory(prefix="company-guarded-commit-") as temporary:
        scratch = Path(temporary) / "work"
        admin = Path(temporary) / "admin"
        scratch.mkdir(); admin.mkdir()
        atomic_text(scratch / ".git", "gitdir: " + str(admin) + "\n")
        atomic_text(admin / "commondir", location["common"] + "\n")
        atomic_text(admin / "gitdir", str(scratch / ".git") + "\n")
        atomic_text(admin / "HEAD", (old_head or "ref: refs/worktree/company-guarded") + "\n")
        worktree_config = gitdir / "config.worktree"
        if worktree_config.exists():
            (admin / "config.worktree").write_bytes(worktree_config.read_bytes())
        def private_git(*arguments, **options):
            return git(scratch, "--git-dir", str(admin), "--work-tree", str(scratch), *arguments, **options)
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
            unchanged()
            require(commit_context(scratch) == native_context, "isolated native config/hooks changed before publication")
            with index_lock.open("wb") as stream:
                stream.write((admin / "index").read_bytes()); stream.flush(); os.fsync(stream.fileno())
            # A captured branch and old object ID make publication atomic. No hook is bypassed.
            private_git("update-ref", "--no-deref", "-m", "guarded commit: " + message, branch, new_head, old_head or "0" * len(new_head))
            require(git(root, "rev-parse", branch) == new_head, "published branch changed; preserve candidate and reconcile")
            require((gitdir / "HEAD").read_bytes() == head_before, "HEAD association changed after publication; reconcile")
            install_commit_index(index_lock, index)
            require(git(root, "write-tree") == candidate_tree and git(root, "rev-parse", "HEAD") == new_head, "published commit/index readback mismatch; preserve and reconcile")
    # No index backing file may depend on the now-removed scratch admin.
    require(git(root, "write-tree") == candidate_tree and git(root, "rev-parse", "HEAD") == new_head, "commit changed after isolated cleanup; preserve and reconcile")
    return new_head


def deliver(root, remote, branch="main", expected_base=None, task_id=None):
    """Fail on stale base. Never force/rebase/reset; retry first reconciles remote."""
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
    if current == head:
        receipt = {"status": "readback-confirmed", "sha256": head, "repeated": True}
        return acknowledge(root, remote, branch, task_id, receipt) if task_id else receipt
    require(not expected_base or current == expected_base, "stale candidate; latest common version changed")
    require(git(root, "merge-base", "--is-ancestor", current, head, check=False).returncode == 0, "candidate conflicts with current common version; preserve both")
    # A normal push checks fast-forward atomically at the server.
    history.unchanged(scope_root(root), snapshot)
    require(history.repository_location(root)==location, 'repository identity changed before push')
    require(git(root, "rev-parse", "HEAD") == head, "HEAD changed before push")
    pushed = git(root, "push", remote, f"{head}:refs/heads/{branch}", check=False)
    # Success is not inferred from push text/exit code; unknown outcomes read back.
    fetched = git(root, "fetch", remote, f"refs/heads/{branch}", check=False)
    require(fetched.returncode == 0, "delivery outcome unknown; inspect remote before retry")
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
