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


def execute_with_current_tokens_for_fixture(root, task_id, artifact, critical, independent_review=None):
    """Sequential fixture convenience only; never use to retry real caller intent."""
    from core import load, task_file
    import operations
    revision = load(task_file(root, task_id))["revision"]
    return operations.execute(root, task_id, artifact, critical, independent_review,
                              expected_revision=revision)


def observe_with_current_tokens_for_fixture(root, task_id, layer, evidence, actor):
    """Sequential fixture convenience; stale-intent tests must pass saved tokens."""
    from core import load, task_file
    import operations
    task = load(task_file(root, task_id))
    return operations.observe(root, task_id, layer, evidence, actor,
                              expected_revision=task["revision"],
                              expected_result_sha256=task["output"]["sha256"])
