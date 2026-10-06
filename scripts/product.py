#!/usr/bin/env python3
"""Thin product entry; shared operations have one source in template/scripts."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "template/scripts"))
from core import digest, require
from delivery import clean, git, preflight
from lifecycle import release_isolated
from validation import repository


def product_preflight(product):
    def authorized(root, remote):
        require(root == Path(product).resolve() and (root / "template/company/config.yaml").is_file(), "product scope required")
        require(remote == "https://github.com/ArtemArzi/company-work-system.git", "product remote is outside the owner's approved repository")
    return preflight(product, authorize=authorized)


def release(product, destination):
    product, destination = Path(product), Path(destination)
    clean(product)
    repository(product / "template")
    sha = git(product, "subtree", "split", "--prefix=template")
    if not destination.exists():
        git(product, "init", "--bare", str(destination))
        git(destination, "symbolic-ref", "HEAD", "refs/heads/main")
    else:
        release_isolated(destination)
    git(product, "push", str(destination), f"{sha}:refs/heads/main")
    release_isolated(destination)
    return sha


def package(release_repo, destination):
    """Export the checked release-only history for transfer to another computer."""
    release_repo, destination = Path(release_repo).resolve(), Path(destination).absolute()
    release_isolated(release_repo)
    require(not destination.exists() and not destination.is_symlink(), "bundle destination already exists; no overwrite")
    require(not destination.resolve().is_relative_to(release_repo), "bundle must be outside release repository")
    destination.parent.mkdir(parents=True, exist_ok=True)
    git(release_repo, "bundle", "create", str(destination), "--all")
    git(release_repo, "bundle", "verify", str(destination))
    return {"release_sha": git(release_repo, "rev-parse", "refs/heads/main"), "bundle": str(destination), "bundle_sha256": digest(destination)}


if __name__ == "__main__":
    if sys.argv[1:] == ["preflight"]:
        try:
            result = product_preflight(ROOT)
        except Exception as exc:
            result = {"status": "blocked", "reason": str(exc), "fresh": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["status"] == "ready" else 2)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--bundle", type=Path, help="New portable bundle of the checked release-only history; never overwrites")
    args = parser.parse_args()
    sha = release(ROOT, args.destination.resolve())
    print(json.dumps(package(args.destination, args.bundle), ensure_ascii=False, indent=2) if args.bundle else sha)
