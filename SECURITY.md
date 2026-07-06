# Security

Please report security issues privately until they can be understood and fixed.

Akshara Vision should not store secrets in repository files, profiles, run manifests, or
generated review logs. Cloud API keys must be passed through environment variables.

Supported secret environment variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `AKSHARA_OPENAI_COMPATIBLE_API_KEY`

Before opening a pull request or publishing a run folder, use:

```bash
rg -n "API_KEY|SECRET|TOKEN|/Users/|/home/" .
```

Then remove generated local artifacts:

```bash
akv clean
```

