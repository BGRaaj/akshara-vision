from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, Iterable, List

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:  # pragma: no cover - optional rendering fallback
    Image = None
    ImageDraw = None
    ImageFont = None

from akshara_vision.exporters.base import ExportResult


class PdfExporter:
    def __init__(self, name: str, suffix: str, heading: str, kind: str = "text") -> None:
        self.name = name
        self.suffix = suffix
        self.heading = heading
        self.kind = kind

    def export(self, text: str, destination: Path, metadata: Dict[str, object]) -> ExportResult:
        path = destination.with_suffix(self.suffix)
        if self.kind == "image":
            _build_image_pdf(path, self.heading, text, metadata)
        else:
            pdf_bytes = _build_pdf_document(self.heading, text, metadata)
            path.write_bytes(pdf_bytes)
        return ExportResult(self.name, path)


def _build_pdf_document(_heading: str, text: str, metadata: Dict[str, object]) -> bytes:
    pages = _paginate_pdf_lines(_pdf_book_lines(text, metadata), metadata)
    return _render_pdf_pages(pages)


def _build_image_pdf(path: Path, heading: str, text: str, metadata: Dict[str, object]) -> None:
    if Image is None or ImageDraw is None or ImageFont is None:
        path.write_bytes(_build_pdf_document(heading, text, metadata))
        return
    pages = _paginate_pdf_lines(_pdf_book_lines(text, metadata), metadata)
    page_count = max(len(pages), 1)
    rendered_pages = [
        _render_image_page(page_lines, index + 1, page_count, metadata)
        for index, page_lines in enumerate(pages)
    ]
    if not rendered_pages:
        rendered_pages = [_render_image_page([""], 1, 1, metadata)]
    rendered_pages[0].save(
        path,
        format="PDF",
        save_all=True,
        append_images=rendered_pages[1:],
    )


def _pdf_book_lines(text: str, metadata: Dict[str, object]) -> List[str]:
    title = str(metadata.get("title") or "Untitled").strip() or "Untitled"
    text = _text_with_missing_asset_markers(text, metadata)
    lines: List[str] = [
        *_wrap_heading(title),
        "",
    ]
    for credit in _publication_credits(metadata):
        lines.extend(_wrap_heading(credit, width=58))
    if len(lines) > 2:
        lines.append("")
    contents_lines = _contents_lines(metadata)
    if contents_lines:
        for idx, entry in enumerate(contents_lines):
            lines.extend(_wrap_heading(entry, width=58 if idx == 0 else 62))
        lines.append("")
    lines.append("")
    for paragraph in _paragraphs(text):
        paragraph = _clean_visible_text(paragraph)
        if _parse_image_marker(paragraph):
            lines.append(paragraph)
        else:
            lines.extend(_wrap_paragraph(paragraph))
        lines.append("")
    return [line.rstrip() for line in lines]


def _paginate_pdf_lines(lines: Iterable[str], metadata: Dict[str, object] | None = None) -> List[List[str]]:
    wrapped_pages: List[List[str]] = []
    page: List[str] = []
    usable_lines = 43
    for line in lines:
        cost = _pdf_line_cost(line, metadata)
        current_cost = sum(_pdf_line_cost(item, metadata) for item in page)
        if page and current_cost + cost > usable_lines:
            wrapped_pages.append(page)
            page = []
        page.append(line)
    if page or not wrapped_pages:
        wrapped_pages.append(page or [""])
    return wrapped_pages


def _pdf_line_cost(line: str, metadata: Dict[str, object] | None = None) -> int:
    marker = _parse_image_marker(line)
    if marker:
        return _asset_line_cost(marker[1], metadata or {})
    if not str(line).strip():
        return 1
    return 1


def _asset_line_cost(path: str, metadata: Dict[str, object]) -> int:
    if Image is None:
        return 14
    source = _asset_source_path(path, metadata)
    if not source or not source.exists():
        return 2
    try:
        with Image.open(source) as asset_image:
            available_width = 1240 - 92 * 2
            render_width = _asset_render_width(path, metadata, available_width)
            scale = min(render_width / max(asset_image.width, 1), 1.0)
            render_height = max(int(asset_image.height * scale), 1)
            return max(6, min(28, (render_height + 92) // 34 + 1))
    except Exception:
        return 14


def _render_pdf_pages(pages: List[List[str]]) -> bytes:
    page_count = max(len(pages), 1)
    page_numbers = [6 + index * 2 for index in range(page_count)]
    content_numbers = [7 + index * 2 for index in range(page_count)]

    objects: Dict[int, bytes] = {
        1: b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
        2: b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Bold >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Italic >>",
        4: (
            f"<< /Type /Pages /Kids [{' '.join(f'{num} 0 R' for num in page_numbers)}] "
            f"/Count {page_count} >>"
        ).encode("utf-8"),
        5: b"<< /Type /Catalog /Pages 4 0 R >>",
    }
    for index, page_lines in enumerate(pages or [[""]]):
        content_obj = content_numbers[index]
        page_obj = page_numbers[index]
        objects[content_obj] = _pdf_stream_obj(
            _page_content_stream(page_lines, index + 1, page_count)
        )
        objects[page_obj] = (
            "<< /Type /Page /Parent 4 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 1 0 R /F2 2 0 R /F3 3 0 R >> >> /Contents {content_obj} 0 R >>"
        ).encode("utf-8")

    rendered: List[bytes] = [b"%PDF-1.4\n"]
    offsets: Dict[int, int] = {}
    for obj_num in range(1, max(objects) + 1):
        offsets[obj_num] = sum(len(chunk) for chunk in rendered)
        rendered.append(f"{obj_num} 0 obj\n".encode("utf-8"))
        rendered.append(objects[obj_num])
        rendered.append(b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in rendered)
    rendered.append(f"xref\n0 {max(objects) + 1}\n".encode("utf-8"))
    rendered.append(b"0000000000 65535 f \n")
    for obj_num in range(1, max(objects) + 1):
        offset = offsets[obj_num]
        rendered.append(f"{offset:010d} 00000 n \n".encode("utf-8"))
    rendered.append(
        (
            "trailer\n"
            f"<< /Size {max(objects) + 1} /Root 5 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("utf-8")
    )
    return b"".join(rendered)


def _page_content_stream(page_lines: List[str], page_number: int, page_count: int) -> bytes:
    safe_lines = [line for line in page_lines if line is not None]
    commands = [
        "BT",
    ]
    commands.append("72 792 Td")
    for index, line in enumerate(safe_lines):
        text = _escape_pdf_text(line)
        if not text:
            commands.append("0 -9 Td")
            continue
        font_name, size, gap = _pdf_line_style(line, index, page_number, page_count)
        commands.append(f"/{font_name} {size} Tf")
        commands.append(f"({text}) Tj")
        commands.append(f"0 -{gap} Td")
    commands.append("/F3 10 Tf")
    commands.append("0 -18 Td")
    commands.append(f"({_escape_pdf_text(str(page_number))}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("utf-8")
    return stream


def _pdf_line_style(line: str, line_index: int, page_number: int, page_count: int) -> tuple[str, int, int]:
    stripped = line.strip()
    if page_number == 1 and line_index == 0:
        return "F2", 18, 24
    if stripped.lower() == "contents":
        return "F2", 16, 22
    if _looks_like_semantic_heading(stripped):
        return "F2", 14, 18
    if _looks_like_contents_entry(stripped):
        return "F3", 10, 13
    if _looks_like_page_marker(stripped):
        return "F3", 10, 13
    if page_number == 1 and line_index <= 4 and stripped:
        return "F3", 11, 16
    if page_number == 1 and line_index <= 2:
        return "F2", 16, 24
    if stripped.startswith("- "):
        return "F1", 11, 15
    if ":" in stripped and line_index < 20:
        return "F1", 11, 14
    return "F1", 11, 15


def _pdf_stream_obj(stream: bytes) -> bytes:
    return (
        f"<< /Length {len(stream)} >>\nstream\n".encode("utf-8")
        + stream
        + b"\nendstream"
    )


def _paragraphs(text: str) -> List[str]:
    return [part.strip() for part in str(text).split("\n\n") if part.strip()]


def _wrap_paragraph(paragraph: str, width: int = 76) -> List[str]:
    words = paragraph.split()
    if not words:
        return [""]
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        tentative = f"{current} {word}"
        if len(tentative) <= width:
            current = tentative
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _wrap_heading(text: str, width: int = 56) -> List[str]:
    return _wrap_paragraph(str(text).strip(), width=width)


def _looks_like_semantic_heading(text: str) -> bool:
    if not text:
        return False
    if len(text) > 90:
        return False
    lowered = text.lower()
    if text == text.upper() and len(text) >= 4:
        return True
    return bool(
        re.match(
            r"^(chapter|section|part|book|preface|foreword|introduction|appendix|index|abstract|references|bibliography|editorial|feature|article|letter|record|schedule|clauses|definitions|policy|coverage|claim|findings|diagnosis|medications|instructions)\b",
            lowered,
            re.I,
        )
    )


def _looks_like_contents_entry(text: str) -> bool:
    if not text or len(text) > 180:
        return False
    return bool(re.match(r"^.+?(?:\.{2,}|\s{2,}|[|:]\s*|-)\s*[ivxlcdm\d]+$", text.strip(), re.I))


def _looks_like_page_marker(text: str) -> bool:
    return bool(re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]+", text.strip(), re.I))


def _wrap_for_draw(text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    words = str(text).split()
    if not words:
        return [""]
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _draw_text_width(candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_text_width(text: str, font: ImageFont.ImageFont) -> int:
    try:
        return int(font.getlength(text))
    except Exception:
        try:
            return font.getbbox(text)[2]
        except Exception:
            return len(text) * 12


def _escape_pdf_text(text: str) -> str:
    return (
        _clean_visible_text(str(text))
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", "")
    )


def _compact_json(value: object) -> str:
    try:
        import json

        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return str(value)


def _render_image_page(
    page_lines: List[str],
    page_number: int,
    page_count: int,
    metadata: Dict[str, object],
) -> Image.Image:
    page_width = 1240
    page_height = 1754
    background = "#f4ecd8"
    text_color = "#3a2417"
    image = Image.new("RGB", (page_width, page_height), background)
    draw = ImageDraw.Draw(image)
    font = _reading_font(24)
    title_font = _reading_font(34)
    line_height = 34
    x_margin = 92
    y = 96
    current_font = title_font
    for line_index, line in enumerate(page_lines):
        line = _clean_visible_text(line)
        if line == "":
            y += line_height // 2
            current_font = font
            continue
        marker = _parse_image_marker(line)
        if marker:
            y = _draw_asset_marker(
                image, draw, line, marker, metadata, x_margin, y, page_width, page_height
            )
            current_font = font
            continue
        if y == 96 and line_index == 0:
            wrapped_title = _wrap_for_draw(line, title_font, page_width - x_margin * 2)
            for title_line in wrapped_title:
                draw.text((x_margin, y), title_line, fill=text_color, font=title_font)
                y += 44
            y += 12
            current_font = font
            continue
        wrapped = _wrap_for_draw(line, current_font, page_width - x_margin * 2)
        for wrapped_line in wrapped:
            draw.text((x_margin, y), wrapped_line, fill=text_color, font=current_font)
            y += 30
        current_font = font
        if y > page_height - 110:
            break
    footer = f"Page {page_number} of {page_count}"
    draw.text((x_margin, page_height - 88), footer, fill=text_color, font=_reading_font(18))
    return image


def _draw_asset_marker(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    line: str,
    marker: tuple[str, str],
    metadata: Dict[str, object],
    x_margin: int,
    y: int,
    page_width: int,
    page_height: int,
) -> int:
    label, marker_path = marker
    line = _clean_visible_text(line)
    label = _clean_visible_text(label)
    source = _asset_source_path(marker_path, metadata)
    font = _reading_font(20)
    caption_font = _reading_font(18)
    text_color = "#3a2417"
    if source and source.exists():
        try:
            with Image.open(source) as asset_image:
                asset = asset_image.convert("RGB")
                max_width = _asset_render_width(marker_path, metadata, page_width - x_margin * 2)
                scale = min(max_width / max(asset.width, 1), 1.0)
                max_height = max(page_height - 120 - y - 72, 0)
                if max_height:
                    scale = min(scale, max_height / max(asset.height, 1))
                render_width = max(int(asset.width * scale), 1)
                render_height = max(int(asset.height * scale), 1)
                if render_height < 80 or y + render_height + 72 > page_height - 120:
                    draw.text((x_margin, y), line, fill=text_color, font=font)
                    return y + 34
                asset = asset.resize((render_width, render_height))
                x = x_margin + max((page_width - x_margin * 2 - render_width) // 2, 0)
                canvas.paste(asset, (x, y))
                y += render_height + 18
        except Exception:
            draw.text((x_margin, y), line, fill=text_color, font=font)
            return y + 34
    else:
        draw.text((x_margin, y), line, fill=text_color, font=font)
        return y + 34
    draw.text((x_margin, y), label, fill=text_color, font=caption_font)
    return y + 44


def _asset_render_width(path: str, metadata: Dict[str, object], available_width: int) -> int:
    asset = _asset_for_path(path, metadata)
    size = ""
    if asset:
        layout = asset.get("layout") if isinstance(asset.get("layout"), dict) else {}
        placement = asset.get("placement") if isinstance(asset.get("placement"), dict) else {}
        size = str(layout.get("size_class") or placement.get("recommended_width") or "")
    ratio = {
        "full-width": 1.0,
        "wide": 1.0,
        "large": 0.82,
        "medium": 0.64,
        "small": 0.46,
        "tall": 0.56,
    }.get(size, 0.64)
    return max(int(available_width * ratio), 160)


def _asset_source_path(path: str, metadata: Dict[str, object]) -> Path | None:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    run_dir = metadata.get("run_dir")
    if isinstance(run_dir, str) and run_dir.strip():
        return Path(run_dir) / candidate
    return candidate


def _asset_for_path(path: str, metadata: Dict[str, object]) -> Dict[str, object] | None:
    normalized = path.replace("\\", "/")
    for asset in metadata.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        candidate = str(asset.get("path") or "").replace("\\", "/")
        if candidate == normalized:
            return asset
    return None


def _parse_image_marker(text: str) -> tuple[str, str] | None:
    match = re.match(r"^\[image:\s*(?P<label>.+?)\s*\|\s*(?P<path>[^\]]+)\]$", text.strip(), re.I)
    if not match:
        return None
    label = match.group("label").strip() or "Figure"
    path = match.group("path").strip()
    if not path:
        return None
    return label, path


def _publication_credits(metadata: Dict[str, object]) -> List[str]:
    structure = metadata.get("document_structure")
    if not isinstance(structure, dict):
        return []
    credits: List[str] = []
    for key in ("contributors", "publishers"):
        values = structure.get(key)
        if isinstance(values, list):
            credits.extend(str(value).strip() for value in values if str(value).strip())
    seen = set()
    result = []
    for credit in credits:
        key = credit.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(credit)
        if len(result) >= 6:
            break
    return result


def _contents_lines(metadata: Dict[str, object]) -> List[str]:
    structure = metadata.get("document_structure")
    if not isinstance(structure, dict):
        return []
    contents = structure.get("contents_entries")
    if not isinstance(contents, list) or not contents:
        return []
    lines = ["Contents"]
    for entry in contents:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        page = str(entry.get("page") or "").strip()
        if not title or not page:
            continue
        lines.append(f"{title} ..... {page}")
    return lines if len(lines) > 1 else []


def _text_with_missing_asset_markers(text: str, metadata: Dict[str, object]) -> str:
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
            rebuilt: List[str] = []
            inserted = False
            for paragraph in paragraphs:
                rebuilt.append(paragraph)
                if not inserted and _paragraph_matches_candidates(paragraph, candidates):
                    rebuilt.append(marker)
                    inserted = True
            if inserted:
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


def _marker_match_candidates(text: str) -> List[str]:
    candidates = []
    for paragraph in _paragraphs(text):
        normalized = _normalize_match_text(paragraph)
        if len(normalized) >= 40:
            candidates.append(normalized[:120])
    normalized_text = _normalize_match_text(text)
    if len(normalized_text) >= 40:
        candidates.append(normalized_text[:120])
    return candidates


def _paragraph_matches_candidates(paragraph: str, candidates: List[str]) -> bool:
    haystack = _normalize_match_text(paragraph)
    return any(candidate and candidate in haystack for candidate in candidates)


def _normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", _clean_visible_text(text)).strip().lower()


_INVISIBLE_TEXT_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060\u00ad\ufe00-\ufe0f]")
_CONTROL_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_visible_text(text: str) -> str:
    cleaned = _INVISIBLE_TEXT_RE.sub("", str(text))
    cleaned = _CONTROL_TEXT_RE.sub("", cleaned)
    return cleaned.replace("\t", " ")


def _rendered_asset_paths(text: str) -> set[str]:
    rendered = set()
    for paragraph in _paragraphs(text):
        marker = _parse_image_marker(paragraph)
        if marker:
            rendered.add(marker[1].replace("\\", "/"))
    return rendered


def _metadata_assets(metadata: Dict[str, object]) -> List[Dict[str, object]]:
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
    return label or "Figure"


def _reading_font(size: int) -> ImageFont.ImageFont:
    for candidate in _font_candidates():
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _font_candidates() -> List[str]:
    candidates = [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Times New Roman.ttf",
        "/Library/Fonts/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/noto/NotoSerif-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    ]
    return candidates


def _public_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key != "run_dir" and not str(key).startswith("_")
    }
