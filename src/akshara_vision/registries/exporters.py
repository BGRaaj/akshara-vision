from typing import Dict

from akshara_vision.exporters.archive import ReviewExporter, SidecarExporter
from akshara_vision.exporters.pdf import PdfNoteExporter
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
        "searchable-pdf": PdfNoteExporter(
            "searchable-pdf",
            ".searchable-pdf.txt",
            "Searchable PDF export was requested.",
        ),
        "image-pdf": PdfNoteExporter(
            "image-pdf",
            ".image-pdf.txt",
            "Cleaned image PDF export was requested.",
        ),
        "review": ReviewExporter(),
    }

