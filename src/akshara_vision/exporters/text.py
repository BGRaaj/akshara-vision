import html
import json
import zipfile
from pathlib import Path
from typing import Dict

from akshara_vision.exporters.base import ExportResult


class TextExporter:
    name = "txt"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        del metadata
        path = destination.with_suffix(".txt")
        path.write_text(text, encoding="utf-8")
        return ExportResult(self.name, path)


class MarkdownExporter:
    name = "md"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        title = metadata.get("title") or "Akshara Vision Output"
        path = destination.with_suffix(".md")
        path.write_text(f"# {title}\n\n{text}", encoding="utf-8")
        return ExportResult(self.name, path)


class HtmlExporter:
    name = "html"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        title = html.escape(str(metadata.get("title") or "Akshara Vision Output"))
        body = "\n".join(f"<p>{html.escape(part)}</p>" for part in text.split("\n\n") if part.strip())
        path = destination.with_suffix(".html")
        path.write_text(
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head><meta charset=\"utf-8\"><title>"
            f"{title}</title></head>\n<body>\n{body}\n</body>\n</html>\n",
            encoding="utf-8",
        )
        return ExportResult(self.name, path)


class JsonExporter:
    name = "json"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(".json")
        payload = {"text": text, "metadata": metadata}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ExportResult(self.name, path)


class JsonlExporter:
    name = "jsonl"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        del metadata
        path = destination.with_suffix(".jsonl")
        lines = []
        for index, paragraph in enumerate([part for part in text.split("\n\n") if part.strip()], start=1):
            lines.append(json.dumps({"index": index, "text": paragraph}, ensure_ascii=False))
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return ExportResult(self.name, path)


class YamlExporter:
    name = "yaml"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(".yaml")
        lines = ["text: |"]
        lines.extend(f"  {line}" for line in text.splitlines())
        lines.append("metadata:")
        for key, value in metadata.items():
            lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ExportResult(self.name, path)


class DocxExporter:
    name = "docx"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        del metadata
        path = destination.with_suffix(".docx")
        document_xml = _docx_document_xml(text)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _docx_content_types())
            archive.writestr("_rels/.rels", _docx_rels())
            archive.writestr("word/document.xml", document_xml)
        return ExportResult(self.name, path)


class EpubExporter:
    name = "epub"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        title = str(metadata.get("title") or "Akshara Vision Output")
        path = destination.with_suffix(".epub")
        body = "\n".join(f"<p>{html.escape(part)}</p>" for part in text.split("\n\n") if part.strip())
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", _epub_container())
            archive.writestr("OEBPS/content.xhtml", _epub_content(title, body))
            archive.writestr("OEBPS/package.opf", _epub_package(title))
        return ExportResult(self.name, path)


def _docx_document_xml(text: str) -> str:
    paragraphs = []
    for paragraph in text.split("\n\n"):
        escaped = html.escape(paragraph).replace("\n", "<w:br/>")
        paragraphs.append(f"<w:p><w:r><w:t>{escaped}</w:t></w:r></w:p>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}</w:body></w:document>"
    )


def _docx_content_types() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def _docx_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )


def _epub_container() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/package.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )


def _epub_content(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    )


def _epub_package(title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">'
        "<metadata><dc:title xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        f"{html.escape(title)}</dc:title></metadata>"
        '<manifest><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="content"/></spine></package>'
    )

