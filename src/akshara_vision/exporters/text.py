import html
import json
import re
import zipfile
from pathlib import Path
from typing import Dict, Optional

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
        title = _metadata_title(metadata)
        path = destination.with_suffix(".md")
        body = _markdown_body(text, metadata)
        path.write_text(f"# {title}\n\n{body}", encoding="utf-8")
        return ExportResult(self.name, path)


class HtmlExporter:
    name = "html"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        title = html.escape(_metadata_title(metadata))
        body = _html_body(text, metadata)
        path = destination.with_suffix(".html")
        path.write_text(
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{title}</title>\n"
            "<style>\n"
            "body{margin:0;background:#f8f5ed;color:#24170f;font-family:Georgia,'Times New Roman',serif;line-height:1.65;}\n"
            "main{max-width:780px;margin:0 auto;padding:56px 28px 72px;}\n"
            "h1{text-align:center;font-size:2.1rem;line-height:1.2;margin:0 0 2.5rem;}\n"
            "p{font-size:1.08rem;margin:0 0 1.05rem;}\n"
            ".page-marker{text-align:center;font-variant-numeric:oldstyle-nums;margin:2rem 0 1rem;}\n"
            ".figure-marker{border:1px solid #6f5a47;padding:.75rem 1rem;text-align:center;font-style:italic;margin:1.5rem 0;}\n"
            ".figure-marker img{max-width:100%;height:auto;display:block;margin:0 auto .75rem;}\n"
            ".contents table{width:100%;border-collapse:collapse;margin:1rem 0 2rem;}\n"
            ".contents td{border-bottom:1px solid #d8cdbc;padding:.35rem 0;}\n"
            ".contents td:last-child{text-align:right;white-space:nowrap;padding-left:1.5rem;}\n"
            ".multi-column{column-gap:2rem;}\n"
            "@media print{body{background:white;color:black}main{max-width:none;padding:0.75in}h1{page-break-after:avoid}}\n"
            "</style>\n"
            "</head>\n"
            f"<body><main><h1>{title}</h1>\n{body}\n</main></body>\n</html>\n",
            encoding="utf-8",
        )
        return ExportResult(self.name, path)


class JsonExporter:
    name = "json"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(".json")
        payload = {"text": text, "metadata": _public_metadata(metadata)}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ExportResult(self.name, path)


class JsonlExporter:
    name = "jsonl"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        del metadata
        path = destination.with_suffix(".jsonl")
        lines = []
        for index, paragraph in enumerate(
            [part for part in text.split("\n\n") if part.strip()], start=1
        ):
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
        for key, value in _public_metadata(metadata).items():
            lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ExportResult(self.name, path)


class DocxExporter:
    name = "docx"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(".docx")
        document_xml = _docx_document_xml(text, _metadata_title(metadata))
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _docx_content_types())
            archive.writestr("_rels/.rels", _docx_rels())
            archive.writestr("word/document.xml", document_xml)
        return ExportResult(self.name, path)


class EpubExporter:
    name = "epub"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        title = _metadata_title(metadata)
        path = destination.with_suffix(".epub")
        body = _html_body(text, metadata)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", _epub_container())
            archive.writestr("OEBPS/content.xhtml", _epub_content(title, body))
            archive.writestr("OEBPS/package.opf", _epub_package(title))
        return ExportResult(self.name, path)


def _metadata_title(metadata: Dict[str, object]) -> str:
    title = str(metadata.get("title") or "").strip()
    return title or "Akshara Vision Output"


def _public_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key != "run_dir" and not str(key).startswith("_")
    }


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _markdown_body(text: str, metadata: Optional[Dict[str, object]] = None) -> str:
    structured = _markdown_structured_body(metadata)
    plain = _markdown_plain_body(text, _export_base_dir(metadata))
    if structured:
        return (structured + "\n\n" + plain).strip() + "\n"
    return plain


def _markdown_plain_body(text: str, base_dir: Optional[Path] = None) -> str:
    parts = []
    for paragraph in _paragraphs(text):
        image = _parse_image_marker(paragraph)
        if image:
            alt, path = image
            if _asset_exists(path, base_dir):
                parts.append(f"![{alt}]({path})")
        elif paragraph.lower().startswith("[image:"):
            parts.append(f"> {paragraph}")
        else:
            parts.append(paragraph)
    return "\n\n".join(parts).strip() + "\n"


def _html_body(text: str, metadata: Optional[Dict[str, object]] = None) -> str:
    structured = _html_structured_body(metadata)
    plain = _html_plain_body(text, _export_base_dir(metadata))
    if structured:
        return structured + "\n" + plain
    return plain


def _html_plain_body(text: str, base_dir: Optional[Path] = None) -> str:
    body = []
    for paragraph in _paragraphs(text):
        escaped = html.escape(paragraph).replace("\n", "<br />\n")
        stripped = paragraph.strip()
        image = _parse_image_marker(stripped)
        if image:
            alt, path = image
            if _asset_exists(path, base_dir):
                body.append(
                    '<figure class="figure-marker">'
                    f'<img src="{html.escape(path, quote=True)}" alt="{html.escape(alt, quote=True)}" />'
                    f"<figcaption>{html.escape(alt)}</figcaption>"
                    "</figure>"
                )
        elif stripped.lower().startswith("[image:"):
            body.append(f'<figure class="figure-marker">{escaped}</figure>')
        elif _looks_like_page_marker(stripped):
            body.append(f'<p class="page-marker">{escaped}</p>')
        else:
            body.append(f"<p>{escaped}</p>")
    return "\n".join(body)


def _semantic_units(metadata: Optional[Dict[str, object]]) -> list[Dict[str, object]]:
    if not metadata:
        return []
    structure = metadata.get("document_structure")
    if not isinstance(structure, dict):
        return []
    units = structure.get("semantic_units")
    return [unit for unit in units if isinstance(unit, dict)] if isinstance(units, list) else []


def _markdown_structured_body(metadata: Optional[Dict[str, object]]) -> str:
    units = _semantic_units(metadata)
    if not units:
        return ""
    parts = []
    contents = []
    footnotes = []
    for unit in units:
        role = str(unit.get("role") or "body")
        heading = _unit_heading(unit)
        if role == "contents":
            entries = unit.get("contents_entries") if isinstance(unit.get("contents_entries"), list) else []
            if entries:
                contents.extend(entries)
                continue
        if role in {"title", "title-page"} and heading:
            parts.append(f"## {heading}")
        elif heading and _role_deserves_heading(role):
            parts.append(f"## {heading}")
        footnotes.extend(unit.get("footnotes") if isinstance(unit.get("footnotes"), list) else [])
    if contents:
        lines = ["## Contents", ""]
        lines.extend(
            f"- {entry.get('title', '').strip()} {entry.get('page', '').strip()}".rstrip()
            for entry in contents
            if isinstance(entry, dict)
        )
        parts.insert(0, "\n".join(lines).strip())
    if footnotes:
        lines = ["## Notes", ""]
        lines.extend(
            f"- {note.get('marker', '').strip()}: {note.get('text', '').strip()}"
            for note in footnotes
            if isinstance(note, dict)
        )
        parts.append("\n".join(lines).strip())
    return "\n\n".join(part for part in parts if part).strip() + ("\n" if parts else "")


def _html_structured_body(metadata: Optional[Dict[str, object]]) -> str:
    units = _semantic_units(metadata)
    if not units:
        return ""
    blocks = []
    contents = []
    footnotes = []
    for unit in units:
        role = str(unit.get("role") or "body")
        heading = _unit_heading(unit)
        if role == "contents":
            entries = unit.get("contents_entries") if isinstance(unit.get("contents_entries"), list) else []
            contents.extend(entry for entry in entries if isinstance(entry, dict))
            continue
        if role in {"title", "title-page"} and heading:
            blocks.append(f'<section class="title-page"><h2>{html.escape(heading)}</h2></section>')
        elif heading and _role_deserves_heading(role):
            blocks.append(
                f'<section class="{html.escape(role)}"><h2>{html.escape(heading)}</h2>'
                "</section>"
            )
        footnotes.extend(unit.get("footnotes") if isinstance(unit.get("footnotes"), list) else [])
    if contents:
        rows = []
        for entry in contents:
            title = html.escape(str(entry.get("title") or ""))
            page = html.escape(str(entry.get("page") or ""))
            rows.append(f"<tr><td>{title}</td><td>{page}</td></tr>")
        blocks.insert(0, '<section class="contents"><h2>Contents</h2><table>' + "".join(rows) + "</table></section>")
    if footnotes:
        notes = []
        for note in footnotes:
            marker = html.escape(str(note.get("marker") or ""))
            body = html.escape(str(note.get("text") or ""))
            notes.append(f"<li><span>{marker}</span> {body}</li>")
        blocks.append('<section class="notes"><h2>Notes</h2><ol>' + "".join(notes) + "</ol></section>")
    return "\n".join(blocks)


def _role_deserves_heading(role: str) -> bool:
    return role in {
        "preface",
        "section",
        "chapter",
        "index",
        "appendix",
        "abstract",
        "references",
        "bibliography",
        "editorial",
        "feature",
        "article",
        "headline",
        "masthead",
        "folio",
        "marginalia",
        "colophon",
        "date-place",
        "salutation",
        "signature",
        "cover-sheet",
        "folder-label",
        "record",
    }


def _unit_heading(unit: Dict[str, object]) -> str:
    headings = unit.get("headings")
    if isinstance(headings, list):
        for heading in headings:
            text = str(heading).strip()
            if text:
                return text
    candidates = unit.get("title_candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            text = str(candidate).strip()
            if text:
                return text
    return ""


def _parse_image_marker(text: str) -> Optional[tuple[str, str]]:
    match = re.match(r"^\[image:\s*(?P<label>.+?)\s*\|\s*(?P<path>[^\]]+)\]$", text.strip(), re.I)
    if not match:
        return None
    label = match.group("label").strip() or "Figure"
    path = match.group("path").strip()
    if not path:
        return None
    return label, path


def _export_base_dir(metadata: Optional[Dict[str, object]]) -> Optional[Path]:
    if not metadata:
        return None
    run_dir = metadata.get("run_dir")
    if isinstance(run_dir, str) and run_dir.strip():
        return Path(run_dir)
    return None


def _asset_exists(path: str, base_dir: Optional[Path]) -> bool:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.exists()
    if base_dir is not None:
        return (base_dir / candidate).exists()
    return True


def _looks_like_page_marker(text: str) -> bool:
    normalized = text.strip().lower().replace("page ", "")
    if not normalized:
        return False
    roman = set("ivxlcdm")
    return normalized.isdigit() or all(char in roman for char in normalized)


def _docx_document_xml(text: str, title: str) -> str:
    paragraphs = [
        "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
        "<w:r><w:rPr><w:b/><w:sz w:val=\"32\"/></w:rPr>"
        f"<w:t>{html.escape(title)}</w:t></w:r></w:p>"
    ]
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
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
        '<metadata><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"{html.escape(title)}</dc:title></metadata>"
        '<manifest><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="content"/></spine></package>'
    )
