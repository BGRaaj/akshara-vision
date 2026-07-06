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

