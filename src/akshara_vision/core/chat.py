from __future__ import annotations

import copy
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from akshara_vision.core.config import ConfigStore
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import RunRequest, WorkflowProfile
from akshara_vision.core.pipeline import run_pipeline
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

    def source_ids(self) -> List[str]:
        return [source.source_id for source in self.sources]


def build_chat_bundle(
    raw_inputs: Optional[Iterable[str]],
    profile: Optional[WorkflowProfile] = None,
    recursive: bool = False,
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
                raise RuntimeError("No supported chat inputs were found.")
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

    bundle_title = next((title for title in titles if title.strip()), "Document")
    return ChatBundle(title=bundle_title, sources=collected, profile=profile)


def answer_question(
    bundle: ChatBundle,
    question: str,
    system_prompt: Optional[str] = None,
    history: Optional[Sequence[Tuple[str, str]]] = None,
) -> tuple[str, dict, List[ChatSource]]:
    prompt_sources = _select_relevant_sources(bundle.sources, question)
    if not prompt_sources:
        raise RuntimeError("No grounded sources are available for this question.")
    system_prompt = (system_prompt or _default_chat_instruction(bundle)).strip()
    prompt = _build_chat_prompt(bundle, question, prompt_sources, history)
    provider = get_provider(bundle.profile.model.provider)
    response, usage = provider.restore_text(prompt, system_prompt, bundle.profile.model)
    return (response or "").strip(), usage or {}, prompt_sources


def chat_session_path(raw_inputs: Iterable[str]) -> Optional[Path]:
    for item in raw_inputs:
        path = Path(str(item)).expanduser()
        if _looks_like_run_folder(path):
            return path / "chat_session.json"
    return None


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


def save_chat_history(path: Optional[Path], history: Sequence[Tuple[str, str]]) -> None:
    if path is None:
        return
    payload = {
        "version": 1,
        "turns": [
            {"question": question, "answer": answer}
            for question, answer in history[-24:]
        ],
    }
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
    return (
        "You are answering questions about a restored document corpus.\n"
        "Use only the provided sources and the conversation history.\n"
        "If the answer is not supported by the sources, say that clearly.\n"
        "Cite claims inline with source ids like [S1], [S2], or [S1/S3].\n"
        "Keep the answer concise, grounded, and factual.\n"
        f"Document title: {bundle.title}\n"
    )


def _build_chat_prompt(
    bundle: ChatBundle,
    question: str,
    sources: Sequence[ChatSource],
    history: Optional[Sequence[Tuple[str, str]]] = None,
) -> str:
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
    parts.extend(
        [
            "QUESTION",
            question.strip(),
            "",
            "INSTRUCTIONS",
            "Answer only from the sources above.",
            "If you reference a fact, cite the relevant source ids inline.",
            "If the question asks for a list or extraction, preserve the document's structure where possible.",
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


def _select_relevant_sources(sources: Sequence[ChatSource], question: str, limit: int = 10) -> List[ChatSource]:
    question_terms = _question_terms(question)
    scored: List[tuple[int, ChatSource]] = []
    for source in sources:
        text = f"{source.label}\n{source.text}".lower()
        score = sum(1 for term in question_terms if term in text)
        score += int(float(source.metadata.get("confidence") or 0) * 10)
        scored.append((score, source))
    ranked = sorted(scored, key=lambda item: (-item[0], item[1].source_id))
    return [source for score, source in ranked[:limit] if score > 0] or list(sources[: min(limit, len(sources))])


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
    return path.suffix.lower() in {".txt", ".md", ".html", ".json", ".jsonl", ".yaml", ".yml"}


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
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                text = _chunk_text(chunk)
                if not text.strip():
                    continue
                chunk_meta = dict(chunk.get("semantic_tags") or {})
                chunk_meta.update(
                    {
                        "source": label,
                        "path": str(record.get("source") or path),
                        "chunk_index": chunk.get("index"),
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
