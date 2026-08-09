import hashlib
import re
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image

from .models import AcceptanceProfile, PageException, SeverityLevel


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def check_file_format(file_path: Path, profile: AcceptanceProfile) -> List[PageException]:
    """Verify file format, readability, DPI, color mode, and dimensions."""
    exceptions = []
    
    # Check extension
    ext = file_path.suffix.lower()
    allowed_exts = set(profile.allowed_master_formats + profile.allowed_derivative_formats)
    if ext not in allowed_exts:
        exceptions.append(PageException(
            file_path=str(file_path),
            check_name="format_extension_check",
            severity=SeverityLevel.CRITICAL,
            message=f"File extension '{ext}' not in allowed formats.",
            evidence={"extension": ext, "allowed": list(allowed_exts)}
        ))
        return exceptions

    # Try to open with Pillow
    try:
        with Image.open(file_path) as img:
            # DPI Check
            dpi = img.info.get("dpi")
            if dpi:
                x_dpi, y_dpi = dpi
                if profile.expected_dpi and (x_dpi != profile.expected_dpi or y_dpi != profile.expected_dpi):
                    exceptions.append(PageException(
                        file_path=str(file_path),
                        check_name="format_dpi_check",
                        severity=SeverityLevel.MEDIUM,
                        message=f"DPI {x_dpi}x{y_dpi} does not match expected {profile.expected_dpi}",
                        evidence={"found_dpi": dpi, "expected_dpi": profile.expected_dpi}
                    ))
                elif profile.min_dpi and (x_dpi < profile.min_dpi or y_dpi < profile.min_dpi):
                    exceptions.append(PageException(
                        file_path=str(file_path),
                        check_name="format_dpi_check",
                        severity=SeverityLevel.HIGH,
                        message=f"DPI {x_dpi}x{y_dpi} is below minimum {profile.min_dpi}",
                        evidence={"found_dpi": dpi, "min_dpi": profile.min_dpi}
                    ))
            else:
                exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="format_dpi_check",
                    severity=SeverityLevel.MEDIUM,
                    message="DPI information missing from file.",
                    evidence={}
                ))

            # Color mode check
            if profile.expected_color_mode and img.mode != profile.expected_color_mode:
                exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="format_color_mode_check",
                    severity=SeverityLevel.HIGH,
                    message=f"Color mode '{img.mode}' does not match expected '{profile.expected_color_mode}'",
                    evidence={"found_mode": img.mode, "expected_mode": profile.expected_color_mode}
                ))

            # Bit depth check estimation
            if profile.expected_bit_depth:
                mode_to_depth = {"1": 1, "L": 8, "P": 8, "RGB": 24, "RGBA": 32, "CMYK": 32, "YCbCr": 24, "I": 32, "F": 32}
                depth = mode_to_depth.get(img.mode)
                if depth and depth != profile.expected_bit_depth:
                    exceptions.append(PageException(
                        file_path=str(file_path),
                        check_name="format_bit_depth_check",
                        severity=SeverityLevel.MEDIUM,
                        message=f"Estimated bit depth {depth} from mode '{img.mode}' does not match expected {profile.expected_bit_depth}",
                        evidence={"estimated_depth": depth, "expected_depth": profile.expected_bit_depth, "mode": img.mode}
                    ))

            # Dimensions check
            width, height = img.size
            if width == 0 or height == 0:
                exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="format_dimensions_check",
                    severity=SeverityLevel.CRITICAL,
                    message=f"Invalid image dimensions: {width}x{height}",
                    evidence={"width": width, "height": height}
                ))
            elif width > 20000 or height > 20000:
                exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="format_dimensions_check",
                    severity=SeverityLevel.LOW,
                    message=f"Unusually large image dimensions: {width}x{height}",
                    evidence={"width": width, "height": height}
                ))

    except Exception as e:
        exceptions.append(PageException(
            file_path=str(file_path),
            check_name="format_readability_check",
            severity=SeverityLevel.CRITICAL,
            message=f"Failed to open or read image: {str(e)}",
            evidence={"error": str(e)}
        ))
        return exceptions

    # Verify image integrity on a separate file handle (Pillow verify() must be
    # called on a freshly opened image before any pixel data is accessed).
    try:
        with Image.open(file_path) as verify_img:
            verify_img.verify()
    except Exception as e:
        exceptions.append(PageException(
            file_path=str(file_path),
            check_name="format_readability_check",
            severity=SeverityLevel.HIGH,
            message=f"Image integrity check failed: {str(e)}",
            evidence={"error": str(e)}
        ))

    return exceptions


def check_naming_pattern(file_path: Path, profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    if profile.naming_pattern:
        if not re.match(profile.naming_pattern, file_path.name):
            exceptions.append(PageException(
                file_path=str(file_path),
                check_name="naming_pattern_check",
                severity=SeverityLevel.HIGH,
                message=f"Filename '{file_path.name}' does not match expected pattern '{profile.naming_pattern}'.",
                evidence={"filename": file_path.name, "pattern": profile.naming_pattern}
            ))
    return exceptions


def check_sequence_gaps(file_paths: List[Path]) -> List[PageException]:
    exceptions = []
    num_pattern = re.compile(r'(\d+)')
    
    sequence_numbers = []
    for fp in file_paths:
        matches = num_pattern.findall(fp.stem)
        if matches:
            seq_num = int(matches[-1])
            sequence_numbers.append((seq_num, fp))
            
    if not sequence_numbers:
        return exceptions
        
    sequence_numbers.sort(key=lambda x: x[0])
    
    expected_next = sequence_numbers[0][0]
    for seq_num, fp in sequence_numbers:
        if seq_num > expected_next:
            for missing_num in range(expected_next, seq_num):
                exceptions.append(PageException(
                    file_path=str(fp),
                    check_name="sequence_gap_check",
                    severity=SeverityLevel.HIGH,
                    message=f"Sequence gap detected: missing page {missing_num}",
                    evidence={"missing_sequence_number": missing_num, "next_found": seq_num, "next_file": str(fp)}
                ))
            expected_next = seq_num + 1
        elif seq_num == expected_next:
            expected_next += 1

    return exceptions


def detect_exact_duplicates(checksums: Dict[str, str]) -> List[PageException]:
    exceptions = []
    hash_to_files: Dict[str, List[str]] = {}
    
    for filepath, sha256_hash in checksums.items():
        if sha256_hash not in hash_to_files:
            hash_to_files[sha256_hash] = []
        hash_to_files[sha256_hash].append(filepath)
        
    for sha256_hash, files in hash_to_files.items():
        if len(files) > 1:
            for filepath in files:
                other_files = [f for f in files if f != filepath]
                exceptions.append(PageException(
                    file_path=filepath,
                    check_name="exact_duplicate_check",
                    severity=SeverityLevel.CRITICAL,
                    message=f"File is an exact binary duplicate of {len(other_files)} other file(s).",
                    evidence={"duplicate_of": other_files, "hash": sha256_hash}
                ))
                
    return exceptions


def check_batch_integrity(batch_dir: Path, profile: AcceptanceProfile) -> Tuple[List[PageException], Dict[str, str]]:
    all_exceptions = []
    checksums = {}
    
    if not batch_dir.is_dir():
        all_exceptions.append(PageException(
            file_path=str(batch_dir),
            check_name="batch_integrity_check",
            severity=SeverityLevel.CRITICAL,
            message="Batch directory does not exist or is not a directory.",
            evidence={"batch_dir": str(batch_dir)}
        ))
        return all_exceptions, checksums

    file_paths = []
    for file_path in batch_dir.rglob("*"):
        if file_path.is_file():
            file_paths.append(file_path)
            
            try:
                sha256_hash = compute_sha256(file_path)
                checksums[str(file_path)] = sha256_hash
            except Exception as e:
                all_exceptions.append(PageException(
                    file_path=str(file_path),
                    check_name="compute_checksum",
                    severity=SeverityLevel.CRITICAL,
                    message=f"Failed to read file for checksum: {str(e)}",
                    evidence={"error": str(e)}
                ))
                continue
                
            all_exceptions.extend(check_file_format(file_path, profile))
            all_exceptions.extend(check_naming_pattern(file_path, profile))

    all_exceptions.extend(check_sequence_gaps(file_paths))
    all_exceptions.extend(detect_exact_duplicates(checksums))

    return all_exceptions, checksums
