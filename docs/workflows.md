# Workflows

## Full Pipeline

```bash
akv r path/to/book.pdf
```

Or inside the interactive session:

```text
akshara: /run path/to/book.pdf
```

Stages:

1. Select inputs.
2. Choose the output folder for this run.
3. Validate dependencies.
4. Render PDF/Zip pages if media, or read raw text.
5. Clean/Restore text with the selected model (multimodal visual transcription for images/PDFs, or prompt-based restoration for raw text).
6. Save per-input files under `items/` and recoverable checkpoints under `stages/`.
7. Optionally request translation through the selected model/profile, preserving each input as its own numbered output.
8. Export selected formats.
9. Write manifest and review files.

Execution modes:

| Mode | Tradeoff |
| --- | --- |
| `fast` | Faster runs with shorter model timeouts and a more throughput-focused prompt |
| `balanced` | Default balance of speed and fidelity |
| `quality` | Slower runs with longer model timeouts and a more fidelity-focused deep analysis prompt |

The run uses chunked restoration for long raw text inputs, so it is processed in
smaller model batches instead of one large prompt. Progress is timer-based and indeterminate;
it shows the active step and elapsed time rather than a fake percentage.

Interrupted runs can be rebuilt later with:

```bash
akv combine path/to/run-folder
```

## Locked Quick Run

```bash
akv q scans/
```

Uses the locked default profile and asks only for input files if none are passed.

## Batch Processing

```bash
akv b scans/
```

Batch mode discovers supported files recursively. Each input is saved under a
numbered `items/` folder using the original filename, so mixed images, PDFs,
archives, and text files remain easy to identify after restoration or translation.

## Cleanup

Remove local generated outputs and build artifacts:

```bash
akv clean
```

## Text-Based Restoration Only

If the inputs are plain text files (e.g. `.txt`, `.md`), the pipeline automatically skips visual rendering and runs text-only restoration prompts.

```bash
akv q raw-ocr.txt
```

This is useful when text extraction was done elsewhere and you only want restoration, cleanup,
translation, or export.
