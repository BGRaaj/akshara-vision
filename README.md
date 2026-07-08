```text
=================================================================================
                     _    _  __ ____  _   _    _    ____      _
                    / \  | |/ // ___|| | | |  / \  |  _ \    / \
                   / _ \ | ' / \___ \| |_| | / _ \ | |_) |  / _ \
                  / ___ \| . \  ___) |  _  |/ ___ \|  _ <  / ___ \
                 /_/   \_\_|\_\|____/|_| |_/_/   \_\_| \_\/_/   \_\
                                              V I S I O N
                              Restore. Read. Preserve.
=================================================================================
```

Akshara Vision is a local-first document-intelligence agent for restoring,
understanding, reviewing, translating, assembling, and exporting scanned
documents.

It is designed for serious keyboard-first document work: guided onboarding,
interactive menus, reusable profiles, model routing, batch input discovery,
native page-layout analysis, transparent run manifests, grounded file chat,
review tools, and publication-style outputs.

Accuracy, translation quality, and language coverage depend on the selected
model, provider, scan quality, script complexity, and document damage.

## Features

| Area | Support |
| --- | --- |
| Interactive CLI | Monochrome terminal UI with orange accent branding, home board, dropdowns, checkboxes, confirmations, profile manager, model setup, doctor checks |
| Restoration | Text cleanup, OCR error correction, uncertainty markers, chunked long-text processing, raw OCR preservation |
| Vision input | Direct multimodal processing for scanned images and rendered PDF pages with dense-page and multilingual-script extraction guidance |
| Document intelligence | Document-type-specific extraction guidance plus semantic roles, native page block geometry, optional ML layout backends, layout hints, and detected structure metadata for books, manuscripts, magazines, newspapers, articles, letters, and archive bundles |
| Chat | Grounded `akv chat` / `akv ask` mode over runs, outputs, raw files, or a single direct image with source citations, saved history, source search, source opening, and page-aware visual rechecks when indexed text is incomplete |
| Assembly enrichment | Optional figure markers plus candidate figure crops with bounding boxes, page zones, relative positions, size, DPI, and placement metadata |
| Review | `akv review` visual block-map preview, low-confidence block audit, and asset inspection for run folders |
| Compare | `akv compare` side-by-side source/output browser report for run folders |
| Language handling | Per-run choice to preserve all readable detected languages/scripts or strictly extract only the declared source language |
| Translation | Automatic final-pass translation when output language differs from source language; manual modes for translate, bilingual, transliterate, and metadata-only workflows |
| Batch processing | Files, folders, recursive folders, globs, ZIP archives, CSV manifests, and JSON manifests |
| Profiles | Portable TOML profiles with defaults for workflow, languages, translation mode, model, output formats, destination, and locked quick runs |
| Models | Ollama, LM Studio, Jan, llama.cpp/OpenAI-compatible local servers, Sarvam, native cloud providers, OpenRouter, and other OpenAI-compatible cloud APIs |
| Reliability | Long model calls wait for completion, transient provider failures retry with backoff, and failed batch items are tracked without corrupting later outputs |
| Exports | Text, Markdown, HTML, DOCX, EPUB, JSON, JSONL, YAML, OCR sidecars, review files, and publication-style PDF outputs |
| Auditability | Live token metrics during long runs, raw OCR file, restored checkpoint, JSON sidecars, staged per-page/per-chunk outputs, copied source inputs, structured run manifest, model usage metadata, truncation warnings, and failure reasons |

## Install

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
akv install
```

### Windows PowerShell

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
akv install
```

If the command is not on PATH yet, use the module entrypoint:

```bash
python -m akshara_vision
python -m akshara_vision install
```

Optional ML layout adapters are available for harder magazines, tables, and
complex page layouts:

```bash
python -m pip install -e ".[layout]"
```

For best PDF output, Akshara Vision prefers an HTML-to-PDF renderer already
available on your machine. It will try `weasyprint`, `wkhtmltopdf`, or a
headless Chromium/Chrome browser before falling back to the built-in PDF path.

## Quick Start

```bash
akshara
akv i
akv q examples/sample.txt
akv chat examples/sample.txt --question "What is this file about?"
```

Inside the interactive shell:

```text
/menu
/run
/quick examples/sample.txt --dry-run
/chat examples/sample.txt --question "What is the main topic?"
/batch path/to/folder
/compare path/to/run-folder
/profiles
/models
/env
/doctor
/exit
```

## Commands

| Command | Alias | Use |
| --- | --- | --- |
| `akshara` | `akv` | Open the interactive home screen |
| `akshara init` | `akv i` | Guided onboarding and default profile creation |
| `akshara run` | `akv r` | Guided full workflow |
| `akshara quick` | `akv q` | Run with saved defaults |
| `akshara batch` | `akv b` | Process folders, manifests, and mixed batches |
| `akshara chat` | `akv chat` | Ask grounded questions over runs or files |
| `akshara profile` | `akv p` | Create, modify, duplicate, delete, import, export, lock, or switch profiles |
| `akshara model` | `akv m` | Detect, test, and choose local/cloud models |
| `akshara instruct` | `akv ins` | View, edit, reset, or install editable instructions |
| `akshara doctor` | `akv d` | Check dependencies, model providers, API keys, and export support |
| `akshara combine` | `akv combine` | Rebuild a final document from staged outputs |
| `akshara resume` | `akv resume` | Recover completed checkpoints from an interrupted run |
| `akshara review` | `akv review` | Inspect layout, block confidence, and figure assets |
| `akshara compare` | `akv compare` | Render source and output side by side |
| `akshara export` | `akv x` | Re-export an existing run or convert an existing output file |
| `akshara check` | `akv t` | Compile and run unit tests |
| `akshara clean` | `akv clean` | Remove generated local artifacts |

## Supported Inputs

| Type | Formats |
| --- | --- |
| PDFs | `.pdf` |
| Images | `.jpg`, `.jpeg`, `.png`, `.webp`, `.tif`, `.tiff`, `.bmp` |
| Text/OCR | `.txt`, `.md`, `.html`, `.hocr`, `.xml`, `.json` |
| Archives | `.zip` |
| Manifests | `.csv`, `.manifest.json`, JSON files with `inputs` or `files` |
| Selection | Single files, multiple paths, folders, recursive folders, and glob patterns |

Mixed batches are supported. Akshara Vision detects each file type and records
missing or unsupported inputs in the run manifest. Batch outputs are numbered,
grouped by input name, and mirror nested folder paths when recursive folders are
processed.

## Supported Outputs

| Type | Formats |
| --- | --- |
| Default | Clean copy-paste `.txt` |
| Publishing | `.md`, `.html`, `.docx`, `.epub` |
| Structured | `.json`, `.jsonl`, `.yaml` |
| OCR sidecars | `.hocr`, `.alto.xml`, `.page.xml` portable sidecars with restored text and available metadata |
| Review | `.review.md`, `raw_ocr.txt`, copied source files, `run_manifest.json` |
| PDF exports | `.searchable.pdf`, `.image.pdf` structured PDF reports built from restored text and run metadata |

The `.txt` export is the primary default. Structured exports include metadata
for inputs, provider, model, workflow, translation state, usage, restoration
chunks, uncertainty notes, and failure reasons.

Markdown, HTML, DOCX, and EPUB exports use the detected title and simple
publication-oriented structure. HTML and EPUB preserve paragraph breaks, center
page-marker-like lines, and style figure markers separately when present.

The export layer also uses semantic document roles. A title page, contents
page, chapter opening, journal abstract, letter signature, or archive record is
treated differently from ordinary body text when the structure is clear.

Each run also writes `items/` for human-friendly per-input outputs and `stages/`
for recoverable page/chunk checkpoints. Interrupted runs can be recombined later
without reprocessing completed pages or chunks. Recombine restores the run's
selected export formats when the original manifest is available.

When figure/image enrichment is enabled in the CLI, Akshara may add concise
`[image: ... | assets/...]` markers for visible figures and saves candidate
figure crops under `assets/` with bounding boxes, width, height, DPI, aspect
ratio, and recommended placement metadata. Markdown, HTML, EPUB, and JSON
exports preserve this metadata; HTML/EPUB render linked figure images when
present. It avoids saving every full page as a figure. The cropper is
conservative and heuristic; unclear page damage, cracks, and tiny marks are
left alone rather than treated as illustrations.

Language handling is selected in the CLI before each run. `preserve-detected`
keeps readable mixed-language snippets in their original script. `strict-source`
asks the model to extract only the declared source language while preserving
necessary names, citations, and technical terms.

A run folder is the timestamped folder created inside your chosen output folder,
for example `akshara-output/default-20260706-120000`. It is not the folder that
contains your source images.

Large PDFs are rendered and restored page by page. The CLI shows the current
page being rendered or restored instead of waiting for the entire PDF to convert
before the model starts.

After each page, image, or text chunk model call, the progress line includes
plain-language token usage for that item and cumulative run totals. Suspicious
restorations that look malformed or gibberish-like are sent through a constrained
review pass before they are checkpointed.

Akshara also tags restored chunks with deterministic semantic roles. Books get
roles such as title matter, contents, preface, chapter, section, appendix, index,
and notes. Magazines and newspapers add roles such as masthead, headline,
article, feature, advertisement, classifieds, and multi-column flow. Manuscripts,
letters, journal articles, and archive bundles get their own role sets. Contents
entries are parsed into title/page pairs when clear, so publishing exports can
build more useful document structure without changing the restored text.

The manifest now also stores per-page `native_layout` blocks plus a
`layout_tree`, `layout_profile`, and `assembly_profile`. Those fields capture
reading order, page flow, role, layout class, native block geometry, page
markers, headings, confidence, and figure metadata so chat, combine, resume, and
export all see the same structure.

Blank pages are preserved as empty outputs and marked as `blank` in the manifest.
If a model accidentally returns JSON-like text for a page, Akshara Vision extracts
the restored text field before writing `.txt` outputs.

The default instruction preset is strict on purpose. It asks the model to
preserve reading order, ignore mirrored bleed-through, mark uncertainty honestly,
return empty output for blank pages, and keep translation as a separate final
pass. The model should not leak wrapper JSON or diagnostic text into ordinary
exports.

Recursive folders keep their nested structure under `items/` and `sources/`.
ZIP archives keep their nested folder structure under `items/<zip>/archive/`.
Nested folders get local `combined__LANG.txt` files, so folder-level batches can
be reviewed without mixing the whole run.

During a run, Akshara Vision keeps a small internal consistency guide for the
current batch or document. It learns recurring layout hints such as paragraph
spacing, heading style, page markers, lists, and table spacing, then passes those
hints to later pages for more uniform formatting. This guide never replaces the
main restoration instruction and is not printed into restored outputs.

See also:

- [docs/document-intelligence.md](docs/document-intelligence.md)
- [docs/chat.md](docs/chat.md)
- [docs/workflows.md](docs/workflows.md)
- [docs/inputs-outputs.md](docs/inputs-outputs.md)

## Translation

Translation runs after extraction and restoration are complete. This keeps the
first pass focused on faithfully reading the source and uses the model only once
the cleaned text is available.

Extraction is saved before translation begins. Translation is then sent as fresh,
smaller text-only model calls, so image context does not consume the translation
budget. Dense scans can still hit a model's output limit; when that happens,
Akshara Vision marks the source as partial and records the reason in the manifest.

Generation limits are passed through to the selected backend. Akshara Vision does
not impose its own fixed maximum; local/cloud providers may still enforce their
own model limits.

Translation modes:

| Mode | Behavior |
| --- | --- |
| `auto` | Translates when output language differs from source language |
| `off` | Keeps restored source text only |
| `translate` | Outputs translated text |
| `bilingual` | Outputs restored source text followed by translation |
| `transliterate` | Keeps the meaning and asks the model to rewrite the text in another script |
| `metadata-only` | Skips translation and focuses on extracted metadata and audit fields |
| `same-language-cleanup` | Cleans the source text without changing its language |

If a profile has `source_language = English`, `output_language = Hindi`, and translation
is `auto` or `off`, the CLI resolves it as `auto -> translate` before the run.

Language fields accept full names or local labels such as `English`,
`Hindi`, or `Kannada`, and the match is case-insensitive.

If a long run is interrupted, use `akv resume <run-folder>` to recover completed
checkpoints into final outputs. Use `akv combine <run-folder>` when you want to
rebuild whatever is currently present for testing.

To create another output format without running extraction again, pass either a
run folder or an existing compiled output file:

```bash
akv export akshara-output/default-20260706-120000 --format epub
akv export akshara-output/default-20260706-120000/akshara_output.txt --format docx
```

## Models And API Keys

Local-first providers:

- Ollama
- LM Studio
- Jan
- llama.cpp/OpenAI-compatible servers

Cloud providers:

- OpenAI
- Anthropic
- Gemini
- OpenRouter, Groq, Mistral, Together, Fireworks, Perplexity, DeepSeek, xAI, Cerebras
- Any custom OpenAI-compatible cloud endpoint

Create a private `.env` from the template:

```bash
cp .env.example .env
```

Then fill only what you use:

```bash
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=
GROQ_API_KEY=
MISTRAL_API_KEY=
AKSHARA_CUSTOM_API_KEY=
AKSHARA_CUSTOM_OPENAI_COMPATIBLE_BASE_URL=https://api.example.com/v1
AKSHARA_OPENAI_COMPATIBLE_BASE_URL=http://localhost:1234/v1
AKSHARA_OPENAI_COMPATIBLE_API_KEY=
```

Secrets are read from the shell or `.env`; they are not saved to profiles or
run manifests.

Recommended vision-capable models include:

| Provider | Models |
| --- | --- |
| Ollama / Local | `gemma4:12b`, `qwen3.6:27b`, `qwen3.5:4b`, `llama3.2-vision:11b` |
| LM Studio / Jan | GGUF variants of Gemma 4, Qwen 3.6, Qwen 3.5, or Llama 3.2 Vision |
| Gemini | `gemini-3.5-flash`, `gemini-3.5-pro`, `gemini-3.1-flash-lite` |
| OpenAI | `gpt-5.5`, `gpt-5.4` |
| Anthropic | `claude-sonnet-5`, `claude-fable-5` |
| OpenAI-compatible clouds | Detected from `/models` when available, or entered manually as an exact provider model id |

For scanned images and PDFs, choose a vision-capable model. If the selected
model does not support image input, the pipeline fails with a clear explanation
instead of silently writing corrupted output.

## Profiles

Profiles are portable TOML files stored in the Akshara Vision config directory.
They can be created, modified, duplicated, deleted, exported, imported, locked,
and used as the default quick-run workflow.

A profile stores:

- Workflow and document type
- Source and output languages
- Translation mode
- Provider, model, endpoint, execution mode, context window, and generation limit
- Output formats and destination folder
- Instruction preset
- Lock/default status

## Execution Modes

| Mode | Behavior |
| --- | --- |
| `fast` | 300 DPI PDF rendering, shorter prompt, heuristic figure crops |
| `balanced` | 400 DPI PDF rendering, default prompt, verifies first figure crop |
| `quality` | 500 DPI PDF rendering, more careful prompt, verifies figure crops |

Profile context and generation limits are passed through to compatible backends.
The CLI offers suggested values, but does not artificially cap them. If a model
truncates output, the run finishes with a visible warning and records the reason
in the manifest.

Actual model calls wait indefinitely by default. In the CLI, profiles and each
interactive run can optionally choose a slow-page policy such as skip after 5,
10, 20, 30, or 60 minutes. Use `Ctrl+C` to interrupt safely when you want to
pause a long run. If a provider call is active, the CLI acknowledges the
interrupt and waits for the current request to finish so completed checkpoints
stay consistent.

## Run Artifacts

Each run writes a timestamped folder under the configured output directory.

Typical files:

- `akshara_output.txt`
- `raw_ocr.txt`
- `run_manifest.json`
- `sources/`
- Any selected additional exports

The manifest is the source of truth for audit data: selected profile, inputs,
missing files, unsupported files, provider, model, instruction preset,
translation status, token usage, restoration chunks, warnings, and exported
file paths.

## Development

```bash
akv check
akv q examples/sample.txt --dry-run
python -m compileall src tests
```

`compileall` is a Python module, so run it as `python -m compileall`. For normal
project checks, use `akv check`.

## Notes

- PDF page rendering for multimodal processing depends on Poppler `pdftoppm`.
- PDF exports are generated as publication-style documents from restored text and
  available figure assets. Searchable PDF remains text-first; image PDF is a
  composed reading PDF.
- OCR sidecar exports are portable text sidecars with available restored text and
  metadata. They are not replacements for specialized hOCR/ALTO/PAGE output from
  a dedicated OCR segmentation model.
- Model output should be reviewed before publication, especially for damaged
  pages, rare scripts, tables, handwriting, or translated passages.
