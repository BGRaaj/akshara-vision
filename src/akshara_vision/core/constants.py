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
    "json-detailed": "Detailed JSON with pages, layout, and asset metadata",
    "jsonl": "Chunked JSONL for pipelines and auditing",
    "yaml": "Human-readable metadata sidecar",
    "hocr": "Layout sidecar for OCR tooling",
    "alto": "ALTO XML sidecar for archive tooling",
    "pagexml": "PAGE XML sidecar for layout-aware workflows",
    "searchable-pdf": "Preferred HTML-backed PDF with calm reading layout",
    "docx-pdf": "DOCX-backed PDF export for office-style rendering",
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
    "Legal document",
    "Finance document",
    "Healthcare document",
    "Insurance document",
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

# Case-insensitive regexes that identify vision-capable (image input) models by name.
#
# These are a FALLBACK. providers/vision.py first asks the provider's own API for
# capability metadata (Anthropic, OpenRouter, Mistral, xAI, Ollama, LM Studio, ...)
# and only uses these rules where the provider's model list carries no capability
# info (OpenAI, Gemini, Groq, Together, Perplexity, DeepSeek, Cerebras, Jan).
# Providers with no entry (e.g. custom-openai-compatible) are "unknown": every model
# is shown with a warning, because guessing there would hide valid choices.
_LOCAL_VISION_NAMES = [
    r"llava", r"bakllava", r"moondream", r"minicpm-v", r"cogvlm", r"pixtral",
    r"vision", r"[-_.]vl", r"vl[:\-_]", r"gemma-?[34]", r"llama-?4", r"mistral-small-?3",
    r"internvl", r"smolvlm", r"ocr",
]

VISION_MODEL_PATTERNS: dict = {
    # OpenAI: /v1/models exposes only id/created/owned_by. o1-mini, o1-preview and
    # o3-mini are text-only, so the o-series is matched narrowly.
    "openai": [
        r"gpt-4o", r"gpt-4\.1", r"gpt-4\.5", r"gpt-4-turbo", r"gpt-4-vision",
        r"gpt-[5-9]", r"chatgpt-4o",
        r"^o1($|-pro|-\d{4})", r"^o3($|-pro|-\d{4})", r"^o4-mini",
    ],
    # Anthropic: every Claude 3+ model accepts images (API metadata is used first).
    "anthropic": [r"claude"],
    # Gemini: models.list has no modality field; all text-output gemini-* models
    # accept images.
    "gemini": [r"gemini"],
    "groq": [r"llama-4", r"scout", r"maverick", r"qwen3\.8", r"vision"],
    "together": [
        r"[-_.]vl", r"vl[:\-_]", r"vision", r"llama-4", r"gemma-[34]", r"pixtral",
    ],
    "mistral": [r"pixtral", r"mistral-small", r"mistral-medium"],
    "xai": [r"grok-[4-9]", r"vision"],
    "fireworks": [r"[-_.]vl", r"vision", r"llama4", r"gemma-?[34]"],
    "perplexity": [r"^sonar(-pro)?$"],
    # DeepSeek: only the experimental vision model accepts images.
    "deepseek": [r"vision"],
    "cerebras": [r"gemma-4"],
    "ollama": _LOCAL_VISION_NAMES,
    "lm-studio": _LOCAL_VISION_NAMES,
    "jan": _LOCAL_VISION_NAMES,
    "llama-cpp": _LOCAL_VISION_NAMES,
    "openai-compatible-local": _LOCAL_VISION_NAMES,
}

# Models matching these are dropped even if an include rule matched: they are not
# chat/OCR models (audio, image generation, embeddings, moderation, ...).
NON_VISION_MODEL_PATTERNS: dict = {
    "openai": [
        r"audio", r"realtime", r"transcribe", r"tts", r"image", r"embedding",
        r"moderation", r"search", r"instruct", r"whisper", r"dall-e",
    ],
    "anthropic": [r"claude-2", r"claude-instant", r"claude-1"],
    "gemini": [
        r"embedding", r"tts", r"live", r"audio", r"image", r"imagen", r"veo",
        r"aqa", r"transcribe", r"translate", r"robotics", r"gemini-1\.0",
    ],
    "groq": [r"guard", r"whisper", r"orpheus", r"tts"],
    "xai": [r"image", r"imagine", r"code"],
}

PROVIDER_TYPES = [
    "ollama",
    "openai-compatible-local",
    "lm-studio",
    "jan",
    "llama-cpp",
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
