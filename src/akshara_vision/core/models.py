from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from akshara_vision.core.constants import DEFAULT_OUTPUT_FORMATS


@dataclass
class ModelSettings:
    provider: str = "mock"
    model: str = "offline-restoration-preview"
    endpoint: Optional[str] = None
    temperature: float = 0.1
    execution_mode: str = "balanced"
    context_window: Optional[int] = None
    generation_limit: Optional[int] = None


@dataclass
class WorkflowProfile:
    name: str = "default"
    workflow: str = "Full pipeline"
    document_type: str = "Book"
    source_language: str = "auto"
    output_language: str = "same"
    translation_mode: str = "off"
    output_formats: List[str] = field(default_factory=lambda: list(DEFAULT_OUTPUT_FORMATS))
    instruction_preset: str = "book_restoration_default"
    model: ModelSettings = field(default_factory=ModelSettings)
    locked: bool = False
    output_dir: str = "akshara-output"

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "workflow": self.workflow,
            "document_type": self.document_type,
            "source_language": self.source_language,
            "output_language": self.output_language,
            "translation_mode": self.translation_mode,
            "output_formats": list(self.output_formats),
            "instruction_preset": self.instruction_preset,
            "locked": self.locked,
            "output_dir": self.output_dir,
            "model": {
                "provider": self.model.provider,
                "model": self.model.model,
                "endpoint": self.model.endpoint or "",
                "temperature": self.model.temperature,
                "execution_mode": self.model.execution_mode,
                "context_window": self.model.context_window,
                "generation_limit": self.model.generation_limit,
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
            translation_mode=str(data.get("translation_mode") or "off"),
            output_formats=list(output_formats),
            instruction_preset=str(data.get("instruction_preset") or "book_restoration_default"),
            locked=bool(data.get("locked") or False),
            output_dir=str(data.get("output_dir") or "akshara-output"),
            model=ModelSettings(
                provider=str(model_data.get("provider") or "mock"),
                model=str(model_data.get("model") or "offline-restoration-preview"),
                endpoint=str(model_data.get("endpoint") or "") or None,
                temperature=float(model_data.get("temperature") or 0.1),
                execution_mode=str(model_data.get("execution_mode") or "balanced"),
                context_window=int(model_data.get("context_window")) if model_data.get("context_window") is not None and str(model_data.get("context_window")).strip() not in {"", "None"} else None,
                generation_limit=int(model_data.get("generation_limit")) if model_data.get("generation_limit") is not None and str(model_data.get("generation_limit")).strip() not in {"", "None"} else None,
            ),
        )


@dataclass
class InputSelection:
    raw: List[str]
    files: List[Path]
    missing: List[str] = field(default_factory=list)
    unsupported: List[Path] = field(default_factory=list)

    @property
    def supported_count(self) -> int:
        return len(self.files)

    def display_files(self, limit: int = 8) -> Iterable[str]:
        for path in self.files[:limit]:
            yield str(path)
        if len(self.files) > limit:
            yield f"... and {len(self.files) - limit} more"


@dataclass
class RunRequest:
    profile: WorkflowProfile
    inputs: InputSelection
    dry_run: bool = False
    resume: bool = True
