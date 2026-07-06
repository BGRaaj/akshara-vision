# Privacy

Akshara Vision is local-first.

By default:

- Source files are never overwritten.
- API keys are read from environment variables only.
- API keys are not saved in profiles.
- API keys are not written to run manifests.
- Run manifests store project-relative paths when possible instead of absolute local
  paths.
- Generated outputs are stored under the selected output folder.

Useful environment variables:

```bash
export AKSHARA_CONFIG_HOME=/tmp/akshara-config
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
export AKSHARA_OPENAI_COMPATIBLE_BASE_URL=http://localhost:1234/v1
```

Use `.env.example` as the template for a private `.env` file.

Clean generated local outputs:

```bash
akv clean
```

For archival work, review generated text before publishing. The tool is designed to
preserve source meaning, but OCR and model cleanup can still make mistakes.
