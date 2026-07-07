from typing import Dict

from akshara_vision.exporters.archive import ReviewExporter, SidecarExporter
from akshara_vision.exporters.pdf import PdfExporter
from akshara_vision.exporters.text import (
    DocxExporter,
    EpubExporter,
    HtmlExporter,
    JsonExporter,
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
        "image-pdf": PdfExporter(
            "image-pdf",
            ".image.pdf",
            "Cleaned Image PDF Export",
            kind="image",
        ),
        "review": ReviewExporter(),
    }
