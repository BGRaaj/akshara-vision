import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import List, Optional

from akshara_vision.core.models import ModelSettings
from akshara_vision.providers.base import ProviderStatus
from akshara_vision.providers.mock import MockProvider


class OllamaProvider:
    name = "ollama"

    def status(self) -> ProviderStatus:
        if shutil.which("ollama") is None:
            return ProviderStatus(self.name, False, "ollama command not found.", [])
        try:
            result = subprocess.run(
                ["ollama", "list"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:  # pragma: no cover - defensive around local installs
            return ProviderStatus(self.name, False, f"ollama check failed: {exc}", [])
        models = _parse_ollama_models(result.stdout)
        available = result.returncode == 0
        detail = "ollama is installed." if available else "ollama is installed but not responding."
        return ProviderStatus(self.name, available, detail, models)

    def restore_text(self, text: str, instruction: str, settings: ModelSettings) -> str:
        prompt = f"{instruction}\n\nSOURCE TEXT:\n{text}"
        try:
            result = subprocess.run(
                ["ollama", "run", settings.model],
                input=prompt,
                check=False,
                capture_output=True,
                text=True,
                timeout=240,
            )
        except Exception:
            return MockProvider().restore_text(text, instruction, settings)
        if result.returncode != 0 or not result.stdout.strip():
            return MockProvider().restore_text(text, instruction, settings)
        return result.stdout.strip() + "\n"


class OpenAICompatibleLocalProvider:
    def __init__(
        self,
        name: str = "openai-compatible-local",
        default_endpoint: str = "http://localhost:1234/v1",
    ) -> None:
        self.name = name
        self.default_endpoint = default_endpoint

    def status(self) -> ProviderStatus:
        endpoint = os.environ.get("AKSHARA_OPENAI_COMPATIBLE_BASE_URL") or self.default_endpoint
        models = _fetch_openai_compatible_models(endpoint)
        if models:
            return ProviderStatus(self.name, True, f"Connected to {endpoint}.", models)
        return ProviderStatus(
            self.name,
            False,
            f"Configure an OpenAI-compatible local endpoint, such as {endpoint}.",
            [],
        )

    def restore_text(self, text: str, instruction: str, settings: ModelSettings) -> str:
        endpoint = settings.endpoint or os.environ.get("AKSHARA_OPENAI_COMPATIBLE_BASE_URL") or self.default_endpoint
        result = openai_compatible_chat(
            endpoint=endpoint,
            model=settings.model,
            instruction=instruction,
            text=text,
            api_key=os.environ.get("AKSHARA_OPENAI_COMPATIBLE_API_KEY"),
        )
        if result:
            return result
        return MockProvider().restore_text(text, instruction, ModelSettings())


def _parse_ollama_models(output: str) -> List[str]:
    models = []
    for line in output.splitlines()[1:]:
        columns = line.split()
        if columns:
            models.append(columns[0])
    return models


def parse_openai_compatible_models(payload: str) -> List[str]:
    data = json.loads(payload)
    values = data.get("data", [])
    return [str(item.get("id")) for item in values if isinstance(item, dict) and item.get("id")]


def _fetch_openai_compatible_models(endpoint: str) -> List[str]:
    url = endpoint.rstrip("/") + "/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return parse_openai_compatible_models(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []


def openai_compatible_chat(
    endpoint: str,
    model: str,
    instruction: str,
    text: str,
    api_key: Optional[str] = None,
) -> str:
    url = endpoint.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text},
        ],
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or choices[0].get("text") or ""
    return str(content).strip() + ("\n" if content else "")
