# CLI Design

Akshara Vision is a terminal product first.

Design rules:

- Black-and-white only.
- Keyboard-first selection.
- Short commands for repeated work.
- Responsive first: the opening board adapts to narrow, normal, and wide terminals.
- Full commands remain available for scripts and documentation.
- No hidden overwrites of source files.
- Every run ends with a review screen before processing.
- Every active run shows progress.
- Every finished run shows a success screen with output paths and next actions.
- Every output run includes a manifest.
- Every optional dependency is checked by `akshara doctor`.

Hero:

```text
AKSHARA VISION
Restore. Read. Preserve.
```

The default hero uses an inscription-style ASCII masthead and keeps the literal
`AKSHARA VISION` text visible for accessibility and searchability.

Core commands:

| Long | Short | Purpose |
| --- | --- | --- |
| `akshara init` | `akv i` | Onboarding |
| `akshara run` | `akv r` | Guided run |
| `akshara quick` | `akv q` | Locked defaults |
| `akshara batch` | `akv b` | Batch processing |
| `akshara profile` | `akv p` | Profiles |
| `akshara model` | `akv m` | Models |
| `akshara env` | `akshara keys` | API keys and local endpoints |
| `akshara instruct` | `akv ins` | Instructions |
| `akshara doctor` | `akv d` | Diagnostics |
| `akshara export` | `akv x` | Re-export |
| `akshara guide` | `akv g` | Choose guidance level |
| `akshara ui` | `akshara theme` | Customize terminal display |
| `akshara shell` | `akv s` | Force interactive session |
| `akshara clean` | `akv c` | Remove generated local artifacts |

Interactive session commands:

| Command | Purpose |
| --- | --- |
| `/menu` | Open the action picker |
| `/run [inputs...]` | Guided full workflow |
| `/quick [inputs...]` | Run the default profile |
| `/batch [folder...]` | Recursive batch workflow |
| `/init` | Create a default profile |
| `/profiles` | Manage profiles |
| `/models` | Check model providers |
| `/env` | Show API key and endpoint setup |
| `/instructions` | View or edit prompts |
| `/guide` | Choose guidance level |
| `/ui` | Customize hero, density, prompt |
| `/doctor` | Check local setup |
| `/clean` | Remove generated outputs |
| `/exit` | Leave the session |

Display options:

| Option | Values |
| --- | --- |
| Hero | `inscription`, `classic`, `minimal` |
| Guide | `balanced`, `full`, `minimal` |
| Density | `comfortable`, `compact` |
| Prompt | `adaptive`, `full`, `short` |

The CLI uses Typer, Rich, and InquirerPy when installed. A small stdlib fallback keeps
the project inspectable in bare Python environments.
