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

The profile output folder is only the default. Interactive runs ask for the
destination again, so each book, batch, or experiment can be saved wherever it
belongs without editing the profile.

If you plan to use the chat mode, the same profile still applies. Chat reuses
the selected provider and model so follow-up questions stay grounded in the
same backend.

Model setup starts with a simple `local` or `cloud` choice, then narrows to the
matching provider list.

The output folder field is validated before it is saved. Existing file paths are
rejected, and new folders are only accepted when their parent directory is valid.

Language fields accept full names or local labels such as `English`,
`Hindi`, or `Kannada`, and matching is case-insensitive.

Recommended first profile:

- Workflow: `Full pipeline`
- Document type: `Book`
- Source language: `auto`
- Output language: `same`
- Language handling: `preserve-detected`
- Translation mode: `auto`
- Provider: `ollama` if installed, otherwise `mock`
- Execution mode: `balanced`
- Outputs: `txt`, `md`, `json`, `review`
- Output folder: `akshara-output`
- Lock profile: yes

`auto` means translation switches on when the output language differs from the
source language.

`preserve-detected` keeps readable mixed-language snippets in their original
script. Use `strict-source` only when you want the run to ignore text outside the
declared source language.

Once locked, run:

```bash
akv q path/to/book.pdf
```

Quick run uses the saved defaults and, in an interactive terminal, still lets you
confirm the output folder, language handling, and figure/image enrichment for
that run.

Use `/mode` later if you want to switch between faster prompting execution,
balanced defaults, or the slower quality-focused analysis path.

For dense pages, damaged scans, or non-English scripts, prefer `quality` mode
and a stronger vision model. The tool saves extraction before translation, but
recognition quality and language coverage still depend on the selected model.

---

## Windows Troubleshooting

### WinError 206 (Filename or extension too long)
When installing with the `layout` extra (`pip install -e ".[layout]"`), Windows may fail during `torch` or `doctr` installation with `OSError: [WinError 206]`. This is caused by the default 260-character path limit on Windows.

To resolve this:
1. **Enable Long Paths**: Open PowerShell as **Administrator** and run:
   ```powershell
   New-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
   ```
2. **Use a Virtual Environment**: Create and activate a `.venv` in a short path (e.g. directly in your project folder rather than globally in AppData) to prevent long nested prefix directories.
