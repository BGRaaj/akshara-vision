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
| `fast` | 300 DPI, shorter prompt, heuristic figure crops |
| `balanced` | 400 DPI, default prompt, verifies first figure crop |
| `quality` | 500 DPI, more careful prompt, verifies figure crops |

The run uses chunked restoration for long raw text inputs, so it is processed in
smaller model batches instead of one large prompt. Progress is timer-based and indeterminate;
it shows the active step and elapsed time rather than a fake percentage. After a
page, image, text chunk, or translation chunk completes, the progress line also
shows item token usage and cumulative run token totals in plain language when
the provider reports usage.

While a provider request is active, Akshara periodically reports that the model
is still working. Pressing `Ctrl+C` during that window shows a safe-stop message
and waits for the active request to finish before returning control, so already
written checkpoints remain usable.

Provider requests wait indefinitely by default. In profiles and before each
interactive run, users may choose an explicit slow-page policy such as skip
after 5, 10, 20, 30, or 60 minutes. If a provider returns a transient network,
rate-limit, timeout, or server error, Akshara Vision retries with exponential
backoff and records failures in the run state instead of corrupting the final
output. In batch runs, a failed input is written as a failed item and later
inputs continue.

Set `AKSHARA_PROVIDER_RETRIES` if a slow cloud provider needs more retries. The
default is `3`; the accepted range is `0` to `10`.

For image and PDF vision runs, each image or rendered page is sent as its own
model request. PDFs are rendered one page at a time, so large books start
producing page checkpoints without waiting for every page to be converted first.
Restored text is written before translation starts. Translation is then performed
as separate text-only requests over smaller restored chunks, which keeps the
translation prompt free from the original image context.

If a restored page or chunk looks malformed, JSON-like, or gibberish-heavy,
Akshara Vision runs a constrained review pass before checkpointing it. That pass
is allowed to fix only clear OCR/restoration corruption. It must preserve
structure, page order, uncertainty markers, and unfinished sentences that may
continue on the next page.

Akshara Vision also keeps a compact local consistency guide during the run. It
learns only formatting signals such as paragraph spacing, heading style, page
markers, lists, and table spacing from completed pages. Later pages receive that
guide as context so similar pages are formatted consistently. It does not add
facts, does not override the restoration instructions, and is not emitted into
the final text.

The selected document type also changes extraction guidance and deterministic
tagging. Books emphasize title matter, contents, chapters, page numbers,
prefaces, indexes, and footnotes. Magazines and newspapers emphasize column
order, article boundaries, headlines, mastheads, bylines, captions,
advertisements, classifieds, sidebars, and multi-column flow. Manuscripts
emphasize folios, marginalia, corrections, colophons, lineated text, uncertain
readings, and damaged text. Journal articles, letters, and archive bundles also
receive their own role sets. The run manifest records semantic units, layout
classes, content features, contents entries, headings, page markers, footnotes,
and figure metadata for later assembly.

The CLI asks whether to enable figure/image enrichment before a run. When
enabled, prompts may insert concise `[image: ...]` markers for visible
illustrations, maps, plates, or diagrams, and Akshara stores conservative
candidate figure crops with bounding boxes, relative page coordinates, page
zones, size, DPI, and placement metadata.
This is disabled by default so normal restored text remains clean. The cropper
does not claim full layout-perfect segmentation; it ignores tiny marks and
ambiguous damage instead of treating them as figures.

The CLI also asks how to handle languages:

| Mode | Behavior |
| --- | --- |
| `preserve-detected` | Default. Preserve every readable language/script visible in the source, without forcing labels or translation. |
| `strict-source` | Extract only the declared source language/script, while preserving necessary names, citations, and technical terms. |

Dense pages and non-English scripts still depend heavily on the chosen vision
model. Quality mode gives the model stronger page-order, region-by-region, and
script-specific instructions. If a model hits its output limit, the run is marked
partial with `model context or output limit reached`.

Interrupted runs can be rebuilt later with:

```bash
akv resume path/to/run-folder
akv combine path/to/run-folder
```

Combine prefers the human-facing `items/*/final__*.txt` files. If a run stopped
before final item files were written, it falls back to translated stage chunks
and then restored stage chunks. `resume` is the friendly recovery command; when
the original inputs are available, it resumes inside the same run folder and
skips existing PDF pages, archive entries, and staged text chunks. If the inputs
are unavailable, it combines whatever completed checkpoints are already present.

Press `Ctrl+C` to stop a long run. Completed pages and sources remain on disk
under `items/` and `stages/`, and the CLI prints the latest run folder for
recovery. If a model request is active, Akshara acknowledges the interrupt and
waits for that request to finish cleanly.

A run folder is the timestamped folder inside the selected output folder, not the
source folder. Example: `akshara-output/default-20260706-120000`.

## Re-Export Existing Output

```bash
akv x path/to/run-folder --format epub
akv x path/to/akshara_output.txt --format docx
```

Export accepts either a run folder or a compiled text-like output file. This
creates a new converted copy and does not call the model again.

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
numbered `items/` folder using the original filename. Nested folders are mirrored
under `items/` and `sources/`, so mixed images, PDFs, archives, and text files
remain easy to identify after restoration or translation.

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
