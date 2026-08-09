import dataclasses
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path

from akshara_vision.core.toml_compat import load_toml


class SeverityLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class PageException:
    file_path: str
    check_name: str
    severity: SeverityLevel
    message: str
    evidence: Dict[str, object] = field(default_factory=dict)
    confidence: float = 1.0
    page_number: Optional[int] = None
    
    def to_dict(self) -> Dict[str, object]:
        return {
            "file_path": self.file_path,
            "check_name": self.check_name,
            "severity": self.severity.value,
            "message": self.message,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "page_number": self.page_number,
        }


@dataclass
class AcceptanceProfile:
    name: str = 'default'
    version: str = '1.0.0'
    description: str = ''
    allowed_master_formats: List[str] = field(default_factory=lambda: ['.tif', '.tiff'])
    allowed_derivative_formats: List[str] = field(default_factory=lambda: ['.jpg', '.jpeg', '.png'])
    expected_dpi: Optional[int] = 300
    min_dpi: Optional[int] = 200
    expected_color_mode: Optional[str] = None
    expected_bit_depth: Optional[int] = None
    naming_pattern: Optional[str] = None
    required_directory_structure: Optional[List[str]] = None
    metadata_required_fields: List[str] = field(default_factory=list)
    metadata_unique_fields: List[str] = field(default_factory=list)
    blur_threshold: float = 100.0
    skew_threshold_degrees: float = 5.0
    blank_threshold: float = 0.99
    near_duplicate_hash_threshold: int = 5
    max_flag_rate: float = 0.10
    
    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "allowed_master_formats": self.allowed_master_formats,
            "allowed_derivative_formats": self.allowed_derivative_formats,
            "expected_dpi": self.expected_dpi,
            "min_dpi": self.min_dpi,
            "expected_color_mode": self.expected_color_mode,
            "expected_bit_depth": self.expected_bit_depth,
            "naming_pattern": self.naming_pattern,
            "required_directory_structure": self.required_directory_structure,
            "metadata_required_fields": self.metadata_required_fields,
            "metadata_unique_fields": self.metadata_unique_fields,
            "blur_threshold": self.blur_threshold,
            "skew_threshold_degrees": self.skew_threshold_degrees,
            "blank_threshold": self.blank_threshold,
            "near_duplicate_hash_threshold": self.near_duplicate_hash_threshold,
            "max_flag_rate": self.max_flag_rate,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> 'AcceptanceProfile':
        valid_keys = {f.name for f in dataclasses.fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered_data)


def load_acceptance_profile(path: Path) -> AcceptanceProfile:
    data = load_toml(path)
    return AcceptanceProfile.from_dict(data)


@dataclass
class BatchManifest:
    batch_path: str
    profile_name: str
    profile_version: str
    software_version: str = '1.1.0'
    total_files: int = 0
    total_exceptions: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    checksums: Dict[str, str] = field(default_factory=dict)
    started_at: str = ''
    completed_at: str = ''
    
    def to_dict(self) -> Dict[str, object]:
        return {
            "batch_path": self.batch_path,
            "profile_name": self.profile_name,
            "profile_version": self.profile_version,
            "software_version": self.software_version,
            "total_files": self.total_files,
            "total_exceptions": self.total_exceptions,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "medium_count": self.medium_count,
            "low_count": self.low_count,
            "info_count": self.info_count,
            "checksums": self.checksums,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class VerificationResult:
    manifest: BatchManifest
    exceptions: List[PageException] = field(default_factory=list)
    profile: Optional[AcceptanceProfile] = None
    passed: bool = True
    
    def to_dict(self) -> Dict[str, object]:
        return {
            "manifest": self.manifest.to_dict(),
            "exceptions": [e.to_dict() for e in self.exceptions],
            "profile": self.profile.to_dict() if self.profile else None,
            "passed": self.passed,
        }
    
    def summary_counts(self) -> Dict[str, int]:
        counts = {level.value: 0 for level in SeverityLevel}
        for exc in self.exceptions:
            counts[exc.severity.value] += 1
        return counts
