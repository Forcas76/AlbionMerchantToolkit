"""Single-source application version exposed to runtime consumers."""

from __future__ import annotations

from app.paths import BUNDLE_ROOT

VERSION_FILE = BUNDLE_ROOT / "VERSION"


def _read_version() -> str:
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0-alpha.0+unknown"
    return value or "0.0.0-alpha.0+unknown"


__version__ = _read_version()
if "-alpha" in __version__.lower():
    RELEASE_CHANNEL = "Alpha"
elif "-beta" in __version__.lower():
    RELEASE_CHANNEL = "Beta"
elif "-rc" in __version__.lower():
    RELEASE_CHANNEL = "Release Candidate"
else:
    RELEASE_CHANNEL = "Stable"
VERSION_LABEL = f"{RELEASE_CHANNEL} · v{__version__}"
APP_USER_AGENT = f"AlbionMerchantToolkit/{__version__}"
