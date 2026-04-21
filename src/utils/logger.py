"""Logger centralizado. Grava em arquivo + stdout."""
import logging
import os
from pathlib import Path

LOG_DIR = os.environ.get("LOG_DIR", "./logs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


def get_logger(name: str) -> logging.Logger:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(LOG_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # File handler
    fh = logging.FileHandler(f"{LOG_DIR}/{name}.log")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger
