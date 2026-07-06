# Profiles

Profiles are portable TOML files stored in:

```text
~/.akshara-vision/profiles/
```

Set `AKSHARA_CONFIG_HOME` to store profiles somewhere else, which is useful for tests,
CI, and shared project sandboxes.

Create a profile:

```bash
akv p create --name book-cleanup
```

List profiles:

```bash
akv p list
```

Open the interactive profile manager:

```bash
akv p
```

Show a profile:

```bash
akv p show --name book-cleanup
```

Set the default:

```bash
akv p use --name book-cleanup
```

Modify through guided prompts:

```bash
akv p modify --name book-cleanup
```

Duplicate a profile:

```bash
akv p duplicate --name book-cleanup
```

Delete an old profile:

```bash
akv p delete --name old-profile
```

Lock it for quick runs:

```bash
akv p lock --name book-cleanup
```

Export a profile path:

```bash
akv p export --name book-cleanup
```

Import a shared profile:

```bash
akv p import --source shared-profile.toml
```

Profiles store workflow choices, language settings, output formats, model provider,
model name, execution mode, context window, generation limit, instruction preset,
and output folder.
