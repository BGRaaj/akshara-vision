# Models

Akshara Vision is local-first.

Supported provider families:

- Ollama
- LM Studio
- Jan
- llama.cpp or any OpenAI-compatible local server
- OpenAI
- Anthropic
- Gemini
- Mock provider for tests and demos

Run:

```bash
akv m
```

To choose a provider interactively and save it to your default profile:

```bash
akv m setup
```

To check environment setup:

```bash
akv env
```

Good local model families for OCR cleanup and book restoration include Gemma, Qwen,
Llama, and Mistral instruction models. The CLI detects available local models where it
can and otherwise shows safe recommendations.

OpenAI-compatible local runtimes use chat completions:

```bash
export AKSHARA_OPENAI_COMPATIBLE_BASE_URL=http://localhost:1234/v1
```

Or copy the template:

```bash
cp .env.example .env
```

Cloud providers are optional. Use:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
```

Then run:

```bash
akv d
```

## Pipeline Extraction Strategies

The Akshara Vision pipeline takes one of two strategies based on the input file type:

1. **Text-based Restoration**:
   - If the input is a plain text file (e.g., `.txt`, `.md`), the pipeline chunks it up.
   - The LLM restores the text using a strict JSON schema prompt to extract the cleaned text and collect metadata.

2. **Multimodal / Vision-based Restoration**:
   - The raw page images (or rendered PDF pages) are sent directly to the model as visual media payloads.
   - The LLM performs visual parsing, OCR, layout extraction, and cleaning in a single step.
   - *Best for*: Highly damaged pages, complex tabular/column layouts, manuscripts, and non-Latin script restoration.
   - *Requirements*: Requires system dependencies (Poppler/pdftoppm), which can be installed automatically via `akv install`.
   - *Supported Models*: Multimodal/vision-capable models only.

### Recommended Vision Models (2026)

| Runtime / Provider | Recommended Model | Description / Size |
| --- | --- | --- |
| **Ollama** | `gemma4:12b` | Outstanding multi-lingual and high-fidelity Indic script vision model |
| **Ollama** | `llama3.2-vision:11b` | Light and fast local vision model |
| **Ollama** | `qwen3.6:27b` / `qwen3.5:4b` | State-of-the-art document visual parsing |
| **Gemini** | `gemini-3.5-flash` / `gemini-3.5-pro` | Best-in-class multi-modal context window and PDF document parsing |
| **OpenAI** | `gpt-5.5` / `gpt-5.4` | Extremely robust and fast general vision performance |
| **Anthropic** | `claude-sonnet-5` / `claude-fable-5` | Unrivaled reasoning, layout understanding, and transcription accuracy |

### Safe Handling of Incompatible Models
If you point it at a text-only model (like `gpt-5.5`'s text-only variants or other non-multimodal local models) while trying to process an image or PDF, the provider client will catch the model incompatibility error and fail-safe immediately with a professional message. This prevents silent failures or corrupted outputs.

### Context Window & Token Limits

For successful Indic transcription and reasoning-based manuscript parsing, verify that your model configuration meets these requirements:
- **Context Length (`num_ctx`):** At least **16,384 tokens**. The vision model needs this room to ingest the system prompt instructions, image embeddings (which take substantial token space), and the document text.
- **Generation Limit (`num_predict` / `max_tokens`):** Akshara Vision requests up to **16,384 output tokens** by default for local and OpenAI-compatible providers, capped there to avoid runaway generations.
- **Truncation Safety:** Akshara Vision enforces these options on compatible local APIs automatically. If a page chunk output is truncated due to context limits, the run finishes with a transparent `Finished (Truncated)` warning banner to prevent silent, corrupt outputs.
