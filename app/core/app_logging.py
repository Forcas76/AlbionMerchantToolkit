"""Central application logging and privacy-safe diagnostic export."""

from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import platform
import sys
import threading
import zipfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.paths import CATALOG_DB_FILE, FROZEN, LOG_DIR, MARKET_DB_FILE, USER_DB_FILE
from app.version import RELEASE_CHANNEL, __version__

LOG_FILE = LOG_DIR / "albion-merchant-toolkit.log"
FAULT_FILE = LOG_DIR / "native-crash.log"
_configured = False
_fault_stream = None


def configure_logging() -> Path:
    """Install rotating file, warning and uncaught-exception handlers once."""

    global _configured, _fault_stream
    if _configured:
        return LOG_FILE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(threadName)s | "
        "%(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    logging.captureWarnings(True)

    previous_hook = sys.excepthook

    def exception_hook(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc_value, traceback)
            return
        logging.getLogger("crash").critical(
            "Kezeletlen kivétel", exc_info=(exc_type, exc_value, traceback)
        )
        previous_hook(exc_type, exc_value, traceback)

    sys.excepthook = exception_hook

    if hasattr(threading, "excepthook"):
        previous_thread_hook = threading.excepthook

        def thread_exception_hook(args: threading.ExceptHookArgs) -> None:
            logging.getLogger("crash.thread").critical(
                "Kezeletlen szálhiba: %s",
                args.thread.name if args.thread else "ismeretlen",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            previous_thread_hook(args)

        threading.excepthook = thread_exception_hook

    try:
        _fault_stream = FAULT_FILE.open("a", encoding="utf-8")
        faulthandler.enable(file=_fault_stream, all_threads=True)
        atexit.register(_close_fault_stream)
    except (OSError, RuntimeError):
        logging.getLogger(__name__).exception("A natív hibalog nem indítható")

    _configured = True
    logging.getLogger("app").info(
        "Alkalmazás indul | version=%s | channel=%s | python=%s | platform=%s | "
        "frozen=%s | pid=%s",
        __version__, RELEASE_CHANNEL, platform.python_version(), platform.platform(),
        FROZEN, os.getpid(),
    )
    logging.getLogger("app").info(
        "Adatkönyvtár előkészítve | mód=%s",
        "telepített" if FROZEN else "forráskód",
    )
    return LOG_FILE


def _close_fault_stream() -> None:
    global _fault_stream
    if _fault_stream is not None:
        try:
            _fault_stream.flush()
            _fault_stream.close()
        finally:
            _fault_stream = None


def install_qt_message_logging() -> None:
    """Forward Qt diagnostics into the same rotating application log."""

    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def qt_handler(message_type, context, message) -> None:
        location = ""
        if context and context.file:
            location = f" ({context.file}:{context.line})"
        logging.getLogger("qt").log(
            levels.get(message_type, logging.INFO), "%s%s", message, location
        )

    qInstallMessageHandler(qt_handler)


def export_diagnostic_bundle(destination: str | Path) -> Path:
    """Create a ZIP with logs and a non-sensitive runtime summary."""

    configure_logging()
    target = Path(destination)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    logging.getLogger(__name__).info(
        "Diagnosztikai csomag exportálása: %s", target.name
    )
    for handler in logging.getLogger().handlers:
        handler.flush()
    if _fault_stream is not None:
        _fault_stream.flush()
    summary = [
        f"created_utc={datetime.now(timezone.utc).isoformat()}",
        f"app_version={__version__}",
        f"release_channel={RELEASE_CHANNEL}",
        f"python={platform.python_version()}",
        f"platform={platform.platform()}",
        f"frozen={FROZEN}",
        f"executable_name={Path(sys.executable).name}",
        f"data_storage={'local_app_data' if FROZEN else 'project_directory'}",
    ]
    for label, path in (
        ("catalog", CATALOG_DB_FILE),
        ("market", MARKET_DB_FILE),
        ("user", USER_DB_FILE),
    ):
        size = path.stat().st_size if path.exists() else 0
        summary.append(f"{label}_db_exists={path.exists()}")
        summary.append(f"{label}_db_bytes={size}")

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.txt", "\n".join(summary) + "\n")
        for log_file in sorted(LOG_DIR.glob("*.log*")):
            if log_file.is_file():
                archive.write(log_file, f"logs/{log_file.name}")
    return target
