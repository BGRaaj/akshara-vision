import json
from pathlib import Path
from typing import Dict

from akshara_vision.exporters.base import ExportResult


class SidecarExporter:
    def __init__(self, name: str, suffix: str, label: str) -> None:
        self.name = name
        self.suffix = suffix
        self.label = label

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(self.suffix)
        payload = {
            "format": self.label,
            "note": "This sidecar is a portable placeholder until a dedicated OCR engine writes native layout data.",
            "text": text,
            "metadata": _public_metadata(metadata),
        }
        if self.suffix.endswith(".xml") or self.name in {"hocr", "alto", "pagexml"}:
            path.write_text(_xml_payload(self.label, text), encoding="utf-8")
        else:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return ExportResult(self.name, path)


class ReviewExporter:
    name = "review"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(".review.md")
        content = [
            "# Akshara Vision Review",
            "",
            "## Run",
            "",
            f"- Workflow: {_public_metadata(metadata).get('workflow')}",
            f"- Document type: {_public_metadata(metadata).get('document_type')}",
            f"- Provider: {_public_metadata(metadata).get('provider')}",
            f"- Model: {_public_metadata(metadata).get('model')}",
            "",
            "## Cleaned Text Preview",
            "",
            text[:4000],
            "",
        ]
        path.write_text("\n".join(content), encoding="utf-8")
        return ExportResult(self.name, path)


def _xml_payload(label: str, text: str) -> str:
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akshara-sidecar format="{label}">\n'
        f"  <text>{escaped}</text>\n"
        "</akshara-sidecar>\n"
    )


def _public_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key != "run_dir" and not str(key).startswith("_")
    }
