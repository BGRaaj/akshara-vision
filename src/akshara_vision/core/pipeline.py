import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from akshara_vision.core.constants import EXECUTION_MODES
from akshara_vision.core.models import RunRequest, WorkflowProfile
from akshara_vision.exporters.base import ExportResult
from akshara_vision.instructions import load_instruction
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import get_provider


TEXT_EXTENSIONS = {".txt", ".md", ".html", ".hocr", ".xml", ".json"}
ProgressCallback = Callable[[str, str, int], None]

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

EXECUTION_MODE_TIMEOUTS = {
    "fast": 120,
    "balanced": 240,
    "quality": 480,
}


def run_pipeline(
    request: RunRequest, progress: Optional[ProgressCallback] = None
) -> Dict[str, object]:
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
    restoration_records: List[Dict[str, object]] = []
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "truncated": False,
    }

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

        if suffix not in TEXT_EXTENSIONS:
            if not _is_vision_model(profile.model.model):
                raise RuntimeError(
                    f"Processing {path.name} requires a multimodal vision model. "
                    f"The selected model '{profile.model.model}' is text-only."
                )
            _notify(progress, "decode", f"Preparing multimodal {path.name}", advance=1)
            if suffix == ".pdf":
                cleaned, restoration_record, usage = _restore_multimodal_pdf(
                    path,
                    instruction,
                    profile,
                    provider,
                    progress,
                )
            elif suffix == ".zip":
                cleaned, restoration_record, usage = _restore_multimodal_zip(
                    path,
                    instruction,
                    profile,
                    provider,
                    progress,
                )
            else:
                cleaned, restoration_record, usage = _restore_multimodal_image(
                    path, instruction, profile, provider
                )
            raw_text = f"[Multimodal Input: {path.name}]"
            _add_usage(usage)
        else:
            _notify(progress, "decode", f"Reading text from {path.name}", advance=1)
            raw_text = path.read_text(encoding="utf-8", errors="replace")
            _notify(progress, "clean", f"Restoring text from {path.name}", advance=1)
            cleaned, restoration_record, usage = _restore_text(
                raw_text, instruction, profile, provider
            )
            _add_usage(usage)

        raw_parts.append(f"===== {path.name} =====\n{raw_text}".strip())
        restoration_records.append(
            {
                "source": _safe_path(path),
                "status": restoration_record["status"],
                "chunks": restoration_record["chunks"],
            }
        )
        cleaned_parts.append(f"===== {path.name} =====\n{cleaned}".strip())
        _notify(progress, "source", f"Bundling source {path.name}", advance=1)
        _copy_source(path, run_dir / "sources", index=index)

    raw_text = "\n\n".join(raw_parts).strip() + "\n"
    cleaned_text = "\n\n".join(cleaned_parts).strip() + "\n"
    _notify(progress, "write", "Writing raw OCR text", advance=1)
    (run_dir / "raw_ocr.txt").write_text(raw_text, encoding="utf-8")

    metadata = {
        "title": f"Akshara Vision - {profile.name}",
        "created_at": timestamp,
        "workflow": profile.workflow,
        "document_type": profile.document_type,
        "source_language": profile.source_language,
        "output_language": profile.output_language,
        "translation_mode": profile.translation_mode,
        "provider": profile.model.provider,
        "model": profile.model.model,
        "instruction_preset": profile.instruction_preset,
        "restoration": restoration_records,
        "inputs": [_safe_path(path) for path in request.inputs.files],
        "missing": request.inputs.missing,
        "unsupported": [_safe_path(path) for path in request.inputs.unsupported],
        "usage": total_usage,
    }

    exports: List[ExportResult] = []
    destination = run_dir / "akshara_output"
    registry = exporter_registry()
    for output_format in profile.output_formats:
        exporter = registry.get(output_format)
        if exporter is None:
            continue
        _notify(progress, "export", f"Exporting {output_format}", advance=1)
        exports.append(exporter.export(cleaned_text, destination, metadata))

    _notify(progress, "manifest", "Writing run manifest", advance=1)
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
    _notify(progress, "complete", "Run complete", advance=1)
    return {"run_dir": run_dir, "exports": exports, "manifest": manifest}


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
    media_path: Optional[Path] = None,
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
        prompt = _task_text(chunk, profile)
        result, usage = provider.restore_text(
            prompt, instruction, profile.model, media_path=media_path
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
        restored_chunks.append(restored_text)
        structured_chunks.append(
            {
                "index": index,
                "input": _short_excerpt(chunk),
                "restored_text": restored_text,
                "uncertain": parsed["uncertain"],
                "notes": parsed["notes"],
                "status": parsed["status"],
            }
        )
    combined = "\n\n".join(part for part in restored_chunks if part.strip()).strip()
    if not combined:
        combined = "[missing text]"
    return combined + "\n", {"status": "restored", "chunks": structured_chunks}, total_usage


def _copy_source(path: Path, destination: Path, index: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{index:04d}-{path.name}"
    try:
        shutil.copy2(path, target)
    except OSError:
        return


def _safe_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def _notify(
    progress: Optional[ProgressCallback], event: str, message: str, advance: int = 1
) -> None:
    if progress:
        progress(event, message, advance)


def _split_text_chunks(text: str, max_chars: int = 5000) -> List[str]:
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
            uncertain = data.get("uncertain") if isinstance(data.get("uncertain"), list) else []
            notes = str(data.get("notes") or "")
            status = str(data.get("status") or "restored")
            return {
                "restored_text": restored_text,
                "uncertain": [str(item) for item in uncertain],
                "notes": notes,
                "status": status,
            }
    if _looks_like_meta_response(candidate):
        return {
            "restored_text": fallback_text.strip(),
            "uncertain": [],
            "notes": "fallback to source chunk because model returned commentary",
            "status": "fallback",
        }
    return {
        "restored_text": candidate,
        "uncertain": [],
        "notes": "",
        "status": "restored",
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


def _task_text(raw_text: str, profile: WorkflowProfile) -> str:
    """Build the user-facing prompt for the LLM.

    For text-chunk restoration (raw text input), we demand strict JSON
    so the parser can extract structured metadata.

    For multimodal (vision) mode, we use a simple, direct prompt asking the
    model to extract and return the text.  Demanding JSON from a vision model
    looking at degraded manuscripts is unreliable — the model works best when
    it can focus entirely on reading the document.
    """
    execution_mode = _execution_mode(profile)
    context = (
        f"Document type: {profile.document_type}\n"
        f"Source language: {profile.source_language}\n"
        f"Output language: {profile.output_language}\n"
        f"Translation mode: {profile.translation_mode}\n"
        f"Execution mode: {execution_mode}\n\n"
    )

    if execution_mode == "quality":
        depth_instruction = (
            "Perform a deep, rigorous analysis of the image. Take your time to carefully parse "
            "faded, complex, or degraded characters before extracting the text.\n"
        )
    elif execution_mode == "balanced":
        depth_instruction = "Perform a careful and thorough extraction of the text in the image.\n"
    else:  # fast
        depth_instruction = "Quickly extract the text from the image, focusing on speed and the most legible characters.\n"

    if not raw_text:
        # Multimodal vision prompt — keep it simple and direct.
        return (
            context
            + "Look at the attached image carefully.\n"
            + depth_instruction
            + "Extract ALL text visible in the image exactly as written.\n"
            "Preserve the original language, script, spelling, line breaks, and formatting.\n"
            "If any words are unclear, mark them as [unclear].\n"
            "Return ONLY the extracted text. Do not add explanations, commentary, "
            "JSON formatting, code fences, or any other markup.\n"
            "If the image is completely unreadable, return only: [missing text]"
        )

    # Text-chunk restoration prompt — strict JSON output.
    return (
        "Return only a valid JSON object with keys restored_text, uncertain, and notes.\n"
        "restored_text must contain only the cleaned text for this chunk.\n"
        "uncertain must be an array of uncertain words or phrases.\n"
        "notes must be a short string or an empty string.\n"
        "Do not include markdown, code fences, or commentary.\n"
        "If the chunk is empty or unreadable, return "
        '{"restored_text":"[missing text]","uncertain":[],"notes":"unreadable source"}.\n'
        + context
        + "SOURCE CHUNK\n"
        + raw_text
    )


def _restore_multimodal_image(
    path: Path, instruction: str, profile: WorkflowProfile, provider
) -> tuple:
    """Send an image directly to the vision model and use its raw text output.

    Unlike text-chunk restoration, we do NOT demand JSON here.  The model's
    raw response IS the extracted text.  We only attempt JSON parsing as a
    bonus — if the model happens to return JSON, we use the structured data;
    otherwise, we take the full response as restored text.
    """
    prompt = _task_text("", profile)
    result, usage = provider.restore_text(prompt, instruction, profile.model, media_path=path)

    # Try JSON parsing first (in case the model does return structured data).
    restored_text = _extract_multimodal_text(result)

    if not restored_text:
        restored_text = "[missing text]"

    record = {
        "status": "restored",
        "chunks": [
            {
                "index": 1,
                "input": f"[Image: {path.name}]",
                "restored_text": restored_text,
                "uncertain": [],
                "notes": "",
                "status": "restored",
            }
        ],
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
                if text and text != "[missing text]":
                    return text
        except json.JSONDecodeError:
            pass

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

    return cleaned


def _restore_multimodal_pdf(
    path: Path,
    instruction: str,
    profile: WorkflowProfile,
    provider,
    progress: Optional[ProgressCallback],
) -> tuple:
    pdftoppm_exe = find_executable("pdftoppm")
    if not pdftoppm_exe:
        raise RuntimeError(
            "PDF page rendering utility not found. Please install the required system dependencies "
            "by running 'akv install' (or 'akshara install')."
        )

    execution_mode = _execution_mode(profile)
    dpi = EXECUTION_MODE_PDF_DPI.get(execution_mode, 300)

    temp_dir = tempfile.TemporaryDirectory(prefix="akshara-multimodal-pdf-")
    try:
        prefix = str(Path(temp_dir.name) / "page")
        render = subprocess.run(
            [pdftoppm_exe, "-r", str(dpi), "-png", str(path), prefix],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=EXECUTION_MODE_TIMEOUTS[execution_mode],
        )
        if render.returncode != 0:
            raise RuntimeError(
                f"pdftoppm rendering failed (exit code {render.returncode}): {render.stderr}"
            )

        page_images = sorted(Path(temp_dir.name).glob("page-*.png"))
        if not page_images:
            raise RuntimeError("No pages rendered from PDF.")

        restored_pages = []
        chunks_record = []
        total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "truncated": False,
        }
        for idx, page_img in enumerate(page_images, start=1):
            _notify(
                progress,
                "clean",
                f"Restoring text from {path.name} (page {idx}/{len(page_images)})",
                advance=1,
            )
            prompt = _task_text("", profile)
            result, usage = provider.restore_text(
                prompt, instruction, profile.model, media_path=page_img
            )
            if usage:
                total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                total_usage["total_tokens"] += usage.get("total_tokens", 0)
                if usage.get("truncated"):
                    total_usage["truncated"] = True
            restored_text = _extract_multimodal_text(result)
            if not restored_text:
                restored_text = "[missing text]"
            restored_pages.append(restored_text)
            chunks_record.append(
                {
                    "index": idx,
                    "input": f"[PDF Page {idx}: {page_img.name}]",
                    "restored_text": restored_text,
                    "uncertain": [],
                    "notes": "",
                    "status": "restored",
                }
            )

        combined = "\n\n".join(restored_pages) + "\n"
        return combined, {"status": "restored", "chunks": chunks_record}, total_usage
    finally:
        temp_dir.cleanup()


def _restore_multimodal_zip(
    path: Path,
    instruction: str,
    profile: WorkflowProfile,
    provider,
    progress: Optional[ProgressCallback],
) -> tuple:
    temp_dir = tempfile.TemporaryDirectory(prefix="akshara-multimodal-zip-")
    try:
        root = Path(temp_dir.name)
        extracted_files = []
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
                extracted_files.append(target)

        restored_parts = []
        chunks_record = []
        total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "truncated": False,
        }
        chunk_idx = 1
        for ext_file in sorted(extracted_files):
            suffix = ext_file.suffix.lower()
            if suffix in TEXT_EXTENSIONS:
                text_content = ext_file.read_text(encoding="utf-8", errors="replace")
                sub_chunks = _split_text_chunks(text_content)
                for sub_chunk in sub_chunks:
                    prompt = _task_text(sub_chunk, profile)
                    result, usage = provider.restore_text(prompt, instruction, profile.model)
                    if usage:
                        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += usage.get("total_tokens", 0)
                        if usage.get("truncated"):
                            total_usage["truncated"] = True
                    parsed = _parse_restoration_result(result, sub_chunk)
                    restored_text = parsed["restored_text"].strip()
                    restored_parts.append(restored_text)
                    chunks_record.append(
                        {
                            "index": chunk_idx,
                            "input": f"[ZIP Text: {ext_file.name}] " + _short_excerpt(sub_chunk),
                            "restored_text": restored_text,
                            "uncertain": parsed["uncertain"],
                            "notes": parsed["notes"],
                            "status": parsed["status"],
                        }
                    )
                    chunk_idx += 1
            elif suffix == ".pdf":
                pdf_clean, pdf_rec, usage = _restore_multimodal_pdf(
                    ext_file, instruction, profile, provider, progress
                )
                if usage:
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    if usage.get("truncated"):
                        total_usage["truncated"] = True
                restored_parts.append(pdf_clean.strip())
                for ch in pdf_rec.get("chunks", []):
                    ch["index"] = chunk_idx
                    ch["input"] = f"[ZIP Archive -> {ext_file.name}] {ch['input']}"
                    chunks_record.append(ch)
                    chunk_idx += 1
            elif suffix in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}:
                prompt = _task_text("", profile)
                result, usage = provider.restore_text(
                    prompt, instruction, profile.model, media_path=ext_file
                )
                if usage:
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    if usage.get("truncated"):
                        total_usage["truncated"] = True
                restored_text = _extract_multimodal_text(result)
                if not restored_text:
                    restored_text = "[missing text]"
                restored_parts.append(restored_text)
                chunks_record.append(
                    {
                        "index": chunk_idx,
                        "input": f"[ZIP Image: {ext_file.name}]",
                        "restored_text": restored_text,
                        "uncertain": [],
                        "notes": "",
                        "status": "restored",
                    }
                )
                chunk_idx += 1

        combined = "\n\n".join(restored_parts) + "\n"
        return combined, {"status": "restored", "chunks": chunks_record}, total_usage
    finally:
        temp_dir.cleanup()
