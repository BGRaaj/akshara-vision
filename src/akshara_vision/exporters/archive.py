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
        records = metadata.get("_records") or []
        
        if self.name == "pagexml":
            payload = _generate_pagexml(records, text)
            path.write_text(payload, encoding="utf-8")
        elif self.name == "alto":
            payload = _generate_alto(records, text)
            path.write_text(payload, encoding="utf-8")
        elif self.name == "hocr":
            payload = _generate_hocr(records, text)
            path.write_text(payload, encoding="utf-8")
        else:
            payload = {
                "format": self.label,
                "note": "This sidecar carries restored text and available run metadata for OCR/archive workflows.",
                "text": text,
                "metadata": _public_metadata(metadata),
            }
            if self.suffix.endswith(".xml"):
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
            "# Restoration Review",
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
        f'<ocr-sidecar format="{label}">\n'
        f"  <text>{escaped}</text>\n"
        "</ocr-sidecar>\n"
    )


def _public_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key != "run_dir" and not str(key).startswith("_")
    }


def _allocate_text_to_blocks(text: str, blocks: list) -> dict:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    allocations = {}
    text_bearing_blocks = [
        b for b in blocks 
        if isinstance(b, dict) and b.get("role") in {"text-region", "title-region", "table-region", "running-header-or-footer"}
    ]
    for i, block in enumerate(text_bearing_blocks):
        order = block.get("order", i + 1)
        if i < len(paragraphs):
            allocations[order] = paragraphs[i]
        else:
            allocations[order] = ""
    if len(paragraphs) > len(text_bearing_blocks) and text_bearing_blocks:
        last_order = text_bearing_blocks[-1].get("order", len(text_bearing_blocks))
        extra = "\n\n".join(paragraphs[len(text_bearing_blocks):])
        allocations[last_order] = (allocations.get(last_order, "") + "\n\n" + extra).strip()
    return allocations


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _generate_pagexml(records: list, default_text: str) -> str:
    content = ['<?xml version="1.0" encoding="UTF-8"?>']
    content.append('<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15 http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15/pagecontent.xsd">')
    
    if not records:
        content.append('  <Page imageFilename="unknown.png" imageWidth="1000" imageHeight="1500">')
        content.append('    <TextRegion id="r_1" type="paragraph">')
        content.append('      <Coords points="0,0 1000,0 1000,1500 0,1500"/>')
        content.append(f'      <TextEquiv><Unicode>{_xml_escape(default_text)}</Unicode></TextEquiv>')
        content.append('    </TextRegion>')
        content.append('  </Page>')
    else:
        for idx, record in enumerate(records, start=1):
            native = record.get("native_layout") or {}
            w = native.get("page_width") or 1000
            h = native.get("page_height") or 1500
            label = record.get("label") or f"page_{idx}.png"
            blocks = native.get("blocks") or []
            
            page_text = ""
            for chunk in record.get("chunks") or []:
                if isinstance(chunk, dict) and chunk.get("restored_text"):
                    page_text += chunk["restored_text"] + "\n\n"
            page_text = page_text.strip() or default_text
            
            allocations = _allocate_text_to_blocks(page_text, blocks)
            
            content.append(f'  <Page imageFilename="{_xml_escape(label)}" imageWidth="{w}" imageHeight="{h}">')
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                role = block.get("role") or "text-region"
                order = block.get("order", 1)
                bbox = block.get("bbox") or [0, 0, w, h]
                l, t, r, b = bbox
                
                el_type = "paragraph"
                if role == "title-region":
                    el_type = "heading"
                elif role == "table-region":
                    el_type = "table"
                elif role == "running-header-or-footer":
                    el_type = "header"
                
                block_id = f"r_{idx}_{order}"
                block_text = allocations.get(order, "")
                
                if el_type == "table":
                    content.append(f'    <TableRegion id="{block_id}">')
                    content.append(f'      <Coords points="{l},{t} {r},{t} {r},{b} {l},{b}"/>')
                    content.append('    </TableRegion>')
                else:
                    content.append(f'    <TextRegion id="{block_id}" type="{el_type}">')
                    content.append(f'      <Coords points="{l},{t} {r},{t} {r},{b} {l},{b}"/>')
                    content.append(f'      <TextEquiv><Unicode>{_xml_escape(block_text)}</Unicode></TextEquiv>')
                    content.append('    </TextRegion>')
            content.append('  </Page>')
            
    content.append('</PcGts>')
    return "\n".join(content)


def _generate_alto(records: list, default_text: str) -> str:
    content = ['<?xml version="1.0" encoding="UTF-8"?>']
    content.append('<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.loc.gov/standards/alto/ns-v4# http://www.loc.gov/standards/alto/v4/alto-4-2.xsd">')
    content.append('  <Description>')
    content.append('    <MeasurementUnit>pixel</MeasurementUnit>')
    content.append('  </Description>')
    content.append('  <Layout>')
    
    if not records:
        content.append('    <Page ID="p_1" WIDTH="1000" HEIGHT="1500">')
        content.append('      <PrintSpace>')
        content.append('        <TextBlock ID="b_1" HPOS="0" VPOS="0" WIDTH="1000" HEIGHT="1500">')
        content.append('          <TextLine HPOS="0" VPOS="0" WIDTH="1000" HEIGHT="1500">')
        content.append(f'            <String CONTENT="{_xml_escape(default_text)}"/>')
        content.append('          </TextLine>')
        content.append('        </TextBlock>')
        content.append('      </PrintSpace>')
        content.append('    </Page>')
    else:
        for idx, record in enumerate(records, start=1):
            native = record.get("native_layout") or {}
            w = native.get("page_width") or 1000
            h = native.get("page_height") or 1500
            blocks = native.get("blocks") or []
            
            page_text = ""
            for chunk in record.get("chunks") or []:
                if isinstance(chunk, dict) and chunk.get("restored_text"):
                    page_text += chunk["restored_text"] + "\n\n"
            page_text = page_text.strip() or default_text
            allocations = _allocate_text_to_blocks(page_text, blocks)
            
            content.append(f'    <Page ID="p_{idx}" WIDTH="{w}" HEIGHT="{h}">')
            content.append('      <PrintSpace>')
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                order = block.get("order", 1)
                bbox = block.get("bbox") or [0, 0, w, h]
                l, t, r, b = bbox
                bw = r - l
                bh = b - t
                block_id = f"b_{idx}_{order}"
                block_text = allocations.get(order, "")
                
                content.append(f'        <TextBlock ID="{block_id}" HPOS="{l}" VPOS="{t}" WIDTH="{bw}" HEIGHT="{bh}">')
                if block_text:
                    for line_idx, line in enumerate(block_text.splitlines(), start=1):
                        content.append(f'          <TextLine ID="{block_id}_l{line_idx}" HPOS="{l}" VPOS="{t}" WIDTH="{bw}" HEIGHT="20">')
                        content.append(f'            <String CONTENT="{_xml_escape(line)}"/>')
                        content.append('          </TextLine>')
                content.append('        </TextBlock>')
            content.append('      </PrintSpace>')
            content.append('    </Page>')
            
    content.append('  </Layout>')
    content.append('</alto>')
    return "\n".join(content)


def _generate_hocr(records: list, default_text: str) -> str:
    content = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"',
        '    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">',
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">',
        '<head>',
        '  <title>OCR Output</title>',
        '  <meta http-equiv="Content-Type" content="text/html;charset=utf-8" />',
        "  <meta name='ocr-system' content='Akshara Vision' />",
        '</head>',
        '<body>'
    ]
    
    if not records:
        content.append('  <div class="ocr_page" id="page_1" title="image \'unknown.png\'; bbox 0 0 1000 1500">')
        content.append('    <div class="ocr_carea" id="block_1_1" title="bbox 0 0 1000 1500">')
        content.append('      <p class="ocr_par" id="par_1_1">')
        content.append(f'        {_xml_escape(default_text)}')
        content.append('      </p>')
        content.append('    </div>')
        content.append('  </div>')
    else:
        for idx, record in enumerate(records, start=1):
            native = record.get("native_layout") or {}
            w = native.get("page_width") or 1000
            h = native.get("page_height") or 1500
            label = record.get("label") or f"page_{idx}.png"
            blocks = native.get("blocks") or []
            
            page_text = ""
            for chunk in record.get("chunks") or []:
                if isinstance(chunk, dict) and chunk.get("restored_text"):
                    page_text += chunk["restored_text"] + "\n\n"
            page_text = page_text.strip() or default_text
            allocations = _allocate_text_to_blocks(page_text, blocks)
            
            content.append(f'  <div class="ocr_page" id="page_{idx}" title="image \'{_xml_escape(label)}\'; bbox 0 0 {w} {h}">')
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                order = block.get("order", 1)
                bbox = block.get("bbox") or [0, 0, w, h]
                l, t, r, b = bbox
                block_id = f"block_{idx}_{order}"
                block_text = allocations.get(order, "")
                
                content.append(f'    <div class="ocr_carea" id="{block_id}" title="bbox {l} {t} {r} {b}">')
                content.append(f'      <p class="ocr_par" id="par_{idx}_{order}">')
                for line in block_text.splitlines():
                    content.append(f'        {_xml_escape(line)}<br />')
                content.append('      </p>')
                content.append('    </div>')
            content.append('  </div>')
            
    content.append('</body>')
    content.append('</html>')
    return "\n".join(content)
