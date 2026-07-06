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
- Progress is timer-based and indeterminate, showing the active step and elapsed time.
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
| `akshara install` | `akv setup` | Install system dependencies (Poppler) |
| `akshara run` | `akv r` | Guided run |
| `akshara quick` | `akv q` | Locked defaults |
| `akshara batch` | `akv b` | Batch processing |
| `akshara profile` | `akv p` | Profiles |
| `akshara model` | `akv m` | Models |
| `akshara env` | `akshara keys` | API keys and local endpoints |
| `akshara instruct` | `akv ins` | Instructions |
| `akshara doctor` | `akv d` | Diagnostics |
| `akshara check` | `akv t` | Compile and run tests |
| `akshara resume` | `akv resume` | Recover interrupted checkpoints |
| `akshara export` | `akv x` | Re-export or convert output files |
| `akshara guide` | `akv g` | Choose guidance level |
| `akshara mode` | `akv speed` | Choose speed versus quality |
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
| `/mode` | Choose speed versus quality |
| `/ui` | Customize hero, density, prompt |
| `/doctor` | Check local setup |
| `/install` | Install PDF/image system dependencies |
| `/status` | Show current configuration |
| `/check`, `/test` | Compile and run unit tests |
| `/clean` | Remove generated outputs |
| `/exit` | Leave the session |

Display options:

| Option | Values |
| --- | --- |
| Hero | `inscription`, `classic`, `minimal` |
| Guide | `balanced`, `full`, `minimal` |
| Execution mode | `fast`, `balanced`, `quality` |
| Density | `comfortable`, `compact` |
| Prompt | `adaptive`, `full`, `short` |

Execution mode controls the OCR and model effort used by the run:

| Mode | Behavior |
| --- | --- |
| `fast` | Lower OCR DPI, shorter provider timeouts, and a throughput-first prompt. |
| `balanced` | Default settings for most runs. |
| `quality` | Higher OCR DPI, longer provider timeouts, and a fidelity-first prompt. |

Restoration requests use smaller text chunks when inputs are long, and the model is
asked to return a JSON object with the cleaned text plus uncertainty notes. The text
exports use the cleaned text, while the JSON export keeps the structured record.

The CLI uses Typer, Rich, and InquirerPy when installed. A small stdlib fallback keeps
the project inspectable in bare Python environments.
