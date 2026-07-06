import gc
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
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


TEXT_EXTENSIONS = {".txt", ".md", ".html", ".hocr", ".xml", ".json"}
ProgressCallback = Callable[[str, str, int], None]
RESTORATION_CHUNK_CHARS = 5000
TRANSLATION_CHUNK_CHARS = 5000
BLANK_PAGE_REASON = "blank page or no readable text"
MAX_PROVIDER_RETRIES = 1

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
        path.write_text(text, encoding="utf-8")
        return path

    def write_item_translated(self, source_index: int, source_name: str, text: str) -> Path:
        item_dir = self._item_dir(source_index, source_name)
        source = _language_slug(self.source_language)
        target = _language_slug(self.output_language)
        path = item_dir / f"translated__{source}-to-{target}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_item_final(self, source_index: int, source_name: str, text: str) -> Path:
        item_dir = self._item_dir(source_index, source_name)
        path = item_dir / f"final__{_language_slug(self.output_language)}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_archive_item_restored(
        self, source_index: int, source_name: str, archive_label: str, text: str
    ) -> Path:
        item_dir = self._archive_item_dir(source_index, source_name, archive_label)
        path = item_dir / f"restored__{_language_slug(self.source_language)}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_archive_item_final(
        self, source_index: int, source_name: str, archive_label: str, text: str
    ) -> Path:
        item_dir = self._archive_item_dir(source_index, source_name, archive_label)
        path = item_dir / f"final__{_language_slug(self.output_language)}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_archive_folder_combined(
        self, source_index: int, source_name: str, folder_label: str, text: str
    ) -> Path:
        folder_dir = self._archive_folder_dir(source_index, source_name, folder_label)
        path = folder_dir / f"combined__{_language_slug(self.output_language)}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def write_final_output_aliases(self, text: str) -> List[Path]:
        aliases = [
            self.run_dir / "akshara_output.txt",
            self.run_dir
            / f"akshara_output__{_language_slug(self.output_language)}.txt",
        ]
        written = []
        for path in aliases:
            path.write_text(text, encoding="utf-8")
            written.append(path)
        return written

    def write_stage_manifest(self, manifest: Dict[str, object]) -> Path:
        path = self.stages_dir / "stage_manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

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
        path = source_dir / f"{piece_index:04d}-{stage_name}__{_language_slug(self.output_language if stage_name == 'translated' else self.source_language)}.txt"
        path.write_text(text, encoding="utf-8")
        return path


def run_pipeline(
    request: RunRequest, progress: Optional[ProgressCallback] = None
) -> Dict[str, object]:
    profile = request.profile
    profile.sync_translation_defaults()
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
            "input_files": [str(p.resolve()) for p in request.inputs.files],
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
                        "path": str(Path(item["path"]).resolve()),
                    }
                    for item in restored_sources
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

    metadata = {
        "title": f"Akshara Vision - {profile.name}",
        "created_at": timestamp,
        "workflow": profile.workflow,
        "document_type": profile.document_type,
        "source_language": profile.source_language,
        "output_language": profile.output_language,
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
                    "path": str(Path(item["path"]).resolve()),
                }
                for item in restored_sources
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
        text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            label = str(output_path.parent.relative_to(items_root)).replace("\\", "/")
            combined_parts.append(f"===== {label} =====\n{text}")
    return combined_parts


def _preferred_item_outputs(items_root: Path) -> List[Path]:
    for pattern in ("final__*.txt", "translated__*.txt", "restored__*.txt"):
        paths = sorted(path for path in items_root.rglob(pattern) if path.is_file())
        if paths:
            return paths
    return []


def _write_nested_folder_combines(items_root: Path, language_suffix: str) -> List[Path]:
    if not items_root.exists():
        return []
    output_paths = _preferred_item_outputs(items_root)
    if not output_paths:
        return []

    folder_parts: Dict[Path, List[tuple[str, str]]] = {}
    for output_path in output_paths:
        text = output_path.read_text(encoding="utf-8", errors="replace").strip()
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
            provider, prompt, instruction, profile.model, media_path=media_path
        )
        if usage:
            total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            total_usage["total_tokens"] += usage.get("total_tokens", 0)
            if usage.get("truncated"):
                total_usage["truncated"] = True

        parsed = _parse_restoration_result(result, chunk)
        restored_text = parsed["restored_text"].strip()
        if not restored_text:
            restored_text = chunk.strip()
            parsed["failure_reason"] = parsed["failure_reason"] or "source unreadable or too blurry"
        if usage and usage.get("truncated"):
            parsed["failure_reason"] = "model context or output limit reached"
            parsed["status"] = "partial"
        restored_chunks.append(restored_text)
        _update_consistency_state(consistency_state, restored_text)
        artifacts.write_restored_piece(
            source_index, source_label or source_path.name, index, restored_text + "\n"
        )
        structured_chunks.append(
            {
                "index": index,
                "input": _short_excerpt(chunk),
                "restored_text": restored_text,
                "uncertain": parsed["uncertain"],
                "notes": parsed["notes"],
                "status": parsed["status"],
                "failure_reason": parsed["failure_reason"],
            }
        )
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


def _restore_with_retry(
    provider,
    prompt: str,
    instruction: str,
    settings,
    media_path: Optional[Path] = None,
) -> tuple[str, dict]:
    last_response = ""
    last_usage: dict = {}
    last_error: Optional[Exception] = None
    for attempt in range(MAX_PROVIDER_RETRIES + 1):
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
            if attempt < MAX_PROVIDER_RETRIES:
                continue
            raise
        last_response = response
        last_usage = usage or {}
        if not _response_needs_retry(response):
            return response, last_usage
    if last_error:
        raise last_error
    return last_response, last_usage


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
        "observed_pages": 0,
        "paragraph_style": "",
        "heading_style": "",
        "page_marker_style": "",
        "layout_notes": [],
    }


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
        "observed_pages": state.get("observed_pages") or 0,
        "paragraph_style": state.get("paragraph_style") or "",
        "heading_style": state.get("heading_style") or "",
        "page_marker_style": state.get("page_marker_style") or "",
        "layout_notes": notes,
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
        + consistency_context
    )

    if execution_mode == "quality":
        depth_instruction = (
            "Perform a deep, rigorous analysis of the whole page before writing. Work through "
            "dense pages region by region, including margins, footnotes, headings, captions, "
            "columns, and small text. Take your time to parse faded, complex, or degraded "
            "characters before extracting the text.\n"
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
            "If the page is dense, prioritize complete extraction over perfect cleanup.\n"
            "If any words are unclear, mark them as [unclear].\n"
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
) -> tuple:
    """Send an image directly to the vision model and use its raw text output.

    Unlike text-chunk restoration, we do NOT demand JSON here.  The model's
    raw response IS the extracted text.  We only attempt JSON parsing as a
    bonus — if the model happens to return JSON, we use the structured data;
    otherwise, we take the full response as restored text.
    """
    prompt = _task_text("", profile, consistency_state)
    result, usage = _restore_with_retry(
        provider, prompt, instruction, profile.model, media_path=path
    )

    # Try JSON parsing first (in case the model does return structured data).
    restored_text = _extract_multimodal_text(result)
    failure_reason = ""

    if not restored_text:
        restored_text = ""
        failure_reason = BLANK_PAGE_REASON
    elif usage and usage.get("truncated"):
        failure_reason = "model context or output limit reached"
    _update_consistency_state(consistency_state, restored_text)
    label = source_label or path.name
    artifacts.write_restored_piece(source_index, label, 1, restored_text + "\n")

    record = {
        "status": _restoration_status(failure_reason),
        "chunks": [
            {
                "index": 1,
                "input": f"[Image: {label}]",
                "restored_text": restored_text,
                "uncertain": [],
                "notes": "",
                "status": _restoration_status(failure_reason),
                "failure_reason": failure_reason,
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
                provider, prompt, translation_instruction, profile.model
            )
            if usage:
                total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                total_usage["total_tokens"] += usage.get("total_tokens", 0)
                if usage.get("truncated"):
                    total_usage["truncated"] = True

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

            _notify(
                progress,
                "clean",
                f"Restoring text from {label} page {idx}/{total_label}",
                advance=1,
            )
            prompt = _task_text("", profile, consistency_state)
            try:
                result, usage = _restore_with_retry(
                    provider, prompt, instruction, profile.model, media_path=page_img
                )
                if usage:
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    if usage.get("truncated"):
                        total_usage["truncated"] = True
                restored_text = _extract_multimodal_text(result)
                failure_reason = ""
                if not restored_text:
                    restored_text = ""
                    failure_reason = BLANK_PAGE_REASON
                elif usage and usage.get("truncated"):
                    failure_reason = "model context or output limit reached"
                del result
            except Exception as exc:
                failed_pages.append((idx, page_img, prompt))
                restored_text = ""
                failure_reason = f"model generation failed: {exc}"

            _update_consistency_state(consistency_state, restored_text)
            _write_consistency_checkpoint(
                artifacts.run_dir, profile, consistency_state, f"{label} page {idx}"
            )
            restored_pages.append(restored_text)
            artifacts.write_restored_piece(source_index, label, idx, restored_text + "\n")
            chunks_record.append(
                {
                    "index": idx,
                    "input": f"[PDF Page {idx}: {label}]",
                    "restored_text": restored_text,
                    "uncertain": [],
                    "notes": "",
                    "status": _restoration_status(failure_reason),
                    "failure_reason": failure_reason,
                }
            )
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
                        provider, prompt, instruction, profile.model, media_path=page_img
                    )
                    if usage:
                        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += usage.get("total_tokens", 0)
                        if usage.get("truncated"):
                            total_usage["truncated"] = True
                    restored_text = _extract_multimodal_text(result)
                    failure_reason = ""
                    if not restored_text:
                        restored_text = ""
                        failure_reason = BLANK_PAGE_REASON
                    elif usage and usage.get("truncated"):
                        failure_reason = "model context or output limit reached"

                    restored_pages[idx - 1] = restored_text
                    for chunk in chunks_record:
                        if chunk["index"] == idx:
                            chunk["restored_text"] = restored_text
                            chunk["status"] = _restoration_status(failure_reason)
                            chunk["failure_reason"] = failure_reason
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
                    prompt = _task_text(sub_chunk, profile, consistency_state)
                    result, usage = _restore_with_retry(
                        provider, prompt, instruction, profile.model
                    )
                    if usage:
                        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += usage.get("total_tokens", 0)
                        if usage.get("truncated"):
                            total_usage["truncated"] = True
                    parsed = _parse_restoration_result(result, sub_chunk)
                    restored_text = parsed["restored_text"].strip()
                    if not restored_text:
                        restored_text = sub_chunk.strip()
                        if not restored_text:
                            parsed["failure_reason"] = parsed["failure_reason"] or BLANK_PAGE_REASON
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
                    chunks_record.append(
                        {
                            "index": chunk_idx,
                            "input": f"[ZIP Text: {archive_label}] " + _short_excerpt(sub_chunk),
                            "restored_text": restored_text,
                            "uncertain": parsed["uncertain"],
                            "notes": parsed["notes"],
                            "status": parsed["status"],
                            "failure_reason": parsed["failure_reason"],
                        }
                    )
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
                prompt = _task_text("", profile, consistency_state)
                result, usage = _restore_with_retry(
                    provider, prompt, instruction, profile.model, media_path=ext_file
                )
                if usage:
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    if usage.get("truncated"):
                        total_usage["truncated"] = True
                restored_text = _extract_multimodal_text(result)
                failure_reason = ""
                if not restored_text:
                    restored_text = ""
                    failure_reason = BLANK_PAGE_REASON
                elif usage and usage.get("truncated"):
                    failure_reason = "model context or output limit reached"
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
                chunks_record.append(
                    {
                        "index": chunk_idx,
                        "input": f"[ZIP Image: {archive_label}]",
                        "restored_text": restored_text,
                        "uncertain": [],
                        "notes": "",
                        "status": _restoration_status(failure_reason),
                        "failure_reason": failure_reason,
                    }
                )
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
