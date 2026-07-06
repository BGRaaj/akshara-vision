from pathlib import Path
from typing import Dict

from akshara_vision.exporters.base import ExportResult


class PdfNoteExporter:
    def __init__(self, name: str, suffix: str, description: str) -> None:
        self.name = name
        self.suffix = suffix
        self.description = description

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        del text
        path = destination.with_suffix(self.suffix)
        path.write_text(
            f"{self.description}\n\n"
            "Native PDF generation requires optional PDF/OCR dependencies. "
            "Run `akshara doctor` for setup guidance.\n\n"
            f"Run metadata: {metadata}\n",
            encoding="utf-8",
        )
        return ExportResult(
            self.name,
            path,
            available=False,
            detail="PDF export needs optional system dependencies.",
        )
