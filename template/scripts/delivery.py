"""The single Git delivery path. Updates and proposals prepare different candidates."""
from __future__ import annotations
from pathlib import Path
import os
import subprocess
import sys
from core import config, digest, ident, load, lock, path, require
import validation


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


def git(root, *arguments, check=True, timeout=None, noninteractive=False):
    command = ["git", "-C", str(root), *arguments]
    environment = git_environment()
    if noninteractive:
        environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    value = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=environment)
    if check:
        require(value.returncode == 0, "Git operation failed: " + value.stderr.strip()[:1500])
    return value.stdout.strip() if check else value


def identity(root):
    # Only newly created repositories; no global identity/config changes.
    git(root, "config", "user.name", "Company Work System")
    git(root, "config", "user.email", "local-system@example.invalid")


def clean(root):
    require(not git(root, "status", "--porcelain", "--untracked-files=all"), "dirty candidate; preserve local work and prepare separately")


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
    require(git(root, "rev-parse", "--show-toplevel") == str(root), "preflight requires the repository root")
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
                validated = subprocess.run([sys.executable, str(actual_root / "scripts/system.py"), "--root", str(actual_root), "validate"], capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                return blocked("Received version validator timed out; dependent work is blocked", local_head=git(root, "rev-parse", "HEAD"), remote_head=actual)
            if validated.returncode:
                return blocked("Received version failed its own validator; dependent work is blocked", local_head=git(root, "rev-parse", "HEAD"), remote_head=actual)
        else:
            validation.repository(scope_root(root))
        return {"status": "ready", "mode": mode, "fresh": True, "local_head": git(root, "rev-parse", "HEAD"), "remote_head": actual, "branch": branch, "pending_delivery": mode == "local-ahead", "next_action": "Read context from disk; deliver only authorized local commits through the common route"}


def commit(root, relative_paths, message):
    require(relative_paths and message.strip(), "explicit changed paths and message required")
    require(not git(root, "diff", "--cached", "--name-only"), "foreign staged work present")
    for relative in relative_paths:
        file = path(root, relative)
        require(file.is_file() or git(root, "ls-files", "--error-unmatch", "--", relative, check=False).returncode == 0, "commit requires exact file paths")
        require(not any(p in {".git", ".env", ".local"} for p in Path(relative).parts), "private/control path cannot be delivered")
    validation.repository(scope_root(root))
    git(root, "add", "--", *relative_paths)
    if not git(root, "diff", "--cached", "--name-only"):
        return git(root, "rev-parse", "HEAD")
    git(root, "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


def deliver(root, remote, branch="main", expected_base=None, task_id=None):
    """Fail on stale base. Never force/rebase/reset; retry first reconciles remote."""
    remote_allowed(root, remote)
    ident(branch)
    clean(root)
    validation.repository(scope_root(root))
    head = git(root, "rev-parse", "HEAD")
    git(root, "fetch", remote, f"refs/heads/{branch}")
    current = git(root, "rev-parse", "FETCH_HEAD")
    import yaml
    config_path = "template/company/config.yaml" if scope_root(root) != Path(root) else "company/config.yaml"
    remote_company = yaml.safe_load(git(root, "show", f"FETCH_HEAD:{config_path}"))
    require(remote_company["id"] == config(scope_root(root))["id"], "remote belongs to another company/product scope")
    if current == head:
        receipt = {"status": "readback-confirmed", "sha256": head, "repeated": True}
        return acknowledge(root, remote, branch, task_id, receipt) if task_id else receipt
    require(not expected_base or current == expected_base, "stale candidate; latest common version changed")
    require(git(root, "merge-base", "--is-ancestor", current, head, check=False).returncode == 0, "candidate conflicts with current common version; preserve both")
    # A normal push checks fast-forward atomically at the server.
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
    from core import event, load, lock, task_file, write
    with lock(root):
        file = task_file(root, task_id)
        t = load(file)
        require(t["status"] == "verified", "only verified result can be acknowledged")
        require(git(root, "show", f"{receipt['sha256']}:{t['output']['path']}") == Path(root, t["output"]["path"]).read_text().strip(), "delivered commit lacks exact result")
        if t["delivery"]["status"] == "delivered" and t["delivery"].get("result_sha256") == t["output"]["sha256"]:
            return receipt
        t["delivery"] = {"status": "delivered", "commit": receipt["sha256"], "readback": True, "result_sha256": t["output"]["sha256"]}
        event(t, "delivery", t["delivery"])
        write(file, t)
        commit(root, [str(file.relative_to(root))], "Record result delivery readback")
    # Same delivery path for the acknowledgment; no independent sync implementation.
    acknowledged = deliver(root, remote, branch, expected_base=receipt.get("remote_head", receipt["sha256"]))
    return {**receipt, "acknowledgment_commit": acknowledged["sha256"]}
