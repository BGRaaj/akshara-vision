# Document Intelligence

Akshara Vision does not treat every scan as plain text. It records structured
document observations and uses them during later assembly and export.

## What Gets Captured

- `semantic_units`: per-chunk roles such as title, contents, chapter, article,
  folio, record, clause, statement, or policy section
- `native_layout`: local page-image geometry with content bbox, relative block
  boxes, page zones, figure/text region guesses, confidence scores, column
  estimate, and flow hint
- `layout_tree`: reading-order nodes with page number, role, layout class,
  native layout, confidence, excerpts, headings, page markers, and attached
  figure metadata
- `layout_profile`: page-flow summary for single-flow, multi-column, list-like,
  dense-prose, or front-matter heavy documents
- `contents_entries`: parsed table-of-contents items when the source is clear
- `table_rows` and chart candidates: clear row/cell structures, visible labels,
  legends, axis text, and candidate numeric series when the source supports it
- `footnotes`, `contributors`, `publishers`, `running_headers`, and `page_markers`
- `assets`: conservative figure crops with page zone, bbox, size, DPI, and
  placement metadata when figure enrichment is enabled
- `assembly_profile`: target-format hints used by combine and export steps

## Why It Matters

- Books can be exported with title matter, contents, chapters, notes, and index
  behavior that feels book-like.
- Magazines and newspapers preserve column flow, article boundaries, captions,
  and sidebars more carefully.
- Manuscripts preserve folios, marginalia, and damaged passages without forcing
  modern prose.
- Legal, finance, healthcare, and insurance documents get stricter role labels
  and cleaner section handling.

## How It Is Used

- `akv combine` rebuilds staged outputs using structured chunk JSON first.
- `akv resume` uses the same staged records to continue interrupted work.
- `akv export` carries figure assets and semantic metadata into publication
  formats.
- `akv chat` grounds answers in the same run metadata and extracted chunks.
- Exporters can use native blocks and figure metadata to choose better document
  classes, asset placement, and publication-style layout.
- Markdown, HTML, EPUB, and DOCX exporters render clear table rows as tables;
  `json-detailed` keeps the richer page/block representation for downstream
  review, analysis, or custom assembly.
- `akv review` inspects layout profile, low-confidence blocks, block-map
  previews, and assets, then writes `layout_review.md` into the run folder.

## Layout Backends

Profiles include a `layout_backend` value. The default `native` backend is a
local heuristic analyzer that runs without cloud services. `off` disables layout
analysis for users who only want text restoration. If installed, Akshara also
recognizes optional `doctr`, `paddleocr`, and `layoutparser` backends and
normalizes their blocks into the same manifest shape.

Install the optional layout adapter dependencies with:

```bash
python -m pip install -e ".[layout]"
```

`layoutparser` requires `AKSHARA_LAYOUTPARSER_CONFIG` to point to a compatible
model config. Contributors can register additional high-accuracy backends
through the layout backend registry without changing the rest of the pipeline.

## Design Rule

The document intelligence layer must improve structure and assembly without
changing the source meaning. It should guide ordering and formatting, not invent
new content.

The native layout pass is deliberately conservative and local. It gives Akshara
Vision first-party page geometry without requiring a separate layout model, while
remaining compatible with future OCR/layout backends that can write richer block
and confidence data.

## Hybrid Layout & CSV Table System

Akshara Vision employs a hybrid vision-first document intelligence flow to maximize accuracy and layout integrity:
1. **Precision Geometry (All Modes)**: Page geometry is detected locally by the native heuristic engine (or layout backends like `doctr`, `paddleocr`) to guarantee that bounding boxes are pixel-accurate and no elements are skipped. This runs unconditionally in all execution modes (`fast`, `balanced`, `quality`).
2. **Vision Role Refinement**: Bounding boxes are then refined by the Vision model (`_llm_classify_layout_blocks`), mapping them to 13 granular semantic roles (e.g. `title`, `table`, `list`, `caption`) and merging fragmented multi-column tables or multi-line titles.
3. **Structured CSV Table Extraction**: Table regions are extracted as structured CSV fenced blocks (` ```csv ... ``` `) within the restored text, ensuring columns remain perfectly aligned. Akshara converts these CSV blocks to clean, responsive HTML tables for interactive comparison and exports.
4. **Reading-Order Text Allocation**: The final restored page text is mapped block-by-block using visual reading order sorting (column-by-column for multi-column documents) to associate the exact text segment to its visual coordinates.
