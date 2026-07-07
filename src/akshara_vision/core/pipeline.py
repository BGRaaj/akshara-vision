import gc
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from akshara_vision.core.constants import (
    EXECUTION_MODES,
    TRANSLATION_FAILURE_REASONS,
)
from akshara_vision.core.models import RunRequest, WorkflowProfile, effective_translation_mode
from akshara_vision.exporters.base import ExportResult
from akshara_vision.instructions import load_instruction
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import get_provider

try:
    from PIL import Image, ImageOps
except ModuleNotFoundError:  # pragma: no cover - optional runtime guard
    Image = None
    ImageOps = None


TEXT_EXTENSIONS = {".txt", ".md", ".html", ".hocr", ".xml", ".json"}
ProgressCallback = Callable[[str, str, int], None]
RESTORATION_CHUNK_CHARS = 5000
TRANSLATION_CHUNK_CHARS = 5000
BLANK_PAGE_REASON = "blank page or no readable text"
DEFAULT_PROVIDER_RETRIES = 3
MAX_FIGURE_CROPS_PER_PAGE = 4

EXECUTION_MODE_PDF_DPI = {
    "fast": 200,
    "balanced": 300,
    "quality": 400,
}

EXECUTION_MODE_IMAGE_PSM = {
    "fast": "6",
    "balanced": "1",
    "quality": "1",
}

@dataclass
class StageWriter:
    run_dir: Path
    source_language: str
    output_language: str

    def __post_init__(self) -> None:
        self.stages_dir = self.run_dir / "stages"
        self.restored_dir = self.stages_dir / "restored"
        self.translated_dir = self.stages_dir / "translated"
        self.combined_dir = self.stages_dir / "combined"
        self.items_dir = self.run_dir / "items"
        self.assets_dir = self.run_dir / "assets"
        self.restored_dir.mkdir(parents=True, exist_ok=True)
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        self.combined_dir.mkdir(parents=True, exist_ok=True)
        self.items_dir.mkdir(parents=True, exist_ok=True)

    def write_raw_checkpoint(self, text: str) -> Path:
        path = self.run_dir / "restored_text.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_raw_ocr(self, text: str) -> Path:
        path = self.run_dir / "raw_ocr.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_restored_piece(
        self, source_index: int, source_name: str, piece_index: int, text: str
    ) -> Path:
        return self._write_piece(
            self.restored_dir, source_index, source_name, piece_index, "restored", text
        )

    def restored_piece_path(self, source_index: int, source_name: str, piece_index: int) -> Path:
        return self._piece_path(self.restored_dir, source_index, source_name, piece_index, "restored")

    def write_translated_piece(
        self, source_index: int, source_name: str, piece_index: int, text: str
    ) -> Path:
        return self._write_piece(
            self.translated_dir, source_index, source_name, piece_index, "translated", text
        )

    def write_combined_restored(self, text: str) -> Path:
        path = self.combined_dir / f"restored__{_language_slug(self.source_language)}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_combined_translated(self, text: str) -> Path:
        path = self.combined_dir / (
            f"translated__{_language_slug(self.source_language)}-to-{_language_slug(self.output_language)}.txt"
        )
        path.write_text(text, encoding="utf-8")
        return path

    def write_item_restored(self, source_index: int, source_name: str, text: str) -> Path:
        item_dir = self._item_dir(source_index, source_name)
        path = item_dir / f"restored__{_language_slug(self.source_language)}.txt"
        self._write_text_with_json(
            path,
            text,
            {
                "kind": "item",
                "stage": "restored",
                "source_index": source_index,
                "source_name": source_name,
                "language": self.source_language,
            },
        )
        return path

    def write_item_translated(self, source_index: int, source_name: str, text: str) -> Path:
        item_dir = self._item_dir(source_index, source_name)
        source = _language_slug(self.source_language)
        target = _language_slug(self.output_language)
        path = item_dir / f"translated__{source}-to-{target}.txt"
        self._write_text_with_json(
            path,
            text,
            {
                "kind": "item",
                "stage": "translated",
                "source_index": source_index,
                "source_name": source_name,
                "source_language": self.source_language,
                "output_language": self.output_language,
            },
        )
        return path

    def write_item_final(self, source_index: int, source_name: str, text: str) -> Path:
        item_dir = self._item_dir(source_index, source_name)
        path = item_dir / f"final__{_language_slug(self.output_language)}.txt"
        self._write_text_with_json(
            path,
            text,
            {
                "kind": "item",
                "stage": "final",
                "source_index": source_index,
                "source_name": source_name,
                "language": self.output_language,
            },
        )
        return path

    def write_archive_item_restored(
        self, source_index: int, source_name: str, archive_label: str, text: str
    ) -> Path:
        item_dir = self._archive_item_dir(source_index, source_name, archive_label)
        path = item_dir / f"restored__{_language_slug(self.source_language)}.txt"
        self._write_text_with_json(
            path,
            text,
            {
                "kind": "archive-item",
                "stage": "restored",
                "source_index": source_index,
                "source_name": source_name,
                "archive_label": archive_label,
                "language": self.source_language,
            },
        )
        return path

    def archive_item_restored_path(
        self, source_index: int, source_name: str, archive_label: str
    ) -> Path:
        item_dir = self._archive_item_dir(source_index, source_name, archive_label)
        return item_dir / f"restored__{_language_slug(self.source_language)}.txt"

    def write_archive_item_final(
        self, source_index: int, source_name: str, archive_label: str, text: str
    ) -> Path:
        item_dir = self._archive_item_dir(source_index, source_name, archive_label)
        path = item_dir / f"final__{_language_slug(self.output_language)}.txt"
        self._write_text_with_json(
            path,
            text,
            {
                "kind": "archive-item",
                "stage": "final",
                "source_index": source_index,
                "source_name": source_name,
                "archive_label": archive_label,
                "language": self.output_language,
            },
        )
        return path

    def write_archive_folder_combined(
        self, source_index: int, source_name: str, folder_label: str, text: str
    ) -> Path:
        folder_dir = self._archive_folder_dir(source_index, source_name, folder_label)
        path = folder_dir / f"combined__{_language_slug(self.output_language)}.txt"
        self._write_text_with_json(
            path,
            text,
            {
                "kind": "archive-folder",
                "stage": "combined",
                "source_index": source_index,
                "source_name": source_name,
                "folder_label": folder_label,
                "language": self.output_language,
            },
        )
        return path

    def write_final_output_aliases(self, text: str) -> List[Path]:
        aliases = [
            self.run_dir / "akshara_output.txt",
            self.run_dir
            / f"akshara_output__{_language_slug(self.output_language)}.txt",
        ]
        written = []
        for path in aliases:
            self._write_text_with_json(path, text, {"kind": "run-alias", "stage": "final"})
            written.append(path)
        return written

    def write_stage_manifest(self, manifest: Dict[str, object]) -> Path:
        path = self.stages_dir / "stage_manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def write_image_asset(
        self,
        source_index: int,
        source_name: str,
        image_path: Path,
        piece_index: int,
        kind: str,
        dpi: Optional[int] = None,
    ) -> Dict[str, object]:
        asset_dir = self.assets_dir / _slugify(source_name)
        asset_dir.mkdir(parents=True, exist_ok=True)
        suffix = image_path.suffix.lower() or ".png"
        asset_path = asset_dir / f"{source_index:04d}-{piece_index:04d}-{_slugify(kind)}{suffix}"
        shutil.copy2(image_path, asset_path)
        width, height = _image_dimensions(asset_path)
        return {
            "kind": kind,
            "path": _safe_path(asset_path),
            "width": width,
            "height": height,
            "dpi": dpi,
            "placement": _asset_placement(width, height),
        }

    def write_figure_asset(
        self,
        source_index: int,
        source_name: str,
        image,
        piece_index: int,
        figure_index: int,
        bbox: tuple[int, int, int, int],
        dpi: Optional[int] = None,
    ) -> Dict[str, object]:
        asset_dir = self.assets_dir / _slugify(source_name)
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"{source_index:04d}-{piece_index:04d}-figure-{figure_index:02d}.png"
        image.save(asset_path)
        width, height = _image_dimensions(asset_path)
        return {
            "kind": "figure-crop",
            "path": _safe_path(asset_path),
            "_local_path": str(asset_path),
            "width": width,
            "height": height,
            "dpi": dpi,
            "bbox": list(bbox),
            "placement": _asset_placement(width, height),
        }

    def _item_dir(self, source_index: int, source_name: str) -> Path:
        parts = _archive_label_parts(source_name)
        if len(parts) == 1:
            item_dir = self.items_dir / f"{source_index:04d}-{_slugify(parts[0])}"
        else:
            item_dir = self.items_dir / _slugify(parts[0])
            for part in parts[1:-1]:
                item_dir = item_dir / _slugify(part)
            item_dir = item_dir / f"{source_index:04d}-{_slugify(parts[-1])}"
        item_dir.mkdir(parents=True, exist_ok=True)
        return item_dir

    def _archive_item_dir(self, source_index: int, source_name: str, archive_label: str) -> Path:
        parts = _archive_label_parts(archive_label)
        item_dir = self._item_dir(source_index, source_name) / "archive"
        for part in parts:
            item_dir = item_dir / _slugify(part)
        item_dir.mkdir(parents=True, exist_ok=True)
        return item_dir

    def _archive_folder_dir(self, source_index: int, source_name: str, folder_label: str) -> Path:
        item_dir = self._item_dir(source_index, source_name) / "archive"
        for part in _archive_label_parts(folder_label):
            item_dir = item_dir / _slugify(part)
        item_dir.mkdir(parents=True, exist_ok=True)
        return item_dir

    def _write_piece(
        self,
        stage_dir: Path,
        source_index: int,
        source_name: str,
        piece_index: int,
        stage_name: str,
        text: str,
    ) -> Path:
        source_dir = _numbered_label_dir(stage_dir, source_index, source_name)
        source_dir.mkdir(parents=True, exist_ok=True)
        path = self._piece_path(stage_dir, source_index, source_name, piece_index, stage_name)
        language = self.output_language if stage_name == "translated" else self.source_language
        self._write_text_with_json(
            path,
            text,
            {
                "kind": "stage-piece",
                "stage": stage_name,
                "source_index": source_index,
                "source_name": source_name,
                "piece_index": piece_index,
                "language": language,
            },
        )
        return path

    def _write_text_with_json(self, path: Path, text: str, metadata: Dict[str, object]) -> None:
        path.write_text(text, encoding="utf-8")
        payload = dict(metadata)
        payload["text"] = text
        path.with_name(path.name + ".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _piece_path(
        self,
        stage_dir: Path,
        source_index: int,
        source_name: str,
        piece_index: int,
        stage_name: str,
    ) -> Path:
        source_dir = _numbered_label_dir(stage_dir, source_index, source_name)
        language = self.output_language if stage_name == "translated" else self.source_language
        return source_dir / f"{piece_index:04d}-{stage_name}__{_language_slug(language)}.txt"


def run_pipeline(
    request: RunRequest, progress: Optional[ProgressCallback] = None
) -> Dict[str, object]:
    profile = request.profile
    profile.sync_translation_defaults()
    if request.resume_run_dir:
        run_dir = Path(request.resume_run_dir).expanduser()
        timestamp = _timestamp_from_run_dir(run_dir) or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_root = Path(profile.output_dir).expanduser()
        run_dir = output_root / f"{profile.name}-{timestamp}"
    _notify(progress, "prepare", "Preparing run folder")
    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts = StageWriter(
        run_dir=run_dir,
        source_language=profile.source_language,
        output_language=profile.output_language,
    )

    _notify(progress, "instructions", "Loading restoration instructions")
    instruction = load_instruction(profile.instruction_preset)
    _notify(progress, "provider", f"Selecting provider: {profile.model.provider}")
    provider = get_provider(profile.model.provider)
    cleaned_parts: List[str] = []
    raw_parts: List[str] = []
    restored_sources: List[Dict[str, object]] = []
    restoration_records: List[Dict[str, object]] = []
    consistency_state = _new_consistency_state(profile)
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "truncated": False,
    }
    _write_run_state(
        run_dir,
        {
            "status": "running",
            "profile": profile.to_dict(),
            "created_at": timestamp,
            "total_inputs": len(request.inputs.files),
            "input_files": [_safe_path(p) for p in request.inputs.files],
            "completed_inputs": [],
            "consistency": consistency_state,
            "next_action": "Run can be recovered with `akv resume <run-folder>` or `akv combine <run-folder>`.",
        },
    )

    def _add_usage(u: dict):
        if not u:
            return
        total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
        total_usage["completion_tokens"] += u.get("completion_tokens", 0)
        total_usage["total_tokens"] += u.get("total_tokens", 0)
        if u.get("truncated"):
            total_usage["truncated"] = True

    for index, path in enumerate(request.inputs.files, start=1):
        suffix = path.suffix.lower()
        source_label = request.inputs.label_for(path)
        try:
            if suffix not in TEXT_EXTENSIONS:
                if not _is_vision_model(profile.model.model):
                    raise RuntimeError(
                        f"Processing {source_label} requires a multimodal vision model. "
                        f"The selected model '{profile.model.model}' is text-only."
                    )
                _notify(progress, "decode", f"Preparing multimodal {source_label}", advance=1)
                if suffix == ".pdf":
                    cleaned, restoration_record, usage = _restore_multimodal_pdf(
                        path,
                        instruction,
                        profile,
                        provider,
                        progress,
                        artifacts,
                        index,
                        source_label=source_label,
                        consistency_state=consistency_state,
                    )
                elif suffix == ".zip":
                    cleaned, restoration_record, usage = _restore_multimodal_zip(
                        path,
                        instruction,
                        profile,
                        provider,
                        progress,
                        artifacts,
                        index,
                        source_label=source_label,
                        consistency_state=consistency_state,
                    )
                else:
                    cleaned, restoration_record, usage = _restore_multimodal_image(
                        path,
                        instruction,
                        profile,
                        provider,
                        artifacts,
                        index,
                        source_label=source_label,
                        consistency_state=consistency_state,
                        progress=progress,
                    )
                raw_text = f"[Multimodal Input: {source_label}]"
                _add_usage(usage)
            else:
                _notify(progress, "decode", f"Reading text from {source_label}", advance=1)
                raw_text = path.read_text(encoding="utf-8", errors="replace")
                _notify(progress, "clean", f"Restoring text from {source_label}", advance=1)
                cleaned, restoration_record, usage = _restore_text(
                    raw_text,
                    instruction,
                    profile,
                    provider,
                    artifacts,
                    index,
                    path,
                    source_label=source_label,
                    consistency_state=consistency_state,
                )
                _add_usage(usage)
        except Exception as exc:
            failure_reason = _infer_failure_reason("", exception=exc)
            cleaned = ""
            raw_text = f"[Failed Input: {source_label}] {exc}"
            restoration_record = {
                "status": "failed",
                "chunks": [
                    {
                        "index": 1,
                        "source": source_label,
                        "restored_text": "",
                        "status": "failed",
                        "failure_reason": failure_reason,
                    }
                ],
                "failure_reason": failure_reason,
            }
            _notify(progress, "error", f"Recorded failed source {source_label}: {failure_reason}", advance=1)

        raw_parts.append(f"===== {source_label} =====\n{raw_text}".strip())
        source_text = cleaned.strip() + "\n"
        artifacts.write_item_restored(index, source_label, source_text)
        restored_sources.append(
            {
                "index": index,
                "name": source_label,
                "path": _safe_path(path),
                "text": source_text,
            }
        )
        restoration_records.append(
            {
                "source": _safe_path(path),
                "label": source_label,
                "status": restoration_record["status"],
                "chunks": restoration_record["chunks"],
                "failure_reason": restoration_record.get("failure_reason", ""),
            }
        )
        _write_run_state(
            run_dir,
            {
                "completed_inputs": [
                    {
                        "index": item["index"],
                        "name": item["name"],
                        "path": str(item["path"]),
                    }
                    for item in restored_sources
                ],
                "failed_inputs": [
                    {
                        "source": record["source"],
                        "label": record["label"],
                        "failure_reason": record.get("failure_reason", ""),
                    }
                    for record in restoration_records
                    if record.get("status") == "failed"
                    or (record.get("failure_reason") and record.get("status") != "restored")
                ],
                "consistency": consistency_state,
            },
        )
        cleaned_parts.append(f"===== {source_label} =====\n{cleaned}".strip())
        _notify(progress, "source", f"Bundling source {source_label}", advance=1)
        _copy_source(path, run_dir / "sources", index=index, label=source_label)
        gc.collect()

    raw_text = "\n\n".join(raw_parts).strip() + "\n"
    cleaned_text = "\n\n".join(cleaned_parts).strip() + "\n"
    _notify(progress, "write", "Writing raw OCR text", advance=1)
    artifacts.write_raw_ocr(raw_text)
    artifacts.write_raw_checkpoint(cleaned_text)
    artifacts.write_combined_restored(cleaned_text)

    translation_result = _apply_translation_stage(
        cleaned_text,
        profile,
        provider,
        artifacts,
        progress,
        restored_sources=restored_sources,
    )
    final_text = translation_result["text"]
    translation_metadata = translation_result["metadata"]
    translation_usage = translation_result["usage"]
    _add_usage(translation_usage)

    document_structure = _document_structure(restoration_records, profile.document_type, profile)
    detected_title = _detected_title(document_structure) or f"Akshara Vision - {profile.name}"
    metadata = {
        "title": detected_title,
        "created_at": timestamp,
        "workflow": profile.workflow,
        "document_type": profile.document_type,
        "source_language": profile.source_language,
        "output_language": profile.output_language,
        "language_policy": profile.language_policy,
        "translation_mode": profile.translation_mode,
        "translation_mode_effective": profile.effective_translation_mode(),
        "provider": profile.model.provider,
        "model": profile.model.model,
        "instruction_preset": profile.instruction_preset,
        "restoration": restoration_records,
        "translation": translation_metadata,
        "inputs": [
            {"path": _safe_path(path), "label": request.inputs.label_for(path)}
            for path in request.inputs.files
        ],
        "missing": request.inputs.missing,
        "unsupported": [_safe_path(path) for path in request.inputs.unsupported],
        "usage": total_usage,
        "consistency": consistency_state,
        "document_structure": document_structure,
        "assembly_profile": _assembly_profile(profile.document_type, profile.output_formats),
    }

    destination = run_dir / "akshara_output"
    exports = _export_text(final_text, destination, metadata, profile.output_formats, progress)

    _notify(progress, "manifest", "Writing run manifest", advance=1)
    _write_nested_folder_combines(artifacts.items_dir, profile.output_language)
    profile_manifest = profile.to_dict()
    profile_manifest["output_dir"] = _safe_path(Path(profile.output_dir).expanduser())
    manifest = {
        "profile": profile_manifest,
        "metadata": metadata,
        "exports": [
            {
                "format": item.format,
                "path": _safe_path(item.path),
                "available": item.available,
                "detail": item.detail,
            }
            for item in exports
        ],
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    artifacts.write_stage_manifest(
        {
            "run_dir": _safe_path(run_dir),
            "source_language": profile.source_language,
            "output_language": profile.output_language,
            "language_policy": profile.language_policy,
            "translation_mode": profile.translation_mode,
            "translation_mode_effective": profile.effective_translation_mode(),
            "inputs": [
                {"path": _safe_path(path), "label": request.inputs.label_for(path)}
                for path in request.inputs.files
            ],
        }
    )
    artifacts.write_final_output_aliases(final_text)
    if translation_metadata.get("status") != "skipped":
        artifacts.write_combined_translated(final_text)
    _notify(progress, "complete", "Run complete", advance=1)
    _write_run_state(
        run_dir,
        {
            "status": "complete",
            "completed_inputs": [
                {
                    "index": item["index"],
                    "name": item["name"],
                    "path": str(item["path"]),
                }
                for item in restored_sources
            ],
            "failed_inputs": [
                {
                    "source": record["source"],
                    "label": record["label"],
                    "failure_reason": record.get("failure_reason", ""),
                }
                for record in restoration_records
                if record.get("status") == "failed"
                or (record.get("failure_reason") and record.get("status") != "restored")
            ],
            "consistency": consistency_state,
            "next_action": "Run complete.",
        },
    )
    return {"run_dir": run_dir, "exports": exports, "manifest": manifest}


def combine_stage_outputs(run_dir: Path) -> Dict[str, object]:
    run_dir = Path(run_dir)
    stage_root = run_dir / "stages"
    items_root = run_dir / "items"
    if not stage_root.exists() and not items_root.exists():
        raise RuntimeError(f"No staged outputs found in {run_dir}.")

    combined_parts = _combined_parts_from_items(items_root)
    if not combined_parts and stage_root.exists():
        combined_parts = _combined_parts_from_stages(stage_root)
    if not combined_parts:
        raise RuntimeError(f"No staged pieces found in {stage_root}.")

    combined_text = "\n\n".join(part for part in combined_parts if part.strip()).strip()
    if not combined_text:
        combined_text = "[missing text]"

    combined_dir = stage_root / "combined"
    combined_dir.mkdir(parents=True, exist_ok=True)
    combined_path = combined_dir / "recombined.txt"
    combined_path.write_text(combined_text + "\n", encoding="utf-8")

    run_manifest = run_dir / "run_manifest.json"
    language_suffix = _combine_language_suffix(run_manifest)

    output_alias = run_dir / f"akshara_output__{language_suffix}.txt"
    output_alias.write_text(combined_text + "\n", encoding="utf-8")
    canonical = run_dir / "akshara_output.txt"
    canonical.write_text(combined_text + "\n", encoding="utf-8")
    _write_nested_folder_combines(items_root, language_suffix)

    manifest = _load_manifest(run_manifest)
    metadata = _combine_metadata(manifest, run_dir, language_suffix)
    output_formats = _output_formats_from_manifest(manifest)
    exports = _export_text(
        combined_text + "\n",
        run_dir / "akshara_output",
        metadata,
        output_formats,
    )
    _write_recombined_manifest(run_manifest, manifest, exports)

    return {
        "run_dir": run_dir,
        "combined_path": combined_path,
        "output_path": canonical,
        "alias_path": output_alias,
        "exports": exports,
    }


def _combined_parts_from_items(items_root: Path) -> List[str]:
    if not items_root.exists():
        return []
    combined_parts: List[str] = []
    for output_path in _preferred_item_outputs(items_root):
        text = _read_structured_output_text(output_path).strip()
        if text:
            label = str(output_path.parent.relative_to(items_root)).replace("\\", "/")
            combined_parts.append(f"===== {label} =====\n{text}")
    return combined_parts


def _preferred_item_outputs(items_root: Path) -> List[Path]:
    for pattern in (
        "final__*.json",
        "translated__*.json",
        "restored__*.json",
        "final__*.txt",
        "translated__*.txt",
        "restored__*.txt",
    ):
        paths = sorted(path for path in items_root.rglob(pattern) if path.is_file())
        if paths:
            return paths
    return []


def _read_structured_output_text(path: Path) -> str:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")
        if isinstance(data, dict):
            return str(data.get("text") or data.get("restored_text") or "")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _write_nested_folder_combines(items_root: Path, language_suffix: str) -> List[Path]:
    if not items_root.exists():
        return []
    output_paths = _preferred_item_outputs(items_root)
    if not output_paths:
        return []

    folder_parts: Dict[Path, List[tuple[str, str]]] = {}
    for output_path in output_paths:
        text = _read_structured_output_text(output_path).strip()
        if not text:
            continue
        label = str(output_path.parent.relative_to(items_root)).replace("\\", "/")
        for folder in _ancestor_output_folders(items_root, output_path.parent):
            folder_parts.setdefault(folder, []).append((label, text))

    written = []
    for folder, parts in sorted(folder_parts.items(), key=lambda item: str(item[0])):
        combined = "\n\n".join(f"===== {label} =====\n{text}" for label, text in parts).strip()
        if not combined:
            continue
        path = folder / f"combined__{_language_slug(language_suffix)}.txt"
        path.write_text(combined + "\n", encoding="utf-8")
        written.append(path)
    return written


def _ancestor_output_folders(items_root: Path, leaf_item_dir: Path) -> List[Path]:
    folders = []
    current = leaf_item_dir.parent
    while current != items_root and items_root in current.parents:
        folders.append(current)
        current = current.parent
    return folders


def _combined_parts_from_stages(stage_root: Path) -> List[str]:
    translated_groups = sorted((stage_root / "translated").glob("*"))
    restored_groups = sorted((stage_root / "restored").glob("*"))
    source_groups = translated_groups if translated_groups else restored_groups
    combined_parts: List[str] = []
    for source_group in source_groups:
        if not source_group.is_dir():
            continue
        piece_paths = sorted(source_group.glob("*.txt"))
        if not piece_paths:
            continue
        pieces = [path.read_text(encoding="utf-8", errors="replace").strip() for path in piece_paths]
        text = "\n".join(part for part in pieces if part).strip()
        if text:
            combined_parts.append(f"===== {source_group.name} =====\n{text}")
    return combined_parts


def _combine_language_suffix(run_manifest: Path) -> str:
    language_suffix = "combined"
    if run_manifest.exists():
        try:
            manifest = json.loads(run_manifest.read_text(encoding="utf-8"))
            metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
            language_suffix = _language_slug(metadata.get("output_language") or language_suffix)
        except json.JSONDecodeError:
            pass
    return language_suffix


def _load_manifest(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _combine_metadata(
    manifest: Dict[str, object], run_dir: Path, language_suffix: str
) -> Dict[str, object]:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    combined = dict(metadata)
    combined.setdefault("title", f"Akshara Vision - {run_dir.name}")
    combined["recombined"] = True
    combined["output_language"] = metadata.get("output_language") or language_suffix
    return combined


def _output_formats_from_manifest(manifest: Dict[str, object]) -> List[str]:
    profile = manifest.get("profile") if isinstance(manifest.get("profile"), dict) else {}
    formats = profile.get("output_formats") if isinstance(profile, dict) else None
    if isinstance(formats, list):
        cleaned = [str(item) for item in formats if str(item).strip()]
        return cleaned or ["txt"]
    if isinstance(formats, str):
        cleaned = [item.strip() for item in formats.split(",") if item.strip()]
        return cleaned or ["txt"]
    return ["txt"]


def _export_text(
    text: str,
    destination: Path,
    metadata: Dict[str, object],
    output_formats: List[str],
    progress: Optional[ProgressCallback] = None,
) -> List[ExportResult]:
    exports: List[ExportResult] = []
    registry = exporter_registry()
    for output_format in output_formats:
        exporter = registry.get(output_format)
        if exporter is None:
            continue
        _notify(progress, "export", f"Exporting {output_format}", advance=1)
        exports.append(exporter.export(text, destination, metadata))
    return exports


def _write_recombined_manifest(
    path: Path, manifest: Dict[str, object], exports: List[ExportResult]
) -> None:
    if not manifest:
        return
    manifest["recombined_exports"] = [
        {
            "format": item.format,
            "path": _safe_path(item.path),
            "available": item.available,
            "detail": item.detail,
        }
        for item in exports
    ]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_run_state(run_dir: Path, state: Dict[str, object]) -> Path:
    path = run_dir / "run_state.json"
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    merged = {**existing, **state}
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _timestamp_from_run_dir(run_dir: Path) -> Optional[str]:
    match = re.search(r"(\d{8}-\d{6})$", run_dir.name)
    return match.group(1) if match else None


def _write_consistency_checkpoint(
    run_dir: Path,
    profile: WorkflowProfile,
    consistency_state: Optional[Dict[str, object]],
    active_source: str,
) -> None:
    if not consistency_state:
        return
    _write_run_state(
        run_dir,
        {
            "active_source": active_source,
            "consistency": consistency_state,
        },
    )


def estimate_progress_units(request: RunRequest) -> int:
    total = 6 + len(request.profile.output_formats)
    for path in request.inputs.files:
        total += 3
        total += max(_estimate_input_words(path), 1) * 2
    return max(total, 1)


def _restore_text(
    raw_text: str,
    instruction: str,
    profile: WorkflowProfile,
    provider,
    artifacts: StageWriter,
    source_index: int,
    source_path: Path,
    media_path: Optional[Path] = None,
    source_label: Optional[str] = None,
    consistency_state: Optional[Dict[str, object]] = None,
    progress: Optional[ProgressCallback] = None,
) -> tuple:
    chunks = _split_text_chunks(raw_text)
    restored_chunks: List[str] = []
    structured_chunks: List[Dict[str, object]] = []
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "truncated": False,
    }
    for index, chunk in enumerate(chunks, start=1):
        prompt = _task_text(chunk, profile, consistency_state)
        result, usage = _restore_with_retry(
            provider,
            prompt,
            instruction,
            profile.model,
            media_path=media_path,
            progress=progress,
        )
        if usage:
            total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            total_usage["total_tokens"] += usage.get("total_tokens", 0)
            if usage.get("truncated"):
                total_usage["truncated"] = True
            _notify_usage(
                progress,
                f"Restored {source_label or source_path.name} chunk {index}/{len(chunks)}",
                usage,
                total_usage,
            )

        parsed = _parse_restoration_result(result, chunk)
        restored_text = parsed["restored_text"].strip()
        if not restored_text:
            restored_text = chunk.strip()
            parsed["failure_reason"] = parsed["failure_reason"] or "source unreadable or too blurry"
        review_note = ""
        pre_review_text = ""
        if restored_text:
            restored_text, review_note, review_usage, pre_review_text = _maybe_review_restored_text(
                restored_text,
                profile,
                provider,
                instruction,
                progress,
                f"{source_label or source_path.name} chunk {index}",
                consistency_state,
            )
            if review_usage:
                total_usage["prompt_tokens"] += review_usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += review_usage.get("completion_tokens", 0)
                total_usage["total_tokens"] += review_usage.get("total_tokens", 0)
                if review_usage.get("truncated"):
                    total_usage["truncated"] = True
                _notify_usage(
                    progress,
                    f"Reviewed {source_label or source_path.name} chunk {index}/{len(chunks)}",
                    review_usage,
                    total_usage,
                )
        if usage and usage.get("truncated"):
            parsed["failure_reason"] = "model context or output limit reached"
            parsed["status"] = "partial"
        restored_chunks.append(restored_text)
        _update_consistency_state(consistency_state, restored_text)
        artifacts.write_restored_piece(
            source_index, source_label or source_path.name, index, restored_text + "\n"
        )
        chunk_record = {
            "index": index,
            "input": _short_excerpt(chunk),
            "restored_text": restored_text,
            "uncertain": parsed["uncertain"],
            "notes": _join_notes(parsed["notes"], review_note),
            "status": parsed["status"],
            "failure_reason": parsed["failure_reason"],
        }
        if pre_review_text:
            chunk_record["pre_review_text"] = pre_review_text
        structured_chunks.append(chunk_record)
    combined = "\n\n".join(part for part in restored_chunks if part.strip()).strip()
    if not combined:
        combined = "[missing text]"
    file_failure_reason = next(
        (chunk["failure_reason"] for chunk in structured_chunks if chunk.get("failure_reason")),
        "",
    )
    file_status = "restored" if not file_failure_reason else "partial"
    return (
        combined + "\n",
        {"status": file_status, "chunks": structured_chunks, "failure_reason": file_failure_reason},
        total_usage,
    )


def _copy_source(path: Path, destination: Path, index: int, label: Optional[str] = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    parts = _archive_label_parts(label or path.name)
    if len(parts) == 1:
        target = destination / f"{index:04d}-{path.name}"
    else:
        target_dir = destination
        for part in parts[:-1]:
            target_dir = target_dir / _slugify(part)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{index:04d}-{path.name}"
    try:
        shutil.copy2(path, target)
    except OSError:
        return


def _safe_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def _slugify(value: object, default: str = "item") -> str:
    text = str(value or "").strip().lower()
    text = "".join(char if char.isalnum() else "-" for char in text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text or default


def _language_slug(value: object) -> str:
    return _slugify(value, default="language")


def _archive_label_parts(label: str) -> List[str]:
    parts = []
    for part in str(label or "").replace("\\", "/").split("/"):
        part = part.strip()
        if part and part not in {".", ".."}:
            parts.append(part)
    return parts or ["root"]


def _numbered_label_dir(root: Path, source_index: int, source_name: str) -> Path:
    parts = _archive_label_parts(source_name)
    if len(parts) == 1:
        return root / f"{source_index:04d}-{_slugify(parts[0])}"
    current = root / _slugify(parts[0])
    for part in parts[1:-1]:
        current = current / _slugify(part)
    return current / f"{source_index:04d}-{_slugify(parts[-1])}"


def _archive_folder_label(label: str) -> str:
    parts = _archive_label_parts(label)
    if len(parts) <= 1:
        return "root"
    return "/".join(parts[:-1])


def _notify(
    progress: Optional[ProgressCallback], event: str, message: str, advance: int = 1
) -> None:
    if progress:
        progress(event, message, advance)


def _notify_usage(
    progress: Optional[ProgressCallback],
    label: str,
    item_usage: Optional[dict],
    total_usage: Dict[str, object],
) -> None:
    if not item_usage:
        return
    _notify(progress, "usage", f"{label} | {_usage_summary(item_usage, total_usage)}", advance=0)


def _usage_summary(item_usage: dict, total_usage: Dict[str, object]) -> str:
    item_prompt = int(item_usage.get("prompt_tokens") or 0)
    item_completion = int(item_usage.get("completion_tokens") or 0)
    item_total = int(item_usage.get("total_tokens") or (item_prompt + item_completion))
    total_prompt = int(total_usage.get("prompt_tokens") or 0)
    total_completion = int(total_usage.get("completion_tokens") or 0)
    total_all = int(total_usage.get("total_tokens") or (total_prompt + total_completion))
    truncated = " truncated" if item_usage.get("truncated") or total_usage.get("truncated") else ""
    return (
        f"tokens page/run {item_total}/{total_all} "
        f"(in {item_prompt}/{total_prompt}, out {item_completion}/{total_completion}){truncated}"
    )


def _restore_with_retry(
    provider,
    prompt: str,
    instruction: str,
    settings,
    media_path: Optional[Path] = None,
    progress: Optional[ProgressCallback] = None,
) -> tuple[str, dict]:
    last_response = ""
    last_usage: dict = {}
    last_error: Optional[Exception] = None
    max_retries = _provider_retry_limit()
    for attempt in range(max_retries + 1):
        retry_prompt = prompt
        if attempt:
            retry_prompt = (
                prompt
                + "\n\nRetry because the previous response was malformed or unusable. "
                "Return only the requested output. Do not include commentary, code fences, "
                "or wrapper JSON unless the prompt explicitly asks for JSON."
            )
        try:
            response, usage = provider.restore_text(
                retry_prompt, instruction, settings, media_path=media_path
            )
        except Exception as exc:
            last_error = exc
            if not _is_retryable_provider_error(exc):
                raise
            if attempt < max_retries:
                delay = _retry_delay(attempt)
                _notify(
                    progress,
                    "retry",
                    f"Provider delayed or unavailable; retrying in {delay:g}s ({attempt + 1}/{max_retries})",
                    advance=0,
                )
                _sleep_before_retry(attempt)
                continue
            raise
        last_response = response
        last_usage = usage or {}
        if not _response_needs_retry(response):
            return response, last_usage
        if attempt < max_retries:
            delay = _retry_delay(attempt)
            _notify(
                progress,
                "retry",
                f"Provider returned unusable output; retrying in {delay:g}s ({attempt + 1}/{max_retries})",
                advance=0,
            )
            _sleep_before_retry(attempt)
    if last_error:
        raise last_error
    return last_response, last_usage


def _sleep_before_retry(attempt: int) -> None:
    time.sleep(_retry_delay(attempt))


def _retry_delay(attempt: int) -> float:
    return min(0.5 * (2**attempt), 8)


def _provider_retry_limit() -> int:
    raw_value = os.environ.get("AKSHARA_PROVIDER_RETRIES")
    if not raw_value:
        return DEFAULT_PROVIDER_RETRIES
    try:
        return min(max(int(raw_value), 0), 10)
    except ValueError:
        return DEFAULT_PROVIDER_RETRIES


def _figure_extraction_enabled(profile: Optional[WorkflowProfile] = None) -> bool:
    return bool(profile and profile.extract_figures)


def _is_retryable_provider_error(exc: Exception) -> bool:
    message = str(exc).lower()
    non_retryable = [
        "does not support",
        "api key",
        "not configured",
        "invalid api key",
        "unauthorized",
        "forbidden",
        "http 400",
        "http 401",
        "http 403",
        "not found",
    ]
    if any(item in message for item in non_retryable):
        return False
    retryable = [
        "timeout",
        "timed out",
        "temporarily",
        "rate limit",
        "too many requests",
        "http 408",
        "http 409",
        "http 425",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "network",
        "connection",
        "remote end closed",
        "empty response",
    ]
    return any(item in message for item in retryable) or not message


def _response_needs_retry(response: str) -> bool:
    candidate = (response or "").strip()
    if not candidate:
        return False
    if _looks_like_meta_response(candidate):
        return True
    json_candidate = _extract_json_object(candidate)
    if not json_candidate:
        if candidate.startswith("{") and any(
            f'"{key}"' in candidate for key in ("restored_text", "translated_text", "text", "output")
        ):
            return True
        return False
    try:
        json.loads(json_candidate)
        return False
    except json.JSONDecodeError:
        has_recoverable_text = any(
            _extract_jsonish_string_value(json_candidate, key) is not None
            for key in ("restored_text", "translated_text", "text", "output")
        )
        return not has_recoverable_text


def _split_text_chunks(text: str, max_chars: int = RESTORATION_CHUNK_CHARS) -> List[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= max_chars:
        return [stripped]
    blocks = [block.strip() for block in re.split(r"\n{2,}", stripped) if block.strip()]
    if not blocks:
        return [stripped]
    chunks: List[str] = []
    current: List[str] = []
    current_size = 0
    for block in blocks:
        if len(block) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_size = 0
            chunks.extend(_split_long_block(block, max_chars))
            continue
        projected = current_size + len(block) + (2 if current else 0)
        if current and projected > max_chars:
            chunks.append("\n\n".join(current))
            current = [block]
            current_size = len(block)
        else:
            current.append(block)
            current_size = projected
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_long_block(block: str, max_chars: int) -> List[str]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        return [block.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_size = 0
    for line in lines:
        projected = current_size + len(line) + (1 if current else 0)
        if current and projected > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_size = len(line)
        else:
            current.append(line)
            current_size = projected
    if current:
        chunks.append("\n".join(current))
    return chunks


def _parse_restoration_result(response: str, fallback_text: str) -> Dict[str, object]:
    candidate = response.strip()
    json_candidate = _extract_json_object(candidate)
    if json_candidate:
        try:
            data = json.loads(json_candidate)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            restored_text = str(
                data.get("restored_text") or data.get("text") or data.get("output") or ""
            ).strip()
            restored_text = _normalize_extracted_text(restored_text)
            uncertain = data.get("uncertain") if isinstance(data.get("uncertain"), list) else []
            notes = str(data.get("notes") or "")
            status = "blank" if restored_text == "" else str(data.get("status") or "restored")
            failure_reason = str(data.get("failure_reason") or "").strip()
            if restored_text == "" and not failure_reason:
                failure_reason = BLANK_PAGE_REASON
            return {
                "restored_text": restored_text,
                "uncertain": [str(item) for item in uncertain],
                "notes": notes,
                "status": status,
                "failure_reason": failure_reason,
            }
        restored_text = _extract_jsonish_string_value(json_candidate, "restored_text")
        if restored_text is None:
            restored_text = _extract_jsonish_string_value(json_candidate, "text")
        if restored_text is None:
            restored_text = _extract_jsonish_string_value(json_candidate, "output")
        if restored_text is not None:
            normalized = _normalize_extracted_text(restored_text)
            return {
                "restored_text": normalized,
                "uncertain": [],
                "notes": _extract_jsonish_string_value(json_candidate, "notes") or "",
                "status": "blank" if normalized == "" else "restored",
                "failure_reason": BLANK_PAGE_REASON if normalized == "" else "",
            }
    if _looks_like_meta_response(candidate):
        return {
            "restored_text": fallback_text.strip(),
            "uncertain": [],
            "notes": "fallback to source chunk because model returned commentary",
            "status": "fallback",
            "failure_reason": "model returned commentary instead of output",
        }
    return {
        "restored_text": candidate,
        "uncertain": [],
        "notes": "",
        "status": "restored",
        "failure_reason": "",
    }


def _extract_json_object(text: str) -> str:
    # Strip <think>...</think> blocks first so reasoning doesn't confuse the JSON search.
    # Handle incomplete/unclosed think blocks in case of truncation.
    text_clean = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.DOTALL).strip()

    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text_clean, re.DOTALL)
    if fenced:
        return fenced[-1]
    start = text_clean.find("{")
    end = text_clean.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text_clean[start : end + 1]
    return ""


def _extract_jsonish_string_value(text: str, key: str) -> Optional[str]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"', text)
    if not match:
        return None
    value = []
    escaped = False
    for char in text[match.end() :]:
        if escaped:
            value.append(
                {
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                    "b": "\b",
                    "f": "\f",
                }.get(char, char)
            )
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            return "".join(value)
        value.append(char)
    return "".join(value).strip() if value else None


def _normalize_extracted_text(text: str) -> str:
    candidate = text.strip()
    if not candidate:
        return ""
    if _is_blank_or_missing_text(candidate):
        return ""
    return candidate


def _is_blank_or_missing_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    normalized = normalized.strip(". ")
    if re.fullmatch(r"(?:\[unclear(?::[^\]]*)?\]\s*)+", normalized):
        return True
    return normalized in {
        "[missing text]",
        "[unclear]",
        "[blank page]",
        "missing text",
        "unclear",
        "blank page",
        "no text",
        "no readable text",
        "no visible text",
    }


def _restoration_status(failure_reason: str) -> str:
    if not failure_reason:
        return "restored"
    if failure_reason == BLANK_PAGE_REASON:
        return "blank"
    return "partial"


def _looks_like_meta_response(text: str) -> bool:
    lowered = text.lower()
    markers = ["thinking...", "role:", "task:", "output requirements:", "self-correction", "wait,"]
    return any(marker in lowered for marker in markers)


def _short_excerpt(text: str, limit: int = 120) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 1].rstrip() + "…"


def _join_notes(*notes: object) -> str:
    return "; ".join(str(note).strip() for note in notes if str(note or "").strip())


def _execution_mode(profile: Optional[WorkflowProfile]) -> str:
    mode = profile.model.execution_mode if profile else "balanced"
    return mode if mode in EXECUTION_MODES else "balanced"


def _is_vision_model(model: str) -> bool:
    model_lower = model.lower()
    vision_keywords = [
        "vision",
        "-vl",
        "vl:",
        "gemma4",
        "gpt-5",
        "claude-3-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "gemini-",
    ]
    return any(kw in model_lower for kw in vision_keywords)


def find_executable(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    if platform.system().lower() == "windows":
        common_dirs = []
        if name == "pdftoppm":
            common_dirs = [
                Path("C:/Program Files/poppler/bin"),
                Path("C:/Program Files (x86)/poppler/bin"),
                Path("C:/poppler/bin"),
                Path("C:/Program Files/poppler-windows/bin"),
                Path("C:/Program Files (x86)/poppler-windows/bin"),
                Path(os.environ.get("USERPROFILE", "")) / "scoop/apps/poppler/current/bin",
            ]
            for base_dir in [Path("C:/Program Files"), Path("C:/Program Files (x86)")]:
                if base_dir.exists():
                    try:
                        for p in base_dir.glob("poppler*"):
                            if p.is_dir():
                                common_dirs.append(p / "bin")
                                common_dirs.append(p)
                    except Exception:
                        pass
        for directory in common_dirs:
            exe_path = directory / f"{name}.exe"
            if exe_path.exists():
                return str(exe_path)
    return None


def _count_words(text: str) -> int:
    return len([part for part in text.split() if part])


def _estimate_input_words(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        try:
            return max(_count_words(path.read_text(encoding="utf-8")), 1)
        except UnicodeDecodeError:
            return max(_count_words(path.read_text(encoding="latin-1")), 1)
        except OSError:
            return 40
    try:
        size = path.stat().st_size
    except OSError:
        return 40
    if suffix == ".pdf":
        return max(size // 64, 60)
    if suffix == ".zip":
        return max(size // 48, 80)
    return max(size // 24, 40)


def _new_consistency_state(profile: WorkflowProfile) -> Dict[str, object]:
    return {
        "document_type": profile.document_type,
        "language_policy": profile.language_policy,
        "observed_pages": 0,
        "paragraph_style": "",
        "heading_style": "",
        "page_marker_style": "",
        "layout_notes": [],
        "encountered_scripts": [],
        "recent_text_excerpt": "",
    }


def _document_type_guidance(profile: WorkflowProfile) -> str:
    document_type = profile.document_type
    kind = str(document_type or "general").strip().lower()
    guidance = {
        "book": (
            "Book restoration skill: preserve title pages, subtitles, author/editor lines, "
            "preface/foreword sections, table of contents, chapter headings, page numbers, "
            "footnotes, indexes, appendices, and running headers without inventing missing data."
        ),
        "magazine": (
            "Magazine restoration skill: identify columns, article boundaries, headlines, decks, "
            "captions, bylines, page numbers, advertisements, and sidebars. Do not merge text from "
            "different columns or adjacent articles."
        ),
        "newspaper": (
            "Newspaper restoration skill: preserve column order, article boundaries, headlines, "
            "datelines, bylines, captions, advertisements, and continuation markers. Avoid mixing "
            "rows across columns."
        ),
        "manuscript": (
            "Manuscript restoration skill: preserve folio/page markers, marginalia, corrections, "
            "scribal marks, uncertain readings, line breaks, and damaged text honestly."
        ),
        "journal article": (
            "Article restoration skill: preserve title, authors, abstract, section headings, "
            "citations, footnotes, tables, figures, captions, and bibliography structure."
        ),
        "letter": (
            "Letter restoration skill: preserve salutation, date, place, body paragraphs, "
            "postscript, signature, and address marks."
        ),
        "archive bundle": (
            "Archive bundle skill: preserve each item boundary, original ordering, labels, dates, "
            "identifiers, and folder-like grouping."
        ),
    }
    selected = guidance.get(kind, "General restoration skill: preserve document order, headings, labels, notes, page markers, and uncertain text.")
    if _figure_extraction_enabled(profile):
        selected += (
            " If a non-text image, illustration, plate, map, table image, seal, or diagram is clearly "
            "present on the front side of the page, insert a concise marker like [image: brief description] "
            "at its position. Do not mark bleed-through, mirrored back-page impressions, borders, stains, "
            "cracks, shadows, or decorative noise as images."
        )
    return selected + "\n"


def _language_policy_guidance(profile: WorkflowProfile) -> str:
    source = str(profile.source_language or "auto").strip() or "auto"
    policy = str(profile.language_policy or "preserve-detected").strip().lower()
    if policy == "strict-source" and source.lower() not in {"", "auto", "detect"}:
        return (
            "Language policy: strict source language only.\n"
            f"Extract only text that is clearly in the declared source language or script: {source}.\n"
            "Ignore unrelated visible text in other languages or scripts unless it is part of a name, "
            "citation, title, quoted phrase, table label, or technical term needed to preserve the source. "
            "Do not translate ignored text. Do not invent replacements for ignored text.\n"
        )
    if policy == "strict-source":
        return (
            "Language policy: strict source language was requested, but source language is auto. "
            "Use cautious detection and do not force uncertain language labels into the output.\n"
        )
    return (
        "Language policy: preserve detected readable languages and scripts.\n"
        "If the page contains clear mixed-language text, preserve each snippet in its original script "
        "and position. Do not translate during restoration. Do not label a language unless it is already "
        "visible in the source or needed for structure. If a script or language is uncertain, preserve the "
        "visible characters when readable, otherwise mark [unclear].\n"
    )


def _document_structure(
    records: List[Dict[str, object]], document_type: str, profile: Optional[WorkflowProfile] = None
) -> Dict[str, object]:
    chunks = []
    for record in records:
        for chunk in record.get("chunks", []):
            if isinstance(chunk, dict):
                chunks.append(chunk)
    observations = [
        _piece_observations(str(chunk.get("restored_text") or ""), document_type, int(chunk.get("index") or 0))
        for chunk in chunks
    ]
    title_candidates = []
    page_markers = []
    section_headings = []
    content_kinds: Dict[str, int] = {}
    asset_count = 0
    for item in observations:
        title_candidates.extend(item.get("title_candidates", []))
        page_marker = item.get("page_marker")
        if page_marker:
            page_markers.append(page_marker)
        section_headings.extend(item.get("section_headings", []))
        content_kind = str(item.get("content_kind") or "body")
        content_kinds[content_kind] = content_kinds.get(content_kind, 0) + 1
    for chunk in chunks:
        chunk_assets = chunk.get("assets")
        if isinstance(chunk_assets, list):
            asset_count += len(chunk_assets)
    return {
        "document_type": document_type,
        "page_count_observed": len(chunks),
        "title_candidates": _unique_limited(title_candidates, 8),
        "section_headings": _unique_limited(section_headings, 24),
        "page_markers": _unique_limited(page_markers, 24),
        "content_kinds": content_kinds,
        "figure_extraction_enabled": _figure_extraction_enabled(profile),
        "asset_count": asset_count,
    }


def _piece_observations(text: str, document_type: str, index: int) -> Dict[str, object]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_lines = lines[:12]
    headings = [
        line
        for line in first_lines
        if 2 <= len(line) <= 90
        and (
            line == line.upper()
            or re.match(r"^(chapter|section|part|book|preface|contents|index|appendix)\b", line, re.I)
        )
    ]
    page_marker = ""
    for line in first_lines + lines[-6:]:
        if re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", line, flags=re.I):
            page_marker = line
            break
    kind = _content_kind(lines, document_type)
    return {
        "index": index,
        "content_kind": kind,
        "page_marker": page_marker,
        "title_candidates": first_lines[:2] if index <= 2 else [],
        "section_headings": headings[:6],
        "has_multi_column_spacing": any(re.search(r"\S\s{4,}\S", line) for line in lines[:80]),
        "has_figure_marker": any("[image:" in line.lower() for line in lines),
    }


def _content_kind(lines: List[str], document_type: str) -> str:
    joined = "\n".join(lines[:40]).lower()
    kind = str(document_type or "").lower()
    if "contents" in joined or "table of contents" in joined:
        return "contents"
    if "preface" in joined or "foreword" in joined:
        return "preface"
    if "index" in joined and kind == "book":
        return "index"
    if re.search(r"\b(chapter|section|part)\b", joined):
        return "section"
    if kind in {"magazine", "newspaper"} and any(re.search(r"\S\s{4,}\S", line) for line in lines[:80]):
        return "multi-column"
    if any("[image:" in line.lower() for line in lines):
        return "illustrated"
    return "body"


def _assembly_profile(document_type: str, output_formats: List[str]) -> Dict[str, object]:
    kind = str(document_type or "General").lower()
    if kind == "book":
        layout = "book-like: title matter, contents, chapters, appendices, index when detected"
    elif kind in {"magazine", "newspaper"}:
        layout = "periodical-like: preserve article and column boundaries when detected"
    elif kind == "manuscript":
        layout = "manuscript-like: preserve folios, marginalia, uncertain readings, and lineation"
    else:
        layout = "document-like: preserve detected headings, page markers, and item order"
    return {
        "layout": layout,
        "target_formats": list(output_formats),
        "uses_structured_sidecars": True,
    }


def _detected_title(document_structure: Dict[str, object]) -> str:
    candidates = document_structure.get("title_candidates")
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        text = str(candidate).strip()
        if 3 <= len(text) <= 120 and not re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", text, re.I):
            return text
    return ""


def _image_dimensions(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        data = path.read_bytes()
    except OSError:
        return None, None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index : index + 2], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if index + 7 <= len(data):
                    height = int.from_bytes(data[index + 3 : index + 5], "big")
                    width = int.from_bytes(data[index + 5 : index + 7], "big")
                    return width, height
                break
            index += max(length, 2)
    return None, None


def _extract_figure_assets(
    artifacts: StageWriter,
    source_index: int,
    source_name: str,
    image_path: Path,
    piece_index: int,
    dpi: Optional[int] = None,
    provider=None,
    profile: Optional[WorkflowProfile] = None,
    progress: Optional[ProgressCallback] = None,
) -> List[Dict[str, object]]:
    if Image is None or ImageOps is None:
        return []
    try:
        with Image.open(image_path) as image:
            source = ImageOps.exif_transpose(image).convert("RGB")
            boxes = _candidate_figure_boxes(source)
            assets = []
            for figure_index, bbox in enumerate(boxes[:MAX_FIGURE_CROPS_PER_PAGE], start=1):
                cropped = source.crop(bbox)
                assets.append(
                    artifacts.write_figure_asset(
                        source_index,
                        source_name,
                        cropped,
                        piece_index,
                        figure_index,
                        bbox,
                        dpi=dpi,
                    )
                )
            return _verify_figure_assets(assets, provider, profile, progress)
    except Exception:
        return []


def _verify_figure_assets(
    assets: List[Dict[str, object]],
    provider,
    profile: Optional[WorkflowProfile],
    progress: Optional[ProgressCallback],
) -> List[Dict[str, object]]:
    verified_assets = []
    for asset in assets:
        local_path = Path(str(asset.get("_local_path") or ""))
        asset.pop("_local_path", None)
        if provider is None or profile is None or not local_path.exists():
            asset["verification"] = "unverified"
            verified_assets.append(asset)
            continue
        prompt = (
            "Return only JSON: {\"keep\": true|false, \"label\": \"short label\", \"reason\": \"short reason\"}.\n"
            "The attached crop was detected as a possible figure from a scanned archival page.\n"
            "Keep it only if it is a real non-text illustration, photograph, map, plate, seal, chart, "
            "diagram, or meaningful visual element. Reject it if it is mostly text, page border, bleed-through, "
            "mirrored back-page impression, stain, crack, scanner noise, blank margin, or accidental crop.\n"
        )
        try:
            response, usage = _restore_with_retry(
                provider, prompt, "", profile.model, media_path=local_path, progress=progress
            )
            _notify_usage(progress, f"Verified figure crop {asset.get('path')}", usage, usage or {})
            if usage:
                asset["verification_usage"] = usage
            decision = _parse_figure_verification(response)
        except Exception:
            decision = {"keep": True, "label": "", "reason": "verification failed"}
        if decision["keep"]:
            asset["verification"] = "verified" if decision.get("label") else "unverified"
            asset["label"] = decision.get("label", "")
            asset["reason"] = decision.get("reason", "")
            verified_assets.append(asset)
        else:
            asset_path = local_path
            try:
                asset_path.unlink()
            except OSError:
                pass
    return verified_assets


def _parse_figure_verification(response: str) -> Dict[str, object]:
    json_candidate = _extract_json_object(response or "")
    if json_candidate:
        try:
            data = json.loads(json_candidate)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            keep = data.get("keep")
            if isinstance(keep, bool):
                return {
                    "keep": keep,
                    "label": str(data.get("label") or "")[:80],
                    "reason": str(data.get("reason") or "")[:160],
                }
    lowered = (response or "").strip().lower()
    if lowered.startswith("false") or "reject" in lowered:
        return {"keep": False, "label": "", "reason": "provider rejected crop"}
    return {"keep": True, "label": "", "reason": "verification inconclusive"}


def _candidate_figure_boxes(image) -> List[tuple[int, int, int, int]]:
    width, height = image.size
    if width < 120 or height < 120:
        return []
    gray = ImageOps.grayscale(image)
    grid_x = 48
    grid_y = 64
    cell_w = max(width // grid_x, 1)
    cell_h = max(height // grid_y, 1)
    active = set()
    for gy in range(grid_y):
        for gx in range(grid_x):
            left = gx * cell_w
            top = gy * cell_h
            right = width if gx == grid_x - 1 else min((gx + 1) * cell_w, width)
            bottom = height if gy == grid_y - 1 else min((gy + 1) * cell_h, height)
            if right <= left or bottom <= top:
                continue
            crop = gray.crop((left, top, right, bottom))
            pixels = crop.histogram()
            dark = sum(pixels[:96])
            total = max((right - left) * (bottom - top), 1)
            dark_ratio = dark / total
            if 0.10 <= dark_ratio <= 0.80:
                active.add((gx, gy))

    boxes = []
    seen = set()
    for cell in sorted(active):
        if cell in seen:
            continue
        stack = [cell]
        seen.add(cell)
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            cx, cy = current
            for neighbor in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if neighbor in active and neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        if not component:
            continue
        min_x = min(x for x, _y in component)
        max_x = max(x for x, _y in component)
        min_y = min(y for _x, y in component)
        max_y = max(y for _x, y in component)
        left = max(min_x * cell_w - cell_w, 0)
        top = max(min_y * cell_h - cell_h, 0)
        right = min((max_x + 2) * cell_w, width)
        bottom = min((max_y + 2) * cell_h, height)
        if _looks_like_figure_box(gray, (left, top, right, bottom), width, height):
            boxes.append((left, top, right, bottom))
    return _dedupe_boxes(boxes)


def _looks_like_figure_box(gray, bbox: tuple[int, int, int, int], page_w: int, page_h: int) -> bool:
    left, top, right, bottom = bbox
    box_w = right - left
    box_h = bottom - top
    if box_w < page_w * 0.16 or box_h < page_h * 0.10:
        return False
    area_ratio = (box_w * box_h) / max(page_w * page_h, 1)
    if area_ratio < 0.025 or area_ratio > 0.75:
        return False
    crop = gray.crop(bbox)
    histogram = crop.histogram()
    dark_ratio = sum(histogram[:96]) / max(box_w * box_h, 1)
    if dark_ratio < 0.06 or dark_ratio > 0.70:
        return False
    return True


def _dedupe_boxes(boxes: List[tuple[int, int, int, int]]) -> List[tuple[int, int, int, int]]:
    kept = []
    for box in sorted(boxes, key=lambda item: (item[1], item[0], -(item[2] - item[0]) * (item[3] - item[1]))):
        if any(_box_overlap_ratio(box, existing) > 0.55 for existing in kept):
            continue
        kept.append(box)
    return kept


def _box_overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return intersection / max(smaller, 1)


def _asset_placement(width: Optional[int], height: Optional[int]) -> Dict[str, object]:
    if not width or not height:
        return {"role": "source-image", "recommended_width": "auto", "aspect_ratio": None}
    aspect_ratio = round(width / height, 4) if height else None
    if width > height * 1.4:
        recommended_width = "full-width"
    elif height > width * 1.4:
        recommended_width = "half-width"
    else:
        recommended_width = "medium"
    return {
        "role": "source-image",
        "recommended_width": recommended_width,
        "aspect_ratio": aspect_ratio,
    }


def _unique_limited(values: List[object], limit: int) -> List[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _consistency_prompt(state: Optional[Dict[str, object]]) -> str:
    if not state:
        return ""
    notes = [
        str(item).strip()
        for item in state.get("layout_notes", [])
        if str(item).strip()
    ][:4]
    values = {
        "document_type": state.get("document_type") or "",
        "language_policy": state.get("language_policy") or "preserve-detected",
        "observed_pages": state.get("observed_pages") or 0,
        "paragraph_style": state.get("paragraph_style") or "",
        "heading_style": state.get("heading_style") or "",
        "page_marker_style": state.get("page_marker_style") or "",
        "layout_notes": notes,
        "encountered_scripts": state.get("encountered_scripts") or [],
        "recent_text_excerpt": state.get("recent_text_excerpt") or "",
    }
    if not any(values[key] for key in ("paragraph_style", "heading_style", "page_marker_style", "layout_notes")):
        return (
            "Local consistency guide: observe this document's recurring layout, paragraph spacing, "
            "heading treatment, page markers, footnotes, and tables. Apply only clear recurring "
            "patterns to later pages. Do not add, remove, summarize, translate, or modernize content. "
            "Do not mention this guide in the output.\n\n"
        )
    return (
        "Local consistency guide for this run, used only for uniform formatting:\n"
        + json.dumps(values, ensure_ascii=False)
        + "\nApply only clear recurring patterns. Do not override the main restoration rules. "
        "Do not add content, remove uncertain text, or mention this guide in the output.\n\n"
    )


def _update_consistency_state(state: Optional[Dict[str, object]], text: str) -> None:
    if not state:
        return
    sample = text.strip()
    if not sample or _is_blank_or_missing_text(sample):
        return
    observed = int(state.get("observed_pages") or 0) + 1
    state["observed_pages"] = min(observed, 9999)
    lines = [line.strip() for line in sample.splitlines() if line.strip()]
    if not lines:
        return
    if not state.get("paragraph_style"):
        state["paragraph_style"] = _detect_paragraph_style(sample)
    if not state.get("heading_style"):
        heading_style = _detect_heading_style(lines)
        if heading_style:
            state["heading_style"] = heading_style
    if not state.get("page_marker_style"):
        marker_style = _detect_page_marker_style(lines)
        if marker_style:
            state["page_marker_style"] = marker_style
    notes = list(state.get("layout_notes") or [])
    for note in _detect_layout_notes(lines):
        if note not in notes:
            notes.append(note)
    state["layout_notes"] = notes[:6]
    encountered = list(state.get("encountered_scripts") or [])
    for script in _detect_scripts(sample):
        if script not in encountered:
            encountered.append(script)
    state["encountered_scripts"] = encountered[:8]
    state["recent_text_excerpt"] = _recent_words(sample, 100)


def _recent_words(text: str, limit: int) -> str:
    words = re.findall(r"\S+", text.strip())
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[-limit:])


def _detect_scripts(text: str) -> List[str]:
    ranges = [
        ("Latin", "\u0041", "\u007a"),
        ("Devanagari", "\u0900", "\u097f"),
        ("Bengali", "\u0980", "\u09ff"),
        ("Gurmukhi", "\u0a00", "\u0a7f"),
        ("Gujarati", "\u0a80", "\u0aff"),
        ("Oriya", "\u0b00", "\u0b7f"),
        ("Tamil", "\u0b80", "\u0bff"),
        ("Telugu", "\u0c00", "\u0c7f"),
        ("Kannada", "\u0c80", "\u0cff"),
        ("Malayalam", "\u0d00", "\u0d7f"),
        ("Sinhala", "\u0d80", "\u0dff"),
        ("Arabic", "\u0600", "\u06ff"),
    ]
    found = []
    for name, start, end in ranges:
        start_ord = ord(start)
        end_ord = ord(end)
        if any(start_ord <= ord(char) <= end_ord and char.isalpha() for char in text):
            found.append(name)
    return found


def _detect_paragraph_style(text: str) -> str:
    if "\n\n" in text:
        return "blank line between paragraphs"
    return "single line breaks preserved"


def _detect_heading_style(lines: List[str]) -> str:
    for line in lines[:8]:
        words = [word for word in re.split(r"\s+", line) if word]
        if 1 <= len(words) <= 10 and line == line.upper() and any(char.isalpha() for char in line):
            return "short uppercase headings preserved"
        if re.match(r"^(chapter|section|part|book|canto|mandala)\b", line, flags=re.IGNORECASE):
            return "explicit chapter/section headings preserved"
    return ""


def _detect_page_marker_style(lines: List[str]) -> str:
    candidates = lines[:4] + lines[-4:]
    for line in candidates:
        if re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", line.strip(), flags=re.IGNORECASE):
            return "standalone page markers preserved"
        if re.search(r"\bpage\s+\d+\b", line, flags=re.IGNORECASE):
            return "page labels preserved"
    return ""


def _detect_layout_notes(lines: List[str]) -> List[str]:
    joined = "\n".join(lines[:80])
    notes = []
    if re.search(r"\s{3,}", joined):
        notes.append("preserve visible table or column spacing when clear")
    if any(line.startswith(("*", "-")) for line in lines[:80]):
        notes.append("preserve list item breaks")
    if re.search(r"\[\d+\]|\(\d+\)|\b\d+\.", joined):
        notes.append("preserve numbered references and footnote markers")
    return notes


def _task_text(
    raw_text: str,
    profile: WorkflowProfile,
    consistency_state: Optional[Dict[str, object]] = None,
) -> str:
    """Build the user-facing prompt for the LLM.

    For text-chunk restoration (raw text input), we demand strict JSON
    so the parser can extract structured metadata.

    For multimodal (vision) mode, we use a simple, direct prompt asking the
    model to extract and return the text.  Demanding JSON from a vision model
    looking at degraded manuscripts is unreliable — the model works best when
    it can focus entirely on reading the document.
    """
    execution_mode = _execution_mode(profile)
    consistency_context = _consistency_prompt(consistency_state)
    context = (
        f"Document type: {profile.document_type}\n"
        f"Source language: {profile.source_language}\n"
        f"Output language: {profile.output_language}\n"
        f"Translation mode: {profile.normalized_translation_mode()}\n"
        f"Execution mode: {execution_mode}\n\n"
        + _document_type_guidance(profile)
        + _language_policy_guidance(profile)
        + consistency_context
    )

    if execution_mode == "quality":
        depth_instruction = (
            "Extract carefully but stay bounded: scan the page once region by region, including "
            "margins, footnotes, headings, captions, columns, and small text. Do not enter long "
            "reasoning loops. If a word cannot be read confidently, keep the best visible reading "
            "or mark [unclear] and continue.\n"
        )
    elif execution_mode == "balanced":
        depth_instruction = (
            "Perform a careful and thorough extraction of the whole page. Work top-to-bottom "
            "and left-to-right unless the page layout clearly uses another reading order.\n"
        )
    else:  # fast
        depth_instruction = (
            "Quickly extract the text from the image while still preserving every clearly "
            "visible line. Focus on legibility and page order.\n"
        )

    if not raw_text:
        # Multimodal vision prompt — keep it simple and direct.
        return (
            context
            + "Look at the attached image carefully.\n"
            + depth_instruction
            + "Restoration stage only: extract ALL text visible in the image exactly as written.\n"
            "Preserve the original language, script, spelling, line breaks, page order, and formatting.\n"
            "Do not skip non-English, Indic, Sanskrit, Kannada, Hindi, Tamil, Telugu, Malayalam, "
            "Bengali, Marathi, Urdu, or mixed-script text.\n"
            "Apply the language policy above exactly. Preserve clear mixed-language snippets only when "
            "they are visible and readable; never force a language guess into the output.\n"
            "If the page is dense, prioritize complete extraction over perfect cleanup.\n"
            "Ignore mirrored, reversed, faint, or bleed-through impressions from the back side of "
            "the page. Do not restore shadows, show-through, stains, cracks, or scan noise as text.\n"
            "If any words are unclear, mark them as [unclear]. If the whole page is blank or "
            "unreadable, return an empty response, not [unclear].\n"
            "Before returning, internally review the restored text for gibberish, malformed words, "
            "wrong-script fragments, and obvious OCR corruption. Fix only clear restoration errors. "
            "Do not complete a sentence merely because it continues on the next page.\n"
            "Do not translate yet. Translation happens as a final stage after extraction.\n"
            "Return ONLY the extracted text. Do not add explanations, commentary, "
            "JSON formatting, code fences, or any other markup.\n"
            "If the page is blank or has no readable text, return an empty response.\n"
            "If the image contains text but it is completely unreadable, return an empty response."
        )

    # Text-chunk restoration prompt — strict JSON output.
    return (
        "Return only a valid JSON object with keys restored_text, uncertain, notes, status, and failure_reason.\n"
        "restored_text must contain only the cleaned text for this chunk.\n"
        "uncertain must be an array of uncertain words or phrases.\n"
        "notes must be a short string or an empty string.\n"
        "status must be restored, partial, fallback, or failed.\n"
        "failure_reason must be one of: "
        + ", ".join(TRANSLATION_FAILURE_REASONS)
        + " or an empty string.\n"
        "Do not include markdown, code fences, or commentary.\n"
        "Apply the language policy exactly. Preserve readable mixed-language snippets only when present "
        "in the source; never invent or normalize language labels.\n"
        "Before returning, review restored_text for gibberish, malformed words, wrong-script fragments, "
        "and obvious OCR corruption. Fix only clear restoration errors without changing structure.\n"
        "Ignore mirrored, reversed, faint, or bleed-through impressions from the back side of a page. "
        "Do not restore shadows, stains, cracks, or scan noise as text.\n"
        "If the chunk is empty or unreadable, return "
        '{"restored_text":"[missing text]","uncertain":[],"notes":"unreadable source","status":"failed","failure_reason":"source unreadable or too blurry"}.\n'
        + context
        + "SOURCE CHUNK\n"
        + raw_text
    )


def _restore_multimodal_image(
    path: Path,
    instruction: str,
    profile: WorkflowProfile,
    provider,
    artifacts: StageWriter,
    source_index: int,
    source_label: Optional[str] = None,
    consistency_state: Optional[Dict[str, object]] = None,
    progress: Optional[ProgressCallback] = None,
) -> tuple:
    """Send an image directly to the vision model and use its raw text output.

    Unlike text-chunk restoration, we do NOT demand JSON here.  The model's
    raw response IS the extracted text.  We only attempt JSON parsing as a
    bonus — if the model happens to return JSON, we use the structured data;
    otherwise, we take the full response as restored text.
    """
    prompt = _task_text("", profile, consistency_state)
    result, usage = _restore_with_retry(
        provider, prompt, instruction, profile.model, media_path=path, progress=progress
    )

    # Try JSON parsing first (in case the model does return structured data).
    restored_text = _extract_multimodal_text(result)
    failure_reason = ""
    label = source_label or path.name
    assets = []
    if _figure_extraction_enabled(profile):
        assets = _extract_figure_assets(
            artifacts,
            source_index,
            label,
            path,
            1,
            provider=provider,
            profile=profile,
            progress=progress,
        )

    if not restored_text:
        restored_text = ""
        failure_reason = BLANK_PAGE_REASON
    elif usage and usage.get("truncated"):
        failure_reason = "model context or output limit reached"
    review_note = ""
    review_usage = {}
    pre_review_text = ""
    if restored_text:
        restored_text, review_note, review_usage, pre_review_text = _maybe_review_restored_text(
            restored_text, profile, provider, instruction, progress, label, consistency_state
        )
    _notify_usage(progress, f"Restored {label}", usage, usage or {})
    _notify_usage(progress, f"Reviewed {label}", review_usage, review_usage)
    _update_consistency_state(consistency_state, restored_text)
    artifacts.write_restored_piece(source_index, label, 1, restored_text + "\n")

    record = {
        "status": _restoration_status(failure_reason),
        "chunks": [
            {
                "index": 1,
                "input": f"[Image: {label}]",
                "restored_text": restored_text,
                "uncertain": [],
                "notes": review_note,
                "status": _restoration_status(failure_reason),
                "failure_reason": failure_reason,
                "assets": assets,
                **({"pre_review_text": pre_review_text} if pre_review_text else {}),
            }
        ],
        "failure_reason": failure_reason,
    }
    return restored_text + "\n", record, usage


def _extract_multimodal_text(response: str) -> str:
    """Extract usable text from a vision model response.

    Strategy (in order):
    1. If the response contains a valid JSON object with ``restored_text``,
       use that value.
    2. Otherwise, strip away any thinking/reasoning markers and use the raw
       response as the extracted text.
    """
    if not response:
        return ""

    candidate = response.strip()

    # Attempt JSON extraction (bonus path).
    json_str = _extract_json_object(candidate)
    if json_str:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                text = str(
                    data.get("restored_text") or data.get("text") or data.get("output") or ""
                ).strip()
                return _normalize_extracted_text(text)
        except json.JSONDecodeError:
            text = _extract_jsonish_string_value(json_str, "restored_text")
            if text is None:
                text = _extract_jsonish_string_value(json_str, "text")
            if text is None:
                text = _extract_jsonish_string_value(json_str, "output")
            if text is not None:
                return _normalize_extracted_text(text)

    # Strip common thinking/reasoning wrappers that some models emit.
    cleaned = candidate
    # Remove <think>...</think> blocks (including unclosed ones in case of truncation).
    cleaned = re.sub(r"<think>.*?(?:</think>|$)", "", cleaned, flags=re.DOTALL).strip()
    # Remove ```...``` code fences.
    fenced = re.findall(r"```(?:\w+)?\s*\n?(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = "\n\n".join(block.strip() for block in fenced if block.strip())
    # Remove leading "Here is the extracted text:" style preambles.
    cleaned = re.sub(
        r"^(?:Here\s+is|Below\s+is|The\s+(?:extracted|transcribed|restored)\s+text\s*(?:is)?)[^\n]*\n",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    jsonish_text = _extract_jsonish_string_value(cleaned, "restored_text")
    if jsonish_text is None:
        jsonish_text = _extract_jsonish_string_value(cleaned, "text")
    if jsonish_text is None:
        jsonish_text = _extract_jsonish_string_value(cleaned, "output")
    if jsonish_text is not None:
        return _normalize_extracted_text(jsonish_text)

    return _normalize_extracted_text(cleaned)


def _infer_failure_reason(
    response: str,
    usage: Optional[dict] = None,
    media_path: Optional[Path] = None,
    exception: Optional[Exception] = None,
) -> str:
    if usage and usage.get("truncated"):
        return "model context or output limit reached"

    if exception is not None:
        message = str(exception).lower()
        if "timeout" in message:
            return "provider timeout"
        if "vision" in message or "image" in message or "support" in message:
            return "model does not support the selected script or language"
        if "connection" in message or "network" in message:
            return "network or API error"
        return "model returned malformed output"

    if not response.strip():
        if media_path is not None:
            return "source unreadable or too blurry"
        return "model returned malformed output"

    if _looks_like_meta_response(response):
        return "model returned malformed output"

    return "source unreadable or too blurry"


def _translation_instruction(profile: WorkflowProfile) -> str:
    mode = effective_translation_mode(
        profile.source_language,
        profile.output_language,
        profile.translation_mode,
    )
    return (
        "You are performing the final translation stage for a restored historical document.\n"
        f"Source language: {profile.source_language}\n"
        f"Target language: {profile.output_language}\n"
        f"Requested mode: {mode}\n"
        "Translate only after restoration is complete. The extraction stage has already been saved; "
        "use only the supplied restored text as your source.\n"
        "Preserve names, dates, citations, paragraph breaks, headings, and page order.\n"
        "Do not add commentary, explanations, summaries, or markdown fences.\n"
        "Preserve [unclear] markers exactly as written.\n"
        "For mixed-language batches, translate only the text that is not already in the target language.\n"
        "If a passage is already in the target language, keep it unchanged and include it in the output.\n"
        "Before returning, review the translated text for gibberish, malformed words, broken script, "
        "and incomplete OCR artifacts. Fix only clear corruption while preserving structure and meaning.\n"
        "Return only the translated text.\n"
        "If the source and target languages are the same, return the cleaned text unchanged.\n"
    )


def _translation_prompt(chunk: str, profile: WorkflowProfile) -> str:
    mode = effective_translation_mode(
        profile.source_language,
        profile.output_language,
        profile.translation_mode,
    )
    if mode == "bilingual":
        return (
            "Return a valid JSON object with keys translated_text and notes.\n"
            "translated_text must contain the translated version of the supplied restored text.\n"
            "notes must be a short string or an empty string.\n"
            "Do not add markdown fences or commentary.\n"
            "SOURCE TEXT\n"
            f"{chunk}"
        )
    return (
        "Return a valid JSON object with keys translated_text and notes.\n"
        "translated_text must contain only the translated text.\n"
        "notes must be a short string or an empty string.\n"
        "Do not add markdown fences or commentary.\n"
        "SOURCE TEXT\n"
        f"{chunk}"
    )


def _parse_translation_result(response: str, fallback_text: str) -> Dict[str, object]:
    candidate = response.strip()
    json_candidate = _extract_json_object(candidate)
    if json_candidate:
        try:
            data = json.loads(json_candidate)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            translated_text = str(
                data.get("translated_text") or data.get("text") or data.get("output") or ""
            ).strip()
            translated_text = _normalize_extracted_text(translated_text)
            notes = str(data.get("notes") or "")
            status = "blank" if translated_text == "" else str(data.get("status") or "translated")
            failure_reason = str(data.get("failure_reason") or "").strip()
            if translated_text == "" and not failure_reason:
                failure_reason = BLANK_PAGE_REASON
            return {
                "translated_text": translated_text,
                "notes": notes,
                "status": status,
                "failure_reason": failure_reason,
            }
        translated_text = _extract_jsonish_string_value(json_candidate, "translated_text")
        if translated_text is None:
            translated_text = _extract_jsonish_string_value(json_candidate, "text")
        if translated_text is None:
            translated_text = _extract_jsonish_string_value(json_candidate, "output")
        if translated_text is not None:
            normalized = _normalize_extracted_text(translated_text)
            return {
                "translated_text": normalized,
                "notes": _extract_jsonish_string_value(json_candidate, "notes") or "",
                "status": "blank" if normalized == "" else "translated",
                "failure_reason": BLANK_PAGE_REASON if normalized == "" else "",
            }

    if _looks_like_meta_response(candidate):
        return {
            "translated_text": fallback_text.strip(),
            "notes": "fallback to source text because model returned commentary",
            "status": "fallback",
            "failure_reason": "model returned malformed output",
        }

    return {
        "translated_text": candidate,
        "notes": "",
        "status": "translated",
        "failure_reason": "",
    }


def _maybe_review_restored_text(
    restored_text: str,
    profile: WorkflowProfile,
    provider,
    instruction: str,
    progress: Optional[ProgressCallback],
    label: str,
    consistency_state: Optional[Dict[str, object]] = None,
) -> tuple[str, str, dict, str]:
    if not _needs_restoration_review(restored_text):
        return restored_text, "", {}, ""
    _notify(progress, "review", f"Reviewing suspicious restoration for {label}", advance=0)
    prompt = _restoration_review_prompt(restored_text, profile, consistency_state)
    try:
        result, usage = _restore_with_retry(
            provider, prompt, instruction, profile.model, progress=progress
        )
    except Exception:
        return restored_text, "quality review failed; kept original restored text", {}, ""
    parsed = _parse_restoration_result(result, restored_text)
    reviewed = str(parsed.get("restored_text") or "").strip()
    if not reviewed:
        return restored_text, "quality review returned no usable correction", usage or {}, ""
    return reviewed, "quality review applied to suspicious restoration", usage or {}, restored_text


def _needs_restoration_review(text: str) -> bool:
    candidate = text.strip()
    if not candidate or _is_blank_or_missing_text(candidate):
        return False
    if _extract_json_object(candidate):
        return True
    if "\ufffd" in candidate:
        return True
    if re.search(r"([A-Za-z])\1{5,}", candidate):
        return True
    words = re.findall(r"[A-Za-z]{3,}", candidate)
    if len(words) >= 12:
        vowel_words = sum(1 for word in words if re.search(r"[aeiouAEIOU]", word))
        if vowel_words / len(words) < 0.35:
            return True
    short_tokens = re.findall(r"\b[A-Za-z]\b", candidate)
    all_tokens = re.findall(r"\b[A-Za-z]+\b", candidate)
    if len(all_tokens) >= 30 and len(short_tokens) / len(all_tokens) > 0.45:
        return True
    symbol_count = sum(1 for char in candidate if not char.isalnum() and not char.isspace())
    if len(candidate) > 120 and symbol_count / len(candidate) > 0.35:
        return True
    return False


def _restoration_review_prompt(
    text: str,
    profile: WorkflowProfile,
    consistency_state: Optional[Dict[str, object]] = None,
) -> str:
    recent_context = ""
    if consistency_state:
        recent_context = str(consistency_state.get("recent_text_excerpt") or "").strip()
    context_block = (
        "RECENT PRIOR CONTEXT FROM THIS DOCUMENT\n"
        f"{recent_context}\n"
        "Use this only to repair obvious OCR corruption in the reviewed text. Do not add missing content.\n"
        if recent_context
        else ""
    )
    return (
        "Return only a valid JSON object with keys restored_text, uncertain, notes, status, and failure_reason.\n"
        "You are reviewing one already-restored page or chunk for OCR corruption and gibberish only.\n"
        "Do not summarize, translate, modernize, add missing facts, complete unfinished page-ending "
        "sentences, or change the document structure.\n"
        "Preserve headings, paragraph breaks, line order, tables, names, dates, citations, page markers, "
        "and [unclear] markers exactly unless they are clearly corrupted by OCR.\n"
        "Fix only words or characters that are visibly nonsensical in context. If a phrase cannot be "
        "confidently repaired, keep [unclear].\n"
        f"Document type: {profile.document_type}\n"
        f"Source language: {profile.source_language}\n"
        f"Language handling: {profile.language_policy}\n"
        f"{context_block}"
        "RESTORED TEXT TO REVIEW\n"
        f"{text}"
    )


def _apply_translation_stage(
    cleaned_text: str,
    profile: WorkflowProfile,
    provider,
    artifacts: StageWriter,
    progress: Optional[ProgressCallback] = None,
    restored_sources: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    mode = effective_translation_mode(
        profile.source_language,
        profile.output_language,
        profile.translation_mode,
    )
    if mode in {"off", "same-language-cleanup", "metadata-only"}:
        for source in restored_sources or []:
            source_index = int(source.get("index") or 1)
            source_name = str(source.get("name") or "source")
            source_text = str(source.get("text") or "").strip() + "\n"
            artifacts.write_item_final(source_index, source_name, source_text)
        return {
            "text": cleaned_text,
            "metadata": {
                "status": "skipped",
                "mode": mode,
                "source_language": profile.source_language,
                "output_language": profile.output_language,
                "resolved_mode": mode,
                "chunks": [],
                "failure_reason": "",
            },
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "truncated": False,
            },
        }

    translated_parts: List[str] = []
    translation_chunks: List[Dict[str, object]] = []
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "truncated": False,
    }
    translation_instruction = _translation_instruction(profile)

    sources = restored_sources or [
        {"index": 1, "name": "combined", "path": "combined", "text": cleaned_text}
    ]
    total_source_count = len(sources)

    for source_number, source in enumerate(sources, start=1):
        source_index = int(source.get("index") or source_number)
        source_name = str(source.get("name") or f"source-{source_index}")
        source_text = str(source.get("text") or "").strip()
        chunks = _split_text_chunks(source_text, max_chars=TRANSLATION_CHUNK_CHARS)
        source_translated_parts: List[str] = []
        if not chunks:
            artifacts.write_item_translated(source_index, source_name, "\n")
            artifacts.write_item_final(source_index, source_name, "\n")
            translated_parts.append(f"===== {source_name} =====")
            translation_chunks.append(
                {
                    "source_index": source_index,
                    "source": source.get("path") or source_name,
                    "index": 1,
                    "input": "",
                    "translated_text": "",
                    "notes": "",
                    "status": "blank",
                    "failure_reason": BLANK_PAGE_REASON,
                }
            )
            continue
        for chunk_index, chunk in enumerate(chunks, start=1):
            _notify(
                progress,
                "translate",
                (
                    f"Translating {source_name} "
                    f"({source_number}/{total_source_count}, chunk {chunk_index}/{len(chunks)})"
                ),
                advance=1,
            )
            prompt = _translation_prompt(chunk, profile)
            result, usage = _restore_with_retry(
                provider, prompt, translation_instruction, profile.model, progress=progress
            )
            if usage:
                total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                total_usage["total_tokens"] += usage.get("total_tokens", 0)
                if usage.get("truncated"):
                    total_usage["truncated"] = True
                _notify_usage(
                    progress,
                    f"Translated {source_name} chunk {chunk_index}/{len(chunks)}",
                    usage,
                    total_usage,
                )

            parsed = _parse_translation_result(result, chunk)
            translated_text = parsed["translated_text"].strip()
            if not translated_text:
                translated_text = chunk.strip()
                parsed["failure_reason"] = (
                    parsed["failure_reason"] or "model returned malformed output"
                )
            if usage and usage.get("truncated"):
                parsed["failure_reason"] = "model context or output limit reached"
                parsed["status"] = "partial"
            source_translated_parts.append(translated_text)
            artifacts.write_translated_piece(
                source_index, source_name, chunk_index, translated_text + "\n"
            )
            translation_chunks.append(
                {
                    "source_index": source_index,
                    "source": source.get("path") or source_name,
                    "index": chunk_index,
                    "input": _short_excerpt(chunk),
                    "translated_text": translated_text,
                    "notes": parsed["notes"],
                    "status": parsed["status"],
                    "failure_reason": parsed["failure_reason"],
                }
            )

        source_translated_text = "\n\n".join(
            part for part in source_translated_parts if part.strip()
        ).strip()
        if not source_translated_text:
            source_translated_text = source_text
        artifacts.write_item_translated(source_index, source_name, source_translated_text + "\n")

        if mode == "bilingual":
            source_final_text = (
                "RESTORED SOURCE\n"
                f"{source_text.strip()}\n\n"
                "TRANSLATION\n"
                f"{source_translated_text}"
            ).strip()
        else:
            source_final_text = source_translated_text
        artifacts.write_item_final(source_index, source_name, source_final_text + "\n")
        translated_parts.append(f"===== {source_name} =====\n{source_final_text}".strip())

    translated_text = "\n\n".join(part for part in translated_parts if part.strip()).strip()
    if not translated_text:
        translated_text = cleaned_text.strip()

    if mode == "bilingual":
        final_text = translated_text
    else:
        final_text = translated_text

    failure_reason = ""
    if any(chunk.get("failure_reason") for chunk in translation_chunks):
        failure_reason = next(
            (str(chunk.get("failure_reason")) for chunk in translation_chunks if chunk.get("failure_reason")),
            "",
        )
    if total_usage.get("truncated"):
        failure_reason = "model context or output limit reached"

    status = "translated"
    if failure_reason:
        status = "partial"

    return {
        "text": final_text + "\n",
        "metadata": {
            "status": status,
            "mode": mode,
            "source_language": profile.source_language,
            "output_language": profile.output_language,
            "resolved_mode": mode,
            "chunks": translation_chunks,
            "failure_reason": failure_reason,
        },
        "usage": total_usage,
    }


def _restore_multimodal_pdf(
    path: Path,
    instruction: str,
    profile: WorkflowProfile,
    provider,
    progress: Optional[ProgressCallback],
    artifacts: StageWriter,
    source_index: int,
    source_label: Optional[str] = None,
    consistency_state: Optional[Dict[str, object]] = None,
) -> tuple:
    pdftoppm_exe = find_executable("pdftoppm")
    if not pdftoppm_exe:
        raise RuntimeError(
            "PDF page rendering utility not found. Please install the required system dependencies "
            "by running 'akv install' (or 'akshara install')."
        )

    execution_mode = _execution_mode(profile)
    dpi = EXECUTION_MODE_PDF_DPI.get(execution_mode, 300)
    page_count = _pdf_page_count(path)
    label = source_label or path.name

    temp_dir = tempfile.TemporaryDirectory(prefix="akshara-multimodal-pdf-")
    try:
        restored_pages = []
        chunks_record = []
        failed_pages = []
        total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "truncated": False,
        }

        page_numbers = range(1, page_count + 1) if page_count else _unknown_pdf_pages()
        for idx in page_numbers:
            total_label = str(page_count) if page_count else "?"
            existing_piece = artifacts.restored_piece_path(source_index, label, idx)
            if existing_piece.exists():
                restored_text = existing_piece.read_text(encoding="utf-8", errors="replace").strip()
                restored_pages.append(restored_text)
                chunks_record.append(
                    {
                        "index": idx,
                        "input": f"[PDF Page {idx}: {label}]",
                        "restored_text": restored_text,
                        "uncertain": [],
                        "notes": "resumed from existing staged output",
                        "status": "restored",
                        "failure_reason": "",
                    }
                )
                _notify(
                    progress,
                    "resume",
                    f"Skipping completed {label} page {idx}/{total_label}",
                    advance=1,
                )
                continue
            _notify(
                progress,
                "render",
                f"Rendering {label} page {idx}/{total_label}",
                advance=1,
            )
            try:
                page_img = _render_pdf_page(pdftoppm_exe, path, Path(temp_dir.name), idx, dpi)
            except RuntimeError:
                if page_count or idx == 1:
                    raise
                break
            if page_img is None:
                if page_count or idx == 1:
                    raise RuntimeError(f"No image rendered for {label} page {idx}.")
                break

            page_assets = []
            if _figure_extraction_enabled(profile):
                page_assets = _extract_figure_assets(
                    artifacts,
                    source_index,
                    label,
                    page_img,
                    idx,
                    dpi=dpi,
                    provider=provider,
                    profile=profile,
                    progress=progress,
                )

            _notify(
                progress,
                "clean",
                f"Restoring text from {label} page {idx}/{total_label}",
                advance=1,
            )
            prompt = _task_text("", profile, consistency_state)
            try:
                result, usage = _restore_with_retry(
                    provider,
                    prompt,
                    instruction,
                    profile.model,
                    media_path=page_img,
                    progress=progress,
                )
                if usage:
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    if usage.get("truncated"):
                        total_usage["truncated"] = True
                    _notify_usage(
                        progress,
                        f"Restored {label} page {idx}/{total_label}",
                        usage,
                        total_usage,
                    )
                restored_text = _extract_multimodal_text(result)
                failure_reason = ""
                if not restored_text:
                    restored_text = ""
                    failure_reason = BLANK_PAGE_REASON
                elif usage and usage.get("truncated"):
                    failure_reason = "model context or output limit reached"
                review_note = ""
                pre_review_text = ""
                if restored_text:
                    restored_text, review_note, review_usage, pre_review_text = _maybe_review_restored_text(
                        restored_text,
                        profile,
                        provider,
                        instruction,
                        progress,
                        f"{label} page {idx}",
                        consistency_state,
                    )
                    if review_usage:
                        total_usage["prompt_tokens"] += review_usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += review_usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += review_usage.get("total_tokens", 0)
                        if review_usage.get("truncated"):
                            total_usage["truncated"] = True
                        _notify_usage(
                            progress,
                            f"Reviewed {label} page {idx}/{total_label}",
                            review_usage,
                            total_usage,
                        )
                del result
            except Exception as exc:
                failed_pages.append((idx, page_img, prompt))
                restored_text = ""
                failure_reason = f"model generation failed: {exc}"
                review_note = ""
                pre_review_text = ""

            _update_consistency_state(consistency_state, restored_text)
            _write_consistency_checkpoint(
                artifacts.run_dir, profile, consistency_state, f"{label} page {idx}"
            )
            restored_pages.append(restored_text)
            artifacts.write_restored_piece(source_index, label, idx, restored_text + "\n")
            chunk_record = {
                "index": idx,
                "input": f"[PDF Page {idx}: {label}]",
                "restored_text": restored_text,
                "uncertain": [],
                "notes": review_note,
                "status": _restoration_status(failure_reason),
                "failure_reason": failure_reason,
                "assets": page_assets,
            }
            if pre_review_text:
                chunk_record["pre_review_text"] = pre_review_text
            chunks_record.append(chunk_record)
            if idx not in [f[0] for f in failed_pages]:
                try:
                    page_img.unlink()
                except OSError:
                    pass
            del restored_text
            gc.collect()

        # Retry failed pages at the end before combining
        if failed_pages:
            _notify(
                progress,
                "clean",
                f"Retrying {len(failed_pages)} failed/stuck pages for {label}...",
                advance=0,
            )
            for idx, page_img, prompt in failed_pages:
                _notify(
                    progress,
                    "clean",
                    f"Retrying restoration for {label} page {idx}...",
                    advance=0,
                )
                try:
                    result, usage = _restore_with_retry(
                        provider,
                        prompt,
                        instruction,
                        profile.model,
                        media_path=page_img,
                        progress=progress,
                    )
                    if usage:
                        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += usage.get("total_tokens", 0)
                        if usage.get("truncated"):
                            total_usage["truncated"] = True
                        _notify_usage(
                            progress,
                            f"Retried {label} page {idx}",
                            usage,
                            total_usage,
                        )
                    restored_text = _extract_multimodal_text(result)
                    failure_reason = ""
                    if not restored_text:
                        restored_text = ""
                        failure_reason = BLANK_PAGE_REASON
                    elif usage and usage.get("truncated"):
                        failure_reason = "model context or output limit reached"
                    review_note = ""
                    pre_review_text = ""
                    if restored_text:
                        restored_text, review_note, review_usage, pre_review_text = _maybe_review_restored_text(
                            restored_text,
                            profile,
                            provider,
                            instruction,
                            progress,
                            f"{label} page {idx}",
                            consistency_state,
                        )
                        if review_usage:
                            total_usage["prompt_tokens"] += review_usage.get("prompt_tokens", 0)
                            total_usage["completion_tokens"] += review_usage.get("completion_tokens", 0)
                            total_usage["total_tokens"] += review_usage.get("total_tokens", 0)
                            if review_usage.get("truncated"):
                                total_usage["truncated"] = True
                            _notify_usage(
                                progress,
                                f"Reviewed retry {label} page {idx}",
                                review_usage,
                                total_usage,
                            )

                    restored_pages[idx - 1] = restored_text
                    for chunk in chunks_record:
                        if chunk["index"] == idx:
                            chunk["restored_text"] = restored_text
                            chunk["status"] = _restoration_status(failure_reason)
                            chunk["failure_reason"] = failure_reason
                            chunk["notes"] = _join_notes(chunk.get("notes", ""), review_note)
                            if pre_review_text:
                                chunk["pre_review_text"] = pre_review_text
                            break
                    artifacts.write_restored_piece(source_index, label, idx, restored_text + "\n")
                    del result, restored_text
                except Exception:
                    pass

            # Clean up remaining temp images
            for idx, page_img, prompt in failed_pages:
                try:
                    page_img.unlink()
                except OSError:
                    pass

        combined = "\n\n".join(restored_pages) + "\n"
        file_failure_reason = next(
            (chunk["failure_reason"] for chunk in chunks_record if chunk.get("failure_reason")),
            "",
        )
        file_status = "restored" if not file_failure_reason else "partial"
        return (
            combined,
            {"status": file_status, "chunks": chunks_record, "failure_reason": file_failure_reason},
            total_usage,
        )
    finally:
        temp_dir.cleanup()


def _pdf_page_count(path: Path) -> Optional[int]:
    pdfinfo_exe = find_executable("pdfinfo")
    if not pdfinfo_exe:
        return None
    try:
        result = subprocess.run(
            [pdfinfo_exe, str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, flags=re.MULTILINE)
    if not match:
        return None
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return None


def _unknown_pdf_pages():
    page = 1
    while True:
        yield page
        page += 1


def _render_pdf_page(
    pdftoppm_exe: str, path: Path, temp_root: Path, page_number: int, dpi: int
) -> Optional[Path]:
    prefix = temp_root / f"page-{page_number:04d}"
    result = subprocess.run(
        [
            pdftoppm_exe,
            "-r",
            str(dpi),
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-singlefile",
            "-png",
            str(path),
            str(prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pdftoppm rendering failed for page {page_number} "
            f"(exit code {result.returncode}): {result.stderr}"
        )
    expected = prefix.with_suffix(".png")
    if expected.exists():
        return expected
    rendered = sorted(temp_root.glob(f"{prefix.name}*.png"))
    return rendered[0] if rendered else None


def _restore_multimodal_zip(
    path: Path,
    instruction: str,
    profile: WorkflowProfile,
    provider,
    progress: Optional[ProgressCallback],
    artifacts: StageWriter,
    source_index: int,
    source_label: Optional[str] = None,
    consistency_state: Optional[Dict[str, object]] = None,
) -> tuple:
    temp_dir = tempfile.TemporaryDirectory(prefix="akshara-multimodal-zip-")
    try:
        root = Path(temp_dir.name)
        extracted_files = []
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                target = _safe_archive_target(root, member.filename)
                if target is None:
                    continue
                with archive.open(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted_files.append(target)

        restored_parts = []
        chunks_record = []
        total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "truncated": False,
        }
        archive_folder_parts: Dict[str, List[str]] = {}
        chunk_idx = 1
        label = source_label or path.name
        for ext_file in sorted(extracted_files):
            suffix = ext_file.suffix.lower()
            archive_label = _safe_relative_archive_label(ext_file, root)
            archive_file_parts: List[str] = []
            if suffix in TEXT_EXTENSIONS:
                text_content = ext_file.read_text(encoding="utf-8", errors="replace")
                sub_chunks = _split_text_chunks(text_content)
                for sub_chunk in sub_chunks:
                    existing_piece = artifacts.restored_piece_path(source_index, label, chunk_idx)
                    if existing_piece.exists():
                        restored_text = existing_piece.read_text(
                            encoding="utf-8", errors="replace"
                        ).strip()
                        restored_parts.append(restored_text)
                        archive_file_parts.append(restored_text)
                        chunks_record.append(
                            {
                                "index": chunk_idx,
                                "input": f"[ZIP Text: {archive_label}] "
                                + _short_excerpt(sub_chunk),
                                "restored_text": restored_text,
                                "uncertain": [],
                                "notes": "resumed from existing staged output",
                                "status": "restored",
                                "failure_reason": "",
                            }
                        )
                        _notify(
                            progress,
                            "resume",
                            f"Skipping completed archive chunk {archive_label}",
                            advance=1,
                        )
                        chunk_idx += 1
                        continue
                    prompt = _task_text(sub_chunk, profile, consistency_state)
                    result, usage = _restore_with_retry(
                        provider, prompt, instruction, profile.model, progress=progress
                    )
                    if usage:
                        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += usage.get("total_tokens", 0)
                        if usage.get("truncated"):
                            total_usage["truncated"] = True
                        _notify_usage(
                            progress,
                            f"Restored archive text {archive_label} chunk {chunk_idx}",
                            usage,
                            total_usage,
                        )
                    parsed = _parse_restoration_result(result, sub_chunk)
                    restored_text = parsed["restored_text"].strip()
                    if not restored_text:
                        restored_text = sub_chunk.strip()
                        if not restored_text:
                            parsed["failure_reason"] = parsed["failure_reason"] or BLANK_PAGE_REASON
                    review_note = ""
                    pre_review_text = ""
                    if restored_text:
                        restored_text, review_note, review_usage, pre_review_text = _maybe_review_restored_text(
                            restored_text,
                            profile,
                            provider,
                            instruction,
                            progress,
                            f"{archive_label} chunk {chunk_idx}",
                            consistency_state,
                        )
                        if review_usage:
                            total_usage["prompt_tokens"] += review_usage.get("prompt_tokens", 0)
                            total_usage["completion_tokens"] += review_usage.get("completion_tokens", 0)
                            total_usage["total_tokens"] += review_usage.get("total_tokens", 0)
                            if review_usage.get("truncated"):
                                total_usage["truncated"] = True
                            _notify_usage(
                                progress,
                                f"Reviewed archive text {archive_label} chunk {chunk_idx}",
                                review_usage,
                                total_usage,
                            )
                    if usage and usage.get("truncated"):
                        parsed["failure_reason"] = "model context or output limit reached"
                        parsed["status"] = "partial"
                    if parsed["failure_reason"] == BLANK_PAGE_REASON:
                        parsed["status"] = "blank"
                    _update_consistency_state(consistency_state, restored_text)
                    _write_consistency_checkpoint(
                        artifacts.run_dir, profile, consistency_state, archive_label
                    )
                    restored_parts.append(restored_text)
                    archive_file_parts.append(restored_text)
                    artifacts.write_restored_piece(
                        source_index, label, chunk_idx, restored_text + "\n"
                    )
                    chunk_record = {
                        "index": chunk_idx,
                        "input": f"[ZIP Text: {archive_label}] " + _short_excerpt(sub_chunk),
                        "restored_text": restored_text,
                        "uncertain": parsed["uncertain"],
                        "notes": _join_notes(parsed["notes"], review_note),
                        "status": parsed["status"],
                        "failure_reason": parsed["failure_reason"],
                    }
                    if pre_review_text:
                        chunk_record["pre_review_text"] = pre_review_text
                    chunks_record.append(chunk_record)
                    chunk_idx += 1
                archive_file_text = "\n\n".join(part for part in archive_file_parts if part.strip())
                artifacts.write_archive_item_restored(
                    source_index, label, archive_label, archive_file_text + "\n"
                )
                _add_archive_folder_part(archive_folder_parts, archive_label, archive_file_text)
            elif suffix == ".pdf":
                pdf_clean, pdf_rec, usage = _restore_multimodal_pdf(
                    ext_file,
                    instruction,
                    profile,
                    provider,
                    progress,
                    artifacts,
                    source_index,
                    source_label=f"{label}/{archive_label}",
                    consistency_state=consistency_state,
                )
                if usage:
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    if usage.get("truncated"):
                        total_usage["truncated"] = True
                    _notify_usage(
                        progress,
                        f"Restored archive PDF {archive_label}",
                        usage,
                        total_usage,
                    )
                restored_parts.append(pdf_clean.strip())
                artifacts.write_archive_item_restored(
                    source_index, label, archive_label, pdf_clean
                )
                _add_archive_folder_part(archive_folder_parts, archive_label, pdf_clean)
                for ch in pdf_rec.get("chunks", []):
                    ch["index"] = chunk_idx
                    ch["input"] = f"[ZIP Archive -> {archive_label}] {ch['input']}"
                    chunks_record.append(ch)
                    chunk_idx += 1
            elif suffix in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}:
                archive_existing = artifacts.archive_item_restored_path(
                    source_index, label, archive_label
                )
                if archive_existing.exists():
                    restored_text = archive_existing.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                    restored_parts.append(restored_text)
                    _add_archive_folder_part(archive_folder_parts, archive_label, restored_text)
                    artifacts.write_restored_piece(
                        source_index, label, chunk_idx, restored_text + "\n"
                    )
                    chunks_record.append(
                        {
                            "index": chunk_idx,
                            "input": f"[ZIP Image: {archive_label}]",
                            "restored_text": restored_text,
                            "uncertain": [],
                            "notes": "resumed from existing staged output",
                            "status": "restored",
                            "failure_reason": "",
                        }
                    )
                    _notify(
                        progress,
                        "resume",
                        f"Skipping completed archive image {archive_label}",
                        advance=1,
                    )
                    chunk_idx += 1
                    continue
                prompt = _task_text("", profile, consistency_state)
                assets = []
                if _figure_extraction_enabled(profile):
                    assets = _extract_figure_assets(
                        artifacts,
                        source_index,
                        f"{label}/{archive_label}",
                        ext_file,
                        chunk_idx,
                        provider=provider,
                        profile=profile,
                        progress=progress,
                    )
                result, usage = _restore_with_retry(
                    provider,
                    prompt,
                    instruction,
                    profile.model,
                    media_path=ext_file,
                    progress=progress,
                )
                if usage:
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    if usage.get("truncated"):
                        total_usage["truncated"] = True
                    _notify_usage(
                        progress,
                        f"Restored archive image {archive_label}",
                        usage,
                        total_usage,
                    )
                restored_text = _extract_multimodal_text(result)
                failure_reason = ""
                if not restored_text:
                    restored_text = ""
                    failure_reason = BLANK_PAGE_REASON
                elif usage and usage.get("truncated"):
                    failure_reason = "model context or output limit reached"
                review_note = ""
                pre_review_text = ""
                if restored_text:
                    restored_text, review_note, review_usage, pre_review_text = _maybe_review_restored_text(
                        restored_text,
                        profile,
                        provider,
                        instruction,
                        progress,
                        archive_label,
                        consistency_state,
                    )
                    if review_usage:
                        total_usage["prompt_tokens"] += review_usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += review_usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += review_usage.get("total_tokens", 0)
                        if review_usage.get("truncated"):
                            total_usage["truncated"] = True
                        _notify_usage(
                            progress,
                            f"Reviewed archive image {archive_label}",
                            review_usage,
                            total_usage,
                        )
                _update_consistency_state(consistency_state, restored_text)
                _write_consistency_checkpoint(
                    artifacts.run_dir, profile, consistency_state, archive_label
                )
                restored_parts.append(restored_text)
                artifacts.write_archive_item_restored(
                    source_index, label, archive_label, restored_text + "\n"
                )
                _add_archive_folder_part(archive_folder_parts, archive_label, restored_text)
                artifacts.write_restored_piece(
                    source_index, label, chunk_idx, restored_text + "\n"
                )
                chunk_record = {
                    "index": chunk_idx,
                    "input": f"[ZIP Image: {archive_label}]",
                    "restored_text": restored_text,
                    "uncertain": [],
                    "notes": review_note,
                    "status": _restoration_status(failure_reason),
                    "failure_reason": failure_reason,
                    "assets": assets,
                }
                if pre_review_text:
                    chunk_record["pre_review_text"] = pre_review_text
                chunks_record.append(chunk_record)
                chunk_idx += 1

        for folder_label, parts in sorted(archive_folder_parts.items()):
            folder_text = "\n\n".join(part for part in parts if part.strip()).strip()
            artifacts.write_archive_folder_combined(
                source_index, label, folder_label, folder_text + "\n"
            )

        combined = "\n\n".join(restored_parts) + "\n"
        file_failure_reason = next(
            (chunk["failure_reason"] for chunk in chunks_record if chunk.get("failure_reason")),
            "",
        )
        file_status = "restored" if not file_failure_reason else "partial"
        return (
            combined,
            {"status": file_status, "chunks": chunks_record, "failure_reason": file_failure_reason},
            total_usage,
        )
    finally:
        temp_dir.cleanup()


def _safe_archive_target(root: Path, member_name: str) -> Optional[Path]:
    candidate = Path(member_name)
    parts = []
    for part in candidate.parts:
        if part in {"", ".", ".."}:
            continue
        if part.endswith(":"):
            continue
        parts.append(part)
    if not parts:
        return None
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _safe_relative_archive_label(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def _add_archive_folder_part(
    archive_folder_parts: Dict[str, List[str]], archive_label: str, text: str
) -> None:
    folder_label = _archive_folder_label(archive_label)
    archive_folder_parts.setdefault(folder_label, []).append(text.strip())
