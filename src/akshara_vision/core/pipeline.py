import gc
import importlib.util
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, replace
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
LayoutBackend = Callable[[Path], Dict[str, object]]
RESTORATION_CHUNK_CHARS = 5000
TRANSLATION_CHUNK_CHARS = 5000
BLANK_PAGE_REASON = "blank page or no readable text"
DEFAULT_PROVIDER_RETRIES = 3
MAX_FIGURE_CROPS_PER_PAGE = 4
_LAYOUT_BACKENDS: Dict[str, LayoutBackend] = {}
_LAYOUT_MODEL_CACHE: Dict[str, object] = {}

DOCUMENT_ROLE_GUIDANCE = {
    "book": {
        "front": {"title", "contents", "preface", "foreword", "introduction"},
        "main": {"chapter", "section", "part", "appendix", "index", "table", "chart"},
        "roles": {
            "title": "title matter",
            "contents": "table of contents",
            "preface": "front matter",
            "chapter": "chapter text",
            "section": "section text",
            "appendix": "appendix",
            "index": "index",
            "table": "table",
            "chart": "chart or graph",
            "footnotes": "notes",
            "body": "body text",
        },
    },
    "magazine": {
        "roles": {
            "cover": "cover or title page",
            "masthead": "masthead",
            "contents": "contents",
            "editorial": "editorial",
            "feature": "feature article",
            "article": "article",
            "advertisement": "advertisement",
            "sidebar": "sidebar",
            "caption": "caption",
            "multi-column": "multi-column article flow",
            "table": "table",
            "chart": "chart or graph",
            "illustrated": "illustrated page",
            "body": "periodical body",
        },
    },
    "newspaper": {
        "roles": {
            "front-page": "front page",
            "headline": "headline",
            "article": "article",
            "dateline": "dateline",
            "byline": "byline",
            "advertisement": "advertisement",
            "classifieds": "classifieds",
            "continuation": "continued article",
            "multi-column": "column flow",
            "caption": "caption",
            "table": "table",
            "chart": "chart or graph",
            "body": "newspaper body",
        },
    },
    "manuscript": {
        "roles": {
            "folio": "folio marker",
            "marginalia": "marginalia",
            "annotation": "annotation",
            "correction": "scribal correction",
            "colophon": "colophon",
            "damaged": "damaged passage",
            "lineated-text": "lineated manuscript text",
            "table": "table",
            "chart": "chart or graph",
            "body": "manuscript body",
        },
    },
    "journal article": {
        "roles": {
            "title": "article title",
            "authors": "authors",
            "abstract": "abstract",
            "section": "section",
            "references": "references",
            "bibliography": "bibliography",
            "footnotes": "notes",
            "figure-table": "figure or table",
            "table": "table",
            "chart": "chart or graph",
            "body": "article body",
        },
    },
    "letter": {
        "roles": {
            "date-place": "date or place line",
            "salutation": "salutation",
            "signature": "signature",
            "postscript": "postscript",
            "address": "address",
            "body": "letter body",
        },
    },
    "archive bundle": {
        "roles": {
            "cover-sheet": "cover sheet",
            "folder-label": "folder label",
            "identifier": "identifier",
            "date": "date",
            "form": "form",
            "record": "record",
            "item-boundary": "item boundary",
            "body": "archive item body",
        },
    },
    "legal document": {
        "roles": {
            "title": "document title",
            "parties": "parties or signatories",
            "recitals": "recitals",
            "definitions": "definitions",
            "clauses": "clauses",
            "schedule": "schedule or annexure",
            "exhibits": "exhibits",
            "signature": "signature block",
            "table": "table",
            "chart": "chart or graph",
            "body": "legal body",
        },
    },
    "finance document": {
        "roles": {
            "title": "document title",
            "statement": "statement section",
            "table": "financial table",
            "account": "account details",
            "summary": "summary",
            "notes": "notes",
            "table": "table",
            "chart": "chart or graph",
            "body": "finance body",
        },
    },
    "healthcare document": {
        "roles": {
            "title": "report title",
            "patient": "patient details",
            "findings": "findings",
            "diagnosis": "diagnosis",
            "medications": "medications",
            "instructions": "instructions",
            "table": "table",
            "chart": "chart or graph",
            "body": "healthcare body",
        },
    },
    "insurance document": {
        "roles": {
            "title": "policy title",
            "policy": "policy section",
            "coverage": "coverage",
            "claim": "claim section",
            "exclusions": "exclusions",
            "premium": "premium details",
            "terms": "terms",
            "table": "table",
            "chart": "chart or graph",
            "body": "insurance body",
        },
    },
}

EXECUTION_MODE_PDF_DPI = {
    "fast": 300,
    "balanced": 400,
    "quality": 500,
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
        self.records_dir = self.stages_dir / "records"
        self.combined_dir = self.stages_dir / "combined"
        self.items_dir = self.run_dir / "items"
        self.assets_dir = self.run_dir / "assets"
        self.restored_dir.mkdir(parents=True, exist_ok=True)
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)
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

    def write_record_piece(
        self,
        source_index: int,
        source_name: str,
        piece_index: int,
        record: Dict[str, object],
        document_type: str = "",
    ) -> Path:
        source_dir = _numbered_label_dir(self.records_dir, source_index, source_name)
        source_dir.mkdir(parents=True, exist_ok=True)
        path = source_dir / f"{piece_index:04d}-record.json"
        payload = dict(record)
        payload.setdefault("source_index", source_index)
        payload.setdefault("source_name", source_name)
        payload.setdefault("piece_index", piece_index)
        payload.setdefault("semantic_tags", _semantic_tags_for_chunk(payload, document_type, source_name))
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

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
            "path": _run_relative_path(self.run_dir, asset_path),
            "width": width,
            "height": height,
            "dpi": dpi,
            "placement": _asset_placement(width, height),
            "layout": _asset_layout_metadata(None, None, width, height),
        }

    def write_figure_asset(
        self,
        source_index: int,
        source_name: str,
        image,
        piece_index: int,
        figure_index: int,
        bbox: tuple[int, int, int, int],
        page_size: Optional[tuple[int, int]] = None,
        dpi: Optional[int] = None,
    ) -> Dict[str, object]:
        asset_dir = self.assets_dir / _slugify(source_name)
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"{source_index:04d}-{piece_index:04d}-figure-{figure_index:02d}.png"
        image.save(asset_path)
        width, height = _image_dimensions(asset_path)
        return {
            "kind": "figure-crop",
            "path": _run_relative_path(self.run_dir, asset_path),
            "_local_path": str(asset_path),
            "width": width,
            "height": height,
            "dpi": dpi,
            "bbox": list(bbox),
            "placement": _asset_placement(width, height),
            "layout": _asset_layout_metadata(bbox, page_size, width, height),
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
            "input_paths": [str(p.expanduser().resolve()) for p in request.inputs.files],
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

        raw_parts.append(raw_text.strip())
        output_cleaned = _text_with_chunk_assets(cleaned, restoration_record.get("chunks"))
        source_text = output_cleaned.strip() + "\n"
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
                "assets": _collect_assets_from_records(restoration_records),
                "input_paths": [str(p.expanduser().resolve()) for p in request.inputs.files],
            },
        )
        cleaned_parts.append(output_cleaned.strip())
        _notify(progress, "source", f"Bundling source {source_label}", advance=1)
        _copy_source(path, run_dir / "sources", index=index, label=source_label)
        gc.collect()

    raw_text = "\n\n\f\n\n".join(raw_parts).strip() + "\n"
    cleaned_text = "\n\n\f\n\n".join(cleaned_parts).strip() + "\n"
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
    detected_title = _detected_title(document_structure) or _publication_fallback_title(profile, request.inputs.files)
    asset_manifest = _collect_assets_from_records(restoration_records)
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
        "assets": asset_manifest,
        "consistency": consistency_state,
        "document_structure": document_structure,
        "assembly_profile": _assembly_profile(profile.document_type, profile.output_formats),
    }

    destination = run_dir / "akshara_output"
    exports = _export_text(
        final_text,
        destination,
        metadata,
        profile.output_formats,
        progress,
        run_dir=run_dir,
    )

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
            "assets": asset_manifest,
            "next_action": "Run complete.",
        },
    )
    return {"run_dir": run_dir, "exports": exports, "manifest": manifest}


def combine_stage_outputs(run_dir: Path) -> Dict[str, object]:
    run_dir = Path(run_dir)
    stage_root = run_dir / "stages"
    items_root = run_dir / "items"
    run_manifest = run_dir / "run_manifest.json"
    manifest = _load_manifest(run_manifest)
    run_state = _load_manifest(run_dir / "run_state.json")
    effective_manifest = manifest or run_state
    _ensure_manifest_structure(effective_manifest)
    if not stage_root.exists() and not items_root.exists() and not effective_manifest:
        raise RuntimeError(f"No staged outputs found in {run_dir}.")

    combined_parts = _combined_parts_from_manifest(effective_manifest)
    if not combined_parts and stage_root.exists():
        combined_parts = _combined_parts_from_record_checkpoints(stage_root / "records")
    if not combined_parts:
        combined_parts = _combined_parts_from_items(items_root)
    if not combined_parts and stage_root.exists():
        combined_parts = _combined_parts_from_stages(stage_root)
    if not combined_parts:
        raise RuntimeError(f"No staged pieces found in {stage_root}.")

    combined_text = "\n\n\f\n\n".join(part for part in combined_parts if part.strip()).strip()
    if not combined_text:
        combined_text = "[missing text]"

    combined_dir = stage_root / "combined"
    combined_dir.mkdir(parents=True, exist_ok=True)
    combined_path = combined_dir / "recombined.txt"
    combined_path.write_text(combined_text + "\n", encoding="utf-8")

    language_suffix = _combine_language_suffix(run_manifest, run_state)

    output_alias = run_dir / f"akshara_output__{language_suffix}.txt"
    output_alias.write_text(combined_text + "\n", encoding="utf-8")
    canonical = run_dir / "akshara_output.txt"
    canonical.write_text(combined_text + "\n", encoding="utf-8")
    _write_nested_folder_combines(items_root, language_suffix)

    metadata = _combine_metadata(effective_manifest, run_dir, language_suffix)
    metadata["assets"] = _collect_assets_from_records(
        (effective_manifest.get("metadata") or {}).get("restoration")
        if isinstance(effective_manifest.get("metadata"), dict)
        else []
    ) or _collect_assets_from_record_checkpoints(stage_root / "records")
    if isinstance(manifest.get("metadata"), dict):
        manifest["metadata"]["title"] = metadata.get("title", "Untitled")
    output_formats = _output_formats_from_manifest(effective_manifest)
    exports = _export_text(
        combined_text + "\n",
        run_dir / "akshara_output",
        metadata,
        output_formats,
        run_dir=run_dir,
    )
    _write_recombined_manifest(run_manifest, effective_manifest, exports)

    return {
        "run_dir": run_dir,
        "combined_path": combined_path,
        "output_path": canonical,
        "alias_path": output_alias,
        "exports": exports,
}


def _combined_parts_from_manifest(manifest: Dict[str, object]) -> List[str]:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    records = metadata.get("restoration") if isinstance(metadata, dict) else None
    if not isinstance(records, list):
        return []
    parts = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        text = _text_with_chunk_assets("", record.get("chunks"))
        if text.strip():
            parts.append(text.strip())
    return parts


def _ensure_manifest_structure(manifest: Dict[str, object]) -> None:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    records = metadata.get("restoration") if isinstance(metadata, dict) else None
    if not isinstance(records, list):
        return
    existing = metadata.get("document_structure")
    if (
        isinstance(existing, dict)
        and existing.get("semantic_units")
        and existing.get("layout_tree")
        and existing.get("layout_profile")
    ):
        return
    profile = manifest.get("profile") if isinstance(manifest.get("profile"), dict) else {}
    document_type = str(
        metadata.get("document_type")
        or profile.get("document_type")
        or "General"
    )
    metadata["document_structure"] = _document_structure(records, document_type)


def _combined_parts_from_record_checkpoints(records_root: Path) -> List[str]:
    if not records_root.exists():
        return []
    parts = []
    for source_group in sorted(path for path in records_root.glob("*") if path.is_dir()):
        chunks = []
        for record_path in sorted(source_group.glob("*-record.json")):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                chunks.append(record)
        text = _text_with_chunk_assets("", chunks)
        if text.strip():
            parts.append(text.strip())
    return parts


def _collect_assets_from_record_checkpoints(records_root: Path) -> List[Dict[str, object]]:
    if not records_root.exists():
        return []
    assets = []
    for record_path in sorted(records_root.rglob("*-record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            for asset in record.get("assets") or []:
                if isinstance(asset, dict):
                    assets.append(asset)
    return assets


def _combined_parts_from_items(items_root: Path) -> List[str]:
    if not items_root.exists():
        return []
    combined_parts: List[str] = []
    for output_path in _preferred_item_outputs(items_root):
        text = _read_structured_output_text(output_path).strip()
        if text:
            combined_parts.append(text)
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


def _chunk_text_with_assets(chunk: Dict[str, object]) -> str:
    text = str(chunk.get("restored_text") or chunk.get("text") or "").strip()
    markers = _asset_markers(chunk.get("assets"))
    if markers and not _contains_asset_marker(text):
        text = (text + "\n\n" + markers).strip() if text else markers
    return text


def _asset_markers(assets: object) -> str:
    if not isinstance(assets, list):
        return ""
    markers = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        path = str(asset.get("path") or "").strip()
        if not path:
            continue
        label = _asset_display_label(asset)
        markers.append(f"[image: {label} | {path}]")
    return "\n".join(markers)


def _asset_display_label(asset: Dict[str, object]) -> str:
    label = str(asset.get("label") or asset.get("kind") or "figure").strip()
    label = label.split(" | ", 1)[0].strip()
    label = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
    return label or "figure"


def _asset_marker_placement(asset: Dict[str, object]) -> str:
    layout = asset.get("layout")
    if isinstance(layout, dict):
        zone = str(layout.get("page_zone") or "").strip()
        size_class = str(layout.get("size_class") or "").strip()
        values = [item for item in [zone if zone != "unknown" else "", size_class] if item]
        if values:
            return ", ".join(values)
    placement = asset.get("placement")
    if isinstance(placement, dict):
        return str(placement.get("recommended_width") or "").strip()
    return str(placement or "").strip()


def _asset_size_label(asset: Dict[str, object]) -> str:
    width = asset.get("width")
    height = asset.get("height")
    if width and height:
        return f"{width}x{height}"
    return ""


def _contains_asset_marker(text: str) -> bool:
    lowered = text.lower()
    return "[image:" in lowered or "![" in lowered


def _text_with_chunk_assets(text: str, chunks: object) -> str:
    if not isinstance(chunks, list) or not chunks:
        return text
    parts = [_chunk_text_with_assets(chunk) for chunk in chunks if isinstance(chunk, dict)]
    combined = "\n\n\f\n\n".join(part for part in parts if part.strip()).strip()
    return combined or text


def _collect_assets_from_records(records: object) -> List[Dict[str, object]]:
    assets: List[Dict[str, object]] = []
    if not isinstance(records, list):
        return assets
    for record in records:
        if not isinstance(record, dict):
            continue
        for chunk in record.get("chunks") or []:
            if not isinstance(chunk, dict):
                continue
            for asset in chunk.get("assets") or []:
                if isinstance(asset, dict):
                    assets.append(asset)
    return assets


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
        combined = "\n\n\f\n\n".join(text for _label, text in parts if text.strip()).strip()
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
        text = "\n\n\f\n\n".join(part for part in pieces if part).strip()
        if text:
            combined_parts.append(text)
    return combined_parts


def _combine_language_suffix(run_manifest: Path, run_state: Optional[Dict[str, object]] = None) -> str:
    language_suffix = "combined"
    if run_manifest.exists():
        try:
            manifest = json.loads(run_manifest.read_text(encoding="utf-8"))
            metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
            language_suffix = _language_slug(metadata.get("output_language") or language_suffix)
        except json.JSONDecodeError:
            pass
    elif run_state:
        profile = run_state.get("profile") if isinstance(run_state.get("profile"), dict) else {}
        language_suffix = _language_slug(profile.get("output_language") or language_suffix)
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
    if not str(combined.get("title") or "").strip():
        combined["title"] = "Untitled"
    combined["recombined"] = True
    combined["output_language"] = metadata.get("output_language") or language_suffix
    return combined


def _output_formats_from_manifest(manifest: Dict[str, object]) -> List[str]:
    profile = manifest.get("profile") if isinstance(manifest.get("profile"), dict) else {}
    formats = profile.get("output_formats") if isinstance(profile, dict) else None
    if isinstance(formats, list):
        cleaned = [str(item).strip() for item in formats if str(item).strip() in exporter_registry()]
        return cleaned or ["txt"]
    if isinstance(formats, str):
        cleaned = [item.strip() for item in formats.split(",") if item.strip() in exporter_registry()]
        return cleaned or ["txt"]
    return ["txt"]


def _export_text(
    text: str,
    destination: Path,
    metadata: Dict[str, object],
    output_formats: List[str],
    progress: Optional[ProgressCallback] = None,
    run_dir: Optional[Path] = None,
) -> List[ExportResult]:
    exports: List[ExportResult] = []
    registry = exporter_registry()
    export_metadata = dict(metadata)
    if run_dir is not None:
        export_metadata["run_dir"] = str(run_dir)
    for output_format in output_formats:
        exporter = registry.get(output_format)
        if exporter is None:
            continue
        _notify(progress, "export", f"Exporting {output_format}", advance=1)
        exports.append(exporter.export(text, destination, export_metadata))
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
        artifacts.write_record_piece(
            source_index,
            source_label or source_path.name,
            index,
            chunk_record,
            profile.document_type,
        )
    combined = "\n\n\f\n\n".join(part for part in restored_chunks if part.strip()).strip()
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


def _run_relative_path(run_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(run_dir.resolve())).replace("\\", "/")
    except ValueError:
        return _safe_path(path)


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
        f"tokens this page: {item_total} "
        f"(input {item_prompt}, output {item_completion}); "
        f"run total: {total_all} "
        f"(input {total_prompt}, output {total_completion}){truncated}"
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
            response, usage = _provider_restore_with_heartbeat(
                provider,
                retry_prompt,
                instruction,
                settings,
                media_path=media_path,
                progress=progress,
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


def _provider_restore_with_heartbeat(
    provider,
    prompt: str,
    instruction: str,
    settings,
    media_path: Optional[Path],
    progress: Optional[ProgressCallback],
) -> tuple[str, dict]:
    results: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=1)
    label = media_path.name if media_path else "text chunk"
    started_at = time.monotonic()

    def worker() -> None:
        try:
            results.put(("ok", provider.restore_text(prompt, instruction, settings, media_path=media_path)))
        except BaseException as exc:  # noqa: BLE001 - preserve provider exception across thread
            results.put(("error", exc))

    thread = threading.Thread(target=worker, name="akshara-provider-call")
    thread.start()
    last_notice = 0.0
    try:
        while thread.is_alive():
            try:
                status, payload = results.get(timeout=0.5)
                if status == "error":
                    raise payload
                return payload  # type: ignore[return-value]
            except queue.Empty:
                elapsed = time.monotonic() - started_at
                if elapsed - last_notice >= 20:
                    last_notice = elapsed
                    _notify(
                        progress,
                        "waiting",
                        f"Model still working on {label} ({_format_elapsed(elapsed)} elapsed)",
                        advance=0,
                    )
        status, payload = results.get_nowait()
        if status == "error":
            raise payload
        return payload  # type: ignore[return-value]
    except KeyboardInterrupt:
        _notify(
            progress,
            "interrupt",
            "Interrupt received. Waiting for the active model request to finish cleanly...",
            advance=0,
        )
        while thread.is_alive():
            thread.join(timeout=2)
            elapsed = time.monotonic() - started_at
            _notify(
                progress,
                "interrupt",
                f"Safe stop pending: active model request is still running ({_format_elapsed(elapsed)} elapsed)",
                advance=0,
            )
        raise


def _format_elapsed(seconds: float) -> str:
    total = max(int(seconds), 0)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


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
        "encountered_structures": [],
        "recent_text_excerpt": "",
    }


def _document_type_guidance(profile: WorkflowProfile) -> str:
    document_type = profile.document_type
    kind = str(document_type or "general").strip().lower()
    guidance = {
        "book": (
            "Book restoration skill: preserve title pages, subtitles, author/editor lines, "
            "preface/foreword sections, table of contents, chapter headings, page numbers, "
            "footnotes, indexes, appendices, publisher lines, running headers, tables, and chart captions without inventing missing data. "
            "Restore body prose as natural paragraphs for later book assembly; do not preserve artificial "
            "scan line wrapping unless the lineation itself carries meaning. "
            "Keep the page readable in one pass; do not spend time reconstructing invisible words."
        ),
        "magazine": (
            "Magazine restoration skill: identify columns, article boundaries, headlines, decks, "
            "captions, bylines, page numbers, advertisements, sidebars, tables, and charts. Do not merge text from "
            "different columns or adjacent articles. Read each article block in its natural flow and "
            "move on promptly when a damaged word is uncertain."
        ),
        "newspaper": (
            "Newspaper restoration skill: preserve column order, article boundaries, headlines, "
            "datelines, bylines, captions, advertisements, continuation markers, tables, and charts. Avoid mixing "
            "rows across columns. Do not infer show-through or back-page impressions as front-page text."
        ),
        "manuscript": (
            "Manuscript restoration skill: preserve folio/page markers, marginalia, corrections, "
            "scribal marks, uncertain readings, line breaks, tables, charts, and damaged text honestly. Do not force "
            "modern spelling or complete damaged readings."
        ),
        "journal article": (
            "Article restoration skill: preserve title, authors, abstract, section headings, "
            "citations, footnotes, tables, figures, charts, captions, and bibliography structure. Keep citation "
            "order exactly as visible."
        ),
        "letter": (
            "Letter restoration skill: preserve salutation, date, place, body paragraphs, "
            "postscript, signature, and address marks. Preserve line breaks where they carry letter form."
        ),
        "archive bundle": (
            "Archive bundle skill: preserve each item boundary, original ordering, labels, dates, "
            "identifiers, and folder-like grouping. Treat each visible record as an item, not as one "
            "continuous book page."
        ),
        "legal document": (
            "Legal document skill: preserve parties, recitals, clauses, schedules, exhibits, signatures, "
            "defined terms, numbering, tables, and charts exactly as visible. Keep clause order, references, and quoted text "
            "stable. Do not merge separate clauses or infer missing legal text."
        ),
        "finance document": (
            "Finance document skill: preserve statements, account labels, line items, totals, tables, notes, "
            "charts, dates, and monetary values exactly as visible. Keep numerical alignment and do not reformat figures "
            "into prose."
        ),
        "healthcare document": (
            "Healthcare document skill: preserve report headings, patient details, findings, measurements, "
            "diagnoses, medications, tables, charts, and instructions exactly as visible. Do not invent missing results or "
            "medical interpretation."
        ),
        "insurance document": (
            "Insurance document skill: preserve policy terms, coverage sections, exclusions, claim fields, "
            "premium details, signatures, tables, and charts exactly as visible. Keep numbering and policy labels stable."
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
    semantic_units = []
    layout_tree = []
    for source_number, record in enumerate(records, start=1):
        source_label = str(record.get("label") or record.get("source") or f"source-{source_number}")
        for chunk in record.get("chunks", []):
            if isinstance(chunk, dict):
                chunk.setdefault(
                    "semantic_tags",
                    _semantic_tags_for_chunk(chunk, document_type, source_label),
                )
                chunks.append(chunk)
                semantic = chunk["semantic_tags"]
                semantic_units.append(semantic)
                layout_tree.append(_layout_tree_node(chunk, semantic, source_label, len(layout_tree) + 1))
    observations = []
    for chunk in chunks:
        observation = _piece_observations(
            str(chunk.get("restored_text") or ""), document_type, int(chunk.get("index") or 0)
        )
        if isinstance(chunk.get("native_layout"), dict):
            observation["native_layout"] = chunk["native_layout"]
        observations.append(observation)
    title_candidates = []
    page_markers = []
    section_headings = []
    contents_entries = []
    footnotes = []
    contributors = []
    publishers = []
    running_headers = []
    content_kinds: Dict[str, int] = {}
    layouts: Dict[str, int] = {}
    content_features: Dict[str, int] = {}
    asset_count = 0
    table_block_count = 0
    chart_block_count = 0
    layout_profile_pages = []
    for item in observations:
        title_candidates.extend(item.get("title_candidates", []))
        page_marker = item.get("page_marker")
        if page_marker:
            page_markers.append(page_marker)
        section_headings.extend(item.get("section_headings", []))
        contents_entries.extend(item.get("contents_entries", []))
        footnotes.extend(item.get("footnotes", []))
        contributors.extend(item.get("contributors", []))
        publishers.extend(item.get("publishers", []))
        running_header = item.get("running_header")
        if running_header:
            running_headers.append(running_header)
        content_kind = str(item.get("content_kind") or "body")
        content_kinds[content_kind] = content_kinds.get(content_kind, 0) + 1
        layout = str(item.get("layout") or "single-flow")
        layouts[layout] = layouts.get(layout, 0) + 1
        for feature in item.get("content_features", []):
            feature_name = str(feature)
            content_features[feature_name] = content_features.get(feature_name, 0) + 1
        if item.get("table_rows"):
            table_block_count += 1
        if item.get("chart_signals"):
            chart_block_count += 1
        layout_profile_pages.append(_layout_page_profile(item))
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
        "contents_entries": contents_entries[:120],
        "footnotes": footnotes[:120],
        "contributors": _unique_limited(contributors, 12),
        "publishers": _unique_limited(publishers, 8),
        "running_headers": _frequent_limited(running_headers, 12),
        "content_kinds": content_kinds,
        "layouts": layouts,
        "content_features": content_features,
        "table_block_count": table_block_count,
        "chart_block_count": chart_block_count,
        "semantic_units": semantic_units,
        "layout_tree": layout_tree,
        "layout_profile": _layout_profile(layout_profile_pages, document_type),
        "figure_extraction_enabled": _figure_extraction_enabled(profile),
        "asset_count": asset_count,
        "assembly_profile": _assembly_profile(
            document_type, list(profile.output_formats) if profile else ["txt"]
        ),
    }


def _layout_tree_node(
    chunk: Dict[str, object],
    semantic: Dict[str, object],
    source_label: str,
    reading_order: int,
) -> Dict[str, object]:
    text = str(chunk.get("restored_text") or chunk.get("text") or "").strip()
    assets = chunk.get("assets") if isinstance(chunk.get("assets"), list) else []
    native_layout = chunk.get("native_layout") if isinstance(chunk.get("native_layout"), dict) else {}
    page_layout = _layout_page_profile(
        {
            "content_kind": semantic.get("role"),
            "layout": semantic.get("layout"),
            "content_features": semantic.get("content_features") or [],
            "page_marker": semantic.get("page_marker") or "",
            "headings": semantic.get("headings") or [],
            "contents_entries": semantic.get("contents_entries") or [],
            "table_rows": semantic.get("table_rows") or [],
            "chart_signals": semantic.get("chart_signals") or [],
            "footnotes": semantic.get("footnotes") or [],
            "running_header": semantic.get("running_header") or "",
            "has_figure_marker": semantic.get("has_figures") or False,
            "text_excerpt": text,
            "native_layout": native_layout,
        }
    )
    return {
        "reading_order": reading_order,
        "source": source_label,
        "page_number": int(chunk.get("index") or chunk.get("page_number") or reading_order),
        "role": str(semantic.get("role") or "body"),
        "role_label": str(semantic.get("role_label") or "body"),
        "layout": semantic.get("layout") or "single-flow",
        "confidence": _layout_confidence(text, semantic, assets),
        "text_excerpt": _short_excerpt(text, 180),
        "page_marker": semantic.get("page_marker") or "",
        "headings": semantic.get("headings") or [],
        "content_features": semantic.get("content_features") or [],
        "table_rows": semantic.get("table_rows") or [],
        "chart_signals": semantic.get("chart_signals") or [],
        "page_layout": page_layout,
        "native_layout": native_layout,
        "assets": [_layout_asset_entry(asset) for asset in assets if isinstance(asset, dict)],
    }


def _layout_asset_entry(asset: Dict[str, object]) -> Dict[str, object]:
    return {
        "kind": asset.get("kind") or "figure-crop",
        "label": asset.get("label") or asset.get("kind") or "figure",
        "path": asset.get("path") or "",
        "width": asset.get("width"),
        "height": asset.get("height"),
        "bbox": asset.get("bbox"),
        "placement": asset.get("placement") or {},
        "layout": asset.get("layout") or {},
    }


def _piece_observations(text: str, document_type: str, index: int) -> Dict[str, object]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_lines = lines[:12]
    line_lengths = [len(line) for line in lines[:80] if line.strip()]
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
    table_rows = _table_rows(lines)
    chart_signals = _chart_signals(lines)
    detected_kind = _content_kind(lines, document_type)
    if table_rows and detected_kind == "body":
        detected_kind = "table"
    elif chart_signals and detected_kind == "body" and len(lines) <= 48:
        detected_kind = "chart"
    kind = "title" if detected_kind == "body" and _looks_like_title_page(lines, index) else detected_kind
    features = _content_features(lines, kind, document_type)
    layout = _layout_class(lines, kind, features)
    return {
        "index": index,
        "content_kind": kind,
        "role_label": _role_label(kind, document_type),
        "assembly_hint": _assembly_hint(kind, document_type),
        "layout": layout,
        "content_features": features,
        "page_marker": page_marker,
        "title_candidates": first_lines[:2] if index <= 2 else [],
        "section_headings": headings[:6],
        "contents_entries": _contents_entries(lines) if kind == "contents" else [],
        "table_rows": table_rows,
        "chart_signals": chart_signals,
        "footnotes": _footnotes(lines),
        "contributors": _contributors(lines),
        "publishers": _publishers(lines),
        "running_header": _running_header(lines),
        "line_count": len(lines),
        "avg_line_length": round(sum(line_lengths) / max(len(line_lengths), 1), 1) if line_lengths else 0.0,
        "has_multi_column_spacing": any(re.search(r"\S\s{4,}\S", line) for line in lines[:80]),
        "has_figure_marker": any("[image:" in line.lower() for line in lines),
    }


def _semantic_tags_for_chunk(
    chunk: Dict[str, object], document_type: str, source_label: str
) -> Dict[str, object]:
    text = str(chunk.get("restored_text") or chunk.get("text") or "")
    index = int(chunk.get("index") or chunk.get("piece_index") or 0)
    observations = _piece_observations(text, document_type, index)
    role = str(observations.get("content_kind") or "body")
    return {
        "source": source_label,
        "index": index,
        "role": role,
        "role_label": observations.get("role_label") or _role_label(role, document_type),
        "layout": observations.get("layout") or "single-flow",
        "assembly_hint": observations.get("assembly_hint") or _assembly_hint(role, document_type),
        "content_features": observations.get("content_features") or [],
        "page_marker": observations.get("page_marker") or "",
        "headings": observations.get("section_headings") or [],
        "title_candidates": observations.get("title_candidates") or [],
        "contents_entries": observations.get("contents_entries") or [],
        "table_rows": observations.get("table_rows") or [],
        "chart_signals": observations.get("chart_signals") or [],
        "footnotes": observations.get("footnotes") or [],
        "contributors": observations.get("contributors") or [],
        "publishers": observations.get("publishers") or [],
        "running_header": observations.get("running_header") or "",
        "has_figures": bool(chunk.get("assets")),
        "asset_count": len(chunk.get("assets") or []) if isinstance(chunk.get("assets"), list) else 0,
    }


def _contents_entries(lines: List[str]) -> List[Dict[str, str]]:
    entries = []
    for line in lines[:120]:
        cleaned = re.sub(r"\s+", " ", line.strip())
        if len(cleaned) < 3:
            continue
        if re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", cleaned, re.I):
            continue
        if len(cleaned) > 180:
            continue
        if cleaned.lower().startswith(("contents", "table of contents")):
            continue
        if _looks_like_body_line(cleaned):
            continue
        page_text = r"(?P<page>[ivxlcdm\d]+)"
        title_text = r"(?P<title>.+?)"
        match = re.match(
            rf"^{title_text}\s*(?:\.{{2,}}|\s{{2,}}|[|:]\s*|-\s+)\s*{page_text}$",
            cleaned,
            re.I,
        )
        if not match:
            match = re.match(rf"^{page_text}\s+{title_text}$", cleaned, re.I)
        if not match:
            continue
        title = match.group("title").strip(" .-")
        page = match.group("page").strip()
        if not title or not page:
            continue
        if len(title) < 2 or len(title) > 140:
            continue
        if re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", title, re.I):
            continue
        if title and page:
            entries.append({"title": title, "page": page, "raw": line.strip()})
    return entries[:80]


def _looks_like_body_line(text: str) -> bool:
    words = re.findall(r"[A-Za-z]+", text)
    if len(words) < 4:
        return False
    if len(text) > 95 and not re.search(r"\.{2,}|\s{2,}|[|:]-?\s*", text):
        return True
    if re.search(r"[.!?]\s+[A-Z]", text):
        return True
    if re.search(r"\b(and|or|but|the|this|that|with|from|into|upon|which)\b", text, re.I) and len(words) >= 8:
        return True
    return False


def _footnotes(lines: List[str]) -> List[Dict[str, str]]:
    notes = []
    for line in lines:
        match = re.match(r"^\s*(?P<marker>(?:\*+|\d+|[a-z]))[\).:\]]\s+(?P<text>.+)$", line, re.I)
        if match and len(match.group("text").strip()) > 8:
            notes.append(
                {
                    "marker": match.group("marker"),
                    "text": match.group("text").strip(),
                }
            )
    return notes[:40]


def _contributors(lines: List[str]) -> List[str]:
    contributors = []
    for line in lines[:30]:
        cleaned = re.sub(r"\s+", " ", line.strip())
        if not cleaned or len(cleaned) > 140:
            continue
        if re.search(r"\b(by|author|edited by|editor|translated by|translator|compiled by)\b", cleaned, re.I):
            contributors.append(cleaned)
    return _unique_limited(contributors, 8)


def _publishers(lines: List[str]) -> List[str]:
    publishers = []
    for line in lines[:50]:
        cleaned = re.sub(r"\s+", " ", line.strip())
        if not cleaned or len(cleaned) > 160:
            continue
        if re.search(r"\b(published by|publisher|press|publication|publications|printing|printer)\b", cleaned, re.I):
            publishers.append(cleaned)
    return _unique_limited(publishers, 6)


def _running_header(lines: List[str]) -> str:
    candidates = []
    for line in lines[:3] + lines[-3:]:
        cleaned = re.sub(r"\s+", " ", line.strip())
        if 4 <= len(cleaned) <= 90 and not re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", cleaned, re.I):
            candidates.append(cleaned)
    return candidates[0] if candidates else ""


def _content_kind(lines: List[str], document_type: str) -> str:
    joined = "\n".join(lines[:40]).lower()
    kind = str(document_type or "").lower()
    first = lines[0].strip().lower() if lines else ""
    if "contents" in joined or "table of contents" in joined:
        return "contents"
    chart_signals = _chart_signals(lines)
    if chart_signals and len(lines) <= 60 and re.search(r"\b(chart|graph|plot|diagram|axis|legend)\b", joined):
        return "chart"
    if _table_rows(lines) and len(lines) <= 80:
        return "table"
    if "preface" in joined or "foreword" in joined:
        return "preface"
    if "abstract" in first or first == "summary":
        return "abstract"
    if re.search(r"\b(references|bibliography|works cited)\b", joined):
        return "references" if kind == "journal article" else "bibliography"
    if "index" in joined and kind == "book":
        return "index"
    if re.search(r"\b(appendix)\b", joined):
        return "appendix"
    if kind == "book" and re.search(r"\bchapter\b", joined):
        return "chapter"
    if kind in {"book", "journal article", "general"} and re.search(r"\b(section|part)\b", joined):
        return "section"
    if kind == "magazine":
        if index_hint := _periodical_role(lines, joined, "magazine"):
            return index_hint
    if kind == "newspaper":
        if index_hint := _periodical_role(lines, joined, "newspaper"):
            return index_hint
    if kind == "manuscript":
        if index_hint := _manuscript_role(lines, joined):
            return index_hint
    if kind == "letter":
        if index_hint := _letter_role(lines, joined):
            return index_hint
    if kind == "archive bundle":
        if index_hint := _archive_role(lines, joined):
            return index_hint
    if kind == "legal document":
        if re.search(r"\b(parties?|agreement|contract|deed|memorandum|terms and conditions)\b", joined):
            return "title" if len(lines) <= 10 else "clauses"
        if re.search(r"\b(recitals?|whereas)\b", joined):
            return "recitals"
        if re.search(r"\b(definitions?|defined terms)\b", joined):
            return "definitions"
        if re.search(r"\b(schedule|annex|annexure|exhibit|appendix)\b", joined):
            return "schedule"
        if re.search(r"\b(signatures?|signed by|executed by)\b", joined):
            return "signature"
    if kind == "finance document":
        if re.search(r"\b(statement|balance sheet|profit and loss|income statement|ledger)\b", joined):
            return "statement"
        if re.search(r"\b(account|invoice|receipt|debit|credit|transaction)\b", joined):
            return "account"
        if re.search(r"\b(total|summary|subtotal|grand total)\b", joined):
            return "summary"
    if kind == "healthcare document":
        if re.search(r"\b(patient|hospital|clinic|report|diagnosis|prescription)\b", joined):
            return "title" if len(lines) <= 10 else "findings"
        if re.search(r"\b(medication|dose|dosage|treatment|instructions)\b", joined):
            return "medications"
    if kind == "insurance document":
        if re.search(r"\b(policy|coverage|claim|premium|exclusion|benefit)\b", joined):
            return "policy" if len(lines) <= 10 else "coverage"
    if kind in {"magazine", "newspaper"} and _has_column_spacing(lines):
        return "multi-column"
    if _table_rows(lines):
        return "table"
    if _chart_signals(lines):
        return "chart"
    if any("[image:" in line.lower() for line in lines):
        return "illustrated"
    return "body"


def _periodical_role(lines: List[str], joined: str, document_type: str) -> str:
    if not lines:
        return ""
    first = lines[0].strip()
    if document_type == "magazine":
        if re.search(r"\b(masthead|editor|publisher|volume|vol\.|issue|no\.)\b", joined):
            return "masthead"
        if re.search(r"\b(editorial|from the editor)\b", joined):
            return "editorial"
        if re.search(r"\b(feature|special report|cover story)\b", joined):
            return "feature"
        if re.search(r"\b(advertisement|advertiser|classified)\b", joined):
            return "advertisement"
        if re.search(r"\b(sidebar|box item)\b", joined):
            return "sidebar"
        if re.search(r"\b(caption|photo|illustration|plate)\b", joined):
            return "caption"
        if _has_column_spacing(lines):
            return "multi-column"
        if len(first) <= 80 and first == first.upper() and len(lines) <= 25:
            return "article"
    else:
        if re.search(r"\b(classifieds?|wanted|for sale|tenders?)\b", joined):
            return "classifieds"
        if re.search(r"\b(advertisement|advertiser)\b", joined):
            return "advertisement"
        if re.search(r"\b(continued from|continued on)\b", joined):
            return "continuation"
        if re.search(r"^[A-Z][A-Z .,'-]{6,}$", first):
            return "headline"
        if re.search(r"\b(by|from our correspondent|staff reporter)\b", "\n".join(lines[:8]).lower()):
            return "byline"
        if re.search(r"\b[A-Z][a-z]+,\s+[A-Z][a-z]+\.?\s+\d{1,2}\b", "\n".join(lines[:8])):
            return "dateline"
        if _has_column_spacing(lines):
            return "multi-column"
    return ""


def _manuscript_role(lines: List[str], joined: str) -> str:
    if not lines:
        return ""
    if re.search(r"\b(folio|fol\.|recto|verso)\b", joined) or re.fullmatch(r"(?:f\.?\s*)?\d+[rv]", lines[0], re.I):
        return "folio"
    if re.search(r"\b(marginalia|margin note|in margin)\b", joined):
        return "marginalia"
    if re.search(r"\b(correction|inserted|deleted|interlineation)\b", joined):
        return "correction"
    if re.search(r"\b(colophon|scribe|copied by|completed by)\b", joined):
        return "colophon"
    if joined.count("[unclear]") >= 3 or "[missing text]" in joined:
        return "damaged"
    if len(lines) >= 8 and all(len(line) <= 90 for line in lines[:12]):
        return "lineated-text"
    return ""


def _letter_role(lines: List[str], joined: str) -> str:
    if not lines:
        return ""
    first = lines[0].strip()
    if re.search(r"\b(dear|respected|sir|madam|my dear)\b", "\n".join(lines[:6]), re.I):
        return "salutation"
    if re.search(r"\b(yours faithfully|yours sincerely|sincerely|obediently|signed)\b", joined):
        return "signature"
    if re.search(r"\b(p\.?s\.?|postscript)\b", joined):
        return "postscript"
    if re.search(r"\b(to|from)\b.+\b(street|road|district|post|address)\b", joined):
        return "address"
    if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", first) or re.search(
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
        first,
        re.I,
    ):
        return "date-place"
    return ""


def _archive_role(lines: List[str], joined: str) -> str:
    if not lines:
        return ""
    if re.search(r"\b(file|folder|bundle|box)\s*(no\.?|number|id)?\b", joined):
        return "folder-label"
    if re.search(r"\b(id|identifier|reference|ref\.|case no\.|serial no\.)\b", joined):
        return "identifier"
    if re.search(r"\b(form|application|register|ledger)\b", joined):
        return "form"
    if re.search(r"\b(date|dated)\b", joined):
        return "date"
    if re.search(r"\b(item|document|record)\s+\d+\b", joined):
        return "item-boundary"
    return "record" if len(lines) <= 35 else ""


def _content_features(lines: List[str], role: str, document_type: str) -> List[str]:
    joined = "\n".join(lines[:100]).lower()
    features = []
    if _has_column_spacing(lines):
        features.append("multi_column")
    if any("[image:" in line.lower() for line in lines):
        features.append("figure_marker")
    if _contents_entries(lines):
        features.append("contents_entries")
    if _footnotes(lines):
        features.append("footnotes")
    if re.search(r"\b(by|from our correspondent|staff reporter)\b", joined):
        features.append("byline")
    if re.search(r"\b(advertisement|classifieds?|wanted|for sale)\b", joined):
        features.append("advertising")
    if re.search(r"\b(caption|photo|illustration|figure|plate|map)\b", joined):
        features.append("visual_reference")
    if re.search(r"\S\s{6,}\S", "\n".join(lines[:80])):
        features.append("table_or_columns")
    if _table_rows(lines):
        features.append("table_rows")
    if _chart_signals(lines):
        features.append("chart_candidate")
    if role in {"marginalia", "annotation", "correction", "folio"} or str(document_type).lower() == "manuscript":
        features.append("manuscript_layout")
    return _unique_limited(features, 12)


def _layout_class(lines: List[str], role: str, features: List[str]) -> str:
    if "table_rows" in features or role == "table":
        return "tabular"
    if "chart_candidate" in features or role == "chart":
        return "chart-led"
    if "multi_column" in features:
        return "multi-column"
    if role in {"contents", "references", "bibliography", "index", "classifieds"}:
        return "list-like"
    if role in {"folio", "marginalia", "lineated-text"}:
        return "lineated"
    if "table_or_columns" in features:
        return "tabular-or-columnar"
    return "single-flow"


def _has_column_spacing(lines: List[str]) -> bool:
    spaced = sum(1 for line in lines[:80] if re.search(r"\S\s{4,}\S", line))
    return spaced >= 2


def _table_rows(lines: List[str]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    if len(lines) < 2:
        return rows
    candidates: List[tuple[str, List[str]]] = []
    for line in lines[:120]:
        raw = line.strip()
        cleaned = re.sub(r"\s+", " ", raw)
        if len(cleaned) < 3 or len(cleaned) > 220:
            continue
        lowered = cleaned.lower()
        if lowered.startswith(("contents", "table of contents")):
            continue
        if _looks_like_body_line(cleaned):
            continue
        cells: List[str] = []
        if "|" in raw:
            cells = [part.strip() for part in raw.split("|") if part.strip()]
        elif re.search(r"\s{2,}", raw):
            cells = [part.strip() for part in re.split(r"\s{2,}", raw) if part.strip()]
        if len(cells) < 2:
            continue
        if sum(1 for cell in cells if re.search(r"\d", cell)) == 0 and len(cells) < 3:
            continue
        candidates.append((line.strip(), cells[:8]))
    if not candidates:
        return rows
    counts: Dict[int, int] = {}
    for _line, cells in candidates:
        counts[len(cells)] = counts.get(len(cells), 0) + 1
    best_column_count = max(counts, key=lambda key: (counts[key], key))
    for raw, cells in candidates:
        if len(cells) != best_column_count:
            continue
        rows.append(
            {
                "cells": cells,
                "cell_count": len(cells),
                "raw": raw,
            }
        )
    return rows[:24]


def _chart_signals(lines: List[str]) -> List[str]:
    joined = "\n".join(lines[:120]).lower()
    signals = []
    if re.search(r"\b(chart|graph|plot|diagram|figure|axis|legend|trend|bar|line chart|pie chart)\b", joined):
        signals.append("chart_terms")
    numeric_lines = 0
    for line in lines[:120]:
        cleaned = re.sub(r"\s+", " ", line.strip())
        if not cleaned:
            continue
        if re.search(r"\b(?:x|y)\s*[- ]?axis\b", cleaned, re.I):
            signals.append("axis")
        if re.search(r"\b\d+(?:\.\d+)?%?\b", cleaned):
            numeric_lines += 1
    if numeric_lines >= 4:
        signals.append("numeric_labels")
    if re.search(r"\b(legend|key|scale|rate|percentage|axis|graph|chart|plot)\b", joined):
        signals.append("chart_annotations")
    return _unique_limited(signals, 6)


def _role_label(role: str, document_type: str) -> str:
    kind = str(document_type or "").lower()
    roles = DOCUMENT_ROLE_GUIDANCE.get(kind, {}).get("roles", {})
    if isinstance(roles, dict):
        label = roles.get(role)
        if label:
            return str(label)
    return role.replace("-", " ")


def _assembly_hint(role: str, document_type: str) -> str:
    kind = str(document_type or "").lower()
    if role in {"title", "cover", "masthead", "cover-sheet", "folder-label"}:
        return "front_matter"
    if role in {"contents"}:
        return "toc"
    if role in {"preface", "abstract", "editorial"}:
        return "introductory"
    if role in {"chapter", "section", "article", "feature", "record", "letter", "body", "lineated-text"}:
        return "main_flow"
    if role in {"table", "chart", "figure-table"}:
        return "tabular"
    if role in {"index", "references", "bibliography", "footnotes"}:
        return "back_matter"
    if role in {"advertisement", "classifieds", "sidebar", "caption", "illustrated", "figure-table"}:
        return "supplementary"
    if kind == "archive bundle":
        return "archive_item"
    return "main_flow"


def _looks_like_title_page(lines: List[str], index: int) -> bool:
    if index > 2 or not lines or len(lines) > 18:
        return False
    if _table_rows(lines) or _chart_signals(lines):
        return False
    text_lines = [
        line for line in lines
        if not re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", line, flags=re.I)
    ]
    if not text_lines:
        return False
    longish = [line for line in text_lines if 4 <= len(line) <= 120]
    has_title_shape = any(line == line.upper() and len(line) >= 4 for line in longish)
    has_credit = any(re.search(r"\b(by|author|editor|translated by)\b", line, re.I) for line in text_lines)
    return bool(has_title_shape and (len(text_lines) <= 10 or has_credit))


def _assembly_profile(document_type: str, output_formats: List[str]) -> Dict[str, object]:
    kind = str(document_type or "General").lower()
    if kind == "book":
        layout = "book-like: title matter, contents, chapters, appendices, index when detected"
    elif kind in {"magazine", "newspaper"}:
        layout = "periodical-like: preserve article and column boundaries when detected"
    elif kind == "manuscript":
        layout = "manuscript-like: preserve folios, marginalia, uncertain readings, and lineation"
    elif kind == "legal document":
        layout = "legal-like: preserve parties, clauses, signatures, schedules, and numbered sections"
    elif kind == "finance document":
        layout = "finance-like: preserve tables, labels, totals, and numeric alignment"
    elif kind == "healthcare document":
        layout = "report-like: preserve findings, measurements, and ordered clinical sections"
    elif kind == "insurance document":
        layout = "policy-like: preserve coverage, exclusions, claim fields, and policy numbering"
    else:
        layout = "document-like: preserve detected headings, page markers, and item order"
    return {
        "layout": layout,
        "target_formats": list(output_formats),
        "uses_structured_sidecars": True,
        "section_order": [
            "title",
            "cover",
            "cover-sheet",
            "masthead",
            "contents",
            "preface",
            "foreword",
            "introduction",
            "chapter",
            "section",
            "article",
            "body",
            "appendix",
            "index",
            "references",
            "bibliography",
            "footnotes",
        ],
        "export_layout": {
            "txt": "plain text with clear section markers",
            "md": "publication-style markdown with headings and notes",
            "html": "structured reading view with reusable semantic classes",
            "docx": "reader-friendly word-processing layout",
            "epub": "book-style reading layout with embedded assets",
            "searchable-pdf": "text-first PDF with stable margins and reading order",
            "image-pdf": "compatibility alias for the same HTML-backed PDF layout",
        },
    }


def _detected_title(document_structure: Dict[str, object]) -> str:
    candidates = document_structure.get("title_candidates")
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        text = str(candidate).strip()
        lowered = text.lower()
        if 3 <= len(text) <= 120 and not re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", text, re.I):
            if "akshara" in lowered or "default" in lowered:
                continue
            if re.search(r"\b(run|output|restoration|export|workflow)\b", lowered) and len(text) < 40:
                continue
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
                        page_size=source.size,
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
    verification_limit = _figure_verification_limit(profile)
    for asset_index, asset in enumerate(assets, start=1):
        local_path = Path(str(asset.get("_local_path") or ""))
        asset.pop("_local_path", None)
        if (
            provider is None
            or profile is None
            or not local_path.exists()
            or asset_index > verification_limit
        ):
            asset["verification"] = "heuristic"
            if asset_index > verification_limit:
                asset["reason"] = "verification skipped by execution mode"
            verified_assets.append(asset)
            continue
        prompt = (
            "Return only JSON: {\"keep\": true|false, \"label\": \"short label\", \"reason\": \"short reason\"}.\n"
            "The attached crop was already pre-screened as a possible figure from a scanned archival page.\n"
            "Keep it only if you are confident it is a real non-text illustration, photograph, map, plate, seal, chart, "
            "diagram, or meaningful visual element.\n"
            "Reject it if it is mostly text, a page border, bleed-through, mirrored back-page impression, stain, crack, "
            "scanner noise, blank margin, or an accidental crop.\n"
            "If you are unsure, reject it.\n"
        )
        try:
            response, usage = _provider_restore_with_heartbeat(
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


def _figure_verification_limit(profile: Optional[WorkflowProfile]) -> int:
    mode = _execution_mode(profile)
    return {
        "fast": 0,
        "balanced": 1,
        "quality": MAX_FIGURE_CROPS_PER_PAGE,
    }.get(mode, 1)


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
    return {"keep": False, "label": "", "reason": "verification inconclusive"}


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

    scored_boxes: List[tuple[float, tuple[int, int, int, int]]] = []
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
        bbox = (left, top, right, bottom)
        score = _figure_box_score(gray, bbox, width, height)
        if score >= 0.34 and _looks_like_figure_box(gray, bbox, width, height):
            scored_boxes.append((score, bbox))
    boxes = [bbox for _score, bbox in sorted(scored_boxes, key=lambda item: (-item[0], item[1][1], item[1][0]))]
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


def _native_table_signals(gray, bbox: tuple[int, int, int, int], page_w: int, page_h: int) -> bool:
    left, top, right, bottom = bbox
    box_w = right - left
    box_h = bottom - top
    if box_w <= 0 or box_h <= 0:
        return False
    area_ratio = (box_w * box_h) / max(page_w * page_h, 1)
    if area_ratio < 0.03 or area_ratio > 0.70:
        return False
    if box_w < page_w * 0.25 or box_h < page_h * 0.08:
        return False
    crop = gray.crop(bbox)
    histogram = crop.histogram()
    dark_ratio = sum(histogram[:96]) / max(box_w * box_h, 1)
    mid_ratio = sum(histogram[96:160]) / max(box_w * box_h, 1)
    if dark_ratio < 0.04 or dark_ratio > 0.72:
        return False
    return mid_ratio >= 0.12 or box_w / max(box_h, 1) >= 1.15


def _native_chart_signals(gray, bbox: tuple[int, int, int, int], page_w: int, page_h: int) -> List[str]:
    left, top, right, bottom = bbox
    box_w = right - left
    box_h = bottom - top
    if box_w <= 0 or box_h <= 0:
        return []
    area_ratio = (box_w * box_h) / max(page_w * page_h, 1)
    if area_ratio < 0.02 or area_ratio > 0.75:
        return []
    crop = gray.crop(bbox)
    histogram = crop.histogram()
    dark_ratio = sum(histogram[:96]) / max(box_w * box_h, 1)
    signals = []
    if box_w > page_w * 0.32 and box_h > page_h * 0.18 and 0.07 <= dark_ratio <= 0.60:
        signals.append("chart-region-shape")
    if box_h > box_w * 0.72 and 0.08 <= dark_ratio <= 0.55:
        signals.append("chart-axis-shape")
    if sum(histogram[96:160]) / max(box_w * box_h, 1) >= 0.12:
        signals.append("mixed-text-and-graphics")
    return _unique_limited(signals, 4)


def _figure_box_score(gray, bbox: tuple[int, int, int, int], page_w: int, page_h: int) -> float:
    left, top, right, bottom = bbox
    box_w = right - left
    box_h = bottom - top
    if box_w <= 0 or box_h <= 0:
        return 0.0
    area_ratio = (box_w * box_h) / max(page_w * page_h, 1)
    crop = gray.crop(bbox)
    histogram = crop.histogram()
    dark_ratio = sum(histogram[:96]) / max(box_w * box_h, 1)
    mid_ratio = sum(histogram[96:192]) / max(box_w * box_h, 1)
    aspect_ratio = box_w / max(box_h, 1)
    score = 0.0
    if 0.04 <= area_ratio <= 0.42:
        score += 0.30
    elif 0.025 <= area_ratio <= 0.55:
        score += 0.18
    if 0.10 <= dark_ratio <= 0.55:
        score += 0.25
    elif 0.06 <= dark_ratio <= 0.70:
        score += 0.12
    if mid_ratio >= 0.12:
        score += 0.08
    if 0.45 <= aspect_ratio <= 2.8:
        score += 0.12
    if box_w >= page_w * 0.18 and box_h >= page_h * 0.12:
        score += 0.15
    return min(score, 1.0)


def _dedupe_boxes(boxes: List[tuple[int, int, int, int]]) -> List[tuple[int, int, int, int]]:
    kept = []
    for box in sorted(boxes, key=lambda item: (item[1], item[0], -(item[2] - item[0]) * (item[3] - item[1]))):
        if any(_box_overlap_ratio(box, existing) > 0.55 for existing in kept):
            continue
        kept.append(box)
    return kept


def register_layout_backend(name: str, backend: LayoutBackend) -> None:
    normalized = str(name or "").strip().lower().replace("_", "-")
    if not normalized:
        raise ValueError("layout backend name is required")
    _LAYOUT_BACKENDS[normalized] = backend


def available_layout_backends() -> List[str]:
    return ["native", "off"] + sorted(
        name for name in _LAYOUT_BACKENDS if name not in {"native", "off"}
    )


def _page_layout(image_path: Path, profile: Optional[WorkflowProfile] = None) -> Dict[str, object]:
    backend_name = str(getattr(profile, "layout_backend", "native") or "native").strip().lower()
    if backend_name in {"", "default"}:
        backend_name = "native"
    if backend_name in {"off", "none", "disabled"}:
        return {}
    if backend_name in {"native", "auto"}:
        preferred = _preferred_layout_backend()
        if preferred and preferred not in {"native", "off"}:
            backend_name = preferred
    backend = _LAYOUT_BACKENDS.get(backend_name) or _LAYOUT_BACKENDS.get("native")
    if backend is None:
        return _native_page_layout(image_path)
    try:
        layout = backend(image_path)
    except Exception:
        if backend_name == "native":
            return {}
        layout = _native_page_layout(image_path)
    return layout if isinstance(layout, dict) else {}


def _preferred_layout_backend() -> str:
    preferred_order = ("doctr", "layoutparser", "paddleocr", "native")
    for name in preferred_order:
        if name in _LAYOUT_BACKENDS:
            return name
    return "native"


def _native_page_layout(image_path: Path) -> Dict[str, object]:
    if Image is None or ImageOps is None:
        return {}
    try:
        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            return _analyze_native_layout(image)
    except Exception:
        return {}


def _analyze_native_layout(image) -> Dict[str, object]:
    width, height = image.size
    if width <= 0 or height <= 0:
        return {}
    gray = ImageOps.grayscale(image)
    components = _native_layout_components(gray, width, height)
    blocks = [_native_layout_block(gray, bbox, width, height, order) for order, bbox in enumerate(components, start=1)]
    blocks = [block for block in blocks if block]
    content_bbox = _merge_bboxes([tuple(block["bbox"]) for block in blocks if block.get("bbox")])
    column_count = _estimate_native_columns(blocks)
    flow = _native_flow(blocks, column_count)
    return {
        "engine": "akshara-native-heuristic",
        "page_width": width,
        "page_height": height,
        "content_bbox": list(content_bbox) if content_bbox else None,
        "relative_content_bbox": _relative_bbox(content_bbox, width, height) if content_bbox else None,
        "column_count_estimate": column_count,
        "dominant_flow": flow,
        "block_count": len(blocks),
        "blocks": blocks[:48],
    }


def _native_layout_components(gray, page_w: int, page_h: int) -> List[tuple[int, int, int, int]]:
    grid_x = 56
    grid_y = 72
    cell_w = max(page_w // grid_x, 1)
    cell_h = max(page_h // grid_y, 1)
    active = set()
    for gy in range(grid_y):
        for gx in range(grid_x):
            left = gx * cell_w
            top = gy * cell_h
            right = page_w if gx == grid_x - 1 else min((gx + 1) * cell_w, page_w)
            bottom = page_h if gy == grid_y - 1 else min((gy + 1) * cell_h, page_h)
            if right <= left or bottom <= top:
                continue
            crop = gray.crop((left, top, right, bottom))
            histogram = crop.histogram()
            dark_ratio = sum(histogram[:104]) / max((right - left) * (bottom - top), 1)
            if 0.025 <= dark_ratio <= 0.88:
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
        right = min((max_x + 2) * cell_w, page_w)
        bottom = min((max_y + 2) * cell_h, page_h)
        if _native_component_is_meaningful((left, top, right, bottom), page_w, page_h):
            boxes.append((left, top, right, bottom))
    return _dedupe_boxes(boxes)


def _native_component_is_meaningful(
    bbox: tuple[int, int, int, int], page_w: int, page_h: int
) -> bool:
    left, top, right, bottom = bbox
    box_w = right - left
    box_h = bottom - top
    if box_w < page_w * 0.025 or box_h < page_h * 0.012:
        return False
    area_ratio = (box_w * box_h) / max(page_w * page_h, 1)
    if area_ratio < 0.0008 or area_ratio > 0.92:
        return False
    return True


def _native_layout_block(
    gray,
    bbox: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
    order: int,
) -> Dict[str, object]:
    left, top, right, bottom = bbox
    box_w = right - left
    box_h = bottom - top
    crop = gray.crop(bbox)
    histogram = crop.histogram()
    dark_ratio = sum(histogram[:104]) / max(box_w * box_h, 1)
    chart_signals = _native_chart_signals(gray, bbox, page_w, page_h)
    table_signals = _native_table_signals(gray, bbox, page_w, page_h)
    area_ratio = round((box_w * box_h) / max(page_w * page_h, 1), 4)
    width_ratio = box_w / max(page_w, 1)
    height_ratio = box_h / max(page_h, 1)
    role = "text-region"
    if _looks_like_figure_box(gray, bbox, page_w, page_h):
        role = "chart-region" if chart_signals else "figure-region"
    elif width_ratio > 0.65 and height_ratio < 0.08:
        role = "running-header-or-footer"
    elif area_ratio < 0.01 and dark_ratio > 0.18:
        role = "small-mark-or-page-number"
    elif table_signals:
        role = "table-region"
    confidence = _native_block_confidence(role, area_ratio, dark_ratio, width_ratio, height_ratio)
    return {
        "order": order,
        "role": role,
        "bbox": [left, top, right, bottom],
        "relative_bbox": _relative_bbox(bbox, page_w, page_h),
        "page_zone": _page_zone(((left + right) / 2) / page_w, ((top + bottom) / 2) / page_h),
        "area_ratio": area_ratio,
        "dark_ratio": round(dark_ratio, 4),
        "chart_signals": chart_signals,
        "table_signals": table_signals,
        "confidence": confidence,
    }


def _native_block_confidence(
    role: str, area_ratio: float, dark_ratio: float, width_ratio: float, height_ratio: float
) -> float:
    confidence = 0.52
    if role == "text-region":
        confidence += 0.18
        if 0.02 <= area_ratio <= 0.45:
            confidence += 0.08
        if 0.03 <= dark_ratio <= 0.40:
            confidence += 0.08
    elif role == "figure-region":
        confidence += 0.12
        if 0.04 <= area_ratio <= 0.55:
            confidence += 0.10
        if 0.08 <= dark_ratio <= 0.62:
            confidence += 0.06
    elif role in {"chart-region", "table-region"}:
        confidence += 0.13
        if 0.03 <= area_ratio <= 0.65:
            confidence += 0.09
        if 0.06 <= dark_ratio <= 0.68:
            confidence += 0.05
    elif role == "running-header-or-footer":
        confidence += 0.08
        if width_ratio > 0.55 and height_ratio < 0.10:
            confidence += 0.10
    else:
        confidence -= 0.05
    if area_ratio < 0.003 or dark_ratio < 0.015:
        confidence -= 0.18
    if area_ratio > 0.82 or dark_ratio > 0.78:
        confidence -= 0.14
    return round(max(0.05, min(confidence, 0.98)), 2)


def _merge_bboxes(boxes: List[tuple[int, int, int, int]]) -> Optional[tuple[int, int, int, int]]:
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _relative_bbox(
    bbox: tuple[int, int, int, int], page_w: int, page_h: int
) -> List[float]:
    left, top, right, bottom = bbox
    return [
        round(left / max(page_w, 1), 4),
        round(top / max(page_h, 1), 4),
        round(right / max(page_w, 1), 4),
        round(bottom / max(page_h, 1), 4),
    ]


def _estimate_native_columns(blocks: List[Dict[str, object]]) -> int:
    text_blocks = [
        block for block in blocks
        if block.get("role") in {"text-region", "running-header-or-footer"}
        and isinstance(block.get("relative_bbox"), list)
    ]
    if len(text_blocks) < 4:
        return 1
    centers = sorted((float(block["relative_bbox"][0]) + float(block["relative_bbox"][2])) / 2 for block in text_blocks)
    left = sum(1 for center in centers if center < 0.42)
    middle = sum(1 for center in centers if 0.42 <= center <= 0.58)
    right = sum(1 for center in centers if center > 0.58)
    if left >= 2 and right >= 2 and middle <= max(left, right):
        return 2
    third_left = sum(1 for center in centers if center < 0.34)
    third_mid = sum(1 for center in centers if 0.34 <= center <= 0.66)
    third_right = sum(1 for center in centers if center > 0.66)
    if min(third_left, third_mid, third_right) >= 2:
        return 3
    return 1


def _native_flow(blocks: List[Dict[str, object]], column_count: int) -> str:
    if column_count > 1:
        return "multi-column"
    figure_count = sum(1 for block in blocks if block.get("role") == "figure-region")
    text_count = sum(1 for block in blocks if block.get("role") == "text-region")
    if figure_count and figure_count >= text_count:
        return "figure-led"
    if text_count >= 12:
        return "dense-prose"
    return "single-flow"


def _doctr_page_layout(image_path: Path) -> Dict[str, object]:
    from doctr.io import DocumentFile  # type: ignore
    from doctr.models import ocr_predictor  # type: ignore

    predictor = _LAYOUT_MODEL_CACHE.get("doctr")
    if predictor is None:
        predictor = ocr_predictor(pretrained=True)
        _LAYOUT_MODEL_CACHE["doctr"] = predictor
    document = DocumentFile.from_images(str(image_path))
    result = predictor(document)
    page = result.pages[0] if getattr(result, "pages", None) else None
    if page is None:
        return _native_page_layout(image_path)
    width, height = _image_size(image_path)
    blocks = []
    for order, block in enumerate(getattr(page, "blocks", []) or [], start=1):
        bbox = _doctr_geometry_to_bbox(getattr(block, "geometry", None), width, height)
        if bbox is None:
            continue
        blocks.append(
            _external_layout_block(
                bbox,
                width,
                height,
                order,
                role="text-region",
                confidence=_average_doctr_confidence(block),
            )
        )
    return _external_layout_payload("doctr", width, height, blocks)


def _paddleocr_page_layout(image_path: Path) -> Dict[str, object]:
    from paddleocr import PPStructure  # type: ignore

    structure = _LAYOUT_MODEL_CACHE.get("paddleocr")
    if structure is None:
        structure = PPStructure(show_log=False)
        _LAYOUT_MODEL_CACHE["paddleocr"] = structure
    result = structure(str(image_path))
    width, height = _image_size(image_path)
    blocks = []
    for order, item in enumerate(result or [], start=1):
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        role = _external_role(item.get("type") or item.get("label"))
        confidence = float(item.get("score") or item.get("confidence") or 0.72)
        blocks.append(_external_layout_block(tuple(map(int, bbox)), width, height, order, role, confidence))
    return _external_layout_payload("paddleocr", width, height, blocks)


def _layoutparser_page_layout(image_path: Path) -> Dict[str, object]:
    import layoutparser as lp  # type: ignore

    config = os.environ.get("AKSHARA_LAYOUTPARSER_CONFIG", "").strip()
    if not config:
        layout = _native_page_layout(image_path)
        if layout:
            layout["engine"] = "layoutparser-not-configured-native-fallback"
        return layout
    model = _LAYOUT_MODEL_CACHE.get(f"layoutparser:{config}")
    if model is None:
        model = lp.Detectron2LayoutModel(config)
        _LAYOUT_MODEL_CACHE[f"layoutparser:{config}"] = model
    if Image is None:
        return {}
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    result = model.detect(image)
    blocks = []
    for order, item in enumerate(result or [], start=1):
        coords = getattr(getattr(item, "block", None), "coordinates", None)
        if not coords or len(coords) != 4:
            continue
        role = _external_role(getattr(item, "type", None))
        confidence = float(getattr(item, "score", 0.72) or 0.72)
        blocks.append(_external_layout_block(tuple(map(int, coords)), width, height, order, role, confidence))
    return _external_layout_payload("layoutparser", width, height, blocks)


def _image_size(image_path: Path) -> tuple[int, int]:
    if Image is None:
        return (1, 1)
    with Image.open(image_path) as opened:
        return opened.size


def _doctr_geometry_to_bbox(geometry, width: int, height: int) -> Optional[tuple[int, int, int, int]]:
    if not geometry or len(geometry) != 2:
        return None
    try:
        (x0, y0), (x1, y1) = geometry
        return (int(float(x0) * width), int(float(y0) * height), int(float(x1) * width), int(float(y1) * height))
    except (TypeError, ValueError):
        return None


def _average_doctr_confidence(block) -> float:
    confidences = []
    for line in getattr(block, "lines", []) or []:
        for word in getattr(line, "words", []) or []:
            value = getattr(word, "confidence", None)
            if value is not None:
                try:
                    confidences.append(float(value))
                except (TypeError, ValueError):
                    pass
    if not confidences:
        return 0.74
    return round(sum(confidences) / len(confidences), 2)


def _external_role(label: object) -> str:
    text = str(label or "").lower()
    if any(term in text for term in ("chart", "graph", "plot", "axis", "legend")):
        return "chart-region"
    if any(term in text for term in ("figure", "image", "table", "formula")):
        return "figure-region" if "table" not in text else "table-region"
    if any(term in text for term in ("header", "footer")):
        return "running-header-or-footer"
    if "title" in text:
        return "title-region"
    return "text-region"


def _external_layout_block(
    bbox: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
    order: int,
    role: str,
    confidence: float,
) -> Dict[str, object]:
    left, top, right, bottom = bbox
    return {
        "order": order,
        "role": role,
        "bbox": [left, top, right, bottom],
        "relative_bbox": _relative_bbox((left, top, right, bottom), page_w, page_h),
        "page_zone": _page_zone(((left + right) / 2) / max(page_w, 1), ((top + bottom) / 2) / max(page_h, 1)),
        "area_ratio": round(((right - left) * (bottom - top)) / max(page_w * page_h, 1), 4),
        "confidence": round(max(0.05, min(float(confidence), 0.99)), 2),
    }


def _external_layout_payload(
    engine: str, width: int, height: int, blocks: List[Dict[str, object]]
) -> Dict[str, object]:
    content_bbox = _merge_bboxes([tuple(block["bbox"]) for block in blocks if block.get("bbox")])
    column_count = _estimate_native_columns(blocks)
    return {
        "engine": engine,
        "page_width": width,
        "page_height": height,
        "content_bbox": list(content_bbox) if content_bbox else None,
        "relative_content_bbox": _relative_bbox(content_bbox, width, height) if content_bbox else None,
        "column_count_estimate": column_count,
        "dominant_flow": _native_flow(blocks, column_count),
        "block_count": len(blocks),
        "blocks": blocks[:96],
    }


def _register_optional_layout_backends() -> None:
    optional = {
        "doctr": ("doctr", _doctr_page_layout),
        "paddleocr": ("paddleocr", _paddleocr_page_layout),
        "layoutparser": ("layoutparser", _layoutparser_page_layout),
    }
    for name, (module_name, backend) in optional.items():
        if importlib.util.find_spec(module_name) is not None:
            register_layout_backend(name, backend)


register_layout_backend("native", _native_page_layout)
_register_optional_layout_backends()


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


def _asset_layout_metadata(
    bbox: Optional[tuple[int, int, int, int]],
    page_size: Optional[tuple[int, int]],
    width: Optional[int],
    height: Optional[int],
) -> Dict[str, object]:
    aspect_ratio = round(width / height, 4) if width and height else None
    layout: Dict[str, object] = {
        "size_class": _asset_size_class(width, height, page_size, bbox),
        "aspect_ratio": aspect_ratio,
        "page_zone": "unknown",
        "relative_bbox": None,
        "assembly": {
            "anchor": "after-nearest-paragraph",
            "flow": "block",
            "preserve_aspect_ratio": True,
        },
    }
    if not bbox or not page_size:
        return layout

    page_w, page_h = page_size
    if page_w <= 0 or page_h <= 0:
        return layout
    left, top, right, bottom = bbox
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    relative_bbox = [
        round(left / page_w, 4),
        round(top / page_h, 4),
        round(right / page_w, 4),
        round(bottom / page_h, 4),
    ]
    page_zone = _page_zone(center_x / page_w, center_y / page_h)
    area_ratio = round(((right - left) * (bottom - top)) / max(page_w * page_h, 1), 4)
    layout.update(
        {
            "page_width": page_w,
            "page_height": page_h,
            "page_zone": page_zone,
            "relative_bbox": relative_bbox,
            "area_ratio": area_ratio,
            "size_class": _asset_size_class(width, height, page_size, bbox),
            "assembly": {
                "anchor": "after-nearest-paragraph",
                "flow": "block",
                "page_zone": page_zone,
                "size_class": _asset_size_class(width, height, page_size, bbox),
                "preserve_aspect_ratio": True,
            },
        }
    )
    return layout


def _asset_size_class(
    width: Optional[int],
    height: Optional[int],
    page_size: Optional[tuple[int, int]],
    bbox: Optional[tuple[int, int, int, int]],
) -> str:
    if bbox and page_size:
        page_w, page_h = page_size
        left, top, right, bottom = bbox
        area_ratio = ((right - left) * (bottom - top)) / max(page_w * page_h, 1)
        width_ratio = (right - left) / max(page_w, 1)
        if area_ratio >= 0.38 or width_ratio >= 0.78:
            return "full-width"
        if area_ratio >= 0.14 or width_ratio >= 0.48:
            return "large"
        if area_ratio >= 0.055:
            return "medium"
        return "small"
    if not width or not height:
        return "unknown"
    if width > height * 1.4:
        return "wide"
    if height > width * 1.4:
        return "tall"
    return "medium"


def _page_zone(x_ratio: float, y_ratio: float) -> str:
    horizontal = "left" if x_ratio < 0.33 else "right" if x_ratio > 0.67 else "center"
    vertical = "top" if y_ratio < 0.33 else "bottom" if y_ratio > 0.67 else "middle"
    return f"{vertical}-{horizontal}"


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


def _frequent_limited(values: List[object], limit: int) -> List[str]:
    counts: Dict[str, int] = {}
    originals: Dict[str, str] = {}
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.lower()
        counts[key] = counts.get(key, 0) + 1
        originals.setdefault(key, text)
    ordered = sorted(counts, key=lambda key: (-counts[key], originals[key].lower()))
    return [originals[key] for key in ordered[:limit] if counts[key] > 1]


def _layout_confidence(
    text: str, semantic: Dict[str, object], assets: object = None
) -> float:
    confidence = 0.48
    role = str(semantic.get("role") or "body")
    if role in {"title", "contents", "chapter", "section", "preface", "abstract"}:
        confidence += 0.18
    if role in {"marginalia", "damaged"}:
        confidence -= 0.08
    if text:
        confidence += min(len(text) / 1800.0, 0.2)
    if _is_blank_or_missing_text(text):
        confidence -= 0.22
    if "[unclear]" in text.lower() or "[missing text]" in text.lower():
        confidence -= 0.12
    if isinstance(assets, list) and assets:
        confidence += 0.06
    if semantic.get("page_marker"):
        confidence += 0.03
    return round(max(0.05, min(confidence, 0.99)), 2)


def _layout_page_profile(observation: Dict[str, object]) -> Dict[str, object]:
    features = [str(item) for item in observation.get("content_features") or []]
    role = str(observation.get("content_kind") or "body")
    layout = str(observation.get("layout") or "single-flow")
    native_layout = (
        observation.get("native_layout")
        if isinstance(observation.get("native_layout"), dict)
        else {}
    )
    line_count = int(observation.get("line_count") or 0)
    avg_line_length = float(observation.get("avg_line_length") or 0.0)
    if "multi_column" in features or layout == "multi-column":
        column_count = 2
        dominant_flow = "multi-column"
    elif role in {"table", "chart"} or "table_rows" in features:
        column_count = 1 if role == "chart" else 2
        dominant_flow = "chart-led" if role == "chart" else "tabular"
    elif "table_or_columns" in features:
        column_count = 2 if avg_line_length < 70 else 3
        dominant_flow = "tabular"
    elif role in {"contents", "references", "bibliography", "index"}:
        column_count = 1
        dominant_flow = "structured-list"
    else:
        column_count = 1
        dominant_flow = "single-flow"
    if line_count >= 65 and avg_line_length < 62 and dominant_flow == "single-flow":
        dominant_flow = "dense-prose"
    if role in {"title", "cover", "cover-sheet"}:
        dominant_flow = "front-matter"
    native_flow = str(native_layout.get("dominant_flow") or "").strip()
    try:
        native_columns = int(native_layout.get("column_count_estimate") or 0)
    except (TypeError, ValueError):
        native_columns = 0
    if native_columns > column_count:
        column_count = native_columns
    if (
        native_flow in {"multi-column", "figure-led", "dense-prose"}
        and dominant_flow in {"single-flow", "dense-prose"}
    ):
        dominant_flow = native_flow
    return {
        "dominant_flow": dominant_flow,
        "column_count_estimate": column_count,
        "native_flow": native_flow,
        "native_block_count": int(native_layout.get("block_count") or 0),
        "line_count": line_count,
        "average_line_length": avg_line_length,
        "has_running_header": bool(observation.get("running_header")),
        "has_page_marker": bool(observation.get("page_marker")),
        "has_figure_marker": bool(observation.get("has_figure_marker")),
        "has_footnotes": bool(observation.get("footnotes")),
        "has_contents_entries": bool(observation.get("contents_entries")),
    }


def _layout_profile(pages: List[Dict[str, object]], document_type: str) -> Dict[str, object]:
    if not pages:
        return {
            "document_type": document_type,
            "dominant_flow": "single-flow",
            "column_count_estimate": 1,
            "page_profiles": [],
        }
    flow_counts: Dict[str, int] = {}
    max_columns = 1
    page_profiles = []
    for page in pages:
        profile = dict(page)
        page_profiles.append(profile)
        flow = str(profile.get("dominant_flow") or "single-flow")
        flow_counts[flow] = flow_counts.get(flow, 0) + 1
        try:
            max_columns = max(max_columns, int(profile.get("column_count_estimate") or 1))
        except (TypeError, ValueError):
            pass
    dominant_flow = max(flow_counts, key=lambda key: (flow_counts[key], key)) if flow_counts else "single-flow"
    return {
        "document_type": document_type,
        "dominant_flow": dominant_flow,
        "column_count_estimate": max_columns,
        "page_flow_counts": flow_counts,
        "page_profiles": page_profiles[:120],
        "notes": _layout_profile_notes(dominant_flow, max_columns, flow_counts),
    }


def _layout_profile_notes(dominant_flow: str, column_count: int, flow_counts: Dict[str, int]) -> List[str]:
    notes = []
    if column_count > 1:
        notes.append(f"Likely {column_count}-column page structure on some pages.")
    if flow_counts.get("front-matter", 0):
        notes.append("Front matter detected on at least one page.")
    if flow_counts.get("structured-list", 0):
        notes.append("List-like pages such as contents or references detected.")
    if flow_counts.get("tabular", 0):
        notes.append("Tabular pages detected and should keep row/cell order.")
    if flow_counts.get("chart-led", 0):
        notes.append("Chart-like pages detected and should keep labels and numeric signals.")
    if dominant_flow == "dense-prose":
        notes.append("Pages are text-dense and should stay paragraph-oriented.")
    return notes[:6]


def _publication_fallback_title(profile: WorkflowProfile, inputs: List[Path]) -> str:
    if inputs:
        first = Path(inputs[0]).stem.replace("_", " ").replace("-", " ").strip()
        if first:
            return first.title()
    name = str(profile.name or "").replace("_", " ").replace("-", " ").strip()
    return name.title() if name else "Untitled"


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
        "encountered_structures": state.get("encountered_structures") or [],
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
    structures = list(state.get("encountered_structures") or [])
    for structure in _detect_structures(sample):
        if structure not in structures:
            structures.append(structure)
    state["encountered_structures"] = structures[:8]
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


def _detect_structures(text: str) -> List[str]:
    structures = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if _table_rows(lines):
        structures.append("table-like")
    if _chart_signals(lines):
        structures.append("chart-like")
    if _has_column_spacing(lines):
        structures.append("multi-column")
    if _contents_entries(lines):
        structures.append("contents-like")
    return structures


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
            "Extract carefully in one bounded pass: scan the page region by region, including "
            "margins, footnotes, headings, captions, columns, and small text. Make decisions quickly. "
            "If a word cannot be read confidently, keep the best visible reading or mark [unclear] "
            "and continue.\n"
        )
    elif execution_mode == "balanced":
        depth_instruction = (
            "Extract the whole page in one careful pass. Work top-to-bottom and left-to-right "
            "unless the page layout clearly uses another reading order. Do not stall on damaged words.\n"
        )
    else:  # fast
        depth_instruction = (
            "Extract quickly while preserving every clearly visible line. Do not pause on damaged "
            "words; keep the best visible reading or mark [unclear] and continue.\n"
        )

    if not raw_text:
        # Multimodal vision prompt — keep it simple and direct.
        return (
            context
            + "Look at the attached image carefully.\n"
            + depth_instruction
            + "Use only visible front-side text. Never use mirrored bleed-through, shadows, texture, "
            "stains, page cracks, or back-side impressions as source text.\n"
            + "Restoration stage only: extract ALL text visible in the image exactly as written.\n"
            "Preserve original language, script, spelling, page order, headings, tables, footnotes, "
            "page markers, captions, and meaningful layout. For normal body prose, join artificial "
            "scan line wraps into readable paragraphs and keep paragraph breaks. Preserve exact line "
            "breaks only for verse, tables, contents pages, manuscript lineation, addresses, captions, "
            "and other places where lineation carries meaning.\n"
            "If the page contains a table or chart, keep rows, cells, labels, axis text, and legend text "
            "together in reading order; do not collapse them into prose or invent missing cells.\n"
            "Do not skip non-English, Indic, Sanskrit, Kannada, Hindi, Tamil, Telugu, Malayalam, "
            "Bengali, Marathi, Urdu, or mixed-script text.\n"
            "Apply the language policy above exactly. Preserve clear mixed-language snippets only when "
            "they are visible and readable; never force a language guess into the output.\n"
            "If the page is dense, prioritize complete extraction over perfect cleanup.\n"
            "Ignore mirrored, reversed, faint, or bleed-through impressions from the back side of "
            "the page. Do not restore shadows, show-through, stains, cracks, or scan noise as text.\n"
            "If any words are unclear, mark them as [unclear]. If the whole page is blank or "
            "unreadable, return an empty response, not [unclear].\n"
            "Do not perform an extended self-review or reasoning loop. Return the extraction promptly; "
            "a separate repair pass will run only if the result appears corrupted. "
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
        "Keep the pass bounded. Fix clear OCR corruption, but do not run an extended review loop.\n"
        "If the chunk is clearly a table, preserve rows and cells instead of rewriting into prose. "
        "If it is a chart or graph, preserve visible labels, tick marks, captions, and legend text in order; "
        "do not invent numeric values.\n"
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
    native_layout = _page_layout(path, profile)
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
                "native_layout": native_layout,
                "media_path": str(path),
                **({"pre_review_text": pre_review_text} if pre_review_text else {}),
            }
        ],
        "failure_reason": failure_reason,
    }
    artifacts.write_record_piece(source_index, label, 1, record["chunks"][0], profile.document_type)
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
        "Preserve [image: ... | path] markers exactly as written; do not translate or alter asset paths.\n"
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
            "Preserve any [image: ... | path] marker exactly.\n"
            "SOURCE TEXT\n"
            f"{chunk}"
        )
    return (
        "Return a valid JSON object with keys translated_text and notes.\n"
        "translated_text must contain only the translated text.\n"
        "notes must be a short string or an empty string.\n"
        "Do not add markdown fences or commentary.\n"
        "Preserve any [image: ... | path] marker exactly.\n"
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
    mode = _execution_mode(profile)
    if not _needs_restoration_review(restored_text, mode, consistency_state):
        return restored_text, "", {}, ""
    if consistency_state is not None:
        try:
            consistency_state["review_count"] = int(consistency_state.get("review_count") or 0) + 1
        except (TypeError, ValueError):
            consistency_state["review_count"] = 1
    _notify(progress, "review", f"Reviewing suspicious restoration for {label}", advance=0)
    prompt = _restoration_review_prompt(restored_text, profile, consistency_state)
    try:
        review_settings = _bounded_model_settings(
            profile.model,
            context_window=8192,
            generation_limit=4096,
        )
        result, usage = _restore_with_retry(
            provider, prompt, instruction, review_settings, progress=progress
        )
    except Exception:
        return restored_text, "quality review failed; kept original restored text", {}, ""
    parsed = _parse_restoration_result(result, restored_text)
    reviewed = str(parsed.get("restored_text") or "").strip()
    if not reviewed:
        return restored_text, "quality review returned no usable correction", usage or {}, ""
    return reviewed, "quality review applied to suspicious restoration", usage or {}, restored_text


def _bounded_model_settings(settings, context_window: int, generation_limit: int):
    try:
        bounded = replace(settings)
    except TypeError:
        return settings
    current_context = getattr(bounded, "context_window", None)
    current_generation = getattr(bounded, "generation_limit", None)
    bounded.context_window = min(int(current_context or context_window), context_window)
    bounded.generation_limit = min(int(current_generation or generation_limit), generation_limit)
    return bounded


def _needs_restoration_review(
    text: str, execution_mode: str = "balanced", consistency_state: Optional[Dict[str, object]] = None
) -> bool:
    candidate = text.strip()
    if not candidate or _is_blank_or_missing_text(candidate):
        return False
    if consistency_state is not None:
        try:
            review_count = int(consistency_state.get("review_count") or 0)
        except (TypeError, ValueError):
            review_count = 0
        if review_count >= 6:
            return False
    if _extract_json_object(candidate):
        return True
    if "\ufffd" in candidate:
        return True
    if re.search(r"([A-Za-z])\1{5,}", candidate):
        return True
    if execution_mode in {"fast", "balanced"}:
        words = re.findall(r"[A-Za-z]{3,}", candidate)
        if len(words) >= 18:
            vowel_words = sum(1 for word in words if re.search(r"[aeiouAEIOU]", word))
            if vowel_words / len(words) < 0.28:
                return True
        if len(candidate) > 180:
            symbol_count = sum(1 for char in candidate if not char.isalnum() and not char.isspace())
            if symbol_count / len(candidate) > 0.48:
                return True
        return False
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

        source_translated_text = "\n\n\f\n\n".join(
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
        translated_parts.append(source_final_text.strip())

    translated_text = "\n\n\f\n\n".join(part for part in translated_parts if part.strip()).strip()
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
            native_layout = _page_layout(page_img, profile)
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
                "native_layout": native_layout,
                "media_path": str(page_img),
            }
            if pre_review_text:
                chunk_record["pre_review_text"] = pre_review_text
            chunks_record.append(chunk_record)
            artifacts.write_record_piece(source_index, label, idx, chunk_record, profile.document_type)
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
                            artifacts.write_record_piece(source_index, label, idx, chunk, profile.document_type)
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

        combined = "\n\n\f\n\n".join(restored_pages) + "\n"
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
                    artifacts.write_record_piece(
                        source_index, label, chunk_idx, chunk_record, profile.document_type
                    )
                    chunk_idx += 1
                archive_file_text = "\n\n\f\n\n".join(part for part in archive_file_parts if part.strip())
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
                            "media_path": str(ext_file),
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
                native_layout = _page_layout(ext_file, profile)
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
                    "native_layout": native_layout,
                    "media_path": str(ext_file),
                }
                if pre_review_text:
                    chunk_record["pre_review_text"] = pre_review_text
                chunks_record.append(chunk_record)
                artifacts.write_record_piece(
                    source_index, label, chunk_idx, chunk_record, profile.document_type
                )
                chunk_idx += 1

        for folder_label, parts in sorted(archive_folder_parts.items()):
            folder_text = "\n\n\f\n\n".join(part for part in parts if part.strip()).strip()
            artifacts.write_archive_folder_combined(
                source_index, label, folder_label, folder_text + "\n"
            )

        combined = "\n\n\f\n\n".join(restored_parts) + "\n"
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
