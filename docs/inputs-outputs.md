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
- `items/<input>/translated__SOURCE-to-TARGET.txt` when translation runs
- `items/<input>/final__LANG.txt`
- `stages/` with per-page and per-chunk checkpoint files
- `run_manifest.json`
- `sources/`

For PDFs, restored stage files are numbered by rendered page. For zip archives,
inner files are processed in sorted order and kept under the archive's numbered
run item. The final export still combines the selected inputs into one document,
but the `items/` folder keeps each input easy to inspect separately.

When a model returns partial text because its output limit was reached, the
manifest marks that source or chunk as `partial` and records
`model context or output limit reached`. The text already returned by the model
is still saved in `items/` and `stages/`.

Run manifests store project-relative paths when possible so local user directories are
not leaked by default.
