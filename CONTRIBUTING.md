# Contributing

Akshara Vision is early and open-ended. Contributions are welcome across OCR,
restoration, model providers, exporters, terminal UX, tests, and documentation.

Keep changes small, readable, and respectful of archival source material.

## Setup

```bash
python3 -m pip install -e ".[dev]"
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Principles

- Never overwrite source files.
- Do not commit generated outputs, local config, virtual environments, or secrets.
- Keep the CLI keyboard-first, black-and-white, responsive, and scriptable.
- Put new providers/exporters behind registries.
- Mark uncertain restoration instead of guessing.

