"""The single Git delivery path. Updates and proposals prepare different candidates."""
from __future__ import annotations
from pathlib import Path
import subprocess
from core import config, digest, ident, load, path, require
import validation


def scope_root(root):
    root = Path(root)
    return root / "template" if not (root / "company/config.yaml").exists() and (root / "template/company/config.yaml").exists() else root


def git(root, *arguments, check=True):
    command = ["git", "-C", str(root), *arguments]
    value = subprocess.run(command, capture_output=True, text=True)
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
    destination = Path(destination)
    require(not destination.exists(), "candidate destination already exists")
    git(root, "clone", "--no-local", "--single-branch", "--branch", branch, remote, str(destination))
    identity(destination)
    return {"path": str(destination), "base": git(destination, "rev-parse", "HEAD"), "branch": branch}


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
