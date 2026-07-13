from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import List

import cv2
import numpy as np


@dataclass(slots=True)
class MatchResult:
    detected_type: str
    matched_reference: str
    match_score: float
    processing_time_ms: float
    is_match: bool
    blur_score: float
    resolution: tuple[int, int]
    notes: List[str] = field(default_factory=list)


def load_image(image_path: str | Path) -> np.ndarray:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Unable to read image: {image_path}")
    return image


def is_empty_reference_image(image_path: str | Path) -> bool:
    path = Path(image_path)
    if not path.exists():
        return True
    return path.stat().st_size < 8


def preprocess_image(image: np.ndarray, max_dimension: int = 1600) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(1.0, max_dimension / max(height, width))
    if scale < 1.0:
        image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 10, 255, cv2.THRESH_BINARY)
    coords = cv2.findNonZero(binary)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        if w > 0 and h > 0:
            image = image[y : y + h, x : x + w]

    if image.size == 0:
        return cv2.cvtColor(cv2.resize(image, (640, 480)), cv2.COLOR_BGR2GRAY)

    return image


def extract_focus_region(image: np.ndarray, device_type: str) -> np.ndarray:
    height, width = image.shape[:2]
    if device_type == "mobile":
        y0 = max(0, int(height * 0.02))
        y1 = max(y0 + 1, int(height * 0.42))
        x0 = max(0, int(width * 0.04))
        x1 = min(width, int(width * 0.96))
    else:
        y0 = max(0, int(height * 0.02))
        y1 = max(y0 + 1, int(height * 0.28))
        x0 = max(0, int(width * 0.08))
        x1 = min(width, int(width * 0.92))

    return image[y0:y1, x0:x1]


def calculate_blur_score(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_32F).var())


def estimate_edge_density(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return float(np.count_nonzero(edges) / max(1, edges.size))


def calculate_orb_similarity(image_a: np.ndarray, image_b: np.ndarray) -> float:
    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(nfeatures=400)
    keypoints_a, descriptors_a = orb.detectAndCompute(gray_a, None)
    keypoints_b, descriptors_b = orb.detectAndCompute(gray_b, None)

    if descriptors_a is None or descriptors_b is None or len(keypoints_a) < 8 or len(keypoints_b) < 8:
        return 0.0

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(descriptors_a, descriptors_b, k=2)
    good_matches = [m for m, n in matches if m.distance < 0.75 * n.distance]

    if len(good_matches) < 8:
        return 0.0

    max_possible = min(120, min(len(keypoints_a), len(keypoints_b)))
    score = (len(good_matches) / max(1, max_possible)) * 100.0
    return float(min(100.0, max(0.0, score)))


def calculate_template_similarity(image_a: np.ndarray, image_b: np.ndarray) -> float:
    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)
    if gray_a.shape[0] != gray_b.shape[0] or gray_a.shape[1] != gray_b.shape[1]:
        target_shape = (max(gray_a.shape[1], gray_b.shape[1]), max(gray_a.shape[0], gray_b.shape[0]))
        gray_a = cv2.resize(gray_a, target_shape, interpolation=cv2.INTER_AREA)
        gray_b = cv2.resize(gray_b, target_shape, interpolation=cv2.INTER_AREA)

    result = cv2.matchTemplate(gray_a, gray_b, cv2.TM_CCOEFF_NORMED)
    _, similarity, _, _ = cv2.minMaxLoc(result)
    return float(max(0.0, min(100.0, similarity * 100.0)))


def compare_images(candidate: np.ndarray, reference: np.ndarray, device_type: str) -> tuple[float, str]:
    candidate_focus = extract_focus_region(candidate, device_type)
    reference_focus = extract_focus_region(reference, device_type)

    orb_score = calculate_orb_similarity(candidate_focus, reference_focus)
    if orb_score >= 1.0:
        return orb_score, "orb"

    template_score = calculate_template_similarity(candidate_focus, reference_focus)
    return template_score, "template"


def match_image(
    image_path: str | Path,
    reference_dir: str | Path,
    threshold: float = 90.0,
) -> MatchResult:
    started = perf_counter()
    reference_dir = Path(reference_dir)
    candidate = preprocess_image(load_image(image_path))
    candidate_height, candidate_width = candidate.shape[:2]
    blur_score = calculate_blur_score(candidate)

    if candidate_width < 320 or candidate_height < 240:
        raise ValueError("Screenshot is too small for reliable verification")

    if blur_score < 0.5:
        notes = ["Image sharpness is very low; verification may be unreliable"]
    else:
        notes = []

    reference_mobile_path = reference_dir / "youtube_mobile.png"
    reference_pc_path = reference_dir / "youtube_pc.png"
    if is_empty_reference_image(reference_mobile_path) or is_empty_reference_image(reference_pc_path):
        raise FileNotFoundError("Reference images are missing or empty. Add real screenshot references before running verification.")

    reference_mobile = preprocess_image(load_image(reference_mobile_path))
    reference_pc = preprocess_image(load_image(reference_pc_path))

    mobile_score, mobile_method = compare_images(candidate, reference_mobile, "mobile")
    pc_score, pc_method = compare_images(candidate, reference_pc, "pc")

    if mobile_score >= pc_score:
        detected_type = "mobile"
        match_score = mobile_score
        matched_reference = "youtube_mobile.png"
        method = mobile_method
    else:
        detected_type = "pc"
        match_score = pc_score
        matched_reference = "youtube_pc.png"
        method = pc_method

    notes: List[str] = [f"Feature engine: {method}", f"Blur score: {blur_score:.1f}"]
    if match_score < threshold:
        notes.append("Reference matching below configured threshold")
    else:
        notes.append("Reference matching passed")

    edge_density = estimate_edge_density(candidate)
    if edge_density > 0.65 and match_score < 60.0:
        notes.append("High edge density suggests an edited or synthetic image")

    processing_time_ms = (perf_counter() - started) * 1000.0
    return MatchResult(
        detected_type=detected_type,
        matched_reference=matched_reference,
        match_score=float(round(match_score, 2)),
        processing_time_ms=float(round(processing_time_ms, 2)),
        is_match=bool(match_score >= threshold),
        blur_score=float(round(blur_score, 2)),
        resolution=(candidate_width, candidate_height),
        notes=notes,
    )
