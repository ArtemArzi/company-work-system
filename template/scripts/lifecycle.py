"""Company creation, evolution and recovery; deliveries use delivery.py."""
from __future__ import annotations
import json
import ast
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
from core import config, digest, ident, load, lock, now, object_hash, path, require, tasks, write
from delivery import clean, commit, git, git_environment, identity, prepare, remote_allowed
import validation


def release_isolated(release):
    release = Path(release)
    require(git(release, "rev-parse", "--is-bare-repository") == "true", "release-only bare repository required")
    from history import release_policy, scan
    return scan(release, release_policy(), all_refs=True)



def create(release, destination, company_id, owner):
    checked = release_isolated(release)
    expected_release = checked["ref_tips"].get("refs/heads/main")
    require(expected_release, "release lacks checked main")
    destination = Path(destination).resolve()
    require(not destination.exists(), "existing company destination; no overwrite")
    ident(company_id)
    require(company_id != "unconfigured" and owner.strip(), "company identity/owner required")
    git(Path(release).parent, "clone", "--no-local", "--single-branch", "--branch", "main", str(Path(release).resolve()), str(destination))
    identity(destination)
    base = git(destination, "rev-parse", "HEAD")
    require(base == expected_release, "release changed after validation; preserve unadapted candidate")
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
    checked = release_isolated(release)
    expected_release = checked["ref_tips"].get("refs/heads/main")
    require(expected_release, "release lacks checked main")
    clean(root)
    candidate = Path(destination).resolve()
    require(not candidate.exists(), "update candidate already exists")
    git(root, "clone", "--no-local", str(Path(root).resolve()), str(candidate))
    identity(candidate)
    previous_head = git(candidate, "rev-parse", "HEAD")
    git(candidate, "fetch", str(Path(release).resolve()), "main")
    target = git(candidate, "rev-parse", "FETCH_HEAD")
    require(target == expected_release, "release changed after validation; preserve unmerged candidate")
    require(git(candidate, "merge-base", "--is-ancestor", metadata["installed"], target, check=False).returncode == 0, "release lacks installed ancestry")
    if target == metadata["installed"]:
        return {"status": "already-installed", "candidate": str(candidate), "target": target}
    require(git(candidate, "merge-base", "--is-ancestor", metadata["installed"], previous_head, check=False).returncode == 0, "company lost template ancestry")
    protected = {p.relative_to(candidate).as_posix(): digest(p) for directory in ["company", "work"] for p in Path(candidate, directory).rglob("*") if p.is_file()}
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
            if changed_bindings(candidate, t["bindings"], t.get("binding_scope")):
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
        require(t["status"] not in {"active", "waiting", "blocked"} or not changed_bindings(candidate, t["bindings"], t.get("binding_scope")), "active task must explicitly reconcile methods")
    version = load(path(candidate, "release.yaml"))["version"]
    metadata = load(path(candidate, ".system/base.json"))
    if metadata["installed"] != target:
        metadata.update(installed=target, version=version, previous=metadata["installed"])
    else:
        # A prior attempt may have written metadata before commit failed.
        require(metadata.get("previous") and metadata["previous"] != target, "interrupted update lacks previous ancestry")
        metadata["version"] = version
    write(path(candidate, ".system/base.json"), metadata)
    git(candidate, "add", "--", ".system/base.json")
    git(candidate, "commit", "-m", f"Apply verified template {version}; preserve local state")
    return {"status": "verified-candidate", "candidate": str(candidate), "target": target, "company_base": expected_company, "sha256": git(candidate, "rev-parse", "HEAD"), "delivery": "pending"}


def backup(root, destination):
    clean(root)
    validation.repository(root, freshness=False)
    destination = Path(destination).resolve()
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
    """Read legacy schema1 archives without trusting tar extraction or checkout EOL."""
    import platform_runtime as platform
    backup_path, destination = Path(backup_path).resolve(), Path(destination).resolve()
    require(not destination.exists(), "restore cannot overwrite existing company")
    manifest = load(backup_path / "manifest.json")
    require(manifest.get("schema_version") == 1 and isinstance(manifest.get("files"), dict), "unsupported backup manifest")
    require(digest(backup_path / "history.bundle") == manifest["bundle_sha256"] and digest(backup_path / "files.tar") == manifest["tar_sha256"], "backup corrupted")
    files = manifest["files"]
    try: platform.path_collisions(files)
    except ValueError as exc: require(False, str(exc))
    require(not any(p.split('/')[0] in {'.git', '.local', '.venv'} for p in files), "backup contains control/runtime paths")
    # Validate complete tar inventory before any destination writes.
    with tarfile.open(backup_path / "files.tar", "r") as archive:
        members = archive.getmembers()
        require(len(members) == len(files) and len({m.name for m in members}) == len(members) and {m.name for m in members} == set(files), "backup archive inventory differs")
        for member in members:
            item = files[member.name]
            if "symlink" in item:
                require(set(item) == {"symlink"} and member.issym() and member.linkname == item["symlink"], "backup projection differs")
            else:
                require(set(item) == {"sha256"} and member.isfile(), "backup file must be regular")
                with archive.extractfile(member) as stream:
                    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                require(checksum == item['sha256'], "backup archived bytes differ")
    git(backup_path, "clone", "--no-checkout", "-c", "core.autocrlf=false", str(backup_path / "history.bundle"), str(destination))
    require(git(destination, "rev-parse", "HEAD") == manifest["head"], "restore version differs")
    inventory = {}
    for row in git(destination, 'ls-tree', '-r', '-z', 'HEAD', binary=True).split(b'\x00'):
        if row:
            header, relative = row.split(b'\t', 1)
            mode, kind, oid = header.decode('ascii').split()
            inventory[relative.decode('utf-8')] = (mode, kind, oid)
    require(set(inventory) == set(files), "backup manifest differs from tracked Git paths")
    projections = {}
    for relative, (mode, kind, oid) in inventory.items():
        require(kind == 'blob' and mode in {'100644', '100755', '120000'}, "backup mode unsupported")
        item = files[relative]
        if mode == '120000':
            import re
            match = re.fullmatch(r'\.(?:agents|claude)/skills/([a-z][a-z0-9-]{1,63})', relative)
            require(match is not None, "backup noncanonical projection")
            expected = '../../skills/' + match[1]
            raw = git(destination, 'cat-file', 'blob', oid, binary=True)
            require(raw == expected.encode() and 'skills/' + match[1] + '/SKILL.md' in files, "backup projection target changed")
            require(item == {'symlink': expected} or item == {'sha256': hashlib.sha256(raw).hexdigest()}, "backup placeholder changed")
            projections[relative] = (match[1], expected)
        else:
            require('symlink' not in item, "backup regular mode mismatch")
    git(destination, 'read-tree', 'HEAD')
    # Rehydrate exact tracked bytes only, no extractall or archive permissions.
    with tarfile.open(backup_path / "files.tar", "r") as archive:
        for relative, item in files.items():
            if relative in projections:
                continue
            target = path(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(relative) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, 65536)
            if inventory[relative][0] == '100755':
                target.chmod(target.stat().st_mode | 0o111)
            require(digest(target) == item['sha256'], "restored file differs")
    for relative, (name, expected) in projections.items():
        file = destination / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        if __import__('os').name == 'nt':
            file.write_bytes(expected.encode())
            git(destination, 'config', 'core.symlinks', 'false')
        else:
            file.symlink_to(expected, target_is_directory=True)
        platform.projection(destination, relative.rsplit('/', 1)[0], name, allow_absent=False)
    validation.repository(destination, freshness=False)
    return {"path": str(destination), "status": "restored-and-verified", "head": manifest["head"], "boundary": "exact tracked bytes; native settings/trust may require re-projection at the new root; restored old runtime keeps its original OS limitations"}


def rollback(root, destination):
    clean(root)
    metadata = load(path(root, ".system/base.json"))
    require(metadata.get("previous"), "no previous installed method version")
    candidate = Path(destination).resolve()
    require(not candidate.exists(), "rollback candidate exists")
    git(root, "clone", "--no-local", str(Path(root).resolve()), str(candidate))
    identity(candidate)
    current, previous = metadata["installed"], metadata["previous"]
    protected = {p.relative_to(candidate).as_posix(): digest(p) for d in ["company", "work"] for p in Path(candidate, d).rglob("*") if p.is_file()}
    delta = git(candidate, "diff", "--binary", current, previous, "--", "standards", "skills", "workflows", "scripts", "adapters", "hooks", "release.yaml", "README.md", "AGENTS.md", ".agents", ".claude/skills", ".gitignore", "requirements.txt", ".gitattributes", "docs/company-system-guide.html", check=False)
    require(delta.returncode == 0, "rollback diff unavailable")
    patch = subprocess.run(["git", "-C", str(candidate), "apply", "--3way", "--index"], input=delta.stdout, text=True, encoding="utf-8", capture_output=True, env=git_environment())
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
    config_sha = digest(path(root, "company/config.yaml"))
    require(config(root, True) == cfg, "company configuration changed before proposal")
    package, destination = Path(package).resolve(), Path(destination).resolve()
    approval_raw = (package / "approval.json").read_bytes()
    approval = json.loads(approval_raw)
    require(approval.get("approved_by") == cfg["owner"] and approval.get("permission") == "share-sanitized-method" and approval.get("purpose"), "proposal sharing permission missing")
    require(not destination.exists(), "proposal destination exists")
    files = approval.get("files", {})
    require(files, "empty proposal")
    import history
    hook_files = {"hooks/manifest.yaml", "hooks/README.md", "scripts/hooks.py"}
    contents = {}
    policy = history.Policy("sanitized-proposal", files=tuple(files))
    for relative, sha in files.items():
        require(relative.startswith(("standards/", "skills/", "workflows/", "checks/")) or relative in hook_files, "proposal allowlist excludes company data/history")
        file = path(package, relative)
        require(file.is_file(), "proposal approved file missing")
        raw = file.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == sha, "proposal approval does not match exact file")
        contents[relative] = raw
        policy.permits(relative, "100644")
        policy.content(relative, [raw])
        text = raw.decode()
        require(not any(marker in text for marker in ["PRIVATE KEY", "Bearer ", cfg["id"]]), "proposal contains company/private identifiers")
        if relative == "scripts/hooks.py":
            # Parse donated code, never import/execute it. These routing literals
            # are part of the shared runtime, not company file contents.
            try:
                ast.parse(text)
            except SyntaxError:
                require(False, "proposal Python syntax invalid")
        else:
            require(not any(marker in text for marker in ["api_key", "company/config", "work/"]) or relative in {"hooks/manifest.yaml", "hooks/README.md"}, "proposal contains company/private identifiers")
    require(digest(path(root, "company/config.yaml")) == config_sha and config(root, True) == cfg, "company configuration/authorization changed before proposal export")
    require((package / "approval.json").read_bytes() == approval_raw, "proposal approval changed before export")
    destination.mkdir(parents=True)
    for relative in files:
        target = path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[relative])
    write(destination / "proposal.json", {"schema_version": 1, "purpose": approval["purpose"], "files": files, "status": "sanitized-candidate", "limitations": ["Automated scan cannot prove anonymity; owner approval covers exact authored files", "Not accepted into the general product"]})
    return {"path": str(destination), "status": "sanitized-candidate", "files": list(files)}


def proposal_candidate(root, package, product_root, product_remote, destination):
    """Prepare a reviewable product candidate using the common delivery route."""
    import tempfile
    import delivery
    product_root, destination = Path(product_root).resolve(), Path(destination).resolve()
    require((product_root / "template/company/config.yaml").is_file(), "target is not a template product")
    with tempfile.TemporaryDirectory(prefix="sanitized-method-") as temporary:
        sanitized = Path(temporary) / "package"
        approved = proposal(root, package, sanitized)
        prepared = delivery.prepare(product_root, product_remote, "main", destination)
        replacements = {}
        hook_files = {"hooks/manifest.yaml", "hooks/README.md", "scripts/hooks.py"}
        for relative in approved["files"]:
            target = path(destination / "template", relative)
            if target.exists():
                require(relative in hook_files, "proposal replacement needs maintainer review; existing method preserved")
                replacements["template/" + relative] = {"base_sha256": digest(target), "candidate_sha256": digest(path(sanitized, relative))}
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path(sanitized, relative), target)
        supported = all(f.startswith("standards/") or f in hook_files or f.startswith("checks/") for f in approved["files"])
        require(supported, "automatic navigation supports standards/hooks/checks only; other packages need explicit consumer wiring")
        changed = ["template/" + f for f in approved["files"]]
        standards = [f for f in approved["files"] if f.startswith("standards/")]
        if standards:
            index = destination / "template/standards/README.md"
            text = index.read_text(encoding="utf-8") if index.exists() else "# Карта стандартов\n\n"
            for relative in standards:
                text += f"- [{Path(relative).stem}]({Path(relative).name}) — proposal, maintainer acceptance pending\n"
            index.write_text(text, encoding="utf-8")
            changed.append("template/standards/README.md")
        # This process uses its trusted validator/handler allowlist, not donated
        # scripts. New executable contracts still require maintainer review.
        validation.repository(destination / "template")
        return {**prepared, "status": "reviewable-product-candidate", "paths": changed, "replacements": replacements,
                "acceptance": "proposal only; replacements/code/tests need maintainer review; donated code was not executed"}
