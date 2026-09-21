from __future__ import annotations

import unittest

from app.version import (
    APP_USER_AGENT,
    RELEASE_CHANNEL,
    VERSION_FILE,
    VERSION_LABEL,
    __version__,
)


class VersionTests(unittest.TestCase):
    def test_runtime_version_comes_from_root_version_file(self) -> None:
        self.assertEqual(__version__, VERSION_FILE.read_text(encoding="utf-8").strip())
        self.assertEqual(RELEASE_CHANNEL, "Alpha")
        self.assertIn(__version__, VERSION_LABEL)
        self.assertTrue(APP_USER_AGENT.endswith(f"/{__version__}"))


if __name__ == "__main__":
    unittest.main()
