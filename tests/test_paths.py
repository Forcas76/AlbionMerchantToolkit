from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.paths import migrate_legacy_data_directory


class DataDirectoryMigrationTests(unittest.TestCase):
    def test_legacy_data_is_copied_once_without_deleting_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "AlbionPrizeShower" / "data"
            destination = root / "AlbionMerchantToolkit" / "data"
            legacy.mkdir(parents=True)
            (legacy / "user.db").write_bytes(b"inventory")

            self.assertTrue(migrate_legacy_data_directory(destination, legacy))
            self.assertEqual((destination / "user.db").read_bytes(), b"inventory")
            self.assertTrue((legacy / "user.db").exists())
            self.assertFalse(migrate_legacy_data_directory(destination, legacy))


if __name__ == "__main__":
    unittest.main()
