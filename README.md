# Akshara Vision

Akshara Vision is a local-first terminal app for OCR cleanup, model-assisted
text restoration, optional translation prompts, and archival export workflows.

It is built for keyboard-first work: interactive boards, dropdowns, checkboxes,
profiles, local/cloud model setup, and clean run manifests.

## Terminal Snapshot

```text
============================================================================
        _        _  __  _____  _   _       _       ____        _
       / \      | |/ / / ___/ | | | |     / \     |  _ \      / \
      / _ \     | ' /  \___ \ | |_| |    / _ \    | |_) |    / _ \
     / ___ \    | . \   ___) ||  _  |   / ___ \   |  _ <    / ___ \
    /_/   \_\   |_|\_\ |____/ |_| |_|  /_/   \_\  |_| \_\  /_/   \_\
                              V I S I O N
                              AKSHARA VISION
                         Restore. Read. Preserve.
============================================================================

Board
-----
+-------------------------------+  +-------------------------------+
| Run                           |  | Quick                         |
| /run                          |  | /quick                        |
| Full guided workflow          |  | Use saved defaults            |
+-------------------------------+  +-------------------------------+
```

Run progress and completion:

```text
Working
-------
  Run complete ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 10/10 0:00:00

============================================================================
                               AKSHARA VISION
                                  Finished
============================================================================
SUCCESS  Run completed.

Output
------
Run folder  akshara-output/<profile-run>
Manifest    akshara-output/<profile-run>/run_manifest.json
Exports     1
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## Start

```bash
akshara
```

Inside the interactive shell:

```text
/menu
/guide
/ui
/env
/models
/quick examples/sample.txt --dry-run
/exit
```

Onboarding looks like this:

```text
============================================================================
                               AKSHARA VISION
                                Onboarding
============================================================================
? Profile name default
? Workflow Full pipeline
? Document type Book
? Source language auto
? Output language same
? Translation mode off
? OCR/decode mode auto
? Model provider ollama / lm-studio / openai / gemini / mock
? Output formats txt, md, json, review
? Lock this profile as the default quick-run workflow? Yes
```

## Common Commands

| Command | Use |
| --- | --- |
| `akv i` | Onboard and create defaults |
| `akv r <input>` | Guided run |
| `akv q <input>` | Quick run with defaults |
| `akv b <folder>` | Batch process |
| `akv m setup` | Choose and save a model |
| `akv env` | Show API key and endpoint setup |
| `akv d` | Check OCR tools and models |
| `akv clean` | Remove generated local artifacts |

## Models And API Keys

Local-first options:

- Ollama
- LM Studio
- Jan
- llama.cpp/OpenAI-compatible servers

Cloud options:

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

Secrets are read from the shell or `.env`; they are not saved to profiles or manifests.

## Inputs And Outputs

Inputs: PDFs, images, text/OCR files, ZIP archives, folders, globs, CSV manifests,
and JSON manifests.

Default output: clean `.txt`.

Optional outputs: Markdown, HTML, DOCX, EPUB, JSON, JSONL, YAML, hOCR, ALTO XML,
PAGE XML, review files, and run manifests.

Note: OCR layout sidecars are portable text sidecars unless a native OCR/layout backend
is installed. PDF/image OCR depends on local tools such as `pdftotext`, `pdftoppm`, and
`tesseract`.

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
akv q examples/sample.txt --dry-run
```
