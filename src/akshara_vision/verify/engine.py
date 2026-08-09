import os
import datetime
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from pathlib import Path
from typing import Optional, Callable, List


def _software_version() -> str:
    try:
        return _pkg_version("akshara-vision")
    except PackageNotFoundError:
        return "0.0.0"

from akshara_vision.verify.models import (
    AcceptanceProfile, BatchManifest, VerificationResult, SeverityLevel,
    load_acceptance_profile, PageException
)
from akshara_vision.verify.integrity import check_batch_integrity
from akshara_vision.verify.quality import check_image_quality, detect_near_duplicates
from akshara_vision.verify.metadata import validate_metadata
from akshara_vision.verify.pdfa import check_pdfa_compliance
from akshara_vision.verify.reports import generate_full_report

def discover_batch_files(batch_dir: Path) -> List[Path]:
    """Discover all files in a batch directory recursively, excluding hidden files and directories."""
    all_files = []
    for root, dirs, files in os.walk(batch_dir):
        # Skip hidden directories in-place so os.walk won't descend into them
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in files:
            if file.startswith('.'):
                continue
            all_files.append(Path(root) / file)

    return sorted(all_files)

def load_profile_by_name(name: str) -> AcceptanceProfile:
    """Load an AcceptanceProfile by name from standard locations."""
    # Reject names with path-traversal characters to prevent directory traversal
    import re as _re
    if not name or not _re.match(r'^[A-Za-z0-9_\-]+$', name):
        raise ValueError(
            f"Invalid profile name '{name}'. Only letters, digits, hyphens, and underscores are allowed."
        )
    # First check verify/profiles directory
    current_dir = Path(__file__).parent
    profiles_dir = current_dir / "profiles"
    profile_path = (profiles_dir / f"{name}.toml").resolve()
    if not str(profile_path).startswith(str(profiles_dir.resolve())):
        raise ValueError(f"Profile path escape detected for name '{name}'.")

    if profile_path.exists():
        return load_acceptance_profile(profile_path)

    # Then check user config
    user_config_dir = Path.home() / ".config" / "akshara-vision" / "profiles"
    user_profile_path = (user_config_dir / f"{name}.toml").resolve()
    if not str(user_profile_path).startswith(str(user_config_dir.resolve())):
        raise ValueError(f"Profile path escape detected for name '{name}'.")

    if user_profile_path.exists():
        return load_acceptance_profile(user_profile_path)

    # Fallback to default
    return AcceptanceProfile()

def run_verification(
    batch_dir: Path, 
    profile: AcceptanceProfile, 
    metadata_path: Optional[Path] = None, 
    output_dir: Optional[Path] = None, 
    progress_callback: Optional[Callable[[str], None]] = None
) -> VerificationResult:
    """Run the complete verification pipeline on a batch."""
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    all_exceptions: List[PageException] = []
    
    if progress_callback:
        progress_callback(f"Discovering files in {batch_dir}")
        
    all_files = discover_batch_files(batch_dir)
    total_files = len(all_files)
    
    if progress_callback:
        progress_callback(f"Running integrity checks on {total_files} files")
        
    integrity_exceptions, checksums = check_batch_integrity(batch_dir, profile)
    all_exceptions.extend(integrity_exceptions)
    
    # Filter image files
    image_extensions = {".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    image_files = [f for f in all_files if f.suffix.lower() in image_extensions]
    
    if progress_callback:
        progress_callback(f"Running quality checks on {len(image_files)} images")
        
    for idx, img_file in enumerate(image_files):
        if progress_callback and idx % 10 == 0:
            progress_callback(f"Processing image {idx+1}/{len(image_files)}")
        img_exceptions = check_image_quality(img_file, profile)
        all_exceptions.extend(img_exceptions)
        
    if progress_callback:
        progress_callback("Detecting near duplicates")
        
    dup_exceptions = detect_near_duplicates(image_files, profile)
    all_exceptions.extend(dup_exceptions)
    
    pdf_files = [f for f in all_files if f.suffix.lower() == ".pdf"]
    if pdf_files:
        if progress_callback:
            progress_callback(f"Checking PDF/A compliance on {len(pdf_files)} PDF documents")
        for pdf_file in pdf_files:
            all_exceptions.extend(check_pdfa_compliance(pdf_file, profile))
    
    if metadata_path and metadata_path.exists():
        if progress_callback:
            progress_callback("Validating metadata")
        meta_exceptions = validate_metadata(metadata_path, all_files, profile)
        all_exceptions.extend(meta_exceptions)
        
    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Calculate counts
    critical_count = 0
    high_count = 0
    medium_count = 0
    low_count = 0
    info_count = 0
    
    for exc in all_exceptions:
        sev_name = exc.severity.name if hasattr(exc.severity, "name") else str(exc.severity)
        if sev_name == "CRITICAL": critical_count += 1
        elif sev_name == "HIGH": high_count += 1
        elif sev_name == "MEDIUM": medium_count += 1
        elif sev_name == "LOW": low_count += 1
        elif sev_name == "INFO": info_count += 1

    manifest = BatchManifest(
        batch_path=str(batch_dir),
        profile_name=profile.name,
        profile_version=profile.version,
        software_version=_software_version(),
        total_files=total_files,
        total_exceptions=len(all_exceptions),
        critical_count=critical_count,
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
        info_count=info_count,
        checksums=checksums,
        started_at=started_at,
        completed_at=completed_at
    )
    
    passed = (critical_count == 0 and high_count == 0)
    
    result = VerificationResult(
        manifest=manifest,
        exceptions=all_exceptions,
        profile=profile,
        passed=passed
    )
    
    if output_dir:
        if progress_callback:
            progress_callback(f"Generating reports in {output_dir}")
        generate_full_report(result, output_dir)
        
    if progress_callback:
        progress_callback("Verification complete")
        
    return result
