```text
=================================================================================
                     _    _  __ ____  _   _    _    ____      _
                    / \  | |/ // ___|| | | |  / \  |  _ \    / \
                   / _ \ | ' / \___ \| |_| | / _ \ | |_) |  / _ \
                  / ___ \| . \  ___) |  _  |/ ___ \|  _ <  / ___ \
                 /_/   \_\_|\_\|____/|_| |_/_/   \_\_| \_\/_/   \_\
                                              V I S I O N
                                   AKSHARA VISION
                              Restore. Read. Preserve.
             Choose a workflow, inspect the plan, then run only when ready.
=================================================================================
```

Akshara Vision is a local-first terminal application for OCR cleanup,
model-assisted book restoration, final-stage translation, batch processing, and
archival exports.

It is designed for keyboard-first archival work: guided onboarding, interactive
menus, reusable profiles, model selection, API key checks, batch input discovery,
transparent run manifests, and clean text-first outputs.

Accuracy, translation quality, and language coverage depend on the selected
model, provider, scan quality, script complexity, and document damage.

## Features

| Area | Support |
| --- | --- |
| Interactive CLI | Monochrome terminal UI, home board, dropdowns, checkboxes, confirmations, profile manager, model setup, doctor checks |
| Restoration | Text cleanup, OCR error correction, uncertainty markers, chunked long-text processing, raw OCR preservation |
| Vision input | Direct multimodal processing for scanned images and rendered PDF pages with vision-capable models |
| Translation | Automatic final-pass translation when output language differs from source language; manual modes for translate, bilingual, transliterate, and metadata-only workflows |
| Batch processing | Files, folders, recursive folders, globs, ZIP archives, CSV manifests, and JSON manifests |
| Profiles | Portable TOML profiles with defaults for workflow, languages, translation mode, model, output formats, destination, and locked quick runs |
| Models | Ollama, LM Studio, Jan, llama.cpp/OpenAI-compatible local servers, OpenAI, Anthropic, Gemini, and mock/offline preview |
| Exports | Text, Markdown, HTML, DOCX, EPUB, JSON, JSONL, YAML, OCR sidecars, review files, and PDF request notes |
| Auditability | Raw OCR file, restored checkpoint, staged per-page/per-chunk outputs, copied source inputs, structured run manifest, model usage metadata, truncation warnings, and failure reasons |

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

## Quick Start

```bash
akshara
akv i
akv q examples/sample.txt
```

Inside the interactive shell:

```text
/menu
/run
/quick examples/sample.txt --dry-run
/batch path/to/folder
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
| `akshara profile` | `akv p` | Create, modify, duplicate, delete, import, export, lock, or switch profiles |
| `akshara model` | `akv m` | Detect, test, and choose local/cloud models |
| `akshara instruct` | `akv ins` | View, edit, reset, or install editable instructions |
| `akshara doctor` | `akv d` | Check dependencies, model providers, API keys, and export support |
| `akshara combine` | `akv combine` | Rebuild a final document from staged outputs |
| `akshara export` | `akv x` | Re-export an existing run |
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
missing or unsupported inputs in the run manifest. Batch outputs are numbered
and grouped by original input name so images, PDFs, archive members, and text
files remain easy to inspect after a run.

## Supported Outputs

| Type | Formats |
| --- | --- |
| Default | Clean copy-paste `.txt` |
| Publishing | `.md`, `.html`, `.docx`, `.epub` |
| Structured | `.json`, `.jsonl`, `.yaml` |
| OCR sidecars | `.hocr`, `.alto.xml`, `.page.xml` sidecar placeholders with restored text |
| Review | `.review.md`, `raw_ocr.txt`, copied source files, `run_manifest.json` |
| PDF requests | `.searchable-pdf.txt`, `.image-pdf.txt` notes when optional PDF assembly dependencies are not available |

The `.txt` export is the primary default. Structured exports include metadata
for inputs, provider, model, workflow, translation state, usage, restoration
chunks, uncertainty notes, and failure reasons.

Each run also writes `items/` for human-friendly per-input outputs and `stages/`
for recoverable page/chunk checkpoints. Interrupted runs can be recombined later
without reprocessing completed pages or chunks.

## Translation

Translation runs after extraction and restoration are complete. This keeps the
first pass focused on faithfully reading the source and uses the model only once
the cleaned text is available.

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

If a long run is interrupted, use `akv combine <run-folder>` to rebuild the
final document from the staged files on disk.

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

Create a private `.env` from the template:

```bash
cp .env.example .env
```

Then fill only what you use:

```bash
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
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
| `fast` | Shorter provider timeout and faster extraction prompt |
| `balanced` | Default balance between speed and fidelity |
| `quality` | Longer provider timeout and more careful extraction prompt |

Akshara Vision requests up to 16,384 output tokens for compatible local and
OpenAI-compatible providers. If a model still truncates output, the run finishes
with a visible warning and records the reason in the manifest.

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
- Native searchable/image PDF assembly is represented by explicit request-note
  files unless optional PDF assembly support is added.
- OCR sidecar exports are portable placeholders containing restored text until a
  dedicated OCR layout engine writes native layout data.
- Model output should be reviewed before publication, especially for damaged
  pages, rare scripts, tables, handwriting, or translated passages.
