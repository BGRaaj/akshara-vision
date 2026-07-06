# Onboarding

Run:

```bash
akv i
```

Windows PowerShell users should activate with `.\.venv\Scripts\Activate.ps1` and
install with `python -m pip install -e .`. Avoid `source .venv/bin/activate`
and avoid `python3 -m pip ...` after activation.

If PowerShell cannot find `akv`, run either:

```powershell
.\.venv\Scripts\akv.exe i
python -m akshara_vision i
```

When a text prompt shows a default value, press `Enter` to accept it. Menus use
arrow keys, search, space for checkbox selection, and `Enter` to continue.

Or open the shell and choose from the board:

```bash
akshara
```

```text
/menu
/guide
/mode
/ui
/env
```

The onboarding flow creates a portable profile. A profile stores the default workflow,
document type, languages, model provider, output formats, instruction preset,
and output folder.

Language fields accept full names or local labels such as `English`,
`Hindi`, or `Kannada`, and matching is case-insensitive.

Recommended first profile:

- Workflow: `Full pipeline`
- Document type: `Book`
- Source language: `auto`
- Output language: `same`
- Translation mode: `auto`
- Provider: `ollama` if installed, otherwise `mock`
- Execution mode: `balanced`
- Outputs: `txt`, `md`, `json`, `review`
- Lock profile: yes

`auto` means translation switches on when the output language differs from the
source language.

Once locked, run:

```bash
akv q path/to/book.pdf
```

Quick run asks only for inputs and uses the saved defaults.

Use `/mode` later if you want to switch between faster prompting execution,
balanced defaults, or the slower quality-focused analysis path.
