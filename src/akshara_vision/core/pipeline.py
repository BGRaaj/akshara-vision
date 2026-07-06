import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from akshara_vision.core.models import RunRequest, WorkflowProfile
from akshara_vision.exporters.base import ExportResult
from akshara_vision.instructions import load_instruction
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import get_provider


TEXT_EXTENSIONS = {".txt", ".md", ".html", ".hocr", ".xml", ".json"}
ProgressCallback = Callable[[str, str], None]


def run_pipeline(request: RunRequest, progress: Optional[ProgressCallback] = None) -> Dict[str, object]:
    profile = request.profile
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_root = Path(profile.output_dir).expanduser()
    run_dir = output_root / f"{profile.name}-{timestamp}"
    _notify(progress, "prepare", "Preparing run folder")
    run_dir.mkdir(parents=True, exist_ok=True)

    _notify(progress, "instructions", "Loading restoration instructions")
    instruction = load_instruction(profile.instruction_preset)
    _notify(progress, "provider", f"Selecting provider: {profile.model.provider}")
    provider = get_provider(profile.model.provider)
    cleaned_parts: List[str] = []
    raw_parts: List[str] = []

    for index, path in enumerate(request.inputs.files, start=1):
        _notify(progress, "decode", f"Decoding {path.name}")
        raw_text = decode_input(path, profile)
        raw_parts.append(f"===== {path.name} =====\n{raw_text}".strip())
        _notify(progress, "clean", f"Restoring text from {path.name}")
        cleaned = provider.restore_text(_task_text(raw_text, profile), instruction, profile.model)
        cleaned_parts.append(f"===== {path.name} =====\n{cleaned}".strip())
        _notify(progress, "source", f"Bundling source {path.name}")
        _copy_source(path, run_dir / "sources", index=index)

    raw_text = "\n\n".join(raw_parts).strip() + "\n"
    cleaned_text = "\n\n".join(cleaned_parts).strip() + "\n"
    _notify(progress, "write", "Writing raw OCR text")
    (run_dir / "raw_ocr.txt").write_text(raw_text, encoding="utf-8")

    metadata = {
        "title": f"Akshara Vision - {profile.name}",
        "created_at": timestamp,
        "workflow": profile.workflow,
        "document_type": profile.document_type,
        "source_language": profile.source_language,
        "output_language": profile.output_language,
        "translation_mode": profile.translation_mode,
        "ocr_mode": profile.ocr_mode,
        "provider": profile.model.provider,
        "model": profile.model.model,
        "instruction_preset": profile.instruction_preset,
        "inputs": [_safe_path(path) for path in request.inputs.files],
        "missing": request.inputs.missing,
        "unsupported": [_safe_path(path) for path in request.inputs.unsupported],
    }

    exports: List[ExportResult] = []
    destination = run_dir / "akshara_output"
    registry = exporter_registry()
    for output_format in profile.output_formats:
        exporter = registry.get(output_format)
        if exporter is None:
            continue
        _notify(progress, "export", f"Exporting {output_format}")
        exports.append(exporter.export(cleaned_text, destination, metadata))

    _notify(progress, "manifest", "Writing run manifest")
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
    _notify(progress, "complete", "Run complete")
    return {"run_dir": run_dir, "exports": exports, "manifest": manifest}


def decode_input(path: Path, profile: WorkflowProfile = None) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="latin-1")
    if suffix == ".pdf":
        return _decode_pdf(path, profile)
    if suffix == ".zip":
        return _decode_zip(path, profile)
    return _decode_image(path, profile)


def _copy_source(path: Path, destination: Path, index: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{index:04d}-{path.name}"
    try:
        shutil.copy2(path, target)
    except OSError:
        return


def _decode_pdf(path: Path, profile: WorkflowProfile = None) -> str:
    mode = profile.ocr_mode if profile else "auto"
    if mode in {"auto", "pdf-text", "hybrid"} and shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=240,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    if mode in {"auto", "force-ocr", "hybrid"}:
        ocr_text = _ocr_pdf_pages(path, profile)
        if ocr_text.strip():
            return ocr_text
    return (
        f"[PDF input: {path.name}]\n"
        "Install poppler `pdftotext` for embedded PDF text extraction, or install "
        "`pdftoppm` and `tesseract` for OCR."
    )


def _ocr_pdf_pages(path: Path, profile: WorkflowProfile = None) -> str:
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return ""
    language = _tesseract_language(profile.source_language if profile else "auto")
    with tempfile.TemporaryDirectory(prefix="akshara-pdf-") as tmp:
        prefix = str(Path(tmp) / "page")
        render = subprocess.run(
            ["pdftoppm", "-r", "300", "-png", str(path), prefix],
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if render.returncode != 0:
            return ""
        page_text = []
        for page in sorted(Path(tmp).glob("page-*.png")):
            page_text.append(f"===== {page.stem} =====\n{_decode_image(page, profile)}")
        return "\n\n".join(page_text)


def _decode_image(path: Path, profile: WorkflowProfile = None) -> str:
    if not shutil.which("tesseract"):
        return (
            f"[Image input: {path.name}]\n"
            "Install `tesseract` to OCR image files."
        )
    command = ["tesseract", str(path), "stdout", "--psm", "1"]
    language = _tesseract_language(profile.source_language if profile else "auto")
    if language:
        command.extend(["-l", language])
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout
    return f"[Image input: {path.name}]\nTesseract could not read this image."


def _decode_zip(path: Path, profile: WorkflowProfile = None) -> str:
    parts = [f"[Archive input: {path.name}]"]
    with tempfile.TemporaryDirectory(prefix="akshara-zip-") as tmp:
        root = Path(tmp)
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    source_name = Path(member.filename).name
                    if not source_name:
                        continue
                    target = root / source_name
                    with archive.open(member) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                    parts.append(f"===== {member.filename} =====\n{decode_input(target, profile)}")
        except (OSError, zipfile.BadZipFile):
            parts.append("Archive could not be read.")
    return "\n\n".join(parts)


def _tesseract_language(language: str) -> str:
    if not language or language == "auto":
        return ""
    aliases = {
        "english": "eng",
        "hindi": "hin",
        "sanskrit": "san",
        "tamil": "tam",
        "telugu": "tel",
        "kannada": "kan",
        "malayalam": "mal",
        "marathi": "mar",
        "bengali": "ben",
        "urdu": "urd",
    }
    return aliases.get(language.lower(), language)


def _safe_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def _notify(progress: Optional[ProgressCallback], event: str, message: str) -> None:
    if progress:
        progress(event, message)


def _task_text(raw_text: str, profile: WorkflowProfile) -> str:
    return (
        "TASK SETTINGS\n"
        f"Workflow: {profile.workflow}\n"
        f"Document type: {profile.document_type}\n"
        f"Source language: {profile.source_language}\n"
        f"Output language: {profile.output_language}\n"
        f"Translation mode: {profile.translation_mode}\n"
        f"OCR/decode mode: {profile.ocr_mode}\n"
        f"Requested output formats: {', '.join(profile.output_formats)}\n\n"
        "SOURCE TEXT\n"
        f"{raw_text}"
    )
