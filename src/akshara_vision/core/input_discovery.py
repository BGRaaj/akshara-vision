import csv
import glob
import json
from pathlib import Path
from typing import Iterable, List

from akshara_vision.core.constants import SUPPORTED_INPUT_EXTENSIONS
from akshara_vision.core.models import InputSelection


def discover_inputs(raw_inputs: Iterable[str], recursive: bool = False) -> InputSelection:
    raw = [item for item in raw_inputs if item]
    files: List[Path] = []
    missing: List[str] = []
    unsupported: List[Path] = []

    for item in raw:
        matches = _expand_one(item, recursive=recursive)
        if not matches:
            missing.append(item)
            continue
        for path in matches:
            if path.is_dir():
                files.extend(_walk_dir(path, recursive=recursive))
            elif path.suffix.lower() == ".csv" or (
                path.suffix.lower() == ".json" and _looks_like_manifest(path)
            ):
                nested = discover_inputs(_read_manifest(path), recursive=recursive)
                files.extend(nested.files)
                missing.extend(nested.missing)
                unsupported.extend(nested.unsupported)
            elif is_supported_input(path):
                files.append(path)
            else:
                unsupported.append(path)

    unique_files = _unique_existing(files)
    return InputSelection(raw=raw, files=unique_files, missing=missing, unsupported=unsupported)


def is_supported_input(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS


def _expand_one(item: str, recursive: bool) -> List[Path]:
    expanded = Path(item).expanduser()
    if any(token in item for token in ["*", "?", "["]):
        return [Path(match).expanduser() for match in glob.glob(item, recursive=recursive)]
    if expanded.exists():
        return [expanded]
    return []


def _walk_dir(path: Path, recursive: bool) -> List[Path]:
    iterator = path.rglob("*") if recursive else path.glob("*")
    files: List[Path] = []
    for item in iterator:
        if not item.is_file():
            continue
        if item.suffix.lower() == ".csv" or (
            item.suffix.lower() == ".json" and _looks_like_manifest(item)
        ):
            files.extend(discover_inputs(_read_manifest(item), recursive=recursive).files)
        elif is_supported_input(item):
            files.append(item)
    return files


def _looks_like_manifest(path: Path) -> bool:
    if path.name.endswith(".manifest.json"):
        return True
    if path.suffix.lower() != ".json":
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, list) or (
        isinstance(data, dict) and ("inputs" in data or "files" in data)
    )


def _read_manifest(path: Path) -> List[str]:
    def resolve_manifest_value(value: str) -> str:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return str(candidate)
        return str(path.parent / candidate)

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        values = []
        for row in rows:
            value = row.get("path") or row.get("file") or row.get("input")
            if value:
                values.append(resolve_manifest_value(value))
        return values
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [
            resolve_manifest_value(str(item.get("path") if isinstance(item, dict) else item))
            for item in data
        ]
    if isinstance(data, dict):
        values = data.get("inputs") or data.get("files") or []
        return [
            resolve_manifest_value(str(item.get("path") if isinstance(item, dict) else item))
            for item in values
        ]
    return []


def _unique_existing(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    unique = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique
