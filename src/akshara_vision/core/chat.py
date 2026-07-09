from __future__ import annotations

import copy
import json
import re
import tempfile
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from akshara_vision.core.config import ConfigStore
from akshara_vision.core.constants import default_config_dir
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import RunRequest, WorkflowProfile
from akshara_vision.core.pipeline import _render_pdf_page, find_executable, run_pipeline
from akshara_vision.registries.providers import get_provider


@dataclass
class ChatSource:
    source_id: str
    label: str
    text: str
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class ChatBundle:
    title: str
    sources: List[ChatSource]
    profile: WorkflowProfile
    origin: str = "run"
    notes: List[str] = field(default_factory=list)

    def source_ids(self) -> List[str]:
        return [source.source_id for source in self.sources]


def build_chat_bundle(
    raw_inputs: Optional[Iterable[str]],
    profile: Optional[WorkflowProfile] = None,
    recursive: bool = False,
    question: Optional[str] = None,
) -> ChatBundle:
    profile = copy.deepcopy(profile or ConfigStore().load_default_profile())
    inputs = [str(item).strip() for item in raw_inputs or [] if str(item).strip()]
    if not inputs:
        raise RuntimeError("Chat requires at least one run folder, output file, or input file.")

    collected: List[ChatSource] = []
    raw_queue: List[str] = []
    titles: List[str] = []

    for item in inputs:
        path = Path(item).expanduser()
        if _looks_like_run_folder(path):
            run_sources, run_title = _sources_from_run_path(path)
            collected.extend(run_sources)
            if run_title:
                titles.append(run_title)
            continue
        if path.is_dir():
            folder_sources, folder_title = _sources_from_folder_path(
                path, recursive=recursive, question=question
            )
            if folder_sources:
                collected.extend(folder_sources)
                titles.append(folder_title)
                continue
        if _looks_like_compiled_output(path):
            collected.append(
                ChatSource(
                    source_id=f"S{len(collected) + 1}",
                    label=path.name,
                    text=_read_text_file(path),
                    metadata={"kind": "compiled-output", "path": str(path)},
                )
            )
            titles.append(path.stem)
            continue
        if len(inputs) == 1 and _looks_like_visual_input(path):
            collected.append(
                ChatSource(
                    source_id="S1",
                    label=path.name,
                    text="[visual image source]",
                    metadata={
                        "kind": "raw-visual",
                        "path": str(path),
                        "media_path": str(path),
                        "source": path.name,
                    },
                )
            )
            titles.append(path.stem)
            continue
        page_refs = _question_page_numbers(question or "")
        if len(inputs) == 1 and page_refs and _looks_like_pdf_input(path):
            collected.extend(_raw_pdf_sources(path, page_refs))
            titles.append(path.stem)
            continue
        raw_queue.append(item)

    if raw_queue:
        temp_dir = tempfile.TemporaryDirectory(prefix="akshara-chat-")
        try:
            temp_profile = copy.deepcopy(profile)
            temp_profile.output_dir = temp_dir.name
            temp_profile.output_formats = ["txt"]
            temp_profile.translation_mode = "off"
            selection = discover_inputs(raw_queue, recursive=recursive)
            if not selection.files:
                raise RuntimeError(
                    _unsupported_chat_input_message(selection.missing, selection.unsupported)
                )
            focused_files = _focus_input_files(selection.files, question or "")
            if focused_files:
                selection.files = focused_files
            result = run_pipeline(RunRequest(profile=temp_profile, inputs=selection))
            run_dir = Path(result["run_dir"])
            manifest_sources, run_title = _sources_from_run_path(run_dir)
            collected.extend(manifest_sources)
            if run_title:
                titles.append(run_title)
        finally:
            temp_dir.cleanup()

    if not collected:
        raise RuntimeError("No readable chat sources were found.")

    collected = _renumber_sources(collected)
    bundle_title = next((title for title in titles if title.strip()), "Document")
    return ChatBundle(title=bundle_title, sources=collected, profile=profile)


def answer_question(
    bundle: ChatBundle,
    question: str,
    system_prompt: Optional[str] = None,
    history: Optional[Sequence[Tuple[str, str]]] = None,
    notes: Optional[Sequence[str]] = None,
    citation_source_ids: Optional[Sequence[str]] = None,
) -> tuple[str, dict, List[ChatSource]]:
    prompt_sources = _select_relevant_sources(
        bundle.sources,
        question,
        citation_source_ids=citation_source_ids,
    )
    if not prompt_sources:
        raise RuntimeError("No grounded sources are available for this question.")
    system_prompt = (system_prompt or _default_chat_instruction(bundle)).strip()
    prompt = _build_chat_prompt(bundle, question, prompt_sources, history, notes=notes)
    provider = get_provider(bundle.profile.model.provider)
    media_path, media_temp = _chat_media_path(prompt_sources, question)
    try:
        response, usage = _restore_text_with_retry(
            provider,
            prompt,
            system_prompt,
            bundle.profile.model,
            media_path=media_path,
        )
    finally:
        if media_temp is not None:
            media_temp.cleanup()
    return (response or "").strip(), usage or {}, prompt_sources


def answer_general_question(
    profile: WorkflowProfile,
    question: str,
    system_prompt: Optional[str] = None,
    history: Optional[Sequence[Tuple[str, str]]] = None,
    notes: Optional[Sequence[str]] = None,
) -> tuple[str, dict]:
    instruction = (
        system_prompt
        or (
            "You are a helpful document assistant and general conversation partner.\n"
            "Answer clearly, keep the response grounded in the conversation, and ask a brief follow-up "
            "only when it helps move the task forward.\n"
        )
    ).strip()
    prompt = _build_general_chat_prompt(question, history=history, notes=notes)
    model = getattr(profile, "chat_model", None) or profile.model
    provider = get_provider(model.provider)
    response, usage = _restore_text_with_retry(provider, prompt, instruction, model)
    return (response or "").strip(), usage or {}


def _restore_text_with_retry(provider, prompt: str, instruction: str, model, media_path: Optional[Path] = None):
    response, usage = provider.restore_text(
        prompt,
        instruction,
        model,
        media_path=media_path,
    )
    if not _response_needs_retry(response, usage):
        return response, usage or {}
    retry_instruction = (
        instruction
        + "\n\n"
        + "The previous answer was incomplete or clipped. Return one complete final answer now. "
        + "Use the partial answer only as context, do not mention this retry, and do not add unsupported claims."
    )
    retry_prompt = (
        prompt
        + "\n\nPARTIAL ANSWER FROM PREVIOUS ATTEMPT:\n"
        + (response or "").strip()
        + "\n\nReturn the complete answer."
    )
    retry_response, retry_usage = provider.restore_text(
        retry_prompt,
        retry_instruction,
        model,
        media_path=media_path,
    )
    merged_usage = _merge_usage(usage or {}, retry_usage or {})
    if len((retry_response or "").strip()) >= len((response or "").strip()):
        return retry_response, merged_usage
    return response, merged_usage


def _response_needs_retry(response: str, usage: Optional[dict]) -> bool:
    text = str(response or "").strip()
    if not text:
        return True
    if isinstance(usage, dict) and usage.get("truncated"):
        return True
    return text.endswith(("...", "—", "-", ":", "(", "[", "{", "/"))


def _merge_usage(first: dict, second: dict) -> dict:
    merged = dict(first or {})
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        merged[key] = int(first.get(key, 0) or 0) + int(second.get(key, 0) or 0)
    merged["truncated"] = bool(second.get("truncated"))
    merged["retry_attempted"] = True
    merged["original_truncated"] = bool(first.get("truncated"))
    return merged


def chat_session_path(raw_inputs: Iterable[str]) -> Optional[Path]:
    for item in raw_inputs:
        path = Path(str(item)).expanduser()
        if _looks_like_run_folder(path):
            return path / "chat_session.json"
    return None


def chat_sessions_root() -> Path:
    root = default_config_dir() / "chats"
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_chat_session_path(title: Optional[str] = None) -> Path:
    slug = _session_slug(title or "chat")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return chat_sessions_root() / f"{slug}-{stamp}.json"


def list_chat_sessions() -> List[Path]:
    root = chat_sessions_root()
    return sorted(
        [path for path in root.glob("*.json") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def delete_chat_session(path: Path) -> bool:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def load_chat_history(path: Optional[Path]) -> List[Tuple[str, str]]:
    if path is None or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    turns = payload.get("turns") if isinstance(payload, dict) else []
    history: List[Tuple[str, str]] = []
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            question = str(turn.get("question") or "").strip()
            answer = str(turn.get("answer") or "").strip()
            if question and answer:
                history.append((question, answer))
    return history[-24:]


def load_chat_notes(path: Optional[Path]) -> List[str]:
    if path is None or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    notes = payload.get("notes") if isinstance(payload, dict) else []
    if not isinstance(notes, list):
        return []
    return [str(note).strip() for note in notes if str(note).strip()][:24]


def load_chat_metadata(path: Optional[Path]) -> Dict[str, object]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    return metadata if isinstance(metadata, dict) else {}


def save_chat_history(
    path: Optional[Path],
    history: Sequence[Tuple[str, str]],
    notes: Optional[Sequence[str]] = None,
    metadata: Optional[Dict[str, object]] = None,
) -> None:
    if path is None:
        return
    payload = {
        "version": 2,
        "turns": [
            {"question": question, "answer": answer}
            for question, answer in history[-24:]
        ],
    }
    if notes is not None:
        payload["notes"] = [str(note).strip() for note in notes if str(note).strip()][:24]
    if metadata is not None:
        payload["metadata"] = metadata
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


def search_sources(sources: Sequence[ChatSource], query: str, limit: int = 8) -> List[ChatSource]:
    terms = _question_terms(query)
    if not terms:
        return []
    scored = []
    for source in sources:
        haystack = f"{source.label}\n{source.text}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            scored.append((score, source))
    return [source for _score, source in sorted(scored, key=lambda item: (-item[0], item[1].source_id))[:limit]]


def _default_chat_instruction(bundle: ChatBundle) -> str:
    visual_hint = ""
    if any(str(source.metadata.get("kind") or "") == "raw-visual" for source in bundle.sources):
        visual_hint = (
            "If the bundle contains a direct visual source, inspect the image itself first when the "
            "indexed text is incomplete or does not answer the question.\n"
        )
    return (
        "You are answering questions about a restored document corpus.\n"
        "Use only the provided sources and the conversation history.\n"
        "If the answer is not supported by the sources, say that clearly.\n"
        "Cite claims inline with source ids like [S1], [S2], or [S1/S3].\n"
        "If a page, figure, label, or image is attached for a source, inspect it directly when the "
        "indexed text is incomplete, missing, or only partially answers the question.\n"
        "Keep the answer concise, grounded, and factual.\n"
        f"{visual_hint}"
        f"Document title: {bundle.title}\n"
    )


def _build_chat_prompt(
    bundle: ChatBundle,
    question: str,
    sources: Sequence[ChatSource],
    history: Optional[Sequence[Tuple[str, str]]] = None,
    notes: Optional[Sequence[str]] = None,
) -> str:
    visual_sources = [source for source in sources if str(source.metadata.get("kind") or "") == "raw-visual"]
    parts = [
        f"DOCUMENT TITLE: {bundle.title}",
        "",
        "CONTEXT SOURCES",
    ]
    for source in sources:
        parts.append(
            "\n".join(
                [
                    f"[{source.source_id}] {source.label}",
                    _format_source_metadata(source.metadata),
                    source.text[:1200].strip() or "[missing text]",
                ]
            ).strip()
        )
        parts.append("")
    if history:
        parts.extend(["CONVERSATION HISTORY"])
        for index, (user_text, assistant_text) in enumerate(history[-4:], start=1):
            parts.append(f"Previous question {index}: {user_text.strip()}")
            parts.append(f"Previous answer {index}: {assistant_text.strip()}")
            parts.append("")
    if notes:
        clean_notes = [str(note).strip() for note in notes if str(note).strip()]
        if clean_notes:
            parts.extend(["SESSION NOTES"])
            for index, note in enumerate(clean_notes[-8:], start=1):
                parts.append(f"Note {index}: {note}")
            parts.append("")
    parts.extend(
        [
            "QUESTION",
            question.strip(),
            "",
            "INSTRUCTIONS",
            "Answer only from the sources above.",
            "If you reference a fact, cite the relevant source ids inline.",
            "If the question asks for a list or extraction, preserve the document's structure where possible.",
            "If the selected text does not fully cover the question but a page image or visual source is available, re-check the image before answering.",
        ]
    )
    if visual_sources:
        parts.extend(
            [
                "",
                "VISUAL SOURCE GUIDANCE",
                "A direct image source is included. If the indexed text does not cover the question, inspect the image itself and answer from visible evidence.",
            ]
        )
    return "\n".join(parts).strip()


def _build_general_chat_prompt(
    question: str,
    history: Optional[Sequence[Tuple[str, str]]] = None,
    notes: Optional[Sequence[str]] = None,
) -> str:
    parts = [
        "CONVERSATION MODE",
        "General chat. No document sources are attached yet.",
    ]
    if history:
        parts.extend(["CONVERSATION HISTORY"])
        for index, (user_text, assistant_text) in enumerate(history[-4:], start=1):
            parts.append(f"Previous question {index}: {user_text.strip()}")
            parts.append(f"Previous answer {index}: {assistant_text.strip()}")
            parts.append("")
    if notes:
        clean_notes = [str(note).strip() for note in notes if str(note).strip()]
        if clean_notes:
            parts.extend(["SESSION NOTES"])
            for index, note in enumerate(clean_notes[-8:], start=1):
                parts.append(f"Note {index}: {note}")
            parts.append("")
    parts.extend(
        [
            "QUESTION",
            question.strip(),
            "",
            "INSTRUCTIONS",
            "Answer naturally and keep the conversation useful and concise.",
        ]
    )
    return "\n".join(parts).strip()


def _format_source_metadata(metadata: Dict[str, object]) -> str:
    if not metadata:
        return "Metadata: none"
    items = []
    for key in ("source", "label", "page_number", "index", "role", "role_label", "confidence"):
        value = metadata.get(key)
        if value not in (None, "", []):
            items.append(f"{key}={value}")
    return f"Metadata: {', '.join(items)}" if items else "Metadata: none"


def _select_relevant_sources(
    sources: Sequence[ChatSource],
    question: str,
    limit: int = 10,
    citation_source_ids: Optional[Sequence[str]] = None,
) -> List[ChatSource]:
    if citation_source_ids:
        pinned = {str(source_id).strip().upper() for source_id in citation_source_ids if str(source_id).strip()}
        selected = [source for source in sources if source.source_id.upper() in pinned]
        if not selected:
            raise RuntimeError("None of the pinned citation sources are available in the current scope.")
        return selected[:limit]
    question_terms = _question_terms(question)
    page_refs = _question_page_numbers(question)
    scored: List[tuple[int, ChatSource]] = []
    for source in sources:
        text = f"{source.label}\n{source.text}".lower()
        score = sum(1 for term in question_terms if term in text)
        score += int(float(source.metadata.get("confidence") or 0) * 10)
        kind = str(source.metadata.get("kind") or "")
        if kind == "page-record":
            score += 22
        elif kind == "raw-visual":
            score += 14
        elif kind == "chunk":
            score += 6
        page_number = _metadata_page_number(source.metadata)
        if page_refs and page_number is not None:
            if page_number in page_refs:
                score += 40
            elif any(abs(page_number - page_ref) <= 1 for page_ref in page_refs):
                score += 16
        scored.append((score, source))
    ranked = sorted(scored, key=lambda item: (-item[0], item[1].source_id))
    if page_refs:
        exact = [source for _score, source in ranked if _source_matches_page_refs(source, page_refs)]
        if exact:
            return exact[:limit]
    return [source for score, source in ranked[:limit] if score > 0] or list(sources[: min(limit, len(sources))])


def _chat_media_path(
    sources: Sequence[ChatSource], question: str = ""
) -> tuple[Optional[Path], Optional[tempfile.TemporaryDirectory]]:
    page_refs = _question_page_numbers(question)
    if page_refs:
        for source in sources:
            if not _source_matches_page_refs(source, page_refs):
                continue
            path, temp = _source_media_path(source)
            if path is not None:
                return path, temp
    if len(sources) == 1:
        path, temp = _source_media_path(sources[0])
        if path is not None:
            return path, temp
    for source in sources:
        if str(source.metadata.get("kind") or "") == "raw-visual":
            path, temp = _source_media_path(source)
            if path is not None:
                return path, temp
    if _question_requests_visual_review(question):
        for source in sources:
            path, temp = _source_media_path(source)
            if path is not None:
                return path, temp
    return None, None


def _source_media_path(source: ChatSource) -> tuple[Optional[Path], Optional[tempfile.TemporaryDirectory]]:
    media_path = str(source.metadata.get("media_path") or "").strip()
    if media_path:
        path = Path(media_path).expanduser()
        if path.exists() and _looks_like_visual_input(path):
            return path, None
    source_path = Path(str(source.metadata.get("path") or "")).expanduser()
    page_number = _metadata_page_number(source.metadata)
    if source_path.exists() and _looks_like_visual_input(source_path):
        return source_path, None
    if source_path.exists() and source_path.suffix.lower() == ".pdf" and page_number:
        return _render_pdf_page_for_chat(source_path, page_number)
    return None, None


def _render_pdf_page_for_chat(
    path: Path, page_number: int
) -> tuple[Optional[Path], Optional[tempfile.TemporaryDirectory]]:
    pdftoppm = find_executable("pdftoppm")
    if not pdftoppm:
        return None, None
    temp_dir = tempfile.TemporaryDirectory(prefix="akshara-chat-page-")
    try:
        rendered = _render_pdf_page(pdftoppm, path, Path(temp_dir.name), page_number, 300)
    except Exception:
        temp_dir.cleanup()
        return None, None
    if rendered and rendered.exists():
        return rendered, temp_dir
    temp_dir.cleanup()
    return None, None


def _metadata_page_number(metadata: Dict[str, object]) -> Optional[int]:
    for key in ("page_number", "page", "index", "chunk_index"):
        value = metadata.get(key)
        if value in (None, "", []):
            continue
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


def _record_page_number(record: Dict[str, object], chunks: Sequence[Dict[str, object]]) -> Optional[int]:
    for key in ("page_number", "page", "index"):
        value = record.get(key)
        if value in (None, "", []):
            continue
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        number = _metadata_page_number(chunk)
        if number is not None:
            return number
    return None


def _record_media_path(record: Dict[str, object], chunks: Sequence[Dict[str, object]]) -> str:
    media_path = str(record.get("media_path") or record.get("image_path") or "").strip()
    if media_path:
        return media_path
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_media = str(chunk.get("media_path") or chunk.get("image_path") or "").strip()
        if chunk_media:
            return chunk_media
    return ""


def _source_matches_page_refs(source: ChatSource, page_refs: Sequence[int]) -> bool:
    page_number = _metadata_page_number(source.metadata)
    if page_number is None:
        return False
    return page_number in page_refs or any(abs(page_number - ref) <= 1 for ref in page_refs)


def _question_page_numbers(question: str) -> List[int]:
    refs: List[int] = []
    patterns = [
        r"\bpage\s*(\d{1,4})\b",
        r"\bpp?\.?\s*(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?",
        r"\bpages?\s*(\d{1,4})(?:\s*[-–/]\s*(\d{1,4}))?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, question, flags=re.IGNORECASE):
            first = int(match.group(1))
            refs.append(first)
            if match.lastindex and match.lastindex >= 2:
                second = match.group(2)
                if second:
                    try:
                        refs.append(int(second))
                    except ValueError:
                        pass
    return sorted(set(refs))


def _question_requests_visual_review(question: str) -> bool:
    lowered = question.lower()
    return any(
        keyword in lowered
        for keyword in (
            "see",
            "look",
            "visual",
            "image",
            "figure",
            "diagram",
            "photo",
            "poster",
            "signboard",
            "chart",
            "what does it show",
            "what is shown",
            "describe",
        )
    )


def _question_terms(question: str) -> List[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "what",
        "which",
        "from",
        "into",
        "about",
        "have",
        "does",
        "where",
        "when",
        "how",
        "why",
        "was",
        "are",
        "you",
        "can",
        "will",
        "please",
    }
    terms = []
    for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{1,}", question.lower()):
        if term not in stop_words:
            terms.append(term)
    return terms[:20]


def _looks_like_run_folder(path: Path) -> bool:
    return path.is_dir() and any(
        (path / name).exists() for name in ("run_manifest.json", "run_state.json", "akshara_output.txt")
    )


def _looks_like_compiled_output(path: Path) -> bool:
    if not path.is_file():
        return False
    return path.suffix.lower() in _CHAT_TEXT_EXTENSIONS


def _looks_like_visual_input(path: Path) -> bool:
    if not path.is_file():
        return False
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _looks_like_pdf_input(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def _raw_pdf_sources(path: Path, page_refs: Sequence[int]) -> List[ChatSource]:
    return [
        ChatSource(
            source_id=f"S{index}",
            label=f"{path.name} page {page_number}",
            text=(
                "[raw PDF page source; this page will be rendered only when the "
                "answer needs visual evidence]"
            ),
            metadata={
                "kind": "raw-pdf-page",
                "source": path.name,
                "path": str(path),
                "page_number": page_number,
            },
        )
        for index, page_number in enumerate(page_refs, start=1)
    ]


def _unsupported_chat_input_message(missing: Sequence[str], unsupported: Sequence[Path]) -> str:
    accepted = ", ".join(sorted(_supported_chat_inputs()))
    parts = [
        "No supported chat inputs were found.",
        f"Accepted chat inputs: {accepted}.",
    ]
    if missing:
        parts.append("Missing paths: " + ", ".join(missing[:8]))
    if unsupported:
        parts.append("Unsupported paths: " + ", ".join(str(path) for path in unsupported[:8]))
    return " ".join(parts)


def _supported_chat_inputs() -> List[str]:
    return [
        "run folders with run_manifest.json or staged outputs",
        "compiled outputs (.txt, .md, .html, .json, .jsonl, .yaml, .yml)",
        "PDFs",
        "images (.jpg, .jpeg, .png, .webp, .tif, .tiff, .bmp)",
        "text/OCR files (.txt, .md, .html, .hocr, .xml, .json)",
        "archives (.zip)",
        "manifests (.csv, .json)",
    ]


_CHAT_TEXT_EXTENSIONS = {".txt", ".md", ".html", ".hocr", ".xml", ".json", ".jsonl", ".yaml", ".yml"}


def _sources_from_run_path(path: Path) -> tuple[List[ChatSource], str]:
    manifest_path = path / "run_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
        sources = _sources_from_manifest(path, manifest)
        title = _manifest_title(manifest, path)
        if sources:
            return sources, title
    compiled = _read_best_text(path)
    if compiled is not None:
        return (
            [
                ChatSource(
                    source_id="S1",
                    label=path.name,
                    text=compiled,
                    metadata={"kind": "compiled-run", "path": str(path)},
                )
            ],
            path.name,
        )
    return [], path.name


def _sources_from_folder_path(
    path: Path, recursive: bool = False, question: Optional[str] = None
) -> tuple[List[ChatSource], str]:
    sources: List[ChatSource] = []
    seen_files: set[Path] = set()
    manifest_paths = sorted(path.rglob("run_manifest.json") if recursive else path.glob("*/run_manifest.json"))
    for manifest_path in manifest_paths[:12]:
        run_dir = manifest_path.parent
        run_sources, _run_title = _sources_from_run_path(run_dir)
        sources.extend(run_sources)
        try:
            seen_files.add(manifest_path.resolve())
        except OSError:
            seen_files.add(manifest_path)

    for candidate in _folder_chat_text_files(path, recursive=recursive, question=question):
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen_files or _is_chat_internal_file(candidate):
            continue
        text = _read_text_file(candidate).strip()
        if not text:
            continue
        try:
            label = str(candidate.relative_to(path)).replace("\\", "/")
        except ValueError:
            label = candidate.name
        sources.append(
            ChatSource(
                source_id=f"S{len(sources) + 1}",
                label=label,
                text=text,
                metadata={
                    "kind": "folder-output",
                    "path": str(candidate),
                    "source": label,
                    "match_score": _path_match_score(candidate, question or ""),
                },
            )
        )
        if len(sources) >= 240:
            break
    return sources, path.name


def _folder_chat_text_files(
    path: Path, recursive: bool = False, question: Optional[str] = None
) -> List[Path]:
    iterator = path.rglob("*") if recursive else path.glob("*")
    files = [
        candidate
        for candidate in iterator
        if candidate.is_file() and candidate.suffix.lower() in _CHAT_TEXT_EXTENSIONS
    ]

    def score(candidate: Path) -> tuple[int, str]:
        name = candidate.name.lower()
        priority = 4
        if name.startswith(("final__", "translated__", "restored__")):
            priority = 0
        elif name.startswith("akshara_output"):
            priority = 1
        elif name.endswith(".detailed.json") or name in {"run_manifest.json", "stage_manifest.json"}:
            priority = 2
        elif candidate.suffix.lower() in {".txt", ".md", ".html", ".json", ".jsonl", ".yaml", ".yml"}:
            priority = 3
        return priority, str(candidate)

    ranked = sorted(files, key=score)
    terms = _question_terms(question or "")
    if not terms:
        return ranked
    matched = [candidate for candidate in ranked if _path_match_score(candidate, question or "") > 0]
    return matched[:48] if matched else ranked


def _path_match_score(path: Path, question: str) -> int:
    terms = _question_terms(question)
    if not terms:
        return 0
    normalized = f"{path.name} {path.stem} {path.parent}".lower().replace("_", " ").replace("-", " ")
    score = 0
    for term in terms:
        if term in normalized:
            score += 3
        elif all(part in normalized for part in term.split()):
            score += 2
    return score


def _focus_input_files(files: Sequence[Path], question: str) -> List[Path]:
    if len(files) <= 1 or not _question_terms(question):
        return []
    scored = [(_path_match_score(path, question), path) for path in files]
    matched = [path for score, path in sorted(scored, key=lambda item: (-item[0], str(item[1]))) if score > 0]
    return matched[:12]


def _is_chat_internal_file(path: Path) -> bool:
    return path.name.lower() in {"run_state.json", "chat_session.json"}


def _renumber_sources(sources: Sequence[ChatSource]) -> List[ChatSource]:
    return [
        ChatSource(
            source_id=f"S{index}",
            label=source.label,
            text=source.text,
            metadata=dict(source.metadata),
        )
        for index, source in enumerate(sources, start=1)
    ]


def _sources_from_manifest(path: Path, manifest: Dict[str, object]) -> List[ChatSource]:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    restoration = metadata.get("restoration") if isinstance(metadata, dict) else []
    sources: List[ChatSource] = []
    counter = 1
    if isinstance(restoration, list):
        for record in restoration:
            if not isinstance(record, dict):
                continue
            label = str(record.get("label") or record.get("source") or f"source-{counter}")
            chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else []
            if not chunks:
                text = _record_text(record)
                if text.strip():
                    sources.append(
                        ChatSource(
                            source_id=f"S{counter}",
                            label=label,
                            text=text.strip(),
                            metadata={"kind": "record", "source": record.get("source", "")},
                        )
                    )
                    counter += 1
                continue
            page_number = _record_page_number(record, chunks)
            record_media_path = _record_media_path(record, chunks)
            page_text = _record_text(record).strip()
            if page_text:
                page_label = label
                if page_number is not None:
                    page_label = f"{label} page {page_number}"
                sources.append(
                    ChatSource(
                        source_id=f"S{counter}",
                        label=page_label,
                        text=page_text,
                        metadata={
                            "kind": "page-record",
                            "source": label,
                            "path": str(record.get("source") or path),
                            "chunk_count": len(chunks),
                            "page_number": page_number,
                            "media_path": record_media_path or "",
                        },
                    )
                )
                counter += 1
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                text = _chunk_text(chunk)
                if not text.strip():
                    continue
                chunk_meta = dict(chunk.get("semantic_tags") or {})
                page_number = chunk_meta.get("page_number") or chunk.get("page_number") or chunk.get("index")
                chunk_meta.update(
                    {
                        "source": label,
                        "path": str(record.get("source") or path),
                        "chunk_index": chunk.get("index"),
                        "page_number": page_number,
                        "media_path": chunk.get("media_path") or chunk_meta.get("media_path") or record_media_path or "",
                        "kind": "chunk",
                    }
                )
                sources.append(
                    ChatSource(
                        source_id=f"S{counter}",
                        label=f"{label} chunk {chunk.get('index') or counter}",
                        text=text.strip(),
                        metadata=chunk_meta,
                    )
                )
                counter += 1
    if not sources:
        compiled = _read_best_text(path)
        if compiled:
            sources.append(
                ChatSource(
                    source_id="S1",
                    label=path.name,
                    text=compiled,
                    metadata={"kind": "compiled-run", "path": str(path)},
                )
            )
    return sources


def _chunk_text(chunk: Dict[str, object]) -> str:
    return str(
        chunk.get("translated_text")
        or chunk.get("restored_text")
        or chunk.get("text")
        or ""
    )


def _record_text(record: Dict[str, object]) -> str:
    chunks = record.get("chunks")
    if isinstance(chunks, list):
        parts = [_chunk_text(chunk) for chunk in chunks if isinstance(chunk, dict)]
        return "\n\n".join(part for part in parts if part.strip())
    return ""


def _manifest_title(manifest: Dict[str, object], path: Path) -> str:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    title = str(metadata.get("title") or "").strip()
    return title or path.name


def _read_best_text(path: Path) -> Optional[str]:
    candidates = [
        path / "akshara_output.txt",
        path / "akshara_output.md",
        path / "raw_ocr.txt",
        path / "restored_text.txt",
        path / "stages" / "combined" / "recombined.txt",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return _read_text_file(candidate)
    if path.is_file():
        return _read_text_file(path)
    return None


def _session_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "chat"


def _read_text_file(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(data, dict):
            return str(data.get("text") or data.get("restored_text") or data.get("translated_text") or "")
        return str(data)
    if suffix == ".jsonl":
        lines = []
        for line in raw.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if isinstance(data, dict):
                lines.append(str(data.get("text") or data.get("restored_text") or data.get("translated_text") or ""))
            else:
                lines.append(str(data))
        return "\n\n".join(part for part in lines if part.strip())
    if suffix == ".html":
        return re.sub(r"<[^>]+>", " ", raw)
    return raw
