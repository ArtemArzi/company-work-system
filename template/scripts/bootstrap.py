#!/usr/bin/env python3
"""Project-owned runtime preparation; only the explicit setup command installs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

sys.path.insert(0, str(Path(__file__).absolute().parent))
from platform_runtime import alias, atomic_text, process_lock


class BootstrapError(RuntimeError):
    pass


def manifest(root: Path) -> dict[str, str | list[str]]:
    result = {}
    for line in (root / 'scripts/bootstrap.lock').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        key, *values = line.split('\t')
        if key in result or not values:
            raise BootstrapError('Invalid bootstrap.lock')
        result[key] = values[0] if len(values) == 1 else values
    return result


def requirements(root: Path) -> dict[str, str]:
    """The existing requirements.txt is the only dependency/pin source."""
    result = {}
    for line in (root / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([0-9][A-Za-z0-9_.+!-]*)', line)
        if not match or match[1].casefold() in result:
            raise BootstrapError('requirements.txt must contain unique exact package pins')
        result[match[1].casefold()] = match[2]
    if 'pyyaml' not in result:
        raise BootstrapError('requirements.txt is missing required PyYAML pin')
    return result


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def guard(path: Path, root: Path) -> None:
    """Normalize OS ancestors, then reject aliases in the company's own components."""
    relative = path.relative_to(root)
    root = root.resolve()
    path = root / relative
    for item in (root, *reversed(path.parents), path):
        if (item == root or root in item.parents) and alias(item):
            raise BootstrapError(f'Path alias is forbidden: {item}')



def environment(root: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('UV_', 'PYTHON', 'PIP_'))
           and k.upper() not in {'VIRTUAL_ENV', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV',
                                 '__PYVENV_LAUNCHER__'}}
    runtime = root / '.local/runtime'
    env.update(UV_PYTHON_INSTALL_DIR=str(runtime / 'python'),
               UV_CACHE_DIR=str(runtime / 'cache'),
               UV_PYTHON_BIN_DIR=str(runtime / 'bin'), UV_NO_CONFIG='1',
               UV_NO_ENV_FILE='1', UV_HTTP_TIMEOUT='60', UV_HTTP_RETRIES='0',
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return env


def run(root: Path, args: list[str], *, timeout: int = 240) -> str:
    try:
        value = subprocess.run(args, cwd=root, env=environment(root), capture_output=True,
                               timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BootstrapError(f'Runtime command failed or timed out: {args[0]}: {error}') from error
    if value.returncode:
        detail = value.stderr[-4096:].decode('utf-8', errors='replace').strip()
        raise BootstrapError(f'Runtime command exit {value.returncode}: {detail}')
    return value.stdout.decode('utf-8').strip()


def write_json(path: Path, data: dict) -> None:
    atomic_text(path, json.dumps(data, sort_keys=True) + '\n')


def owner(root: Path) -> dict:
    return {'schema': 1, 'root': str(root), 'manifest': digest(root / 'scripts/bootstrap.lock'),
            'requirements': digest(root / 'requirements.txt')}


def check_owner(root: Path, *, create: bool = False) -> dict:
    runtime = root / '.local/runtime'
    state = runtime / 'environment.json'
    for path in (runtime, state, root / '.venv'):
        guard(path, root)
    expected = owner(root)
    if state.exists():
        try:
            current = json.loads(state.read_text(encoding='utf-8'))
        except (ValueError, OSError) as error:
            raise BootstrapError('Invalid environment ownership; preserve .venv and inspect it') from error
        if not isinstance(current, dict) or any(current.get(k) != v for k, v in expected.items()):
            raise BootstrapError('Foreign, relocated or outdated .venv; preserve it and set up a fresh checkout')
        return current
    if (root / '.venv').exists():
        raise BootstrapError('Foreign .venv without ownership; preserve it and use a fresh checkout')
    if not create:
        raise BootstrapError('Environment is not ready; run scripts/run.sh setup or scripts/run.ps1 setup')
    current = {**expected, 'status': 'preparing'}
    write_json(state, current)
    return current


def interpreter(root: Path) -> Path:
    return root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def owned_interpreter(root: Path) -> Path:
    python = interpreter(root)
    managed = root / '.local/runtime/python'
    guard(managed, root)
    guard(python.parent, root)
    if not python.is_file():
        raise BootstrapError('Owned interpreter is missing; rerun explicit setup')
    try:
        python.resolve().relative_to(managed.resolve() if os.name != 'nt' else root / '.venv')
        config = root / '.venv/pyvenv.cfg'
        guard(config, root)
        entries = {key.strip(): value.strip() for line in config.read_text(encoding='utf-8').splitlines()
                   if '=' in line for key, value in [line.split('=', 1)]}
        Path(entries['home'].strip()).resolve().relative_to(managed.resolve())
        if entries.get('include-system-site-packages') != 'false':
            raise ValueError('System site packages are forbidden')
    except (ValueError, KeyError, OSError) as error:
        raise BootstrapError('Interpreter points outside the owned runtime') from error
    return python


def validate(root: Path, state: dict) -> dict:
    if state.get('status') != 'ready':
        raise BootstrapError('Setup was interrupted; rerun explicit setup')
    python = owned_interpreter(root)
    managed = root / '.local/runtime/python'
    probe = run(root, [str(python), '-I', '-c',
                      'import json,sys,yaml; print(json.dumps({"prefix":sys.prefix,'
                      '"base":sys.base_prefix,"version":list(sys.version_info[:3]),'
                      '"yaml":yaml.__version__}))'], timeout=30)
    try:
        result = json.loads(probe)
        if Path(result['prefix']).resolve() != (root / '.venv').resolve():
            raise ValueError('Venv prefix differs')
        Path(result['base']).resolve().relative_to(managed.resolve())
        data = manifest(root)
        if (result['version'] != [int(part) for part in str(data['PYTHON_REQUEST']).split('.')]
                or result['version'][:2] < [int(part) for part in str(data['MIN_PYTHON']).split('.')]
                or result['yaml'] != requirements(root)['pyyaml']):
            raise ValueError('Runtime version differs')
    except (ValueError, KeyError, TypeError) as error:
        raise BootstrapError('Owned runtime validation failed; rerun explicit setup') from error
    return {'status': 'ready', 'python': str(python), 'managed_python': result['base'],
            'python_version': result['version'], 'pyyaml': result['yaml']}


def git_check(root: Path) -> str:
    git = shutil.which('git')
    if not git:
        raise BootstrapError('Git 2.43+ is required. Agent: use the native OS installer within your '
                             'existing rights (https://git-scm.com/downloads); privileged or '
                             'interactive installation requires a separate permitted step.')
    value = run(root, [git, '--version'], timeout=15)
    try:
        parts = value.split()[2].split('.')
        version = tuple(int(p) for p in parts[:2])
    except (ValueError, IndexError) as error:
        raise BootstrapError(f'Cannot identify Git version: {value}') from error
    if version < (2, 43):
        raise BootstrapError(f'Git 2.43+ required; found {value}. Use the native OS installer.')
    return value


def verified_uv(root: Path, data: dict) -> Path:
    runtime = root / '.local/runtime'
    system = {'Linux': 'linux', 'Darwin': 'macos', 'Windows': 'windows'}.get(platform.system())
    machine = platform.machine().lower()
    arch = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}.get(machine)
    key = f'{system}-{arch}'
    if key not in data:
        raise BootstrapError('Unsupported OS/architecture')
    name, expected, entry = data[key]
    archive, uv = runtime / name, runtime / ('uv.exe' if os.name == 'nt' else 'uv')
    for path in (archive, uv):
        guard(path, root)
    if not archive.is_file() or archive.stat().st_size > int(data['MAX_ARCHIVE_BYTES']) or digest(archive) != expected:
        raise BootstrapError('Pinned uv archive missing or SHA256 mismatch; use the launcher setup')
    if not uv.is_file() or uv.stat().st_size > int(data['MAX_EXECUTABLE_BYTES']):
        raise BootstrapError('Pinned uv executable missing or oversized; use the launcher setup')
    try:
        if name.endswith('.zip'):
            with zipfile.ZipFile(archive) as package:
                matches = [item for item in package.infolist() if item.filename == entry]
                if len(matches) != 1 or matches[0].file_size > int(data['MAX_EXECUTABLE_BYTES']):
                    raise ValueError('Invalid uv archive entry')
                contents = package.read(matches[0])
        else:
            with tarfile.open(archive, 'r:gz') as package:
                matches = [item for item in package.getmembers() if item.name == entry]
                if len(matches) != 1 or not matches[0].isreg() or matches[0].size > int(data['MAX_EXECUTABLE_BYTES']):
                    raise ValueError('Invalid uv archive entry')
                with package.extractfile(matches[0]) as stream:
                    contents = stream.read()
    except (ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise BootstrapError('Cannot verify pinned uv archive executable') from error
    if digest(uv) != hashlib.sha256(contents).hexdigest():
        raise BootstrapError('uv executable differs from pinned archive')
    return uv


def setup(root: Path) -> dict:
    data = manifest(root)
    requirements(root)
    git = git_check(root)
    runtime = root / '.local/runtime'
    guard(runtime, root)
    if not runtime.is_dir() or not (runtime / 'owner').is_file():
        raise BootstrapError('Use the shell launcher for the first explicit setup')
    if Path((runtime / 'owner').read_text(encoding='utf-8').strip()).resolve() != root.resolve():
        raise BootstrapError('Foreign or relocated runtime; use a fresh checkout')
    uv = verified_uv(root, data)
    guard(runtime / "setup.lock", root)
    with process_lock(runtime / "setup.lock"):
        state = check_owner(root, create=True)
        if state.get('status') == 'ready':
            try:
                return {**validate(root, state), 'git': git, 'changed': False}
            except BootstrapError:
                # Explicit setup may repair missing dependencies in an already owned venv.
                state['status'] = 'preparing'
                write_json(runtime / 'environment.json', state)
        python = run(root, [str(uv), '--no-config', 'python', 'find', '--offline', '--system',
                            '--managed-python', '--no-project', '--no-python-downloads',
                            str(data['PYTHON_REQUEST'])], timeout=30)
        try:
            Path(python).resolve().relative_to((runtime / 'python').resolve())
        except ValueError as error:
            raise BootstrapError('uv selected Python outside the owned installation') from error
        if not interpreter(root).is_file():
            # Resume only the directory reserved by our ownership state; never clear it.
            for directory, dirs, files in os.walk(root / '.venv', followlinks=False):
                for name in dirs + files:
                    guard(Path(directory) / name, root)
            run(root, [str(uv), '--no-config', 'venv', '--allow-existing', '--no-project', '--no-python-downloads',
                       '--python', python, str(root / '.venv')])
        owned_interpreter(root)
        run(root, [str(uv), '--no-config', 'pip', 'install', '--python', str(interpreter(root)),
                   '--no-python-downloads', '--default-index', str(data['PYPI_INDEX']),
                   '--keyring-provider', 'disabled', '--only-binary', ':all:', '--no-deps',
                   '--requirements', str(root / 'requirements.txt')])
        state['status'] = 'ready'
        result = validate(root, state)
        write_json(runtime / 'environment.json', state)
        return {**result, 'git': git, 'changed': True}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent.parent
    try:
        guard(root, root)
        if args == ['setup']:
            print(json.dumps(setup(root), ensure_ascii=False))
            return 0
        state = check_owner(root)
        ready = validate(root, state)
        if args == ['bootstrap-status']:
            print(json.dumps(ready, ensure_ascii=False))
            return 0
        if args and args[0] == 'python':
            command = [ready['python'], *args[1:]]
        else:
            command = [ready['python'], str(root / 'scripts/system.py'), '--root', str(root), *args]
        return subprocess.call(command, cwd=root, env=environment(root))
    except (BootstrapError, OSError) as error:
        print(f'bootstrap blocked: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
