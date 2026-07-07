import os
import sys
from pathlib import Path

APP_NAME = "Akshara Vision"
APP_SLUG = "akshara-vision"
PRIMARY_COMMAND = "akshara"
SHORT_COMMAND = "akv"

SUPPORTED_INPUT_EXTENSIONS = {
    ".pdf": "PDF",
    ".jpg": "Image",
    ".jpeg": "Image",
    ".png": "Image",
    ".webp": "Image",
    ".tif": "Image",
    ".tiff": "Image",
    ".bmp": "Image",
    ".txt": "Text/OCR",
    ".md": "Text/OCR",
    ".html": "Text/OCR",
    ".hocr": "Text/OCR",
    ".xml": "Text/OCR",
    ".json": "Text/OCR",
    ".zip": "Archive",
    ".csv": "Manifest",
}

OUTPUT_FORMATS = {
    "txt": "Plain copy-paste text for archival review",
    "md": "Readable markdown for GitHub and hand editing",
    "html": "Browser reading with calm typography and figures",
    "docx": "Editorial handoff with document-style structure",
    "epub": "E-reader friendly book-style reading",
    "json": "Structured JSON for assembly and automation",
    "jsonl": "Chunked JSONL for pipelines and auditing",
    "yaml": "Human-readable metadata sidecar",
    "hocr": "Layout sidecar for OCR tooling",
    "alto": "ALTO XML sidecar for archive tooling",
    "pagexml": "PAGE XML sidecar for layout-aware workflows",
    "searchable-pdf": "Text-first PDF with calm reading layout",
    "image-pdf": "Visually composed PDF with clean page rendering",
    "review": "Review notes and before/after diff",
}

DEFAULT_OUTPUT_FORMATS = ["txt"]

TRANSLATION_MODES = [
    "auto",
    "off",
    "same-language-cleanup",
    "translate",
    "bilingual",
    "transliterate",
    "metadata-only",
]

TRANSLATION_FAILURE_REASONS = [
    "blank page or no readable text",
    "source unreadable or too blurry",
    "page rendering or OCR dependency missing",
    "model context or output limit reached",
    "provider timeout",
    "model does not support the selected script or language",
    "model returned malformed output",
    "network or API error",
]

DOCUMENT_TYPES = [
    "Book",
    "Manuscript",
    "Newspaper",
    "Magazine",
    "Journal article",
    "Letter",
    "Archive bundle",
    "General",
]

WORKFLOWS = [
    "Full pipeline",
    "Restore pages",
    "OCR only",
    "Clean OCR text",
    "Translate",
    "Custom",
]


EXECUTION_MODES = [
    "fast",
    "balanced",
    "quality",
]

PROVIDER_TYPES = [
    "ollama",
    "openai-compatible-local",
    "lm-studio",
    "jan",
    "llama-cpp",
    "sarvam",
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "groq",
    "mistral",
    "together",
    "fireworks",
    "perplexity",
    "deepseek",
    "xai",
    "cerebras",
    "custom-openai-compatible",
    "mock",
]


def default_config_dir() -> Path:
    """Return the user config directory without importing platform-specific packages."""
    override = os.environ.get("AKSHARA_CONFIG_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or "~"
        return Path(base).expanduser() / "akshara-vision"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "akshara-vision"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
        return Path(base).expanduser() / "akshara-vision"
