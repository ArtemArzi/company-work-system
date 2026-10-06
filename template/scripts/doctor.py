"""Read-only environment diagnosis; usable before PyYAML is installed."""
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from platform_runtime import path_collisions, projection


def diagnose(root):
    root = Path(root).resolve()
    blockers = []
    if sys.version_info < (3, 12):
        blockers.append("Python3.12+ required; run project setup")
    try:
        yaml_version = importlib.metadata.version("PyYAML")
        if yaml_version != "6.0.1": blockers.append("PyYAML6.0.1 required; run project setup")
    except importlib.metadata.PackageNotFoundError:
        yaml_version = None
        blockers.append("PyYAML missing; run project setup")
    git_version = None
    if not shutil.which("git"):
        blockers.append("Git2.43+ missing; install the OS Git package within actual rights")
    else:
        value = subprocess.run(["git", "--version"], capture_output=True, encoding="utf-8", errors="replace", timeout=10)
        git_version = value.stdout.strip()
        import re
        match = re.search(r"(\d+)\.(\d+)", git_version)
        if value.returncode or not match or tuple(map(int, match.groups())) < (2, 43):
            blockers.append("Git2.43+ required")
    files = []
    representations = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {".git", ".local", ".venv", ".system", ".agents", ".claude"}]
        files.extend((Path(directory) / name).relative_to(root).as_posix() for name in names)
    try: path_collisions(files)
    except ValueError as exc: blockers.append(str(exc))
    attributes = {}
    if git_version and (root / '.git').exists():
        protected = [p for p in files if p.startswith(('company/', 'work/', '.system/', '.codex/', '.claude/'))]
        protected.extend(p.relative_to(root).as_posix() for folder in ['.system', '.codex', '.claude'] for p in (root / folder).glob('*.json') if p.is_file())
        if protected:
            checked = subprocess.run(['git', '-C', str(root), 'check-attr', '-z', '--stdin', 'text', 'eol', 'filter'],
                                     input=('\x00'.join(protected) + '\x00').encode(), capture_output=True, timeout=10)
            if checked.returncode: blockers.append('Git attribute inspection failed')
            else:
                rows = checked.stdout.decode('utf-8').rstrip('\x00').split('\x00')
                for index in range(0, len(rows), 3):
                    relative, attribute, value = rows[index:index + 3]
                    attributes.setdefault(relative, {})[attribute] = value
                    if (attribute == 'text' and value not in {'unset', 'unspecified'} or
                        attribute == 'eol' and value != 'unspecified' or
                        attribute == 'filter' and value not in {'unset', 'unspecified'}):
                        blockers.append('Byte-preserving Git attributes overridden: ' + relative + ' ' + attribute)
                autocrlf = subprocess.run(['git', '-C', str(root), 'config', '--get', 'core.autocrlf'], capture_output=True, timeout=10)
                if autocrlf.stdout.strip() in {b'true', b'input'} and any(a.get('text') == 'unspecified' for a in attributes.values()):
                    blockers.append('Unprotected business bytes with core.autocrlf; preserve existing bytes before adaptation')
    for skill in (root / "skills").glob("*/SKILL.md"):
        for directory in [".agents/skills", ".claude/skills"]:
            try: representations[directory + "/" + skill.parent.name] = projection(root, directory, skill.parent.name)
            except ValueError as exc: blockers.append(str(exc))
    environment = "wsl2" if "microsoft" in platform.release().lower() else {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
    if environment == "wsl2" and "wsl2" not in platform.release().lower():
        blockers.append("WSL version unconfirmed; WSL2 required, WSL1 unsupported")
    return {"status": "blocked" if blockers else "ready", "root": str(root), "platform": environment,
            "python": platform.python_version(), "interpreter": sys.executable, "git": git_version, "pyyaml": yaml_version,
            "projections": representations, "git_attributes": attributes, "blockers": blockers,
            "next_action": "Resolve listed blockers; shared preflight before independent work" if blockers else "Read AGENTS.md and company map; shared preflight before independent work",
            "boundary": "Read-only local prerequisites; no sync, installation, provider login, native discovery/hooks or business proof",
            "durability": "atomic file replacement + file fsync; directory power-loss durability not claimed" if os.name == "nt" else "file and parent-directory fsync",
            "shared_checkout": "Do not write one checkout from Windows and WSL simultaneously; separate clones share Git"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = diagnose(args.root)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(2 if result["blockers"] else 0)
