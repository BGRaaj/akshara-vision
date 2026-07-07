from typing import Dict

from akshara_vision.providers.cloud import CloudProvider, OpenAICompatibleCloudProvider
from akshara_vision.providers.local import OllamaProvider, OpenAICompatibleLocalProvider
from akshara_vision.providers.mock import MockProvider


def provider_registry() -> Dict[str, object]:
    return {
        "mock": MockProvider(),
        "ollama": OllamaProvider(),
        "openai-compatible-local": OpenAICompatibleLocalProvider(),
        "lm-studio": OpenAICompatibleLocalProvider("lm-studio", "http://localhost:1234/v1"),
        "jan": OpenAICompatibleLocalProvider("jan", "http://localhost:1337/v1"),
        "llama-cpp": OpenAICompatibleLocalProvider("llama-cpp", "http://localhost:8080/v1"),
        "openai": CloudProvider("openai", "OPENAI_API_KEY", ["gpt-5.5", "gpt-5.4"]),
        "anthropic": CloudProvider(
            "anthropic",
            "ANTHROPIC_API_KEY",
            ["claude-sonnet-5", "claude-fable-5"],
        ),
        "gemini": CloudProvider(
            "gemini",
            "GEMINI_API_KEY",
            ["gemini-3.5-flash", "gemini-3.5-pro", "gemini-3.1-flash-lite"],
        ),
        "openrouter": OpenAICompatibleCloudProvider(
            "openrouter",
            "OPENROUTER_API_KEY",
            "https://openrouter.ai/api/v1",
        ),
        "groq": OpenAICompatibleCloudProvider(
            "groq",
            "GROQ_API_KEY",
            "https://api.groq.com/openai/v1",
        ),
        "mistral": OpenAICompatibleCloudProvider(
            "mistral",
            "MISTRAL_API_KEY",
            "https://api.mistral.ai/v1",
        ),
        "together": OpenAICompatibleCloudProvider(
            "together",
            "TOGETHER_API_KEY",
            "https://api.together.xyz/v1",
        ),
        "fireworks": OpenAICompatibleCloudProvider(
            "fireworks",
            "FIREWORKS_API_KEY",
            "https://api.fireworks.ai/inference/v1",
        ),
        "perplexity": OpenAICompatibleCloudProvider(
            "perplexity",
            "PERPLEXITY_API_KEY",
            "https://api.perplexity.ai",
        ),
        "deepseek": OpenAICompatibleCloudProvider(
            "deepseek",
            "DEEPSEEK_API_KEY",
            "https://api.deepseek.com",
        ),
        "xai": OpenAICompatibleCloudProvider(
            "xai",
            "XAI_API_KEY",
            "https://api.x.ai/v1",
        ),
        "cerebras": OpenAICompatibleCloudProvider(
            "cerebras",
            "CEREBRAS_API_KEY",
            "https://api.cerebras.ai/v1",
        ),
        "custom-openai-compatible": OpenAICompatibleCloudProvider(
            "custom-openai-compatible",
            "AKSHARA_CUSTOM_API_KEY",
            "https://api.example.com/v1",
        ),
    }


def get_provider(name: str):
    return provider_registry().get(name) or provider_registry()["mock"]
