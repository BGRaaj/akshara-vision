from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Protocol


@dataclass
class ExportResult:
    format: str
    path: Path
    available: bool = True
    detail: str = ""


class Exporter(Protocol):
    name: str

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        ...

