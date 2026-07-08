# Instructions

The default instruction preset is:

```text
book_restoration_default
```

View it:

```bash
akv ins
```

Create an editable copy:

```bash
akv ins edit
```

Reset to the packaged default:

```bash
akv ins reset
```

Instructions are stored in:

```text
~/.akshara-vision/instructions/
```

The default preset is conservative. It restores OCR damage, preserves historical voice,
marks uncertain text, avoids invented metadata, and returns only the requested output.

For best results, keep the editable preset strict:

- preserve page order and visible section order
- ignore mirrored bleed-through and back-side impressions
- use [unclear] only when the source is genuinely uncertain
- return blank output for blank or unreadable pages
- keep translation as a separate final pass
- avoid leaking wrapper JSON or diagnostic text into the final export
