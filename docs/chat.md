# Chat Over Files

Akshara Vision includes a grounded document chat mode:

```bash
akv chat path/to/run-folder
akv ask path/to/akshara_output.txt --question "What is the book about?"
```

Inside interactive chat, Akshara also supports local chat tools:

```text
/where TERM
/cite S1 S2
/scope TERM
/attach path/to/file-or-folder
/remember NOTE
/sources
/find keyword or phrase
/open S1
/focus TERM
/clear
/exit
```

If you start `akv chat` without a file path, Akshara opens a general
conversation mode first. You can attach a document, folder, run folder, or raw
file later from the prompt flow when you want grounded answers.
Saved conversations are kept under the Akshara config directory so you can
resume or delete them later from the chat picker.
Use `TERM` as any helpful clue: a keyword, page number, source id like `S12`,
file name, topic, or short phrase from the document.

If the saved-conversation picker is empty, Akshara returns to the chat mode
menu instead of exiting, so you can choose general conversation or document
chat without restarting the session.

## What It Uses

- run folders with `run_manifest.json`
- compiled outputs such as `.txt`, `.md`, `.html`, `.json`, `.json-detailed`, `.jsonl`, `.yaml`, and `.pdf`
- raw input files or folders, which are first processed through the existing
  pipeline when needed
- a single raw image file can be discussed directly in vision mode when the
  selected model supports image inputs
- a single raw PDF can be handled lazily for page-specific questions, so
  `page 66` style questions render only that page instead of pre-indexing the
  full file
- when a document source is already indexed, chat reuses the stored page/chunk
  metadata first, then re-checks the page image or visual source when the
  indexed text is not enough
- if a question points to a page or a direct visual source is available, the
  chat layer can reopen that image and use it again when the indexed text is
  incomplete

## Behavior

- Answers are generated from the provided sources only.
- Responses should cite the source ids used, such as `[S1]` or `[S1/S3]`.
- If the source material does not support the claim, the assistant should say so.
- Chat is interactive, but it stays separate from deterministic restoration.
- Run-folder chats save a local `chat_session.json` history file so follow-up
  questions can use previous turns. General chats save named session files in
  the config folder so they can be resumed later.
- `/sources`, `/find`, and `/open` work locally against indexed chunks before
  any model call, which keeps review fast and grounded.
- `/where TERM` jumps to the strongest matching source before you ask the
  next question. `TERM` can be a keyword, page number, source id, file name, or
  topic.
- `/cite S1 S2` pins the answer to specific sources, which is useful when you
  want a strict, source-anchored reply.
- `/scope TERM` and `/focus TERM` narrow the active source set without
  rebuilding the full bundle. Use `/scope all` or `/focus all` to return to
  every indexed source. `TERM` follows the same rules as `/where`.
- `/remember` stores a short run-local note and feeds it back into later turns.
- `/sessions` lists saved conversation sessions.
- `/attach PATH` adds a document, folder, run folder, manifest, or output file
  after starting in general conversation mode.

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
- Narrow a long folder chat to one file, page range, or topic before answering.

## Notes

Chat is only as reliable as the underlying restoration and manifest metadata.
Use it as a grounded review layer, not as a replacement for source review.
