from dataclasses import dataclass
from typing import List, Protocol

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

    def restore_text(self, text: str, instruction: str, settings: ModelSettings) -> str:
        ...

