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

1. **Setup Virtual Environment and Python Dependencies**:

   **macOS / Linux**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

   **Windows PowerShell**:
   ```powershell
   python -m venv .venv
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   ```

2. **Install System Dependencies (Poppler)**:
   ```bash
   akv install
   ```

If the `akv` command is not recognized, you can run:
```bash
python -m akshara_vision install
```

## Start

```bash
akshara
```

The module entrypoint is also available anywhere the package is installed:

```bash
python -m akshara_vision
```

Inside the interactive shell:

```text
/menu
/guide
/mode
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
? Model provider ollama / lm-studio / openai / gemini / mock
? Execution mode balanced
? Output formats txt, md, json, review
? Lock this profile as the default quick-run workflow? Yes
```

Execution mode changes how hard the pipeline works:

| Mode | What it changes |
| --- | --- |
| `fast` | Uses lighter OCR defaults, shorter provider timeouts, and a more throughput-focused prompt. |
| `balanced` | Keeps the default mix of speed and fidelity. |
| `quality` | Uses heavier OCR defaults, longer provider timeouts, and a more fidelity-focused prompt. |

Restoration now asks the model for a JSON object with `restored_text`, `uncertain`, and
`notes`, then extracts the cleaned text for the text-based outputs. Long inputs are split
into smaller restoration chunks so the model works on smaller batches instead of one huge
prompt.

Progress shown during a run is timer-based and indeterminate. You will see the active
step and elapsed time, not a fabricated percentage or word count.

## Common Commands

| Command | Use |
| --- | --- |
| `akv i` | Onboard and create defaults |
| `akv setup` | Install system dependencies (Poppler) |
| `akv r <input>` | Guided run |
| `akv q <input>` | Quick run with defaults |
| `akv b <folder>` | Batch process |
| `akv p` | Open profile manager |
| `akv m setup` | Choose and save a model |
| `akv env` | Show API key and endpoint setup |
| `akv d` | Check OCR tools and models |
| `akv install` | Install/check PDF rendering dependencies |
| `akv check` / `akv t` | Compile and run unit tests |
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

### Multimodal Vision Models

Akshara Vision supports handing over raw document page files directly to multimodal/vision-capable LLMs. Pages are analyzed visually by the LLM instead of converting them via local command-line OCR tools.

To run page-by-page PDF analysis in multimodal mode, make sure system dependencies are installed and configured via:
```bash
akv install
```

Recommended vision-capable models include:
- **Ollama / Local**: `gemma4:12b`, `qwen3.6:27b`, `qwen3.5:4b`, `llama3.2-vision:11b`
- **LM Studio / Jan**: Any GGUF variants of Llama 3.2 Vision, Qwen 3.6, or Gemma 4
- **Gemini (Cloud)**: `gemini-3.5-flash`, `gemini-3.5-pro`, `gemini-3.1-flash-lite`
- **OpenAI (Cloud)**: `gpt-5.5`, `gpt-5.4`
- **Anthropic (Cloud)**: `claude-sonnet-5`, `claude-fable-5`

If a selected model does not support image or PDF vision inputs, the pipeline will immediately fail-safe with a clear explanation, avoiding corrupted or silently failed output text.

### Context Window & Token Limits

To prevent model truncation during deep reasoning or manuscript transcription:
- **Context Size (`num_ctx`):** A minimum of **16,384 tokens** is required to accommodate system prompts, page content, and image embeddings.
- **Generation Limit (`num_predict` / `max_tokens`):** Akshara Vision now scales output up to a hard limit of **16,384 tokens** for local and compatible providers.
- If a completion is still truncated due to model/runtime constraints, the tool reports a warning at the end of the run for full transparency. Increase the model context window or split the input into smaller pages if needed.

## Inputs And Outputs

Inputs: PDFs, images, text/OCR files, ZIP archives, folders, globs, CSV manifests,
and JSON manifests.

Default output: clean `.txt`.

Optional outputs: Markdown, HTML, DOCX, EPUB, JSON, JSONL, YAML, hOCR, ALTO XML,
PAGE XML, review files, and run manifests.

JSON output includes the structured restoration metadata from the run, including chunk
records and uncertainty notes.

Note: PDF rendering for multimodal mode depends on local tools such as `pdftoppm`.

The active execution mode affects prompting depth and model timeouts, but it does not change
your selected input files or output formats.

## Test

```bash
akv check
akv q examples/sample.txt --dry-run
```

`compileall` is a Python module, so run it directly as `python -m compileall` if
you need the raw command. For normal project checks, use `akv check`.
