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
- OpenRouter
- Groq
- Mistral
- Together
- Fireworks
- Perplexity
- DeepSeek
- xAI
- Cerebras
- Custom OpenAI-compatible cloud endpoints
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
export OPENROUTER_API_KEY=...
export GROQ_API_KEY=...
export MISTRAL_API_KEY=...
```

For custom OpenAI-compatible clouds:

```bash
export AKSHARA_CUSTOM_API_KEY=...
export AKSHARA_CUSTOM_OPENAI_COMPATIBLE_BASE_URL=https://api.example.com/v1
```

When a provider exposes a `/models` endpoint, Akshara Vision lists available
models in the picker. Otherwise, choose manual entry and paste the exact model
id from the provider dashboard or documentation.

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

### Recommended Vision Models

Use a model that explicitly supports image or PDF inputs for scanned pages.

| Runtime / Provider | Recommended Model | Notes |
| --- | --- | --- |
| **Ollama / Local** | `gemma4:12b` | High-fidelity multilingual and Indic script vision work |
| **Ollama / Local** | `qwen3.6:27b` / `qwen3.5:4b` | Strong document visual parsing options |
| **Ollama / Local** | `llama3.2-vision:11b` | Useful lighter local vision baseline |
| **LM Studio / Jan** | GGUF variants of Gemma 4, Qwen 3.6, Qwen 3.5, or Llama 3.2 Vision | Depends on your local runtime and hardware |
| **Gemini** | `gemini-3.5-flash`, `gemini-3.5-pro`, `gemini-3.1-flash-lite` | Cloud multimodal document workflows |
| **OpenAI** | `gpt-5.5`, `gpt-5.4` | Cloud vision workflows |
| **Anthropic** | `claude-sonnet-5`, `claude-fable-5` | Cloud vision and reasoning workflows |
| **OpenAI-compatible clouds** | Listed from `/models` or entered manually | OpenRouter, Groq, Mistral, Together, Fireworks, Perplexity, DeepSeek, xAI, Cerebras, or custom endpoints |

Akshara Vision detects local models where a runtime exposes them and also offers
a custom model-id entry so profiles can track the model names used by your local
server or cloud account.

Translation is a separate final stage after extraction. The selected model still
determines how well the tool handles the source script, target language, and
long-form output.

### Safe Handling of Incompatible Models
If you point it at a text-only model while trying to process an image or PDF, the
provider client will catch common incompatibility errors and fail-safe with a clear
message. This prevents silent failures or corrupted outputs.

Model predictions are still model predictions: review output before publishing,
especially for damaged pages, rare scripts, tables, handwriting, or translated
passages that use unfamiliar terminology.

### Context Window & Token Limits

For successful Indic transcription and reasoning-based manuscript parsing, verify that your model configuration meets these requirements:
- **Context Length (`num_ctx`):** At least **16,384 tokens**. The vision model needs this room to ingest the system prompt instructions, image embeddings (which take substantial token space), and the document text.
- **Generation Limit (`num_predict` / `max_tokens`):** Akshara Vision requests up to **16,384 output tokens** by default for local and OpenAI-compatible providers, capped there to avoid runaway generations.
- **Truncation Safety:** Akshara Vision enforces these options on compatible local APIs automatically. If a page chunk output is truncated due to context limits, the run finishes with a transparent `Finished (Truncated)` warning banner to prevent silent, corrupt outputs.
