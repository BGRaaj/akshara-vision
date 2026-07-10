# Security Policy

We take security seriously and want to ensure Akshara Vision remains safe and secure for everyone. 

## Reporting a Vulnerability

If you discover a security vulnerability, please report it privately using GitHub's Private Vulnerability Reporting feature in our repository instead of opening a public issue or submitting a code change. This gives us the opportunity to understand and fix the issue before it is made public. We will investigate and respond to security reports as quickly as possible.

## Key Management and Safety

Akshara Vision does not store secrets in repository files, profiles, run manifests, or generated review logs. Cloud API keys must be passed safely through environment variables.

Supported secret environment variables:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `AKSHARA_CUSTOM_API_KEY`

Before publishing a run folder, sharing logs, or posting issue snippets, please check your files to ensure no sensitive paths or secrets are visible:

```bash
rg -n "API_KEY|SECRET|TOKEN" .
```

To clean up all local checkpoints and output caches:
```bash
akv clean
```

We appreciate your help in keeping Akshara Vision secure!
