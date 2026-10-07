"""Bootstrap negatives plus an explicit optional real managed-Python installation."""
from __future__ import annotations
import hashlib
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import io
import tarfile
import tempfile
import unittest
from unittest.mock import patch

PRODUCT = Path(__file__).resolve().parents[1]
SCRIPTS = PRODUCT / 'template/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('cws_bootstrap', SCRIPTS / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cws-bootstrap-')
        self.root = Path(self.temp.name).resolve() / 'company with spaces'
        (self.root / 'scripts').mkdir(parents=True)
        for name in ('bootstrap.py', 'bootstrap.lock', 'run.sh', 'run.ps1', 'platform_runtime.py'):
            shutil.copy2(SCRIPTS / name, self.root / 'scripts' / name)
        (self.root / 'requirements.txt').write_text('PyYAML==6.0.1\n', encoding='utf-8')
        (self.root / 'scripts/system.py').write_text('import json,sys;print(json.dumps(sys.argv[1:]))\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def reserve(self):
        runtime = self.root / '.local/runtime'
        runtime.mkdir(parents=True)
        (runtime / 'owner').write_text(str(self.root) + '\n', encoding='utf-8')
        (runtime / ('uv.exe' if os.name == 'nt' else 'uv')).write_bytes(b'fake uv for mocked calls')
        return runtime

    def wrapper(self, args, env=None):
        command = (['powershell.exe', '-NoProfile', '-File', str(self.root / 'scripts/run.ps1')]
                   if os.name == 'nt' else ['/bin/sh', str(self.root / 'scripts/run.sh')])
        return subprocess.run(command + args, cwd=self.root, env=env, capture_output=True, timeout=360)

    @unittest.skipUnless(shutil.which('powershell.exe'), 'native Windows PowerShell unavailable')
    def test_native_powershell_owned_process_forwards_utf8_and_exit_without_python(self):
        # Read the exact production function; use native PowerShell/cmd only.
        # This also runs from WSL where Windows has no installed Python.
        shell = shutil.which('powershell.exe')
        source = (self.root / 'scripts/run.ps1').read_text(encoding='utf-8')
        functions = source.split('\ntry {\n', 1)[0]
        prefix = '$ProgressPreference="SilentlyContinue"\n' + functions + '\n$root=$env:SystemRoot; $runtime=$root + "\\unused-runtime"\n'
        def native(script):
            encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
            return subprocess.run([shell, '-NoProfile', '-EncodedCommand', encoded],
                                  capture_output=True, timeout=10)
        raw_stdout = '{"status":"ready","text":"готово"}\n'.encode('utf-8')
        raw_stderr = 'проверка stderr\n'.encode('utf-8')
        child = ('$out=[Convert]::FromBase64String("' + base64.b64encode(raw_stdout).decode() + '");'
                 '$err=[Convert]::FromBase64String("' + base64.b64encode(raw_stderr).decode() + '");'
                 '$o=[Console]::OpenStandardOutput();$o.Write($out,0,$out.Length);$o.Flush();'
                 '$e=[Console]::OpenStandardError();$e.Write($err,0,$err.Length);$e.Flush();exit 7')
        child64 = base64.b64encode(child.encode('utf-16le')).decode()
        script = prefix + (f'$code=OwnedProcess ($env:SystemRoot + "\\System32\\WindowsPowerShell\\v1.0\\powershell.exe") '
                           f'@("-NoProfile","-EncodedCommand","{child64}") 5 $false;exit $code')
        result = native(script)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, raw_stdout)
        self.assertEqual(result.stderr, raw_stderr)
        # The uv-find mode must still return a captured path, without emitting it twice.
        script = prefix + ('$path=OwnedProcess ($env:SystemRoot + "\\System32\\cmd.exe") '
                           '@("/d","/c","echo C:\\managed-python\\python.exe") 5 $true;'
                           '[Console]::Write($path)')
        result = native(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'C:\\managed-python\\python.exe')
        self.assertEqual(result.stderr, b'')
        # Process and stream completion share the same deadline.
        child64 = base64.b64encode('Start-Sleep -Seconds 6'.encode('utf-16le')).decode()
        script = prefix + ('try { [void](OwnedProcess ($env:SystemRoot + "\\System32\\WindowsPowerShell\\v1.0\\powershell.exe") '
                           f'@("-NoProfile","-EncodedCommand","{child64}") 1 $false); exit 0 }} '
                           'catch { [Console]::Error.WriteLine($_.Exception.Message); exit 2 }')
        started = time.monotonic()
        result = native(script)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(b'timed out', result.stderr)
        self.assertLess(time.monotonic() - started, 4)

    def test_manifest_pinned_single_source(self):
        data = bootstrap.manifest(self.root)
        self.assertEqual(data['UV_VERSION'], '0.12.23')
        self.assertEqual(data['PYTHON_REQUEST'], '3.12.15')
        self.assertEqual(bootstrap.requirements(self.root), {'pyyaml': '6.0.1'})
        self.assertNotIn('REQUIREMENT', data)
        for key in ('linux-x86_64', 'linux-aarch64', 'macos-x86_64', 'macos-aarch64', 'windows-x86_64', 'windows-aarch64'):
            self.assertRegex(data[key][1], r'^[0-9a-f]{64}$')

    def test_redirect_environment_cleaned_without_changing_parent(self):
        poisoned = {'UV_CACHE_DIR': '/foreign', 'UV_PYTHON_INSTALL_DIR': '/foreign',
                    'UV_CONFIG_FILE': '/foreign', 'UV_PYTHON_DOWNLOADS_JSON_URL': 'https://invalid',
                    'UV_INDEX': 'https://invalid', 'PIP_TARGET': '/foreign', 'PYTHONHOME': '/foreign',
                    'PYTHONPATH': '/foreign', 'VIRTUAL_ENV': '/foreign', 'CONDA_PREFIX': '/foreign'}
        with patch.dict(os.environ, poisoned):
            before = dict(os.environ)
            child = bootstrap.environment(self.root)
            self.assertEqual(os.environ, before)
            for key in poisoned:
                if key in ('UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR'):
                    self.assertTrue(child[key].startswith(str(self.root)))
                else:
                    self.assertNotIn(key, child)
            self.assertEqual(child['UV_NO_ENV_FILE'], '1')
            self.assertEqual(child['UV_NO_CONFIG'], '1')

    def test_foreign_venv_preserved(self):
        self.reserve()
        (self.root / '.venv').mkdir()
        foreign = self.root / '.venv/precious.txt'
        foreign.write_bytes(b'original')
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'Foreign .venv'):
            bootstrap.check_owner(self.root, create=True)
        self.assertEqual(foreign.read_bytes(), b'original')
        self.assertFalse((self.root / '.local/runtime/environment.json').exists())

    def test_relocated_and_manifest_changed_venv_rejected(self):
        self.reserve()
        state = bootstrap.check_owner(self.root, create=True)
        state['root'] = str(self.root.parent / 'old location')
        bootstrap.write_json(self.root / '.local/runtime/environment.json', state)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'relocated'):
            bootstrap.check_owner(self.root)
        state['root'] = str(self.root)
        state['manifest'] = '0' * 64
        bootstrap.write_json(self.root / '.local/runtime/environment.json', state)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'outdated'):
            bootstrap.check_owner(self.root)

    @unittest.skipIf(os.name == 'nt', 'Windows symlink permission is not required')
    def test_symlink_venv_and_runtime_rejected(self):
        self.reserve()
        external = self.root.parent / 'foreign'
        external.mkdir()
        (self.root / '.venv').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'alias'):
            bootstrap.check_owner(self.root, create=True)
        result = self.wrapper(['bootstrap-status'])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'alias', result.stderr)
        self.assertEqual(list(external.iterdir()), [])

    def test_offline_no_python_ordinary_command_never_downloads(self):
        env = dict(os.environ, UV_PYTHON_DOWNLOADS_JSON_URL='https://must-not-be-used.invalid',
                   UV_CACHE_DIR=str(self.root / 'poisoned-cache'))
        result = self.wrapper(['doctor'], env)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'not ready', result.stderr)
        self.assertFalse((self.root / '.local').exists())
        self.assertFalse((self.root / 'poisoned-cache').exists())

    def test_cached_archive_corruption_rejected_without_execution(self):
        runtime = self.reserve()
        data = bootstrap.manifest(self.root)
        if os.name == 'nt': key = 'windows-x86_64'
        elif sys.platform == 'darwin': key = 'macos-aarch64' if os.uname().machine == 'arm64' else 'macos-x86_64'
        else: key = 'linux-aarch64' if os.uname().machine == 'aarch64' else 'linux-x86_64'
        (runtime / data[key][0]).write_bytes(b'corrupt archive; never execute')
        result = self.wrapper(['setup'])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'SHA256 mismatch', result.stderr)
        self.assertFalse((runtime / 'python').exists())
        self.assertFalse((self.root / '.venv').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX downloader fixture')
    def test_download_failure_and_corruption_leave_no_success_marker(self):
        tools = self.root.parent / 'tools'
        tools.mkdir()
        curl = tools / 'curl'
        curl.write_text('#!/bin/sh\nexit 7\n')
        curl.chmod(0o700)
        env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'])
        result = self.wrapper(['setup'], env)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'download failed/offline', result.stderr)
        runtime = self.root / '.local/runtime'
        self.assertFalse((runtime / 'uv').exists())
        self.assertFalse((runtime / 'environment.json').exists())
        curl.write_text('#!/bin/sh\nfor arg do output=$arg; done\nprintf corrupt > "$output"\n')
        result = self.wrapper(['setup'], env)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'SHA256 mismatch', result.stderr)
        self.assertFalse((runtime / 'uv').exists())
        self.assertFalse((self.root / '.venv').exists())
        self.assertEqual(list(runtime.glob('.fetch-*')), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX ancestor aliases and signals')
    def test_system_ancestor_alias_allowed_but_internal_alias_rejected(self):
        ancestor = self.root.parent / 'os-ancestor-alias'
        ancestor.symlink_to(self.root.parent, target_is_directory=True)
        logical_root = ancestor / self.root.name
        bootstrap.guard(logical_root / 'scripts/bootstrap.lock', logical_root)
        external = self.root.parent / 'external'
        external.mkdir()
        (self.root / '.venv').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'alias'):
            bootstrap.guard(logical_root / '.venv', logical_root)
        result = self.wrapper(['bootstrap-status'])
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'alias', result.stderr)
        self.assertEqual(list(external.iterdir()), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX watchdog regression')
    def test_watchdog_kills_and_reaps_owned_child_ignoring_sigterm(self):
        # Execute the complete launcher with a synthetic pinned uv artifact.
        # The one-second deadline is data in this isolated fixture's manifest.
        runtime = self.root / '.local/runtime'
        runtime.mkdir(parents=True)
        (runtime / 'owner').write_text(str(self.root) + '\n')
        data = bootstrap.manifest(self.root)
        system = 'macos' if sys.platform == 'darwin' else 'linux'
        arch = 'aarch64' if os.uname().machine in ('arm64', 'aarch64') else 'x86_64'
        name, _, entry = data[f'{system}-{arch}']
        pid_file = runtime / 'child.pid'
        late_file = runtime / 'late-write'
        executable = ('#!' + sys.executable + '\nimport os,signal,time\n'
                      'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                      f'open({str(pid_file)!r},"w").write(str(os.getpid()))\n'
                      'time.sleep(3)\n'
                      f'open({str(late_file)!r},"w").write("must never happen")\n'
                      'time.sleep(30)\n').encode()
        archive = runtime / name
        with tarfile.open(archive, 'w:gz') as package:
            item = tarfile.TarInfo(entry); item.mode = 0o700; item.size = len(executable)
            package.addfile(item, io.BytesIO(executable))
        lock = self.root / 'scripts/bootstrap.lock'
        lines = lock.read_text().splitlines()
        for index, line in enumerate(lines):
            if line.startswith('TIMEOUT_SECONDS\t'): lines[index] = 'TIMEOUT_SECONDS\t1'
            if line.startswith(f'{system}-{arch}\t'):
                lines[index] = '\t'.join([f'{system}-{arch}', name, hashlib.sha256(archive.read_bytes()).hexdigest(), entry])
        lock.write_text('\n'.join(lines) + '\n')
        sibling = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            started = time.monotonic()
            result = self.wrapper(['setup'])
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(b'timed out', result.stderr)
            self.assertLess(elapsed, 3.5)
            self.assertIsNone(sibling.poll(), 'unrelated child must remain running')
            pid = int(pid_file.read_text())
            with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
            self.assertFalse(late_file.exists())
            self.assertFalse((runtime / 'environment.json').exists())
        finally:
            sibling.kill(); sibling.wait()

    def test_pyvenv_config_spaces_and_foreign_base(self):
        runtime = self.reserve()
        managed = runtime / 'python/cpython/bin/python'
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b'owned managed interpreter')
        python = bootstrap.interpreter(self.root)
        python.parent.mkdir(parents=True)
        if os.name == 'nt': python.write_bytes(b'owned redirector')
        else: python.symlink_to(managed)
        config = self.root / '.venv/pyvenv.cfg'
        config.write_text('home = ' + str(managed.parent) + '\ninclude-system-site-packages = false\n')
        self.assertEqual(bootstrap.owned_interpreter(self.root), python)
        config.write_text('home = ' + str(self.root.parent / 'foreign') + '\n')
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'outside'):
            bootstrap.owned_interpreter(self.root)

    def test_foreign_interpreter_is_never_given_to_installer(self):
        runtime = self.reserve()
        bootstrap.check_owner(self.root, create=True)
        managed = runtime / 'python/cpython/bin/python'
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b'owned')
        target = bootstrap.interpreter(self.root)
        target.parent.mkdir(parents=True)
        target.write_bytes(b'foreign')
        (self.root / '.venv/pyvenv.cfg').write_text('home = /foreign\n')
        calls = []
        def finder(root, args, **kwargs):
            calls.append(args)
            if 'find' in args: return str(managed)
            self.fail('Foreign interpreter must never reach installer')
        with patch.object(bootstrap, 'git_check', return_value='git version 2.43.0'), patch.object(bootstrap, 'verified_uv', return_value=runtime / ('uv.exe' if os.name == 'nt' else 'uv')), patch.object(bootstrap, 'run', side_effect=finder):
            with self.assertRaisesRegex(bootstrap.BootstrapError, 'outside'):
                bootstrap.setup(self.root)
        self.assertEqual(len(calls), 1)
        self.assertEqual(target.read_bytes(), b'foreign')

    def test_interrupted_partial_setup_resumes_only_owned_environment(self):
        runtime = self.reserve()
        bootstrap.check_owner(self.root, create=True)
        (self.root / '.venv').mkdir()
        managed = runtime / 'python/cpython/bin/python'
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b'managed')
        calls = []
        def fake_run(root, args, **kwargs):
            calls.append(args)
            if 'find' in args: return str(managed)
            if 'venv' in args:
                path = bootstrap.interpreter(root)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'owned')
                (root / '.venv/pyvenv.cfg').write_text('home = ' + str(managed.parent) + '\n')
            return ''
        with patch.object(bootstrap, 'git_check', return_value='git version 2.43.0'), patch.object(bootstrap, 'verified_uv', return_value=runtime / ('uv.exe' if os.name == 'nt' else 'uv')), patch.object(bootstrap, 'run', side_effect=fake_run), patch.object(bootstrap, 'validate', return_value={'status': 'ready'}), patch.object(bootstrap, 'owned_interpreter', return_value=bootstrap.interpreter(self.root)):
            result = bootstrap.setup(self.root)
        self.assertTrue(result['changed'])
        self.assertTrue(any('--allow-existing' in command for command in calls))
        self.assertFalse(any('--clear' in command for command in calls))
        self.assertEqual(json.loads((runtime / 'environment.json').read_text())['status'], 'ready')

    def test_ready_repeat_does_not_reinstall(self):
        runtime = self.reserve()
        state = bootstrap.check_owner(self.root, create=True)
        state['status'] = 'ready'
        bootstrap.write_json(runtime / 'environment.json', state)
        with patch.object(bootstrap, 'git_check', return_value='git version 2.43.0'), patch.object(bootstrap, 'verified_uv', return_value=runtime / ('uv.exe' if os.name == 'nt' else 'uv')), patch.object(bootstrap, 'validate', return_value={'status': 'ready'}), patch.object(bootstrap, 'run') as command:
            result = bootstrap.setup(self.root)
        self.assertFalse(result['changed'])
        command.assert_not_called()

    def test_missing_git_actionable_blocker(self):
        with patch.object(bootstrap.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(bootstrap.BootstrapError, 'native OS installer'):
                bootstrap.git_check(self.root)

    def test_install_failure_keeps_preparing_state(self):
        runtime = self.reserve()
        bootstrap.check_owner(self.root, create=True)
        managed = runtime / 'python/python'
        managed.parent.mkdir()
        managed.write_bytes(b'owned')
        target = bootstrap.interpreter(self.root)
        target.parent.mkdir(parents=True)
        target.write_bytes(b'owned')
        (self.root / '.venv/pyvenv.cfg').write_text('home = ' + str(managed.parent) + '\n')
        def fail_install(root, args, **kwargs):
            if 'find' in args: return str(managed)
            raise bootstrap.BootstrapError('offline')
        with patch.object(bootstrap, 'git_check', return_value='git version 2.43.0'), patch.object(bootstrap, 'verified_uv', return_value=runtime / ('uv.exe' if os.name == 'nt' else 'uv')), patch.object(bootstrap, 'run', side_effect=fail_install), patch.object(bootstrap, 'owned_interpreter', return_value=target):
            with self.assertRaisesRegex(bootstrap.BootstrapError, 'offline'):
                bootstrap.setup(self.root)
        self.assertEqual(json.loads((runtime / 'environment.json').read_text())['status'], 'preparing')

    @unittest.skipUnless(os.environ.get('CWS_BOOTSTRAP_NETWORK') == '1', 'explicit opt-in real network setup')
    def test_real_setup_without_path_python_host_preserved(self):
        home = self.root.parent / 'fake home'
        home.mkdir()
        (home / '.profile').write_bytes(b'preserve profile\n')
        (home / 'uv.toml').write_bytes(b'preserve user configuration\n')
        before = {p.relative_to(home).as_posix(): p.read_bytes() for p in home.rglob('*') if p.is_file()}
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), UV_CACHE_DIR=str(home / 'poisoned-cache'),
                   UV_PYTHON_INSTALL_DIR=str(home / 'poisoned-python'), PIP_TARGET=str(home / 'poisoned-target'),
                   PYTHONHOME=str(home / 'poisoned-home'), VIRTUAL_ENV=str(home / 'poisoned-venv'))
        if os.name == 'nt':
            git_dir = str(Path(shutil.which('git')).parent)
            windows = Path(os.environ['SystemRoot'])
            env['PATH'] = os.pathsep.join([git_dir, str(windows / 'System32'), str(windows),
                                          str(windows / 'System32/WindowsPowerShell/v1.0')])
            self.assertIsNone(shutil.which('python', path=env['PATH']))
            self.assertIsNone(shutil.which('python3', path=env['PATH']))
        else:
            # No PATH python/python3/py/uv: only basic platform tools plus existing Git.
            tools = self.root.parent / 'tools'
            tools.mkdir()
            for name in ('dirname', 'uname', 'awk', 'cat', 'env', 'wc', 'tar', 'gzip', 'mktemp', 'curl', 'sleep', 'rm', 'mv', 'mkdir', 'chmod', 'xcrun', 'sha256sum', 'shasum', 'git'):
                executable = shutil.which(name)
                if executable: (tools / name).symlink_to(executable)
            env['PATH'] = str(tools)
            self.assertFalse((tools / 'python').exists())
            self.assertFalse((tools / 'python3').exists())
        result = self.wrapper(['setup'], env)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        ready = json.loads(result.stdout)
        self.assertTrue(ready['managed_python'].startswith(str(self.root / '.local/runtime/python')))
        self.assertEqual(ready['python_version'][:2], [3, 12])
        self.assertEqual(ready['pyyaml'], '6.0.1')
        # uv --no-project still discovers cwd/.venv. Reproduce that old selection
        # and prove --system selects our managed base while the strict guard stays.
        uv = self.root / '.local/runtime' / ('uv.exe' if os.name == 'nt' else 'uv')
        find = [str(uv), '--no-config', 'python', 'find', '--offline', '--managed-python',
                '--no-project', '--no-python-downloads', '3.12.15']
        legacy = subprocess.run(find, cwd=self.root, env=bootstrap.environment(self.root),
                                capture_output=True, timeout=30)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertEqual(Path(legacy.stdout.decode('utf-8').strip()), bootstrap.interpreter(self.root))
        selected = subprocess.run(find + ['--system'], cwd=self.root,
                                  env=bootstrap.environment(self.root), capture_output=True, timeout=30)
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertTrue(Path(selected.stdout.decode('utf-8').strip()).is_relative_to(self.root / '.local/runtime/python'))
        state = self.root / '.local/runtime/environment.json'
        original = state.read_bytes(); original_mtime = state.stat().st_mtime_ns
        # Block HTTPS in these owned children only, then prove the block against
        # the real package index. No host proxy/profile/network settings change.
        offline = dict(env, HTTPS_PROXY='http://127.0.0.1:1', HTTP_PROXY='http://127.0.0.1:1',
                       ALL_PROXY='http://127.0.0.1:1', https_proxy='http://127.0.0.1:1',
                       http_proxy='http://127.0.0.1:1', all_proxy='http://127.0.0.1:1',
                       NO_PROXY='', no_proxy='')
        curl = shutil.which('curl', path=offline['PATH'])
        self.assertIsNotNone(curl)
        blocked_network = subprocess.run([curl, '--disable', '--fail', '--silent', '--show-error',
                                          '--connect-timeout', '1', '--max-time', '2',
                                          'https://pypi.org/simple'], cwd=self.root, env=offline,
                                         capture_output=True, timeout=5)
        self.assertNotEqual(blocked_network.returncode, 0)
        stable_paths = [uv, uv.parent / bootstrap.manifest(self.root)[
            ('windows' if os.name == 'nt' else 'macos' if sys.platform == 'darwin' else 'linux') + '-' +
            ('aarch64' if bootstrap.platform.machine().lower() in ('aarch64', 'arm64') else 'x86_64')][0],
            Path(ready['managed_python']) / ('python.exe' if os.name == 'nt' else 'bin/python3.12')]
        runtime_before = {str(path): (bootstrap.digest(path), path.stat().st_mtime_ns) for path in stable_paths}
        repeated = self.wrapper(['setup'], offline)
        self.assertEqual(repeated.returncode, 0, repeated.stderr.decode('utf-8', errors='replace'))
        self.assertFalse(json.loads(repeated.stdout)['changed'])
        self.assertEqual(state.read_bytes(), original)
        self.assertEqual(state.stat().st_mtime_ns, original_mtime)
        status = self.wrapper(['bootstrap-status'], offline)
        self.assertEqual(status.returncode, 0, status.stderr.decode('utf-8', errors='replace'))
        routed = self.wrapper(['context'], offline)
        self.assertEqual(routed.returncode, 0, routed.stderr.decode('utf-8', errors='replace'))
        self.assertEqual(json.loads(routed.stdout), ['--root', str(self.root), 'context'])
        doctor = self.wrapper(['doctor'], offline)
        self.assertEqual(doctor.returncode, 0, doctor.stderr.decode('utf-8', errors='replace'))
        self.assertEqual(json.loads(doctor.stdout), ['--root', str(self.root), 'doctor'])
        self.assertEqual({str(path): (bootstrap.digest(path), path.stat().st_mtime_ns) for path in stable_paths}, runtime_before)
        after = {p.relative_to(home).as_posix(): p.read_bytes() for p in home.rglob('*') if p.is_file()}
        self.assertEqual(after, before)
        print(json.dumps({'bootstrap_network': 'pass', 'platform': sys.platform, 'setup_exit': result.returncode,
                          'repeat_exit': repeated.returncode, 'status_exit': status.returncode,
                          'managed_python': ready['managed_python'], 'host_files_unchanged': True,
                          'offline_reuse': True, 'blocked_index_probe_exit': blocked_network.returncode}))


if __name__ == '__main__':
    unittest.main()
