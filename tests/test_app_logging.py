from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.core import app_logging


class DiagnosticBundleTests(unittest.TestCase):
    def test_export_contains_logs_but_not_databases_or_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_dir = root / "logs"
            log_dir.mkdir()
            (log_dir / "albion-merchant-toolkit.log").write_text(
                "teszt napló\n", encoding="utf-8"
            )
            destination = root / "diagnostics.zip"

            with (
                patch.object(app_logging, "LOG_DIR", log_dir),
                patch.object(app_logging, "_configured", True),
            ):
                result = app_logging.export_diagnostic_bundle(destination)

            self.assertEqual(result, destination)
            with zipfile.ZipFile(result) as archive:
                names = set(archive.namelist())
                summary = archive.read("diagnostics.txt").decode("utf-8")

            self.assertIn("logs/albion-merchant-toolkit.log", names)
            self.assertFalse(any(name.endswith(".db") for name in names))
            self.assertNotIn(str(root), summary)
            self.assertIn("executable_name=", summary)


if __name__ == "__main__":
    unittest.main()
