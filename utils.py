from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable


def ensure_reference_images(reference_dir: str | Path) -> list[Path]:
    reference_dir = Path(reference_dir)
    reference_dir.mkdir(parents=True, exist_ok=True)

    required_files = [
        reference_dir / "youtube_mobile.png",
        reference_dir / "youtube_pc.png",
    ]
    missing = [path.name for path in required_files if not path.exists()]
    if missing:
        for path in required_files:
            if not path.exists():
                path.touch()
        return required_files
    return required_files


def build_logger(name: str, log_file: str | Path | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        if log_file is not None:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().lower()
