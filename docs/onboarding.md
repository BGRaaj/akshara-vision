# Onboarding

Run:

```bash
akv i
```

Or open the shell and choose from the board:

```bash
akshara
```

```text
/menu
/guide
/ui
/env
```

The onboarding flow creates a portable profile. A profile stores the default workflow,
document type, OCR mode, languages, model provider, output formats, instruction preset,
and output folder.

Recommended first profile:

- Workflow: `Full pipeline`
- Document type: `Book`
- Source language: `auto`
- Output language: `same`
- Translation mode: `off`
- OCR mode: `auto`
- Provider: `ollama` if installed, otherwise `mock`
- Outputs: `txt`, `md`, `json`, `review`
- Lock profile: yes

Once locked, run:

```bash
akv q path/to/book.pdf
```

Quick run asks only for inputs and uses the saved defaults.
