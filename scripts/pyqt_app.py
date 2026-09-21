"""Compatibility entry point for the PyQt6 application."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.app_logging import configure_logging

configure_logging()

from app.ui.qt_app import run_gui


if __name__ == "__main__":
    run_gui()
