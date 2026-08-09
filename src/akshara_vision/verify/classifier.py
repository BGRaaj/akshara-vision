"""Calibrated defect classifiers and profile threshold tuning from human reviewer feedback."""
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageFilter, ImageStat

from akshara_vision.verify.models import AcceptanceProfile


def classify_archival_defect(
    file_path: Path,
    defect_type: str,
    raw_score: float,
    profile: AcceptanceProfile,
) -> Tuple[bool, float, str]:
    """Distinguish genuine manuscript degradation from scanning defects.

    Returns:
        (is_genuine_defect, adjusted_confidence, explanation)
    """
    if not file_path.exists():
        return True, 1.0, "File missing"

    try:
        with Image.open(file_path) as img:
            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            mean_lum = stat.mean[0]
            std_lum = stat.stddev[0]

            # For blur detection: check if low variance is due to historical paper staining
            # vs camera motion blur. Historical manuscripts with ink have sharp local edges
            # even if global variance is lower.
            if defect_type == "blur_detection":
                edges = gray.filter(ImageFilter.FIND_EDGES)
                edge_stat = ImageStat.Stat(edges)
                edge_max = edge_stat.extrema[0][1]

                # If there are sharp black/white ink transitions (>180 max edge),
                # but overall variance is low due to aged yellow paper, it's likely manuscript degradation
                if edge_max > 180 and raw_score >= profile.blur_threshold * 0.65:
                    return (
                        False,
                        0.40,
                        "Historical paper staining / aged manuscript (sharp ink edges present)",
                    )
                return True, 0.95, "Confirmed optical/camera focus blur"

            # For blank detection: check if paper has faint ink bleed-through or watermarks
            if defect_type == "blank_detection":
                if std_lum > 8.0 and mean_lum < 252:
                    return (
                        False,
                        0.50,
                        "Faint manuscript ink or archival watermark detected",
                    )
                return True, 0.98, "Confirmed blank or overexposed page"

            return True, 1.0, "Standard defect classification"
    except Exception as exc:
        return True, 1.0, f"Heuristic check fallback: {exc}"


def calibrate_profile_from_reviews(
    decisions_file: Path, base_profile: AcceptanceProfile
) -> AcceptanceProfile:
    """Tune acceptance thresholds automatically from supervisor QA review decisions.

    Reads `review_decisions.json` and adjusts blur, skew, and blank thresholds
    if the supervisor override rate for a check exceeds 20%.
    """
    if not decisions_file.exists():
        return base_profile

    try:
        data = json.loads(decisions_file.read_text(encoding="utf-8"))
    except Exception:
        return base_profile

    decisions = data.get("decisions", {})
    if not decisions:
        return base_profile

    # Count overrides per check_name
    check_totals: Dict[str, int] = {}
    check_overrides: Dict[str, int] = {}

    for file_path, info in decisions.items():
        check_name = info.get("check_name", "")
        action = info.get("action", "")
        if not check_name:
            continue

        check_totals[check_name] = check_totals.get(check_name, 0) + 1
        if action == "approve":
            check_overrides[check_name] = check_overrides.get(check_name, 0) + 1

    # Clone base profile to tune
    tuned_dict = base_profile.to_dict()

    # Calibrate blur_threshold
    blur_total = check_totals.get("blur_detection", 0)
    if blur_total >= 3:
        override_rate = check_overrides.get("blur_detection", 0) / blur_total
        if override_rate > 0.20:
            # Lower blur threshold by 15% to reduce false positives on aged paper
            tuned_dict["blur_threshold"] = max(20.0, base_profile.blur_threshold * 0.85)

    # Calibrate blank_threshold
    blank_total = check_totals.get("blank_detection", 0)
    if blank_total >= 3:
        override_rate = check_overrides.get("blank_detection", 0) / blank_total
        if override_rate > 0.20:
            tuned_dict["blank_threshold"] = min(0.999, base_profile.blank_threshold + 0.005)

    # Calibrate skew_threshold_degrees
    skew_total = check_totals.get("skew_detection", 0)
    if skew_total >= 3:
        override_rate = check_overrides.get("skew_detection", 0) / skew_total
        if override_rate > 0.20:
            tuned_dict["skew_threshold_degrees"] = min(
                10.0, base_profile.skew_threshold_degrees + 1.5
            )

    return AcceptanceProfile.from_dict(tuned_dict)
