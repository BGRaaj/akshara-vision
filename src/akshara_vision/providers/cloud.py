import json
import os
import urllib.error
import urllib.parse
import urllib.request

from akshara_vision.core.models import ModelSettings
from akshara_vision.providers.base import ProviderStatus
from akshara_vision.providers.local import openai_compatible_chat
from akshara_vision.providers.mock import MockProvider


class CloudProvider:
    def __init__(self, name: str, env_var: str, default_models: list) -> None:
        self.name = name
        self.env_var = env_var
        self.default_models = default_models

    def status(self) -> ProviderStatus:
        has_key = bool(os.environ.get(self.env_var))
        detail = f"{self.env_var} is configured." if has_key else f"{self.env_var} is not set."
        return ProviderStatus(self.name, has_key, detail, self.default_models if has_key else [])

    def restore_text(self, text: str, instruction: str, settings: ModelSettings) -> str:
        api_key = os.environ.get(self.env_var)
        if not api_key:
            return MockProvider().restore_text(text, instruction, ModelSettings())
        if self.name == "openai":
            result = openai_compatible_chat(
                endpoint="https://api.openai.com/v1",
                model=settings.model,
                instruction=instruction,
                text=text,
                api_key=api_key,
            )
        elif self.name == "anthropic":
            result = _anthropic_message(api_key, settings.model, instruction, text)
        elif self.name == "gemini":
            result = _gemini_generate(api_key, settings.model, instruction, text)
        else:
            result = ""
        if result:
            return result
        return MockProvider().restore_text(text, instruction, ModelSettings())


def _anthropic_message(api_key: str, model: str, instruction: str, text: str) -> str:
    payload = {
        "model": model,
        "max_tokens": 8192,
        "temperature": 0.1,
        "system": instruction,
        "messages": [{"role": "user", "content": text}],
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return ""
    content = data.get("content") or []
    parts = [part.get("text", "") for part in content if isinstance(part, dict)]
    result = "\n".join(part for part in parts if part).strip()
    return result + ("\n" if result else "")


def _gemini_generate(api_key: str, model: str, instruction: str, text: str) -> str:
    model_name = urllib.parse.quote(model, safe="")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": instruction}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"temperature": 0.1},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return ""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    result = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    return result + ("\n" if result else "")
