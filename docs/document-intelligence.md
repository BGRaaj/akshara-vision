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
