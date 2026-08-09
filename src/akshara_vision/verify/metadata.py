import csv
import json
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Any

from akshara_vision.verify.models import SeverityLevel, PageException, AcceptanceProfile


logger = logging.getLogger(__name__)


def check_encoding(metadata_path: Path) -> List[PageException]:
    exceptions = []
    try:
        # Check for invalid UTF-8 sequences and null bytes
        with open(metadata_path, "rb") as f:
            content = f.read()
            if b"\x00" in content:
                exceptions.append(PageException(
                    file_path=str(metadata_path),
                    check_name="metadata_encoding",
                    severity=SeverityLevel.LOW,
                    message="Metadata file contains null bytes.",
                    evidence={},
                    confidence=1.0
                ))
            try:
                content.decode('utf-8')
            except UnicodeDecodeError as e:
                exceptions.append(PageException(
                    file_path=str(metadata_path),
                    check_name="metadata_encoding",
                    severity=SeverityLevel.LOW,
                    message="Metadata file is not valid UTF-8.",
                    evidence={'error': str(e)},
                    confidence=1.0
                ))
    except Exception as e:
        logger.error(f"Failed to read metadata file {metadata_path}: {e}")
    return exceptions


def load_metadata_file(metadata_path: Path) -> Tuple[List[Dict[str, str]], str]:
    rows = []
    format_type = "unknown"
    
    if not metadata_path.exists():
        return rows, format_type
        
    try:
        with open(metadata_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read().strip()
            
            if content.startswith('[') or content.startswith('{'):
                # Try JSON
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        data = [data]
                    if isinstance(data, list) and all(isinstance(i, dict) for i in data):
                        rows = [{str(k): str(v) for k, v in item.items()} for item in data]
                        format_type = "json"
                except json.JSONDecodeError:
                    pass
            
            if format_type == "unknown":
                # Try CSV
                f.seek(0)
                reader = csv.DictReader(f)
                if reader.fieldnames:
                    rows = [{str(k): str(v) for k, v in row.items()} for row in reader]
                    format_type = "csv"
    except Exception as e:
        logger.error(f"Error parsing metadata file {metadata_path}: {e}")
        
    return rows, format_type


def check_required_fields(rows: List[Dict[str, str]], required_fields: List[str], source_file: str) -> List[PageException]:
    exceptions = []
    for idx, row in enumerate(rows):
        missing = [f for f in required_fields if f not in row or not str(row[f]).strip()]
        if missing:
            exceptions.append(PageException(
                file_path=source_file,
                check_name="metadata_required_fields",
                severity=SeverityLevel.HIGH,
                message=f"Row {idx} is missing required fields.",
                evidence={'row_index': idx, 'missing_fields': missing},
                confidence=1.0
            ))
    return exceptions


def check_unique_fields(rows: List[Dict[str, str]], unique_fields: List[str], source_file: str) -> List[PageException]:
    exceptions = []
    for field in unique_fields:
        seen = {}
        for idx, row in enumerate(rows):
            val = str(row.get(field, "")).strip()
            if not val:
                continue
            if val in seen:
                seen[val].append(idx)
            else:
                seen[val] = [idx]
                
        for val, indices in seen.items():
            if len(indices) > 1:
                exceptions.append(PageException(
                    file_path=source_file,
                    check_name="metadata_unique_fields",
                    severity=SeverityLevel.HIGH,
                    message=f"Duplicate values found for unique field '{field}'.",
                    evidence={'field': field, 'duplicate_value': val, 'row_indices': indices},
                    confidence=1.0
                ))
    return exceptions


def check_filename_metadata_consistency(rows: List[Dict[str, str]], file_paths: List[Path], source_file: str) -> List[PageException]:
    exceptions = []
    file_names = {p.name: p for p in file_paths}
    
    # Try to find the filename column
    filename_col = None
    if rows:
        candidates = ['filename', 'file', 'path', 'file_name']
        for c in candidates:
            if c in rows[0]:
                filename_col = c
                break
                
    if not filename_col:
        # Can't check
        return exceptions
        
    referenced_files = set()
    for idx, row in enumerate(rows):
        val = str(row.get(filename_col, "")).strip()
        if val:
            referenced_files.add(val)
            # check if it exists in file_paths (basename comparison)
            if val not in file_names:
                exceptions.append(PageException(
                    file_path=source_file,
                    check_name="metadata_filename_consistency",
                    severity=SeverityLevel.MEDIUM,
                    message=f"Metadata row {idx} references missing file.",
                    evidence={'row_index': idx, 'missing_file': val},
                    confidence=1.0
                ))
                
    # Check for orphaned files
    for fn in file_names:
        if fn not in referenced_files:
            exceptions.append(PageException(
                file_path=str(file_names[fn]),
                check_name="metadata_filename_consistency",
                severity=SeverityLevel.MEDIUM,
                message="File is not referenced in metadata.",
                evidence={'unreferenced_file': fn},
                confidence=1.0
            ))
            
    return exceptions


def validate_metadata(metadata_path: Path, file_paths: List[Path], profile: AcceptanceProfile) -> List[PageException]:
    exceptions = []
    
    # 1. Encoding check
    exceptions.extend(check_encoding(metadata_path))
    
    # 2. Load
    rows, fmt = load_metadata_file(metadata_path)
    if not rows:
        return exceptions
        
    source_file = str(metadata_path)
    
    # 3. Required fields
    if profile.metadata_required_fields:
        exceptions.extend(check_required_fields(rows, profile.metadata_required_fields, source_file))
        
    # 4. Unique fields
    if profile.metadata_unique_fields:
        exceptions.extend(check_unique_fields(rows, profile.metadata_unique_fields, source_file))
        
    # 5. Consistency
    exceptions.extend(check_filename_metadata_consistency(rows, file_paths, source_file))
    
    return exceptions
