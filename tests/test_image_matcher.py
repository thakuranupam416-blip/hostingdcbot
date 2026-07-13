import unittest
from pathlib import Path

from image_matcher import MatchResult, match_image
from utils import ensure_reference_images


class ImageMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference_dir = Path(__file__).resolve().parent.parent / "reference"
        ensure_reference_images(self.reference_dir)

    def test_reference_images_exist(self) -> None:
        self.assertTrue((self.reference_dir / "youtube_mobile.png").exists())
        self.assertTrue((self.reference_dir / "youtube_pc.png").exists())

    def test_matching_returns_structured_result(self) -> None:
        result = match_image(
            image_path=self.reference_dir / "youtube_mobile.png",
            reference_dir=self.reference_dir,
            threshold=50.0,
        )
        self.assertIsInstance(result, MatchResult)
        self.assertGreaterEqual(result.match_score, 0.0)
        self.assertLessEqual(result.match_score, 100.0)
        self.assertIn(result.detected_type, {"mobile", "pc"})


if __name__ == "__main__":
    unittest.main()
