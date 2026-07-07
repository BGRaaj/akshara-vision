# Inputs And Outputs

## Inputs

Akshara Vision accepts individual files, multiple files, folders, recursive folders,
globs, and manifest files.

Supported input formats:

| Type | Extensions |
| --- | --- |
| PDF | `.pdf` |
| Image | `.jpg`, `.jpeg`, `.png`, `.webp`, `.tif`, `.tiff`, `.bmp` |
| Text/OCR | `.txt`, `.md`, `.html`, `.hocr`, `.xml`, `.json` |
| Archive | `.zip` |
| Manifest | `.manifest.csv`, `.manifest.json` |

CSV manifests should include one of these columns: `path`, `file`, or `input`.
Any `.csv` input is treated as a manifest. Relative paths are resolved from the
manifest file's folder.

JSON manifests can be either a list of paths or an object with an `inputs` or `files`
array. JSON files without that shape are treated as regular text/OCR inputs. Relative
JSON manifest paths also resolve from the manifest file's folder.

## Outputs

Default:

- `txt`: clean, copy-paste-friendly text

Selectable:

- `md`: Markdown
- `html`: HTML
- `docx`: Word document
- `epub`: EPUB
- `json`: structured JSON
- `jsonl`: paragraph/chunk JSONL
- `yaml`: YAML
- `hocr`: hOCR sidecar
- `alto`: ALTO XML sidecar
- `pagexml`: PAGE XML sidecar
- `searchable-pdf`: setup note until PDF OCR backend is configured
- `image-pdf`: setup note until image PDF backend is configured
- `review`: run review notes and text preview

The current OCR/archive sidecars are portable text sidecars. They are not full
layout-accurate OCR exports unless a future native OCR/layout backend writes that data.

Every run also writes:

- `raw_ocr.txt`
- `restored_text.txt`
- `items/` with one numbered folder per input, such as `0001-page-one-png`
- `items/<input>/restored__LANG.txt`
- `items/<input>/restored__LANG.txt.json`
- `items/<input>/translated__SOURCE-to-TARGET.txt` when translation runs
- `items/<input>/final__LANG.txt`
- `items/<input>/final__LANG.txt.json`
- `stages/` with per-page and per-chunk checkpoint files
- `assets/` with opt-in candidate figure crops and sizing metadata in chunk records
- `run_state.json` with interruption/recovery state while a run is active
- `run_manifest.json`
- `sources/`

The run folder is the timestamped folder created under the selected output
folder. For example, if the output folder is `akshara-output`, a run folder might
be `akshara-output/default-20260706-120000`.

For PDFs, pages are rendered and restored incrementally, and restored stage files
are numbered by rendered page. Recursive folder inputs preserve their nested
folder labels under `items/`, `sources/`, and staged checkpoints. For zip
archives, nested folders are traversed recursively, inner files are labeled with
their archive-relative paths, and the same folder structure is mirrored under
`items/<zip>/archive/`. Nested folders get local `combined__LANG.txt` files. The
final export still combines the selected inputs into one document, but the
`items/` folder keeps each input easy to inspect separately.

`akv combine` rebuilds from structured item JSON first, then falls back to text
files. The priority is final JSON, translated JSON, restored JSON, final text,
translated text, and restored text. When the original manifest is available,
combine also rebuilds the run's selected export formats.

`akv resume <run-folder>` is the friendlier recovery command for interrupted
runs. It reads `run_state.json` when present, reports completed inputs, resumes
inside the original run folder when source inputs are available, and skips
already staged PDF pages, archive entries, and text chunks.

`run_state.json` may also contain a compact internal consistency guide. It is
used only to keep formatting uniform across similar pages in a local batch or
document and is not added to restored text outputs. The guide may record
encountered scripts such as Latin, Devanagari, or Kannada to help later pages
preserve repeated mixed-language patterns more consistently.

`run_manifest.json` includes `document_structure` and `assembly_profile` fields.
These are deterministic observations such as title candidates, section headings,
page markers, content kind counts, and target-format assembly hints. They improve
final assembly while keeping the restored text itself clean.

If figure/image enrichment is enabled, chunk records may also include `assets`
entries with path, width, height, DPI, aspect ratio, bounding box, and
recommended placement. These are conservative candidate figure crops, not
guaranteed layout-perfect segmentation. Akshara avoids saving whole pages as
figures and ignores tiny marks, cracks, or ambiguous noise.

When staged outputs are combined, Akshara rebuilds text from structured chunk
records where possible, re-inserts figure markers, carries asset metadata into
JSON/YAML exports, and renders linked figures in HTML/EPUB outputs.

For long runs, progress updates show token usage after each completed page,
image, text chunk, or translation chunk when the provider reports usage. The
final manifest still stores the aggregate usage summary.

Suspicious restoration output that looks malformed or gibberish-heavy can be
reviewed by the selected model before it is written to final item outputs. The
review prompt is constrained to fix only clear corruption while preserving
structure and source meaning. When review changes a chunk, `pre_review_text`
is kept in structured outputs so reviewers can audit what changed.

Markdown, HTML, DOCX, and EPUB exports use the detected title where possible.
HTML and EPUB add lightweight publication styling for paragraphs, page markers,
and figure markers while keeping the restored text unchanged.

`akv export` can take either a run folder or a compiled output file such as
`.txt`, `.md`, `.html`, `.json`, `.jsonl`, `.yaml`, `.hocr`, or `.xml`. It writes
a converted copy in the selected output format without re-running extraction.

When a model returns partial text because its output limit was reached, the
manifest marks that source or chunk as `partial` and records
`model context or output limit reached`. The text already returned by the model
is still saved in `items/` and `stages/`.

Blank pages or pages with no readable text are saved as empty text, marked as
`blank` in the manifest, and kept out of final text exports so JSON or diagnostic
markers do not leak into copy-paste outputs.

Run manifests store project-relative paths when possible so local user directories are
not leaked by default.
