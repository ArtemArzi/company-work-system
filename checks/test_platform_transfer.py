"""The CI transfer proof must reject incomplete or unrelated artifacts."""
from pathlib import Path
import tempfile
import unittest
from checks.platform_transfer import EXPECTED, transfer_inventory


class TransferInventory(unittest.TestCase):
    def test_each_os_and_legacy_backup_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for relative in EXPECTED:
                file = root / relative; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(b'{}')
            self.assertEqual(len(transfer_inventory(root)), 4)
            for relative in EXPECTED:
                file = root / relative; file.unlink()
                with self.assertRaisesRegex(RuntimeError, 'missing='):
                    transfer_inventory(root)
                file.write_bytes(b'{}')
            extra = root / 'unrelated/manifest.json'; extra.parent.mkdir(); extra.write_bytes(b'{}')
            with self.assertRaisesRegex(RuntimeError, 'extra='):
                transfer_inventory(root)
