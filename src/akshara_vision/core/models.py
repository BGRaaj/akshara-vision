from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from akshara_vision.core.constants import DEFAULT_OUTPUT_FORMATS, TRANSLATION_MODES


@dataclass
class ModelSettings:
    provider: str = "mock"
    model: str = "offline-restoration-preview"
    endpoint: Optional[str] = None
    temperature: float = 0.1
    execution_mode: str = "balanced"
    context_window: Optional[int] = None
    generation_limit: Optional[int] = None
    request_timeout_seconds: Optional[int] = None


@dataclass
class WorkflowProfile:
    name: str = "default"
    workflow: str = "Full pipeline"
    document_type: str = "Book"
    source_language: str = "auto"
    output_language: str = "same"
    translation_mode: str = "auto"
    output_formats: List[str] = field(default_factory=lambda: list(DEFAULT_OUTPUT_FORMATS))
    instruction_preset: str = "book_restoration_default"
    model: ModelSettings = field(default_factory=ModelSettings)
    locked: bool = False
    output_dir: str = "akshara-output"
    extract_figures: bool = False
    language_policy: str = "preserve-detected"
    layout_backend: str = "native"

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "workflow": self.workflow,
            "document_type": self.document_type,
            "source_language": self.source_language,
            "output_language": self.output_language,
            "translation_mode": self.normalized_translation_mode(),
            "output_formats": list(self.output_formats),
            "instruction_preset": self.instruction_preset,
            "locked": self.locked,
            "output_dir": self.output_dir,
            "extract_figures": self.extract_figures,
            "language_policy": normalize_language_policy(self.language_policy),
            "layout_backend": normalize_layout_backend(self.layout_backend),
            "model": {
                "provider": self.model.provider,
                "model": self.model.model,
                "endpoint": self.model.endpoint or "",
                "temperature": self.model.temperature,
                "execution_mode": self.model.execution_mode,
                "context_window": self.model.context_window,
                "generation_limit": self.model.generation_limit,
                "request_timeout_seconds": self.model.request_timeout_seconds,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "WorkflowProfile":
        model_data = data.get("model") or {}
        if not isinstance(model_data, dict):
            model_data = {}
        output_formats = data.get("output_formats") or DEFAULT_OUTPUT_FORMATS
        if isinstance(output_formats, str):
            output_formats = [item.strip() for item in output_formats.split(",") if item.strip()]
        return cls(
            name=str(data.get("name") or "default"),
            workflow=str(data.get("workflow") or "Full pipeline"),
            document_type=str(data.get("document_type") or "Book"),
            source_language=str(data.get("source_language") or "auto"),
            output_language=str(data.get("output_language") or "same"),
            translation_mode=normalize_translation_mode(data.get("translation_mode")),
            output_formats=list(output_formats),
            instruction_preset=str(data.get("instruction_preset") or "book_restoration_default"),
            locked=bool(data.get("locked") or False),
            output_dir=str(data.get("output_dir") or "akshara-output"),
            extract_figures=bool(data.get("extract_figures") or False),
            language_policy=normalize_language_policy(data.get("language_policy")),
            layout_backend=normalize_layout_backend(data.get("layout_backend")),
            model=ModelSettings(
                provider=str(model_data.get("provider") or "mock"),
                model=str(model_data.get("model") or "offline-restoration-preview"),
                endpoint=str(model_data.get("endpoint") or "") or None,
                temperature=float(model_data.get("temperature") or 0.1),
                execution_mode=str(model_data.get("execution_mode") or "balanced"),
                context_window=int(model_data.get("context_window"))
                if model_data.get("context_window") is not None
                and str(model_data.get("context_window")).strip() not in {"", "None"}
                else None,
                generation_limit=int(model_data.get("generation_limit"))
                if model_data.get("generation_limit") is not None
                and str(model_data.get("generation_limit")).strip() not in {"", "None"}
                else None,
                request_timeout_seconds=int(model_data.get("request_timeout_seconds"))
                if model_data.get("request_timeout_seconds") is not None
                and str(model_data.get("request_timeout_seconds")).strip() not in {"", "None"}
                else None,
            ),
        )

    def normalized_translation_mode(self) -> str:
        return normalize_translation_mode(self.translation_mode)

    def translation_required(self) -> bool:
        return translation_required_for_languages(
            self.source_language,
            self.output_language,
            self.normalized_translation_mode(),
        )

    def effective_translation_mode(self) -> str:
        return effective_translation_mode(
            self.source_language,
            self.output_language,
            self.normalized_translation_mode(),
        )

    def sync_translation_defaults(self) -> None:
        self.translation_mode = normalized_profile_translation_mode(
            self.source_language,
            self.output_language,
            self.normalized_translation_mode(),
        )


@dataclass
class InputSelection:
    raw: List[str]
    files: List[Path]
    missing: List[str] = field(default_factory=list)
    unsupported: List[Path] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)

    @property
    def supported_count(self) -> int:
        return len(self.files)

    def display_files(self, limit: int = 8) -> Iterable[str]:
        for path in self.files[:limit]:
            yield self.label_for(path)
        if len(self.files) > limit:
            yield f"... and {len(self.files) - limit} more"

    def label_for(self, path: Path) -> str:
        resolved = str(path.expanduser().resolve())
        return self.labels.get(resolved, path.name)


@dataclass
class RunRequest:
    profile: WorkflowProfile
    inputs: InputSelection
    dry_run: bool = False
    resume: bool = True
    resume_run_dir: Optional[str] = None


def normalize_translation_mode(value: object) -> str:
    mode = str(value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "on": "translate",
        "yes": "translate",
        "true": "translate",
        "cleanup": "same-language-cleanup",
        "same-language": "same-language-cleanup",
        "same-language-cleanup": "same-language-cleanup",
        "translit": "transliterate",
        "transliteration": "transliterate",
        "metadata": "metadata-only",
        "metadata-only": "metadata-only",
        "off": "off",
        "auto": "auto",
        "translate": "translate",
        "bilingual": "bilingual",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in TRANSLATION_MODES else "auto"


def _normalize_language(value: object) -> str:
    language = str(value or "").strip().lower().replace("_", "-")
    if not language:
        return "auto"
    return language


def translation_required_for_languages(
    source_language: object, output_language: object, translation_mode: object
) -> bool:
    mode = normalize_translation_mode(translation_mode)
    if mode in {"off", "same-language-cleanup", "metadata-only"}:
        return False
    if mode in {"translate", "bilingual", "transliterate"}:
        return True
    source = _normalize_language(source_language)
    target = _normalize_language(output_language)
    if target in {"same", "auto"}:
        return False
    if source in {"auto", "unknown"}:
        return True
    return source != target


def effective_translation_mode(
    source_language: object, output_language: object, translation_mode: object
) -> str:
    mode = normalize_translation_mode(translation_mode)
    if mode != "auto":
        return mode
    return "translate" if translation_required_for_languages(source_language, output_language, "auto") else "off"


def normalized_profile_translation_mode(
    source_language: object, output_language: object, translation_mode: object
) -> str:
    mode = normalize_translation_mode(translation_mode)
    if mode == "off" and translation_required_for_languages(source_language, output_language, "auto"):
        return "auto"
    if mode == "auto" and not translation_required_for_languages(source_language, output_language, "auto"):
        return "auto"
    return mode


def normalize_language_policy(value: object) -> str:
    policy = str(value or "preserve-detected").strip().lower().replace("_", "-")
    aliases = {
        "all": "preserve-detected",
        "mixed": "preserve-detected",
        "preserve": "preserve-detected",
        "preserve-detected": "preserve-detected",
        "detect": "preserve-detected",
        "detected": "preserve-detected",
        "strict": "strict-source",
        "source-only": "strict-source",
        "strict-source": "strict-source",
        "input-only": "strict-source",
    }
    return aliases.get(policy, "preserve-detected")


def normalize_layout_backend(value: object) -> str:
    backend = str(value or "native").strip().lower().replace("_", "-")
    aliases = {
        "default": "native",
        "heuristic": "native",
        "akshara": "native",
        "akshara-native": "native",
        "none": "off",
        "disabled": "off",
        "disable": "off",
    }
    backend = aliases.get(backend, backend)
    if backend in {"native", "off"}:
        return backend
    if backend and all(char.isalnum() or char in {"-"} for char in backend):
        return backend
    return "native"
