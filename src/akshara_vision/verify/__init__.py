"""Akshara Verify — Offline acceptance-preflight and audit for document digitization."""

from .models import (
    SeverityLevel,
    PageException,
    AcceptanceProfile,
    BatchManifest,
    VerificationResult,
    load_acceptance_profile,
)
from .integrity import (
    compute_sha256,
    check_file_format,
    check_naming_pattern,
    check_sequence_gaps,
    detect_exact_duplicates,
    check_batch_integrity,
)

from .pdfa import check_pdfa_compliance
from .classifier import classify_archival_defect, calibrate_profile_from_reviews
from .review_ui import start_review_server

__all__ = [
    "SeverityLevel",
    "PageException",
    "AcceptanceProfile",
    "BatchManifest",
    "VerificationResult",
    "load_acceptance_profile",
    "compute_sha256",
    "check_file_format",
    "check_naming_pattern",
    "check_sequence_gaps",
    "detect_exact_duplicates",
    "check_batch_integrity",
    "check_pdfa_compliance",
    "classify_archival_defect",
    "calibrate_profile_from_reviews",
    "start_review_server",
]
