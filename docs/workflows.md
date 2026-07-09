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
7. Analyze native page layout when enabled, then tag structure and layout
   metadata for later assembly.
8. Optionally request translation through the selected model/profile, preserving each input as its own numbered output.
9. Export selected formats.
10. Write manifest and review files.

Execution modes:

| Mode | Tradeoff |
| --- | --- |
| `fast` | 300 DPI, shorter prompt, no restoration retries |
| `balanced` | 400 DPI, default prompt, one informed retry |
| `quality` | 500 DPI, careful prompt, up to three retries |

The run uses chunked restoration for long raw text inputs, so it is processed in
smaller model batches instead of one large prompt. Progress is timer-based and indeterminate;
it shows the active step and elapsed time rather than a fake percentage. After a
page, image, text chunk, or translation chunk completes, Akshara writes a usage
log with item token usage and cumulative run token totals in plain language when
the provider reports usage.

While a provider request is active, Akshara periodically reports that the model
is still working. Pressing `Ctrl+C` during that window shows a safe-stop message
and waits for the active request to finish before returning control, so already
written checkpoints remain usable.

If a provider request is slow but still progressing, Akshara keeps polling and
retries only when the error is transient or explicitly retryable.

Provider requests wait indefinitely by default. In profiles and before each
interactive run, users may choose an explicit slow-page policy such as skip
after 5, 10, 20, 30, or 60 minutes. If a provider returns a transient network,
rate-limit, timeout, or server error, Akshara Vision retries with exponential
backoff and records failures in the run state instead of corrupting the final
output. In batch runs, a failed input is written as a failed item and later
inputs continue.

Retry depth follows the selected execution mode. `fast` makes one attempt and
moves on, `balanced` allows one informed retry, and `quality` allows up to
three retries for malformed or transiently failed responses.

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

The guide may also remember recently observed scripts or language cues within
the same batch so repeated mixed-language patterns stay uniform without forcing
translation or inventing language labels.

The selected document type also changes extraction guidance and deterministic
tagging. Books emphasize title matter, contents, chapters, page numbers,
prefaces, indexes, and footnotes. Magazines and newspapers emphasize column
order, article boundaries, headlines, mastheads, bylines, captions,
advertisements, classifieds, sidebars, and multi-column flow. Manuscripts
emphasize folios, marginalia, corrections, colophons, lineated text, uncertain
readings, and damaged text. Journal articles, letters, and archive bundles also
receive their own role sets. The run manifest records semantic units, layout
classes, a layout profile, content features, contents entries, headings, page
markers, footnotes, and figure metadata for later assembly.

Legal, finance, healthcare, and insurance documents receive stricter role
labels so their exports can feel like the original document type instead of a
generic prose dump.

The CLI asks whether to enable figure/image enrichment before a run. When
enabled, prompts may insert concise `[image: ...]` markers for visible
illustrations, maps, plates, or diagrams, and Akshara stores conservative
candidate figure crops with bounding boxes, relative page coordinates, page
zones, size, DPI, and placement metadata.
This is disabled by default so normal restored text remains clean. The cropper
does not claim full layout-perfect segmentation; it ignores tiny marks and
ambiguous damage instead of treating them as figures.

The CLI also asks which layout analysis backend to use. `native` is the default
local analyzer and records page blocks, confidence scores, columns, and
figure/text hints. `off` skips that step. Optional `doctr`, `paddleocr`, and
`layoutparser` backends appear when their packages are installed; their output is
normalized into the same layout tree used by combine, review, chat, and export.

The CLI also asks how to handle languages:

| Mode | Behavior |
| --- | --- |
| `preserve-detected` | Default. Preserve every readable language/script visible in the source, without forcing labels or translation. |
| `strict-source` | Extract only the declared source language/script, while preserving necessary names, citations, and technical terms. |

The run can also ask whether the user wants to strictly stick to the input
language or accept every readable script detected on the page.

After a run, use `akv review path/to/run-folder` to inspect layout profile,
visual block-map previews, low-confidence blocks, and figure assets. It writes
`layout_review.md` next to the manifest so reviewers can audit crops and layout
signals before final assembly.

Use `akv compare path/to/run-folder` to open a browser-friendly before/after
report. The compare view renders source pages and PDF pages as images whenever
possible, then places the detected overlays on top so you can check whether
placement, reading order, and figures still match the source material. Multi-page
PDFs and multi-image runs are shown as page/image-specific cards, keeping each
page's restored output beside its matching source preview.

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

`akv chat` can read either a run folder, a compiled output, or a raw input path
and answer grounded questions from the same restored material. If you launch it
without a file path, it starts in general conversation mode and lets you attach
documents later. General chat sessions are saved under the Akshara config
folder so they can be resumed or deleted later.

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

The nested folder combine writes folder-local `combined__LANG.txt` files when a
subtree has finished, which makes partial review easier during big archive runs.

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
The home board can be reopened at any time with `/home`, and the interactive
session help stays focused on the current workflow rather than listing every
command at once.
