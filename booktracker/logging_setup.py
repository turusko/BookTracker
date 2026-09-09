import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIRECTORY = Path(__file__).resolve().parent.parent / "Logs"


def configure_logging(directory=None):
    """Configure one rotating UTF-8 file handler, including on repeated startup."""
    directory = Path(directory) if directory is not None else LOG_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    log_path = (directory / "booktracker.log").resolve()
    logger = logging.getLogger("booktracker")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(log_path):
            return handler
    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    return handler
