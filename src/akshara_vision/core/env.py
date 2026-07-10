import os
from pathlib import Path
from typing import Dict, Iterable, List


ENV_KEYS = [
    "SARVAM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
    "PERPLEXITY_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
    "CEREBRAS_API_KEY",
    "AKSHARA_CUSTOM_API_KEY",
    "AKSHARA_CUSTOM_OPENAI_COMPATIBLE_BASE_URL",
    "AKSHARA_OPENAI_COMPATIBLE_BASE_URL",
    "AKSHARA_OPENAI_COMPATIBLE_API_KEY",
    "AKSHARA_CONFIG_HOME",
]


from akshara_vision.core.constants import default_config_dir


def load_env_files(paths: Iterable[Path] = None) -> List[Path]:
    """Load simple KEY=value pairs from .env files without overwriting the shell."""
    candidates = list(
        paths
        or [
            Path.cwd() / ".env",
            default_config_dir() / ".env",
            Path.home() / ".akshara-vision" / ".env",
        ]
    )
    loaded = []
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and (key not in os.environ or not os.environ[key].strip()):
                os.environ[key] = value
        loaded.append(path)
    return loaded


def env_status() -> Dict[str, str]:
    return {key: "set" if os.environ.get(key) else "not set" for key in ENV_KEYS}
