"""Preserve directory link types when Python copytree creates Windows fixtures."""
import os
from pathlib import Path


def directory_projections(root):
    if os.name != 'nt':
        return
    for directory in ['.agents/skills', '.claude/skills']:
        for file in (Path(root) / directory).glob('*'):
            if file.is_symlink():
                target = file.readlink()
                file.unlink()
                file.symlink_to(target, target_is_directory=True)
