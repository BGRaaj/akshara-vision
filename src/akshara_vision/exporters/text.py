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
        path = destination.with_suffix(".md")
        body = _markdown_body(text, metadata)
        path.write_text(body, encoding="utf-8")
        return ExportResult(self.name, path)


class HtmlExporter:
    name = "html"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        title = html.escape(_metadata_title(metadata))
        body = _html_body(text, metadata)
        document_class = _document_class(metadata)
        path = destination.with_suffix(".html")
        path.write_text(
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{title}</title>\n"
            "<style>\n"
            "body{margin:0;background:#f4efe4;color:#24170f;font-family:Georgia,'Iowan Old Style','Palatino Linotype','Times New Roman',serif;line-height:1.7;text-rendering:optimizeLegibility;-webkit-font-smoothing:antialiased;}\n"
            "main{max-width:8.27in;margin:0 auto;padding:0;}\n"
            ".document-page{min-height:11.69in;box-sizing:border-box;padding:0.88in 0.88in 0.92in;position:relative;break-after:page;page-break-after:always;background:#fffdf8;}\n"
            ".document-page:last-child{break-after:auto;page-break-after:auto;}\n"
            ".page-content{min-height:9.62in;}\n"
            ".page-footer{position:absolute;right:0.88in;bottom:0.47in;font-size:.82rem;letter-spacing:.045em;color:#726356;text-transform:uppercase;}\n"
            ".blank-page .page-content{min-height:10.02in;}\n"
            ".document-magazine,.document-newspaper{max-width:920px;}\n"
            ".document-manuscript{font-family:'Times New Roman',Georgia,serif;line-height:1.85;}\n"
            ".document-legal-document,.document-finance-document,.document-healthcare-document,.document-insurance-document{max-width:860px;}\n"
            ".layout-multi-column .multi-column{column-gap:2.2rem;column-rule:1px solid #d8cdbc;}\n"
            ".layout-structured-list .contents table,.layout-structured-list .notes ol{max-width:32rem;margin-left:auto;margin-right:auto;}\n"
            "h1{text-align:center;font-size:2.55rem;line-height:1.1;margin:0 0 1.2rem;letter-spacing:.01em;font-weight:700;}\n"
            "h2{font-size:1.28rem;line-height:1.28;margin:1.85rem 0 .95rem;}\n"
            ".semantic-title h2,.semantic-title-page h2,.semantic-cover h2,.semantic-cover-sheet h2{font-size:1.95rem;text-align:center;margin:0 0 1.55rem;letter-spacing:.01em;}\n"
            ".semantic-preface h2,.semantic-foreword h2,.semantic-introduction h2,.semantic-editorial h2,.semantic-abstract h2{font-size:1.16rem;letter-spacing:.04em;text-transform:uppercase;}\n"
            ".semantic-chapter h2,.semantic-section h2,.semantic-article h2,.semantic-feature h2,.semantic-record h2,.semantic-letter h2{font-size:1.38rem;}\n"
            ".semantic-index h2,.semantic-appendix h2,.semantic-references h2,.semantic-bibliography h2,.semantic-footnotes h2{font-size:1.08rem;letter-spacing:.02em;}\n"
            "p{font-size:1.05rem;margin:0 0 1rem;orphans:3;widows:3;}\n"
            "ul,ol{margin:0 0 1rem 1.3rem;padding:0;}\n"
            "li{margin:0 0 .45rem;}\n"
            "blockquote{margin:1.1rem 0;padding:0 0 0 .95rem;border-left:2px solid #d8cdbc;color:#3f3026;font-style:italic;}\n"
            "table{font-size:1rem;line-height:1.5;}\n"
            ".page-marker{text-align:center;font-variant-numeric:oldstyle-nums;margin:1.65rem 0 .95rem;color:#705c4d;}\n"
            ".page-break{break-after:page;page-break-after:always;height:0;margin:0;padding:0;border:0;}\n"
            ".figure-marker{break-inside:avoid;padding:0;text-align:center;font-style:normal;margin:1.45rem auto;}\n"
            ".figure-marker img{max-width:100%;height:auto;display:block;margin:0 auto;}\n"
            ".figure-full-width{width:100%;}.figure-large{width:80%;}.figure-medium{width:62%;}.figure-small{width:44%;}.figure-wide{width:100%;}.figure-tall{width:54%;}\n"
            ".zone-top-left,.zone-middle-left,.zone-bottom-left{margin-left:0;}.zone-top-right,.zone-middle-right,.zone-bottom-right{margin-right:0;}\n"
            ".contents table{width:100%;border-collapse:collapse;margin:1rem 0 1.85rem;}\n"
            ".contents td{border-bottom:1px solid #d8cdbc;padding:.42rem 0;vertical-align:top;}\n"
            ".contents td:first-child{padding-right:1rem;}\n"
            ".contents td:last-child{text-align:right;white-space:nowrap;padding-left:1.4rem;color:#6c5a4f;}\n"
            ".multi-column{column-gap:2.15rem;column-fill:auto;}\n"
            ".document-page h1:first-child{margin-top:0;}\n"
            ".document-page > .page-content > :first-child{margin-top:0;}\n"
            ".document-page > .page-content > :last-child{margin-bottom:0;}\n"
            "@page{size:A4;margin:0;}\n"
            "@media print{body{background:white;color:black}main{max-width:none}.document-page{background:white}h1{page-break-after:avoid}}\n"
            "</style>\n"
            "</head>\n"
            f'<body><main class="{_document_classes(metadata)}" data-document-type="{html.escape(_document_type_slug(metadata), quote=True)}" data-document-title="{title}">\n{body}\n</main></body>\n</html>\n',
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
        media_entries = _docx_media_entries(metadata)
        document_xml = _docx_document_xml(text, _metadata_title(metadata), metadata, media_entries)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _docx_content_types(media_entries))
            archive.writestr("_rels/.rels", _docx_package_rels())
            archive.writestr("word/_rels/document.xml.rels", _docx_document_rels(media_entries))
            archive.writestr("word/document.xml", document_xml)
            for entry in media_entries:
                archive.write(entry["source"], f"word/{entry['target']}")
        return ExportResult(self.name, path)


class EpubExporter:
    name = "epub"

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(".epub")
        epub_assets = _epub_asset_entries(metadata)
        epub_metadata = dict(metadata)
        epub_metadata["_asset_path_map"] = {
            original: packaged for original, packaged, _source in epub_assets
        }
        body = _html_body(text, epub_metadata)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", _epub_container())
            archive.writestr("OEBPS/content.xhtml", _epub_content(_metadata_title(metadata), body, metadata))
            archive.writestr("OEBPS/package.opf", _epub_package(_metadata_title(metadata), epub_assets))
            for _original, packaged, source in epub_assets:
                archive.write(source, f"OEBPS/{packaged}")
        return ExportResult(self.name, path)


def _metadata_title(metadata: Dict[str, object]) -> str:
    title = str(metadata.get("title") or "").strip()
    return title or "Untitled"


def _public_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key != "run_dir" and not str(key).startswith("_")
    }


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _markdown_body(text: str, metadata: Optional[Dict[str, object]] = None) -> str:
    pages = [part for part in str(text).split("\f")]
    page_count = max(len(pages), 1)
    rendered_pages = []
    for index, page in enumerate(pages, start=1):
        unit = _semantic_unit_for_page(metadata, index, page_count)
        page_body = _markdown_page_body(
            _text_with_missing_asset_markers(page, metadata), metadata, unit, index, page_count
        )
        if not page_body.strip():
            page_body = "[blank page]"
        rendered_pages.append(f"<!-- Page {index} of {page_count} -->\n\n{page_body}".strip())
    return "\n\n\f\n\n".join(rendered_pages).strip() + "\n"


def _publication_credits(metadata: Optional[Dict[str, object]]) -> list[str]:
    if not metadata:
        return []
    structure = metadata.get("document_structure")
    if not isinstance(structure, dict):
        return []
    credits = []
    for key in ("contributors", "publishers"):
        values = structure.get(key)
        if isinstance(values, list):
            credits.extend(str(value).strip() for value in values if str(value).strip())
    return _unique_strings(credits, 6)


def _html_credits(metadata: Optional[Dict[str, object]]) -> str:
    credits = _publication_credits(metadata)
    if not credits:
        return ""
    return '<section class="credits">' + "".join(
        f"<p>{html.escape(credit)}</p>" for credit in credits
    ) + "</section>\n"


def _unique_strings(values: list[str], limit: int) -> list[str]:
    seen = set()
    result = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _markdown_plain_body(text: str, base_dir: Optional[Path] = None) -> str:
    parts = []
    for paragraph in _paragraphs(text):
        if paragraph == "\f":
            parts.append("\n<div class=\"page-break\"></div>\n")
            continue
        image = _parse_image_marker(paragraph)
        if image:
            _alt, path = image
            if _asset_exists(path, base_dir):
                parts.append(f"![]({path})")
        elif paragraph.lower().startswith("[image:"):
            parts.append(f"> {paragraph}")
        else:
            parts.append(paragraph)
    return "\n\n".join(parts).strip() + "\n"


def _html_body(text: str, metadata: Optional[Dict[str, object]] = None) -> str:
    pages = [part for part in str(text).split("\f")]
    rendered_pages = []
    page_count = max(len(pages), 1)
    for index, page in enumerate(pages, start=1):
        unit = _semantic_unit_for_page(metadata, index, page_count)
        page_body = _html_page_body(
            _text_with_missing_asset_markers(page, metadata), metadata, unit, index, page_count
        )
        if not page_body.strip():
            page_body = '<div class="blank-page"></div>'
        rendered_pages.append(
            '<section class="document-page">'
            '<div class="page-content">'
            f"{page_body}"
            '</div>'
            f'<div class="page-footer">Page {index} of {page_count}</div>'
            '</section>'
        )
    return "\n".join(rendered_pages)


def _html_plain_body(text: str, metadata_or_base_dir: Optional[object] = None) -> str:
    metadata = metadata_or_base_dir if isinstance(metadata_or_base_dir, dict) else None
    base_dir = _export_base_dir(metadata) if metadata else metadata_or_base_dir
    body = []
    for paragraph in _paragraphs(text):
        if paragraph == "\f":
            body.append('<div class="page-break"></div>')
            continue
        escaped = html.escape(paragraph).replace("\n", "<br />\n")
        stripped = paragraph.strip()
        image = _parse_image_marker(stripped)
        if image:
            _alt, path = image
            if _asset_exists(path, base_dir):
                asset = _asset_for_path(path, metadata)
                render_path = _asset_render_path(path, metadata)
                classes = " ".join(["figure-marker"] + _asset_figure_classes(asset))
                style = _asset_figure_style(asset)
                style_attr = f' style="{html.escape(style, quote=True)}"' if style else ""
                body.append(
                    f'<figure class="{html.escape(classes, quote=True)}"{style_attr}>'
                    f'<img src="{html.escape(render_path, quote=True)}" alt="" />'
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


def _semantic_unit_for_page(
    metadata: Optional[Dict[str, object]], page_index: int, page_count: int
) -> Optional[Dict[str, object]]:
    units = _semantic_units(metadata)
    for unit in units:
        try:
            if int(unit.get("index") or 0) == page_index:
                return unit
        except (TypeError, ValueError):
            continue
    if page_count == 1:
        return _semantic_unit_summary(units)
    return None


def _markdown_page_body(
    text: str,
    metadata: Optional[Dict[str, object]],
    unit: Optional[Dict[str, object]],
    page_index: int,
    page_count: int,
) -> str:
    parts = []
    if unit:
        page_heading = _markdown_unit_block(unit)
        if page_heading:
            parts.append(page_heading)
    plain = _markdown_plain_body(text, _export_base_dir(metadata))
    if plain.strip():
        parts.append(plain.strip())
    if not parts:
        return ""
    return "\n\n".join(parts).strip() + "\n"


def _html_page_body(
    text: str,
    metadata: Optional[Dict[str, object]],
    unit: Optional[Dict[str, object]],
    page_index: int,
    page_count: int,
) -> str:
    blocks = []
    if unit:
        page_heading = _html_unit_block(unit)
        if page_heading:
            blocks.append(page_heading)
    plain = _html_plain_body(text, metadata)
    if plain.strip():
        blocks.append(plain)
    return "\n".join(blocks)


def _markdown_unit_block(unit: Dict[str, object]) -> str:
    role = str(unit.get("role") or "body")
    heading = _unit_heading(unit)
    blocks = []
    if role == "contents":
        entries = unit.get("contents_entries") if isinstance(unit.get("contents_entries"), list) else []
        if entries:
            lines = ["## Contents", ""]
            lines.extend(
                f"- {entry.get('title', '').strip()} {entry.get('page', '').strip()}".rstrip()
                for entry in entries
                if isinstance(entry, dict)
            )
            blocks.append("\n".join(lines).strip())
    if role in {"title", "title-page"} and heading:
        blocks.append(f"## {heading}")
    if heading and _role_deserves_heading(role):
        blocks.append(f"## {heading}")
    footnotes = unit.get("footnotes") if isinstance(unit.get("footnotes"), list) else []
    if footnotes:
        lines = ["## Notes", ""]
        lines.extend(
            f"- {note.get('marker', '').strip()}: {note.get('text', '').strip()}"
            for note in footnotes
            if isinstance(note, dict)
        )
        blocks.append("\n".join(lines).strip())
    return "\n\n".join(blocks).strip()


def _html_unit_block(unit: Dict[str, object]) -> str:
    role = str(unit.get("role") or "body")
    heading = _unit_heading(unit)
    blocks = []
    if role == "contents":
        entries = unit.get("contents_entries") if isinstance(unit.get("contents_entries"), list) else []
        rows = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = html.escape(str(entry.get("title") or ""))
            page = html.escape(str(entry.get("page") or ""))
            rows.append(f"<tr><td>{title}</td><td>{page}</td></tr>")
        if rows:
            blocks.append(
                '<section class="contents semantic semantic-contents"><h2>Contents</h2><table>'
                + "".join(rows)
                + "</table></section>"
            )
    if role in {"title", "title-page"} and heading:
        blocks.append(
            f'<section class="title-page semantic semantic-{_css_slug(role)}"><h2>{html.escape(heading)}</h2></section>'
        )
    if heading and _role_deserves_heading(role):
        blocks.append(
            f'<section class="semantic semantic-{html.escape(_css_slug(role), quote=True)}"><h2>{html.escape(heading)}</h2></section>'
        )
    footnotes = unit.get("footnotes") if isinstance(unit.get("footnotes"), list) else []
    if footnotes:
        notes = []
        for note in footnotes:
            marker = html.escape(str(note.get("marker") or ""))
            body = html.escape(str(note.get("text") or ""))
            notes.append(f"<li><span>{marker}</span> {body}</li>")
        if notes:
            blocks.append(
                '<section class="notes semantic semantic-footnotes"><h2>Notes</h2><ol>'
                + "".join(notes)
                + "</ol></section>"
            )
    return "\n".join(blocks)


def _semantic_unit_summary(units: list[Dict[str, object]]) -> Optional[Dict[str, object]]:
    if not units:
        return None
    summary: Dict[str, object] = {
        "role": str(units[0].get("role") or "body"),
        "role_label": str(units[0].get("role_label") or "body text"),
        "layout": str(units[0].get("layout") or "single-flow"),
        "headings": [],
        "contents_entries": [],
        "footnotes": [],
        "title_candidates": [],
    }
    headings = []
    contents_entries = []
    footnotes = []
    title_candidates = []
    for unit in units:
        headings.extend(unit.get("headings") or [])
        contents_entries.extend(unit.get("contents_entries") or [])
        footnotes.extend(unit.get("footnotes") or [])
        title_candidates.extend(unit.get("title_candidates") or [])
    summary["headings"] = headings
    summary["contents_entries"] = contents_entries
    summary["footnotes"] = footnotes
    summary["title_candidates"] = title_candidates
    return summary


def _role_deserves_heading(role: str) -> bool:
    return role in {
        "cover",
        "cover-sheet",
        "front-page",
        "parties",
        "recitals",
        "definitions",
        "clauses",
        "schedule",
        "exhibits",
        "statement",
        "account",
        "summary",
        "patient",
        "findings",
        "diagnosis",
        "medications",
        "instructions",
        "policy",
        "coverage",
        "claim",
        "exclusions",
        "premium",
        "terms",
        "preface",
        "foreword",
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


def _docx_document_xml(
    text: str,
    title: str,
    metadata: Optional[Dict[str, object]] = None,
    media_entries: Optional[list[Dict[str, object]]] = None,
) -> str:
    del title
    media_by_path = {
        str(entry["original"]): entry for entry in media_entries or []
    }
    text = _text_with_missing_asset_markers(text, metadata)
    paragraphs = []
    pages = str(text).split("\f")
    for index, page in enumerate(pages, start=1):
        unit = _semantic_unit_for_page(metadata, index, len(pages))
        if index > 1:
            paragraphs.append("<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>")
        if unit:
            paragraphs.extend(_docx_unit_paragraphs(unit))
        for paragraph in page.split("\n\n"):
            if not paragraph.strip():
                continue
            marker = _parse_image_marker(paragraph.strip())
            if marker:
                _label, path = marker
                entry = media_by_path.get(path)
                if entry:
                    paragraphs.append(_docx_image_paragraph(entry))
                continue
            if _looks_like_page_marker(paragraph.strip()):
                escaped = html.escape(paragraph.strip())
                paragraphs.append(
                    "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
                    f"<w:r><w:t>{escaped}</w:t></w:r></w:p>"
                )
                continue
            escaped = html.escape(paragraph).replace("\n", "<w:br/>")
            paragraphs.append(f"<w:p><w:r><w:t>{escaped}</w:t></w:r></w:p>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<w:body>{''.join(paragraphs)}</w:body></w:document>"
    )


def _docx_unit_paragraphs(unit: Dict[str, object]) -> list[str]:
    role = str(unit.get("role") or "body")
    heading = _unit_heading(unit)
    paragraphs: list[str] = []
    if role == "contents":
        entries = unit.get("contents_entries") if isinstance(unit.get("contents_entries"), list) else []
        if entries:
            paragraphs.append(_docx_centered_heading("Contents"))
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                title_text = str(entry.get("title") or "").strip()
                page_text = str(entry.get("page") or "").strip()
                if title_text and page_text:
                    paragraphs.append(_docx_contents_line(title_text, page_text))
            return paragraphs
    if role in {"title", "title-page"} and heading:
        paragraphs.append(_docx_centered_italic_paragraph(html.escape(heading)))
        return paragraphs
    if heading and _role_deserves_heading(role):
        paragraphs.append(_docx_centered_heading(heading))
    footnotes = unit.get("footnotes") if isinstance(unit.get("footnotes"), list) else []
    if footnotes:
        paragraphs.append(_docx_centered_heading("Notes"))
        for note in footnotes:
            if not isinstance(note, dict):
                continue
            marker = str(note.get("marker") or "").strip()
            body = str(note.get("text") or "").strip()
            if marker and body:
                paragraphs.append(_docx_footnote_line(marker, body))
    return paragraphs


def _docx_centered_heading(text: str) -> str:
    return (
        "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
        "<w:r><w:rPr><w:b/><w:sz w:val=\"26\"/></w:rPr>"
        f"<w:t>{html.escape(text)}</w:t></w:r></w:p>"
    )


def _docx_contents_entries(metadata: Optional[Dict[str, object]]) -> list[tuple[str, str]]:
    if not metadata:
        return []
    structure = metadata.get("document_structure")
    if not isinstance(structure, dict):
        return []
    contents = structure.get("contents_entries")
    if not isinstance(contents, list):
        return []
    entries = []
    for entry in contents:
        if not isinstance(entry, dict):
            continue
        title_text = str(entry.get("title") or "").strip()
        page_text = str(entry.get("page") or "").strip()
        if title_text and page_text:
            entries.append((title_text, page_text))
    return entries


def _docx_contents_line(title_text: str, page_text: str) -> str:
    return (
        "<w:p><w:pPr><w:tabs><w:tab w:val=\"right\" w:pos=\"8000\"/></w:tabs></w:pPr>"
        "<w:r><w:t>"
        f"{html.escape(title_text)}"
        "</w:t></w:r><w:r><w:tab/></w:r>"
        "<w:r><w:rPr><w:i/></w:rPr>"
        f"<w:t>{html.escape(page_text)}</w:t></w:r></w:p>"
    )


def _docx_footnote_entries(metadata: Optional[Dict[str, object]]) -> list[tuple[str, str]]:
    if not metadata:
        return []
    structure = metadata.get("document_structure")
    if not isinstance(structure, dict):
        return []
    footnotes = structure.get("footnotes")
    if not isinstance(footnotes, list):
        return []
    entries = []
    for note in footnotes:
        if not isinstance(note, dict):
            continue
        marker = str(note.get("marker") or "").strip()
        body = str(note.get("text") or "").strip()
        if marker and body:
            entries.append((marker, body))
    return entries


def _docx_footnote_line(marker: str, body: str) -> str:
    return (
        "<w:p><w:pPr><w:ind w:left=\"320\" w:hanging=\"320\"/></w:pPr>"
        "<w:r><w:rPr><w:b/></w:rPr>"
        f"<w:t>{html.escape(marker)}</w:t></w:r><w:r><w:t> </w:t></w:r>"
        f"<w:r><w:t>{html.escape(body)}</w:t></w:r></w:p>"
    )


def _docx_content_types(media_entries: Optional[list[Dict[str, object]]] = None) -> str:
    defaults = [
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
    ]
    media_defaults = []
    seen = set()
    for entry in media_entries or []:
        extension = str(entry["extension"]).lstrip(".")
        if extension in seen:
            continue
        seen.add(extension)
        media_defaults.append(
            f'<Default Extension="{extension}" ContentType="{entry["content_type"]}"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + "".join(defaults)
        + "".join(media_defaults)
        +
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def _docx_package_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )


def _docx_document_rels(media_entries: Optional[list[Dict[str, object]]] = None) -> str:
    relationships = []
    for entry in media_entries or []:
        relationships.append(
            f'<Relationship Id="{entry["rid"]}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="{entry["target"]}"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(relationships)
        + "</Relationships>"
    )


def _docx_media_entries(metadata: Dict[str, object]) -> list[Dict[str, object]]:
    base_dir = _export_base_dir(metadata)
    entries: list[Dict[str, object]] = []
    seen = set()
    for asset in metadata.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        original = str(asset.get("path") or "").strip()
        if not original or original in seen:
            continue
        source = Path(original)
        if not source.is_absolute() and base_dir is not None:
            source = base_dir / source
        if not source.exists() or not source.is_file():
            continue
        seen.add(original)
        extension = _docx_image_extension(source)
        target = f"media/image{len(entries) + 1}{extension}"
        entries.append(
            {
                "original": original,
                "source": source,
                "target": target,
                "rid": f"rIdImage{len(entries) + 1}",
                "extension": extension,
                "content_type": _docx_image_content_type(extension),
                "width_emu": 4_800_000,
                "height_emu": _docx_scaled_height_emu(asset, 4_800_000),
            }
        )
    return entries


def _docx_image_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return ".jpg"
    if suffix == ".gif":
        return ".gif"
    return ".png"


def _docx_image_content_type(extension: str) -> str:
    if extension == ".jpg":
        return "image/jpeg"
    if extension == ".gif":
        return "image/gif"
    return "image/png"


def _docx_scaled_height_emu(asset: Dict[str, object], width_emu: int) -> int:
    width = asset.get("width")
    height = asset.get("height")
    if width and height:
        return max(int(width_emu * (float(height) / max(float(width), 1.0))), 500_000)
    return 3_000_000


def _docx_centered_italic_paragraph(text: str) -> str:
    return (
        "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
        "<w:r><w:rPr><w:i/><w:sz w:val=\"22\"/></w:rPr>"
        f"<w:t>{text}</w:t></w:r></w:p>"
    )


def _docx_image_paragraph(entry: Dict[str, object]) -> str:
    rid = html.escape(str(entry["rid"]))
    width = int(entry["width_emu"])
    height = int(entry["height_emu"])
    return (
        "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr><w:r><w:drawing>"
        "<wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">"
        f"<wp:extent cx=\"{width}\" cy=\"{height}\"/>"
        "<wp:docPr id=\"1\" name=\"Figure\"/>"
        "<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
        "<pic:pic><pic:nvPicPr><pic:cNvPr id=\"0\" name=\"Figure\"/>"
        "<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>"
        f"<a:blip r:embed=\"{rid}\"/>"
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        "<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/>"
        f"<a:ext cx=\"{width}\" cy=\"{height}\"/>"
        "</a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>"
        "</pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r></w:p>"
    )


def _epub_container() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/package.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )


def _epub_content(title: str, body: str, metadata: Optional[Dict[str, object]] = None) -> str:
    document_class = _document_classes(metadata)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"<title>{html.escape(title)}</title>"
        "<style>"
        "body{font-family:Georgia,'Times New Roman',serif;line-height:1.7;color:#24170f;max-width:42em;margin:0 auto;padding:2.75em 1.4em 4em;}"
        "h2{font-size:1.22em;margin:2em 0 1em;}"
        ".document-magazine,.document-newspaper{max-width:48em;}"
        ".document-manuscript{line-height:1.85;}"
        ".document-legal-document,.document-finance-document,.document-healthcare-document,.document-insurance-document{max-width:45em;}"
        ".layout-multi-column .multi-column{column-gap:2rem;column-rule:1px solid #d8cdbc;}"
        ".layout-structured-list .contents table,.layout-structured-list .notes ol{max-width:34em;margin-left:auto;margin-right:auto;}"
        ".layout-front-matter h1{margin-bottom:2.5em;}"
        ".semantic-title h2,.semantic-title-page h2,.semantic-cover h2,.semantic-cover-sheet h2{text-align:center;font-size:1.6em;margin:0 0 1.5em;}"
        ".semantic-preface h2,.semantic-foreword h2,.semantic-introduction h2,.semantic-editorial h2,.semantic-abstract h2{text-transform:uppercase;letter-spacing:.02em;}"
        ".semantic-chapter h2,.semantic-section h2,.semantic-article h2,.semantic-feature h2,.semantic-record h2,.semantic-letter h2{font-size:1.28em;}"
        ".semantic-index h2,.semantic-appendix h2,.semantic-references h2,.semantic-bibliography h2,.semantic-footnotes h2{font-size:1.08em;}"
        "p{margin:0 0 1em;}"
        ".page-marker{text-align:center;margin:1.8em 0 1em;}"
        ".page-break{break-after:page;page-break-after:always;height:0;margin:0;padding:0;border:0;}"
        ".figure-marker{text-align:center;font-style:normal;margin:1.5em auto;break-inside:avoid;}"
        ".figure-marker img{max-width:100%;height:auto;display:block;margin:0 auto;}"
        ".figure-full-width{width:100%;}.figure-large{width:82%;}.figure-medium{width:64%;}.figure-small{width:46%;}.figure-wide{width:100%;}.figure-tall{width:56%;}"
        ".contents table{width:100%;border-collapse:collapse;}"
        ".contents td{border-bottom:1px solid #d8cdbc;padding:.3em 0;}"
        ".contents td:last-child{text-align:right;white-space:nowrap;padding-left:1em;}"
        "</style>"
        f'</head><body class="{document_class}" data-document-type="{html.escape(_document_type_slug(metadata), quote=True)}">{body}</body></html>'
    )


def _asset_for_path(path: str, metadata: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
    if not metadata:
        return None
    normalized = path.replace("\\", "/")
    for asset in metadata.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        candidate = str(asset.get("path") or "").replace("\\", "/")
        if candidate == normalized:
            return asset
    return None


def _asset_render_path(path: str, metadata: Optional[Dict[str, object]]) -> str:
    if metadata:
        path_map = metadata.get("_asset_path_map")
        if isinstance(path_map, dict) and path in path_map:
            return str(path_map[path])
    return path


def _text_with_missing_asset_markers(text: str, metadata: Optional[Dict[str, object]]) -> str:
    if not metadata:
        return text
    existing = _rendered_asset_paths(text)
    output = text.rstrip()
    for asset in _metadata_assets(metadata):
        path = str(asset.get("path") or "").strip()
        normalized = path.replace("\\", "/")
        if not path or normalized in existing:
            continue
        label = _asset_display_label(asset)
        marker = f"[image: {label} | {path}]"
        output = _insert_marker_near_related_text(output, marker, asset, metadata)
    return output.strip() if output.strip() else text


def _insert_marker_near_related_text(
    text: str, marker: str, asset: Dict[str, object], metadata: Dict[str, object]
) -> str:
    related = _asset_related_text(asset, metadata)
    paragraphs = _paragraphs(text)
    if related and paragraphs:
        candidates = _marker_match_candidates(related)
        if candidates:
            rebuilt = []
            inserted = False
            for paragraph in paragraphs:
                rebuilt.append(paragraph)
                if not inserted and _paragraph_matches_candidates(paragraph, candidates):
                    rebuilt.append(marker)
                    inserted = True
            if inserted:
                return "\n\n".join(rebuilt)
    index = _asset_insertion_index(asset, paragraphs)
    if index is not None:
        rebuilt = list(paragraphs)
        rebuilt.insert(index, marker)
        return "\n\n".join(rebuilt)
    return (text.rstrip() + "\n\n" + marker).strip()


def _asset_related_text(asset: Dict[str, object], metadata: Dict[str, object]) -> str:
    path = str(asset.get("path") or "").replace("\\", "/")
    if not path:
        return ""
    records = metadata.get("restoration")
    if not isinstance(records, list):
        return ""
    for record in records:
        chunks = record.get("chunks") if isinstance(record, dict) else None
        if not isinstance(chunks, list):
            continue
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            for chunk_asset in chunk.get("assets") or []:
                if not isinstance(chunk_asset, dict):
                    continue
                candidate = str(chunk_asset.get("path") or "").replace("\\", "/")
                if candidate == path:
                    return str(
                        chunk.get("translated_text")
                        or chunk.get("restored_text")
                        or chunk.get("text")
                        or ""
                    )
    return ""


def _marker_match_candidates(text: str) -> list[str]:
    candidates = []
    for paragraph in _paragraphs(text):
        normalized = _normalize_match_text(paragraph)
        if len(normalized) >= 40:
            candidates.append(normalized[:120])
    normalized_text = _normalize_match_text(text)
    if len(normalized_text) >= 40:
        candidates.append(normalized_text[:120])
    return candidates


def _paragraph_matches_candidates(paragraph: str, candidates: list[str]) -> bool:
    haystack = _normalize_match_text(paragraph)
    return any(candidate and candidate in haystack for candidate in candidates)


def _normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _asset_insertion_index(asset: Dict[str, object], paragraphs: list[str]) -> Optional[int]:
    if not paragraphs:
        return None
    layout = asset.get("layout") if isinstance(asset.get("layout"), dict) else {}
    relative_bbox = layout.get("relative_bbox") if isinstance(layout.get("relative_bbox"), list) else None
    if isinstance(relative_bbox, list) and len(relative_bbox) == 4:
        try:
            top = float(relative_bbox[1])
            bottom = float(relative_bbox[3])
            center_y = (top + bottom) / 2.0
            if center_y <= 0.24:
                return 0
            if center_y >= 0.76:
                return len(paragraphs)
            return max(1, min(int(round(center_y * len(paragraphs))), len(paragraphs) - 1))
        except (TypeError, ValueError):
            pass
    zone = str(layout.get("page_zone") or "").strip().lower()
    if zone.startswith("top"):
        return 0
    if zone.startswith("bottom"):
        return len(paragraphs)
    if zone.startswith("middle"):
        return max(1, min(len(paragraphs) // 2, len(paragraphs) - 1))
    placement = asset.get("placement") if isinstance(asset.get("placement"), dict) else {}
    width_hint = str(placement.get("recommended_width") or "").strip().lower()
    if width_hint in {"full-width", "wide"}:
        return 0
    return len(paragraphs)


def _rendered_asset_paths(text: str) -> set[str]:
    rendered = set()
    for paragraph in _paragraphs(text):
        marker = _parse_image_marker(paragraph)
        if marker:
            rendered.add(marker[1].replace("\\", "/"))
    return rendered


def _metadata_assets(metadata: Optional[Dict[str, object]]) -> list[Dict[str, object]]:
    if not metadata:
        return []
    assets = metadata.get("assets")
    return [asset for asset in assets if isinstance(asset, dict)] if isinstance(assets, list) else []


def _asset_marker_placement(asset: Dict[str, object]) -> str:
    layout = asset.get("layout")
    if isinstance(layout, dict):
        zone = str(layout.get("page_zone") or "").strip()
        size_class = str(layout.get("size_class") or "").strip()
        values = [item for item in [zone if zone != "unknown" else "", size_class] if item]
        if values:
            return ", ".join(values)
    placement = asset.get("placement")
    if isinstance(placement, dict):
        return str(placement.get("recommended_width") or "").strip()
    return str(placement or "").strip()


def _asset_size_label(asset: Dict[str, object]) -> str:
    width = asset.get("width")
    height = asset.get("height")
    if width and height:
        return f"{width}x{height}"
    return ""


def _asset_display_label(asset: Dict[str, object]) -> str:
    label = str(asset.get("label") or asset.get("kind") or "Figure").strip()
    label = label.split(" | ", 1)[0].strip()
    label = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
    return label or "Figure"


def _asset_figure_classes(asset: Optional[Dict[str, object]]) -> list[str]:
    if not asset:
        return ["figure-medium"]
    layout = asset.get("layout") if isinstance(asset.get("layout"), dict) else {}
    placement = asset.get("placement") if isinstance(asset.get("placement"), dict) else {}
    size = str(layout.get("size_class") or placement.get("recommended_width") or "medium").strip()
    zone = str(layout.get("page_zone") or "").strip()
    classes = [f"figure-{_css_slug(size)}"]
    if zone and zone != "unknown":
        classes.append(f"zone-{_css_slug(zone)}")
    return classes


def _asset_figure_style(asset: Optional[Dict[str, object]]) -> str:
    if not asset:
        return ""
    width = asset.get("width")
    height = asset.get("height")
    if width and height:
        return f"aspect-ratio:{width}/{height};"
    return ""


def _document_class(metadata: Optional[Dict[str, object]]) -> str:
    document_type = "general"
    if metadata:
        document_type = str(metadata.get("document_type") or "general")
    return f"document-{_css_slug(document_type)}"


def _document_type_slug(metadata: Optional[Dict[str, object]]) -> str:
    if not metadata:
        return "general"
    return _css_slug(str(metadata.get("document_type") or "general"))


def _document_layout_class(metadata: Optional[Dict[str, object]]) -> str:
    if not metadata:
        return "layout-single-flow"
    structure = metadata.get("document_structure")
    if not isinstance(structure, dict):
        return "layout-single-flow"
    layout_profile = structure.get("layout_profile")
    if not isinstance(layout_profile, dict):
        return "layout-single-flow"
    flow = str(layout_profile.get("dominant_flow") or "single-flow").strip()
    return f"layout-{_css_slug(flow)}"


def _document_classes(metadata: Optional[Dict[str, object]]) -> str:
    return " ".join(
        part for part in (_document_class(metadata), _document_layout_class(metadata)) if part
    )


def _css_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "default"


def _epub_asset_entries(metadata: Dict[str, object]) -> list[tuple[str, str, Path]]:
    base_dir = _export_base_dir(metadata)
    entries: list[tuple[str, str, Path]] = []
    seen = set()
    for asset in metadata.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        original = str(asset.get("path") or "").strip()
        if not original or original in seen:
            continue
        source = Path(original)
        if not source.is_absolute() and base_dir is not None:
            source = base_dir / source
        if not source.exists() or not source.is_file():
            continue
        seen.add(original)
        packaged = f"assets/{len(entries) + 1:04d}-{_css_slug(source.stem)}{source.suffix.lower() or '.png'}"
        entries.append((original, packaged, source))
    return entries


def _epub_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/png"


def _epub_package(title: str, assets: Optional[list[tuple[str, str, Path]]] = None) -> str:
    asset_items = []
    for index, (_original, packaged, source) in enumerate(assets or [], start=1):
        media_type = _epub_media_type(source)
        asset_items.append(
            f'<item id="asset{index}" href="{html.escape(packaged, quote=True)}" media-type="{media_type}"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">'
        '<metadata><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"{html.escape(title)}</dc:title></metadata>"
        '<manifest><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>'
        + "".join(asset_items)
        + "</manifest>"
        '<spine><itemref idref="content"/></spine></package>'
    )
