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
