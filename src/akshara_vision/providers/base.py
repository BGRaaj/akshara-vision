from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

from akshara_vision.core.models import ModelSettings


@dataclass
class ProviderStatus:
    name: str
    available: bool
    detail: str
    models: List[str]


class TextProvider(Protocol):
    name: str

    def status(self) -> ProviderStatus:
        ...

    def restore_text(
        self,
        text: str,
        instruction: str,
        settings: ModelSettings,
        media_path: Optional[Path] = None,
    ) -> tuple[str, dict]:
        ...

