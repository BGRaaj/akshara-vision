from typing import Dict

from akshara_vision.exporters.archive import ReviewExporter, SidecarExporter
from akshara_vision.exporters.pdf import DocxPdfExporter, PdfExporter
from akshara_vision.exporters.text import (
    DocxExporter,
    EpubExporter,
    HtmlExporter,
    JsonExporter,
    JsonDetailedExporter,
    JsonlExporter,
    MarkdownExporter,
    TextExporter,
    YamlExporter,
)


def exporter_registry() -> Dict[str, object]:
    return {
        "txt": TextExporter(),
        "md": MarkdownExporter(),
        "html": HtmlExporter(),
        "docx": DocxExporter(),
        "epub": EpubExporter(),
        "json": JsonExporter(),
        "json-detailed": JsonDetailedExporter(),
        "jsonl": JsonlExporter(),
        "yaml": YamlExporter(),
        "hocr": SidecarExporter("hocr", ".hocr", "hOCR"),
        "alto": SidecarExporter("alto", ".alto.xml", "ALTO XML"),
        "pagexml": SidecarExporter("pagexml", ".page.xml", "PAGE XML"),
        "searchable-pdf": PdfExporter(
            "searchable-pdf",
            ".searchable.pdf",
            "Searchable PDF Export",
        ),
        "docx-pdf": DocxPdfExporter("docx-pdf", ".docx.pdf"),
        "review": ReviewExporter(),
    }
