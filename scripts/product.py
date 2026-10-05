#!/usr/bin/env python3
"""Release packaging only. Company operations have one source in template/scripts."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "template/scripts"))
from core import require
from delivery import clean, git
from lifecycle import release_isolated
from validation import repository


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(release(ROOT, args.destination.resolve()))
