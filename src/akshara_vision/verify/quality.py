import logging
import math
from pathlib import Path
from typing import List, Optional, Dict

from PIL import Image, ImageFilter, ImageStat

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from akshara_vision.verify.models import SeverityLevel, PageException, AcceptanceProfile


logger = logging.getLogger(__name__)


def detect_blank_pages(file_path: Path, profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    try:
        with Image.open(file_path) as img:
            img_gray = img.convert("L")
            # Get pixel data
            pixels = img_gray.tobytes()  # tobytes() is equivalent to getdata() for mode-L images
            total_pixels = len(pixels)
            if total_pixels == 0:
                return exceptions
            
            # Count near-white pixels (> 250)
            white_pixels = sum(1 for p in pixels if p > 250)
            white_ratio = white_pixels / total_pixels
            
            if white_ratio > profile.blank_threshold:
                exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="blank_page",
                    severity=SeverityLevel.HIGH,
                    message="Page appears to be blank based on white pixel ratio.",
                    evidence={'white_ratio': white_ratio, 'threshold': profile.blank_threshold},
                    confidence=0.9
                ))
    except Exception as e:
        logger.debug(f"Failed to process {file_path} for blank detection: {e}")
    return exceptions


def detect_blur(file_path: Path, profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    try:
        if HAS_CV2:
            img = cv2.imread(str(file_path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                blur_score = cv2.Laplacian(img, cv2.CV_64F).var()
                method = "cv2_laplacian"
                if blur_score < profile.blur_threshold:
                    exceptions.append(PageException(
                        file_path=str(file_path),
                        check_name="blur",
                        severity=SeverityLevel.MEDIUM,
                        message="Image appears blurry.",
                        evidence={'blur_score': float(blur_score), 'threshold': profile.blur_threshold, 'method': method},
                        confidence=0.8
                    ))
                return exceptions
        
        # Fallback to Pillow
        with Image.open(file_path) as img:
            img_gray = img.convert("L")
            edges = img_gray.filter(ImageFilter.FIND_EDGES)
            stat = ImageStat.Stat(edges)
            # Use variance of the edges
            blur_score = stat.var[0]
            method = "pil_find_edges"
            
            if blur_score < profile.blur_threshold:
                exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="blur",
                    severity=SeverityLevel.MEDIUM,
                    message="Image appears blurry.",
                    evidence={'blur_score': blur_score, 'threshold': profile.blur_threshold, 'method': method},
                    confidence=0.7
                ))
    except Exception as e:
        logger.debug(f"Failed to process {file_path} for blur detection: {e}")
    return exceptions


def detect_skew(file_path: Path, profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    try:
        if HAS_CV2:
            img = cv2.imread(str(file_path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                edges = cv2.Canny(img, 50, 150, apertureSize=3)
                lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 100, minLineLength=100, maxLineGap=10)
                angles = []
                if lines is not None:
                    for line in lines:
                        x1, y1, x2, y2 = line[0]
                        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
                        angles.append(angle)
                
                if angles:
                    # Basic approach: median angle
                    estimated_angle = float(np.median(angles))
                    # Normalize around 0, 90, -90
                    if estimated_angle > 45:
                        estimated_angle -= 90
                    elif estimated_angle < -45:
                        estimated_angle += 90
                        
                    if abs(estimated_angle) > profile.skew_threshold_degrees:
                        exceptions.append(PageException(
                            file_path=str(file_path),
                            check_name="skew",
                            severity=SeverityLevel.MEDIUM,
                            message=f"Image is skewed by {estimated_angle:.2f} degrees.",
                            evidence={'estimated_angle': estimated_angle, 'threshold': profile.skew_threshold_degrees},
                            confidence=0.8
                        ))
                return exceptions
        
        # Fallback to Pillow
        exceptions.append(PageException(
            file_path=str(file_path),
            check_name="skew",
            severity=SeverityLevel.INFO,
            message="Skew detection requires OpenCV.",
            evidence={'estimated_angle': 0.0, 'threshold': profile.skew_threshold_degrees},
            confidence=1.0
        ))
    except Exception as e:
        logger.debug(f"Failed to process {file_path} for skew detection: {e}")
    return exceptions


def compute_perceptual_hash(file_path: Path, hash_size: int = 8) -> Optional[str]:
    try:
        with Image.open(file_path) as img:
            img = img.resize((hash_size, hash_size), Image.Resampling.LANCZOS)
            img = img.convert("L")
            pixels = list(img.tobytes())  # tobytes() is equivalent to getdata() for mode-L images
            avg = sum(pixels) / len(pixels)
            bits = "".join(['1' if p > avg else '0' for p in pixels])
            hex_representation = "{0:0{1}x}".format(int(bits, 2), (hash_size * hash_size) // 4)
            return hex_representation
    except Exception as e:
        logger.debug(f"Failed to compute perceptual hash for {file_path}: {e}")
        return None


def _hamming_distance(hash1: str, hash2: str) -> int:
    return bin(int(hash1, 16) ^ int(hash2, 16)).count('1')


def detect_near_duplicates(file_paths: List[Path], profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    hashes = {}
    
    # Compute hashes
    for path in file_paths:
        h = compute_perceptual_hash(path)
        if h:
            hashes[path] = h
            
    paths = list(hashes.keys())
    flagged = set()
    
    # Compare
    for i in range(len(paths)):
        path1 = paths[i]
        if path1 in flagged:
            continue
            
        for j in range(i + 1, len(paths)):
            path2 = paths[j]
            dist = _hamming_distance(hashes[path1], hashes[path2])
            
            if dist <= profile.near_duplicate_hash_threshold:
                flagged.add(path1)
                flagged.add(path2)
                
                exceptions.append(PageException(
                    file_path=str(path1),
                    check_name="near_duplicate",
                    severity=SeverityLevel.HIGH,
                    message="Near duplicate detected.",
                    evidence={'duplicate_of': str(path2), 'hamming_distance': dist, 'threshold': profile.near_duplicate_hash_threshold},
                    confidence=0.9
                ))
                exceptions.append(PageException(
                    file_path=str(path2),
                    check_name="near_duplicate",
                    severity=SeverityLevel.HIGH,
                    message="Near duplicate detected.",
                    evidence={'duplicate_of': str(path1), 'hamming_distance': dist, 'threshold': profile.near_duplicate_hash_threshold},
                    confidence=0.9
                ))
                
    return exceptions


def detect_crop_risk(file_path: Path, profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    try:
        with Image.open(file_path) as img:
            img_gray = img.convert("L")
            width, height = img_gray.size
            if width == 0 or height == 0:
                return exceptions
                
            w_strip = max(1, int(width * 0.02))
            h_strip = max(1, int(height * 0.02))
            
            borders = {
                'top': img_gray.crop((0, 0, width, h_strip)),
                'bottom': img_gray.crop((0, height - h_strip, width, height)),
                'left': img_gray.crop((0, 0, w_strip, height)),
                'right': img_gray.crop((width - w_strip, 0, width, height))
            }
            
            border_content_ratio = {}
            for name, region in borders.items():
                pixels = list(region.tobytes())  # tobytes() is equivalent to getdata() for mode-L images
                # non-white pixels
                dark_pixels = sum(1 for p in pixels if p < 250)
                ratio = dark_pixels / len(pixels) if pixels else 0
                border_content_ratio[name] = ratio
                
            # If any border has a high concentration of dark pixels, flag it
            if any(ratio > 0.1 for ratio in border_content_ratio.values()):
                exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="crop_risk",
                    severity=SeverityLevel.MEDIUM,
                    message="Significant content found in image margins, risk of cutoff.",
                    evidence={'border_content_ratio': border_content_ratio},
                    confidence=0.7
                ))
    except Exception as e:
        logger.debug(f"Failed to process {file_path} for crop risk: {e}")
    return exceptions


def check_image_quality(file_path: Path, profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    exceptions.extend(detect_blank_pages(file_path, profile))
    exceptions.extend(detect_blur(file_path, profile))
    exceptions.extend(detect_skew(file_path, profile))
    exceptions.extend(detect_crop_risk(file_path, profile))
    return exceptions
