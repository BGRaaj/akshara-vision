# Chat Over Files

Akshara Vision includes a grounded document chat mode:

```bash
akv chat path/to/run-folder
akv ask path/to/akshara_output.txt --question "What is the book about?"
```

Inside interactive chat, Akshara also supports local chat tools:

```text
/sources
/find keyword or phrase
/open S1
/clear
/exit
```

## What It Uses

- run folders with `run_manifest.json`
- compiled outputs such as `.txt`, `.md`, `.html`, `.json`, `.jsonl`, and `.yaml`
- raw input files or folders, which are first processed through the existing
  pipeline when needed
- a single raw image file can be discussed directly in vision mode when the
  selected model supports image inputs
- a single raw PDF can be handled lazily for page-specific questions, so
  `page 66` style questions render only that page instead of pre-indexing the
  full file
- if a question points to a page or a direct visual source is available, the
  chat layer can reopen that image and use it again when the indexed text is
  incomplete

## Behavior

- Answers are generated from the provided sources only.
- Responses should cite the source ids used, such as `[S1]` or `[S1/S3]`.
- If the source material does not support the claim, the assistant should say so.
- Chat is interactive, but it stays separate from deterministic restoration.
- Run-folder chats save a local `chat_session.json` history file so follow-up
  questions can use previous turns.
- `/sources`, `/find`, and `/open` work locally against indexed chunks before
  any model call, which keeps review fast and grounded.

## Good Uses

- Ask what a restored run says about a topic, chapter, clause, or page range.
- Inspect a run folder without reopening the whole pipeline.
- Ask follow-up questions after batch processing, review, or export.
- Search a large run for a phrase and open the exact cited chunk before asking
  the model to reason over it.
- Ask a direct question about a single uploaded image, such as a photo,
  illustration, signboard, chart, or poster, when the model supports vision.
- Ask a page-specific question against a raw PDF before deciding whether to run
  a full restoration.

## Notes

Chat is only as reliable as the underlying restoration and manifest metadata.
Use it as a grounded review layer, not as a replacement for source review.
