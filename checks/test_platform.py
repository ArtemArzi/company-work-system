"""Portability contracts and failures, independent of real provider accounts."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import unittest
from unittest.mock import patch
from checks.test_system import Fixture
from core import Rejected, digest, load, write
import delivery
import doctor
import hooks
import lifecycle
import platform_runtime as platform
import validation


class Platforms(Fixture):
    def test_utf8_lf_and_failure_before_replace_preserves_bytes(self):
        file = self.root / 'company/note.json'
        write(file, {'note': 'Аксиния и компания'})
        before = file.read_bytes()
        self.assertNotIn(b'\r\n', before)
        self.assertIn('Аксиния'.encode(), before)
        with patch.object(platform.os, 'replace', side_effect=OSError('synthetic denied')):
            with self.assertRaises(OSError): write(file, {'note': 'new'})
        self.assertEqual(file.read_bytes(), before)
        self.assertEqual(list(file.parent.glob('.write-*')), [])

    def test_windows_atomic_contract_does_not_open_directory(self):
        with patch.object(platform, 'WINDOWS', True), patch.object(platform.os, 'open', wraps=os.open) as opened:
            write(self.root / 'company/note.json', {'note': 'readback'})
            self.assertTrue(load(self.root / 'company/note.json'))
            self.assertTrue(all(not Path(call.args[0]).is_dir() for call in opened.call_args_list))

    @unittest.skipIf(os.name == 'nt', 'POSIX directory fsync contract; Windows contract has its own test')
    def test_failure_after_replace_is_explicit_readback(self):
        file = self.root / 'company/note.json'
        write(file, {'note': 'old'})
        original = platform.os.fsync
        calls = 0
        def failed(fd):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('synthetic directory failure')
            return original(fd)
        with patch.object(platform.os, 'fsync', side_effect=failed):
            with self.assertRaisesRegex(OSError, 'replacement applied'): write(file, {'note': 'new'})
        self.assertEqual(load(file), {'note': 'new'})

    def test_process_death_releases_lock_without_deleting_it(self):
        code = "from core import lock; from pathlib import Path; import time;\nwith lock(Path(" + repr(str(self.root)) + ")):\n print('locked',flush=True); time.sleep(20)"
        env = dict(os.environ, PYTHONPATH=str(self.root / 'scripts'))
        child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, env=env)
        try:
            self.assertEqual(child.stdout.readline().rstrip(b'\r\n'), b'locked')
            from core import lock
            with self.assertRaises(Rejected):
                with lock(self.root): pass
            other = self.base / 'other'; other.mkdir()
            with lock(other): pass
            child.terminate(); child.wait(timeout=5)
            with lock(self.root): pass
            self.assertTrue((self.root / '.system/lock').exists())
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=5)
            child.stdout.close()

    def test_portable_paths_and_case_unicode_directory_collisions(self):
        for relative in ['C:/x', 'a\\b', 'CON.txt', 'x/LPT1', 'x/a.', 'a /b', '/absolute', '../a', 'a:b']:
            with self.subTest(path=relative), self.assertRaises(ValueError): platform.portable_path(relative)
        for paths in [['A/x', 'a/y'], ['café/x', 'cafe\u0301/y']]:
            with self.assertRaisesRegex(ValueError, 'collision'): platform.path_collisions(paths)
        platform.path_collisions(['company/Аксиния.md', 'work/task/output.json'])

    def test_exact_placeholders_and_absence_use_canonical_explicit_read(self):
        for directory in ['.agents/skills', '.claude/skills']:
            for file in (self.root / directory).iterdir():
                if file.is_symlink(): file.unlink()
                file.write_bytes(('../../skills/' + file.name).encode())
        self.assertEqual(validation.repository(self.root)['skills'], 12)
        file = self.root / '.agents/skills/company-context'
        file.unlink()
        validation.repository(self.root)
        file.write_bytes(b'../../skills/company-context\n')
        with self.assertRaisesRegex(Rejected, 'projection'): validation.repository(self.root)

    def test_doctor_is_readonly_and_installed_runtime_is_not_business_memory(self):
        before = {p.relative_to(self.root).as_posix(): digest(p) for p in (self.root / 'company').rglob('*') if p.is_file()}
        bad = self.root / '.local/runtime/package/SKILL.md'; bad.parent.mkdir(parents=True); bad.write_text('not a business skill', encoding='utf-8', newline="\n")
        bad = self.root / '.venv/foreign.md'; bad.parent.mkdir(); bad.write_text('[bad](missing)', encoding='utf-8', newline="\n")
        validation.repository(self.root)
        self.assertEqual(doctor.diagnose(self.root)['status'], 'ready')
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): digest(p) for p in (self.root / 'company').rglob('*') if p.is_file()})

    def test_projection_windows_has_explicit_shell_and_fingerprint(self):
        fingerprint = hooks.definition_hash(self.root, 'codex')
        with patch.object(platform, 'WINDOWS', True):
            codex = hooks._projection(self.root, 'codex', fingerprint, True)[0]['group']['hooks'][0]
            claude = hooks._projection(self.root, 'claude', fingerprint, True)[0]['group']['hooks'][0]
        encoded = codex['commandWindows'].split()[-1]
        command = base64.b64decode(encoded).decode('utf-16le')
        self.assertIn(hooks.NAMESPACE, command)
        self.assertTrue(command.startswith('& '))
        self.assertEqual(claude['shell'], 'powershell')
        file = self.root / 'scripts/platform_runtime.py'
        file.write_bytes(file.read_bytes() + b'\n# changed helper\n')
        self.assertNotEqual(hooks.definition_hash(self.root, 'codex'), fingerprint)

    def test_backup_preserves_raw_crlf_utf8_and_settings_without_filter(self):
        raw = self.root / 'company/projects/raw.md'; raw.write_bytes('Исторический источник\r\n'.encode())
        write(self.root / '.codex/hooks.json', {'hooks': {}, 'custom': 'company choice'})
        write(self.root / '.system/hooks-project-codex.json', {'synthetic': 'tracked owner proof'})
        self.seed()
        delivery.git(self.root, 'config', 'core.autocrlf', 'true')
        backup = self.base / 'backup'; lifecycle.backup(self.root, backup)
        restored = self.base / 'restored'
        lifecycle.restore(backup, restored)
        self.assertEqual((restored / 'company/projects/raw.md').read_bytes(), raw.read_bytes())
        for relative in ['.codex/hooks.json', '.system/hooks-project-codex.json']:
            self.assertEqual((restored / relative).read_bytes(), (self.root / relative).read_bytes())
        with self.assertRaisesRegex(Rejected, 'overwrite'): lifecycle.restore(backup, restored)

    def test_legacy_placeholder_backup_rehydrates_safe_projection(self):
        self.seed()
        for directory in ['.agents/skills', '.claude/skills']:
            for file in (self.root / directory).iterdir():
                target = ('../../skills/' + file.name).encode()
                if file.is_symlink(): file.unlink()
                file.write_bytes(target)
        delivery.git(self.root, 'config', 'core.symlinks', 'false')
        backup = self.base / 'backup'; lifecycle.backup(self.root, backup)
        # Actual schema1 Windows backups stored mode120000 projections as
        # regular placeholder bytes. Preserve this old reader contract.
        manifest = load(backup / 'manifest.json')
        relative = '.agents/skills/company-context'
        placeholder = b'../../skills/company-context'
        rebuilt = backup / 'legacy-files.tar'
        with tarfile.open(backup / 'files.tar', 'r') as source, tarfile.open(rebuilt, 'w') as target:
            for member in source:
                if member.name == relative:
                    member = tarfile.TarInfo(relative); member.size = len(placeholder)
                    target.addfile(member, io.BytesIO(placeholder))
                else:
                    target.addfile(member, source.extractfile(member) if member.isfile() else None)
        rebuilt.replace(backup / 'files.tar')
        manifest['files'][relative] = {'sha256': hashlib.sha256(placeholder).hexdigest()}
        manifest['tar_sha256'] = digest(backup / 'files.tar')
        write(backup / 'manifest.json', manifest)
        restored = self.base / 'restored'; lifecycle.restore(backup, restored)
        self.assertIn(platform.projection(restored, '.agents/skills', 'company-context'), ['symlink', 'git-placeholder'])
        self.assertEqual(delivery.git(restored, 'rev-parse', 'HEAD'), delivery.git(self.root, 'rev-parse', 'HEAD'))

    def test_archive_link_or_duplicate_rejected_before_destination(self):
        self.seed(); backup = self.base / 'backup'; lifecycle.backup(self.root, backup)
        tar = backup / 'files.tar'
        with tarfile.open(tar, 'a') as archive:
            member = tarfile.TarInfo('company/config.yaml'); member.type = tarfile.LNKTYPE; member.linkname = '../../outside'
            archive.addfile(member)
        manifest = load(backup / 'manifest.json'); manifest['tar_sha256'] = digest(tar); write(backup / 'manifest.json', manifest)
        target = self.base / 'rejected'
        with self.assertRaisesRegex(Rejected, 'inventory'): lifecycle.restore(backup, target)
        self.assertFalse(target.exists())
