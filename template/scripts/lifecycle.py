"""Company creation, evolution and recovery; deliveries use delivery.py."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from core import config, digest, ident, load, lock, now, object_hash, path, require, tasks, write
from delivery import clean, commit, git, identity, prepare, remote_allowed
import validation


def release_isolated(release):
    release = Path(release)
    allowed = {"README.md", "AGENTS.md", ".gitignore", "requirements.txt", "release.yaml", "company", "standards", "skills", "workflows", "scripts", "adapters", ".agents", ".claude", "checks"}
    for row in git(release, "rev-list", "--objects", "--all").splitlines():
        if " " in row:
            relative = row.split(" ", 1)[1]
            if not relative:
                continue  # Git names the root tree with an empty path.
            require(relative.split("/", 1)[0] in allowed, "release includes non-template history")
    require(git(release, "rev-parse", "--is-bare-repository") == "true", "release-only bare repository required")


def create(release, destination, company_id, owner):
    release_isolated(release)
    destination = Path(destination)
    require(not destination.exists(), "existing company destination; no overwrite")
    ident(company_id)
    require(company_id != "unconfigured" and owner.strip(), "company identity/owner required")
    git(Path(release).parent, "clone", "--no-local", "--single-branch", "--branch", "main", str(Path(release).resolve()), str(destination))
    identity(destination)
    base = git(destination, "rev-parse", "HEAD")
    cfg = config(destination)
    cfg.update(id=company_id, name=company_id, owner=owner)
    cfg["permissions"]["local_work"] = True
    cfg["decisions"].append({"id": "initial-adaptation", "by": owner, "scope": "local file work only; no external account/rights/budget inferred", "at": now()})
    write(path(destination, "company/config.yaml"), cfg)
    write(path(destination, ".system/base.json"), {"schema_version": 1, "source": str(Path(release).resolve()), "installed": base, "version": load(path(destination, "release.yaml"))["version"]})
    validation.repository(destination)
    commit(destination, ["company/config.yaml", ".system/base.json"], "Adapt clean company; preserve unknown business definitions")
    return {"path": str(destination), "base": base, "company": company_id}


def update(root, release, destination):
    """Always prepare independently. Current/dirty workspace is never mutated."""
    config(root, True)
    metadata = load(path(root, ".system/base.json"))
    require(metadata.get("schema_version") == 1, "unknown template ancestry")
    release_isolated(release)
    clean(root)
    candidate = Path(destination)
    require(not candidate.exists(), "update candidate already exists")
    git(root, "clone", "--no-local", str(Path(root).resolve()), str(candidate))
    identity(candidate)
    previous_head = git(candidate, "rev-parse", "HEAD")
    git(candidate, "fetch", str(Path(release).resolve()), "main")
    target = git(candidate, "rev-parse", "FETCH_HEAD")
    require(git(candidate, "merge-base", "--is-ancestor", metadata["installed"], target, check=False).returncode == 0, "release lacks installed ancestry")
    if target == metadata["installed"]:
        return {"status": "already-installed", "candidate": str(candidate), "target": target}
    require(git(candidate, "merge-base", "--is-ancestor", metadata["installed"], previous_head, check=False).returncode == 0, "company lost template ancestry")
    protected = {str(p.relative_to(candidate)): digest(p) for directory in ["company", "work"] for p in Path(candidate, directory).rglob("*") if p.is_file()}
    changes = git(candidate, "diff", "--name-only", metadata["installed"], target).splitlines()
    require(not any(p.startswith(("company/", "work/")) for p in changes), "release changes company-owned paths; separate adaptation decision required")
    merged = git(candidate, "merge", "--no-ff", "--no-commit", target, check=False)
    if merged.returncode != 0:
        return {"status": "conflict", "candidate": str(candidate), "target": target, "files": git(candidate, "diff", "--name-only", "--diff-filter=U").splitlines(), "next_action": "Resolve with the method/company owner; both sides retained in Git"}
    failures = []
    for relative, sha in protected.items():
        if not path(candidate, relative).is_file() or digest(path(candidate, relative)) != sha:
            failures.append("company data changed: " + relative)
    try:
        # Historical evidence is preserved; currently active tasks are separately blocked.
        validation.repository(candidate, freshness=False)
    except Exception as exc:
        failures.append(str(exc))
    for file in tasks(candidate):
        t = load(file)
        if t["status"] in {"active", "blocked", "waiting"}:
            from core import changed_bindings
            if changed_bindings(candidate, t["bindings"]):
                failures.append("active task pinned to previous version: " + t["id"])
    if failures:
        return {"status": "needs-reconciliation", "candidate": str(candidate), "target": target, "issues": failures, "next_action": "Keep exact candidate; reconcile bindings and old sessions; finish-update"}
    return finish_update(candidate, target, previous_head)


def finish_update(candidate, target, expected_company):
    require(git(candidate, "rev-parse", "HEAD") == expected_company, "company candidate head changed")
    require(not git(candidate, "diff", "--name-only", "--diff-filter=U"), "unresolved update conflict")
    require(git(candidate, "rev-parse", "MERGE_HEAD") == target, "update target changed")
    validation.repository(candidate, freshness=False)
    from core import changed_bindings
    for file in tasks(candidate):
        t = load(file)
        require(t["status"] not in {"active", "waiting"} or not changed_bindings(candidate, t["bindings"]), "active task must explicitly reconcile methods")
    version = load(path(candidate, "release.yaml"))["version"]
    metadata = load(path(candidate, ".system/base.json"))
    metadata.update(installed=target, version=version, previous=metadata["installed"])
    write(path(candidate, ".system/base.json"), metadata)
    git(candidate, "add", "--", ".system/base.json")
    git(candidate, "commit", "-m", f"Apply verified template {version}; preserve local state")
    return {"status": "verified-candidate", "candidate": str(candidate), "target": target, "company_base": expected_company, "sha256": git(candidate, "rev-parse", "HEAD"), "delivery": "pending"}


def backup(root, destination):
    clean(root)
    validation.repository(root, freshness=False)
    destination = Path(destination)
    require(not destination.exists() and not destination.resolve().is_relative_to(Path(root).resolve()), "backup destination must be new/outside company")
    destination.mkdir(parents=True)
    git(root, "bundle", "create", str(destination / "history.bundle"), "--all")
    files = git(root, "ls-files", "-z").split("\x00")
    manifest = {}
    with tarfile.open(destination / "files.tar", "w") as archive:
        for relative in files:
            if not relative:
                continue
            file = Path(root, relative)
            if file.is_symlink():
                manifest[relative] = {"symlink": str(file.readlink())}
            else:
                manifest[relative] = {"sha256": digest(file)}
            archive.add(file, arcname=relative, recursive=False)
    write(destination / "manifest.json", {"schema_version": 1, "head": git(root, "rev-parse", "HEAD"), "files": manifest, "bundle_sha256": digest(destination / "history.bundle"), "tar_sha256": digest(destination / "files.tar")})
    return {"path": str(destination), "tracked_files": len(manifest), "boundary": "tracked authorized files only; ignored secrets/external data need separate owner backup"}


def restore(backup_path, destination):
    backup_path, destination = Path(backup_path), Path(destination)
    require(not destination.exists(), "restore cannot overwrite existing company")
    manifest = load(backup_path / "manifest.json")
    require(digest(backup_path / "history.bundle") == manifest["bundle_sha256"] and digest(backup_path / "files.tar") == manifest["tar_sha256"], "backup corrupted")
    git(backup_path, "clone", str(backup_path / "history.bundle"), str(destination))
    require(git(destination, "rev-parse", "HEAD") == manifest["head"], "restore version differs")
    # Git reconstructs the files and safe declared projections; tar isn't blindly extracted.
    for relative, item in manifest["files"].items():
        file = Path(destination, relative)
        if "symlink" in item:
            require(file.is_symlink() and str(file.readlink()) == item["symlink"] and file.resolve().is_relative_to(destination.resolve()), "restored projection unsafe")
        else:
            require(path(destination, relative).is_file() and digest(file) == item["sha256"], "restored file differs")
    validation.repository(destination, freshness=False)
    return {"path": str(destination), "status": "restored-and-verified", "head": manifest["head"]}


def rollback(root, destination):
    clean(root)
    metadata = load(path(root, ".system/base.json"))
    require(metadata.get("previous"), "no previous installed method version")
    candidate = Path(destination)
    require(not candidate.exists(), "rollback candidate exists")
    git(root, "clone", "--no-local", str(Path(root).resolve()), str(candidate))
    identity(candidate)
    current, previous = metadata["installed"], metadata["previous"]
    protected = {str(p.relative_to(candidate)): digest(p) for d in ["company", "work"] for p in Path(candidate, d).rglob("*") if p.is_file()}
    delta = git(candidate, "diff", "--binary", current, previous, "--", "standards", "skills", "workflows", "scripts", "adapters", "release.yaml", "README.md", "AGENTS.md", ".agents", ".claude", "requirements.txt", check=False)
    require(delta.returncode == 0, "rollback diff unavailable")
    patch = subprocess.run(["git", "-C", str(candidate), "apply", "--3way", "--index"], input=delta.stdout, text=True, capture_output=True)
    if patch.returncode:
        return {"status": "conflict", "candidate": str(candidate), "next_action": "Preserve local methods and resolve reverse patch"}
    require(all(path(candidate, r).is_file() and digest(path(candidate, r)) == sha for r, sha in protected.items()), "rollback touched company data")
    validation.repository(candidate, freshness=False)
    metadata.update(installed=previous, previous=current, version=load(path(candidate, "release.yaml"))["version"])
    write(path(candidate, ".system/base.json"), metadata)
    git(candidate, "add", "--", ".system/base.json")
    git(candidate, "commit", "-m", "Restore previous methods; keep current company work")
    return {"status": "verified-candidate", "candidate": str(candidate), "sha256": git(candidate, "rev-parse", "HEAD")}


def proposal(root, package, destination):
    """Only a deliberately authored sanitized package, never company Git history."""
    cfg = config(root, True)
    package, destination = Path(package), Path(destination)
    approval = load(package / "approval.json")
    require(approval.get("approved_by") == cfg["owner"] and approval.get("permission") == "share-sanitized-method" and approval.get("purpose"), "proposal sharing permission missing")
    require(not destination.exists(), "proposal destination exists")
    files = approval.get("files", {})
    require(files, "empty proposal")
    for relative, sha in files.items():
        require(relative.startswith(("standards/", "skills/", "workflows/", "checks/")), "proposal allowlist excludes company data/history")
        file = path(package, relative)
        require(file.is_file() and digest(file) == sha, "proposal approval does not match exact file")
        text = file.read_text()
        require(not any(marker in text for marker in ["PRIVATE KEY", "Bearer ", "api_key", cfg["id"], "company/config", "work/"]), "proposal contains company/private identifiers")
    destination.mkdir(parents=True)
    for relative in files:
        target = path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path(package, relative), target)
    write(destination / "proposal.json", {"schema_version": 1, "purpose": approval["purpose"], "files": files, "status": "sanitized-candidate", "limitations": ["Automated scan cannot prove anonymity; owner approval covers exact authored files", "Not accepted into the general product"]})
    return {"path": str(destination), "status": "sanitized-candidate", "files": list(files)}


def proposal_candidate(root, package, product_root, product_remote, destination):
    """Prepare a reviewable product candidate using the common delivery route."""
    import tempfile
    import delivery
    product_root, destination = Path(product_root), Path(destination)
    require((product_root / "template/company/config.yaml").is_file(), "target is not a template product")
    with tempfile.TemporaryDirectory(prefix="sanitized-method-") as temporary:
        sanitized = Path(temporary) / "package"
        approved = proposal(root, package, sanitized)
        prepared = delivery.prepare(product_root, product_remote, "main", destination)
        for relative in approved["files"]:
            target = path(destination / "template", relative)
            require(not target.exists(), "proposal replacement needs maintainer review; existing method preserved")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path(sanitized, relative), target)
        require(all(f.startswith("standards/") for f in approved["files"]), "automatic navigation supports new standards only; other packages need explicit consumer wiring")
        index = destination / "template/standards/README.md"
        text = index.read_text() if index.exists() else "# Карта стандартов\n\n"
        for relative in approved["files"]:
            text += f"- [{Path(relative).stem}]({Path(relative).name}) — proposal, maintainer acceptance pending\n"
        index.write_text(text)
        validation.repository(destination / "template")
        return {**prepared, "status": "reviewable-product-candidate", "paths": ["template/" + f for f in approved["files"]] + ["template/standards/README.md"], "acceptance": "proposal only; general release requires maintainer review"}
