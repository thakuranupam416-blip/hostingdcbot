from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from image_matcher import MatchResult, match_image
from utils import build_logger, ensure_reference_images


logger = build_logger("verification", log_file=Path(__file__).resolve().parent / "bot.log")


@dataclass(slots=True)
class VerificationConfig:
    threshold: float = 90.0
    reference_dir: str | Path | None = None
    max_image_size: int = 1600

    @property
    def reference_path(self) -> Path:
        return Path(self.reference_dir or Path(__file__).resolve().parent / "reference")


class VerificationService:
    def __init__(self, config: Optional[VerificationConfig] = None) -> None:
        self.config = config or VerificationConfig()
        self.reference_path = self.config.reference_path
        ensure_reference_images(self.reference_path)

    def verify_image(self, image_path: str | Path) -> MatchResult:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")

        result = match_image(
            image_path=image_path,
            reference_dir=self.reference_path,
            threshold=self.config.threshold,
        )
        logger.info(
            "verification result type=%s reference=%s score=%.2f matched=%s time=%.2fms",
            result.detected_type,
            result.matched_reference,
            result.match_score,
            result.is_match,
            result.processing_time_ms,
        )
        return result
