"""Real legacy history and checkout bytes cannot redefine company truth."""
import os
from pathlib import Path
import shutil
from unittest.mock import patch
from checks.test_system import Fixture, TEMPLATE
from checks.platform_transfer import old_code
from core import digest, write
import delivery
import lifecycle
import product


class EvolutionBytes(Fixture):
    def legacy_company(self):
        # The positive case deliberately begins with working bytes equal to Git.
        # Windows system autocrlf must not silently define this test precondition.
        home = self.base / 'initial-home'; home.mkdir()
        (home / '.gitconfig').write_bytes(b'[core]\n autocrlf = false\n')
        with patch.dict(os.environ, {'HOME': str(home), 'USERPROFILE': str(home)}):
            return self._legacy_company()

    def _legacy_company(self):
        p = self.base / 'old-product'; p.mkdir()
        old_code(p / 'template')
        delivery.git(p, 'init', '-b', 'main'); delivery.identity(p); delivery.git(p, 'add', '.')
        if os.name == 'nt':
            delivery.git(p, 'config', 'core.symlinks', 'false')
            # Keep actual Git mode120000 while Windows reads exact placeholders.
            for directory in ['.agents/skills', '.claude/skills']:
                for file in (p / 'template' / directory).iterdir():
                    oid = delivery.git(p, 'hash-object', '-w', str(file))
                    delivery.git(p, 'update-index', '--cacheinfo', '120000', oid, file.relative_to(p).as_posix())
        delivery.git(p, 'commit', '-m', 'Actual released1.2 template')
        release = self.base / 'release.git'; product.release(p, release)
        company = self.base / 'old-company'; lifecycle.create(release, company, 'byte-company', 'test-owner')
        notes = company / 'company/projects/notes.md'; notes.write_bytes(b'Original business bytes\n')
        write(company / '.codex/hooks.json', {'hooks': {}, 'company_choice': 'Сохранить exact bytes'})
        write(company / '.system/hooks-project-codex.json', {'synthetic': 'opaque tracked proof; not native event evidence'})
        delivery.git(company, 'add', '.'); delivery.git(company, 'commit', '-m', 'Original company bytes/settings/proof')
        # The real old/new template company files are unchanged by this feature.
        for file in TEMPLATE.rglob('*'):
            if '__pycache__' in file.parts or file.is_symlink() or file.is_dir(): continue
            target = p / 'template' / file.relative_to(TEMPLATE)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(file, target)
        delivery.git(p, 'add', '.'); delivery.git(p, 'commit', '-m', 'Prepared portable release')
        product.release(p, release)
        return company, release

    def test_actual_old_upgrade_and_rollback_keep_source_bytes_under_global_autocrlf(self):
        company, release = self.legacy_company()
        expected = lifecycle._protected_snapshot(company)
        for relative, sha in expected.items():
            blob = delivery.git(company, "show", "HEAD:" + relative, binary=True)
            self.assertEqual((company / relative).read_bytes(), blob, relative)
        home = self.base / 'fake-home'; home.mkdir()
        cfg = home / '.gitconfig'; cfg.write_bytes(b'[core]\n autocrlf = true\n')
        before = cfg.read_bytes()
        with patch.dict(os.environ, {'HOME': str(home), 'USERPROFILE': str(home)}):
            candidate = self.base / 'updated'
            result = lifecycle.update(company, release, candidate)
            self.assertEqual(result['status'], 'verified-candidate', result)
            self.assertEqual(lifecycle._protected_snapshot(candidate), expected)
            self.assertEqual(delivery.git(candidate, 'status', '--porcelain'), '')
            self.assertEqual(delivery.git(candidate, 'config', '--local', 'core.autocrlf'), 'false')
            rolled = self.base / 'rolled'
            result = lifecycle.rollback(candidate, rolled)
            self.assertEqual(result['status'], 'verified-candidate', result)
            self.assertEqual(lifecycle._protected_snapshot(rolled), expected)
        self.assertEqual(cfg.read_bytes(), before)
        self.assertEqual(lifecycle._protected_snapshot(company), expected)

    def test_legacy_working_native_proof_different_from_git_blocks_before_merge(self):
        company, release = self.legacy_company()
        delivery.git(company, 'config', 'core.autocrlf', 'true')
        proof = company / '.system/hooks-project-codex.json'
        proof.write_bytes(proof.read_bytes().replace(b'\n', b'\r\n'))
        delivery.git(company, 'add', '.system/hooks-project-codex.json')
        self.assertEqual(delivery.git(company, 'status', '--porcelain'), '')
        original = proof.read_bytes(); head = delivery.git(company, 'rev-parse', 'HEAD')
        result = lifecycle.update(company, release, self.base / 'reconcile')
        self.assertEqual(result['status'], 'needs-reconciliation', result)
        self.assertEqual(result['phase'], 'clone')
        self.assertEqual(proof.read_bytes(), original)
        self.assertEqual(delivery.git(company, 'rev-parse', 'HEAD'), head)
        self.assertNotEqual((self.base / 'reconcile/.system/hooks-project-codex.json').read_bytes(), original)
        self.assertNotEqual(delivery.git(self.base / 'reconcile', 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode, 0)

    def test_source_head_race_preserves_both_states_and_blocks_before_merge(self):
        company, release = self.legacy_company()
        original = lifecycle.git
        def racing(root, *args, **kwargs):
            if Path(root) == company and args[0] == 'clone':
                file = company / 'company/projects/other-owner.md'; file.write_bytes(b'Concurrent accepted work\n')
                original(company, 'add', '.'); original(company, 'commit', '-m', 'Other owner during preparation')
            return original(root, *args, **kwargs)
        with patch.object(lifecycle, 'git', side_effect=racing):
            result = lifecycle.update(company, release, self.base / 'race')
        self.assertEqual(result['status'], 'needs-reconciliation', result)
        self.assertEqual(result['phase'], 'clone')
        self.assertTrue(any('HEAD' in issue for issue in result['issues']))
        self.assertEqual((company / 'company/projects/other-owner.md').read_bytes(), b'Concurrent accepted work\n')
