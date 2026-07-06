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
2. Validate dependencies.
3. Restore or prepare pages.
4. Decode embedded text or OCR.
5. Clean text with the selected model.
6. Optionally request translation through the selected model/profile.
7. Export selected formats.
8. Write manifest and review files.

## Locked Quick Run

```bash
akv q scans/
```

Uses the locked default profile and asks only for input files if none are passed.

## Batch Processing

```bash
akv b scans/
```

Batch mode discovers supported files recursively.

## Cleanup

Remove local generated outputs and build artifacts:

```bash
akv clean
```

## Text Cleanup Only

Create a profile with OCR mode `text-cleanup-only`, then run:

```bash
akv q raw-ocr.txt
```

This is useful when OCR was done elsewhere and the user wants restoration, cleanup,
translation, or export only.
