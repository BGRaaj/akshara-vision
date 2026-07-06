import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

from akshara_vision.core.constants import EXECUTION_MODES
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
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
        except Exception as exc:  # pragma: no cover - defensive around local installs
            return ProviderStatus(self.name, False, f"ollama check failed: {exc}", [])
        models = _parse_ollama_models(result.stdout)
        available = result.returncode == 0
        detail = "ollama is installed." if available else "ollama is installed but not responding."
        return ProviderStatus(self.name, available, detail, models)

    def restore_text(
        self,
        text: str,
        instruction: str,
        settings: ModelSettings,
        media_path: Optional[Path] = None,
    ) -> tuple[str, dict]:
        prompt = f"{instruction}\n\nSOURCE TEXT:\n{text}" if text else instruction

        # If media_path is provided, we MUST use HTTP API because CLI cannot handle images.
        if media_path:
            response, usage = _ollama_chat_http(
                settings,
                instruction,
                text,
                media_path,
                None,
            )
            if response:
                return response, usage
            raise RuntimeError(
                f"Failed to obtain vision response from Ollama using model '{settings.model}'."
            )

        # Fallback for text-only: try HTTP first, then CLI
        try:
            response, usage = _ollama_chat_http(
                settings,
                instruction,
                text,
                None,
                None,
            )
        except RuntimeError:
            response, usage = "", {}
        if response:
            return response, usage

        try:
            result = subprocess.run(
                ["ollama", "run", settings.model],
                input=prompt,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            return MockProvider().restore_text(text, instruction, settings)
        stdout = result.stdout or ""
        if result.returncode != 0 or not stdout.strip():
            return MockProvider().restore_text(text, instruction, settings)
        return stdout.strip() + "\n", {}


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

    def restore_text(
        self,
        text: str,
        instruction: str,
        settings: ModelSettings,
        media_path: Optional[Path] = None,
    ) -> tuple[str, dict]:
        endpoint = (
            settings.endpoint
            or os.environ.get("AKSHARA_OPENAI_COMPATIBLE_BASE_URL")
            or self.default_endpoint
        )
        result, usage = openai_compatible_chat(
            endpoint=endpoint,
            settings=settings,
            instruction=instruction,
            text=text,
            api_key=os.environ.get("AKSHARA_OPENAI_COMPATIBLE_API_KEY"),
            media_path=media_path,
        )
        if result:
            return result, usage
        if media_path:
            raise RuntimeError(
                f"Failed to obtain response from OpenAI-compatible local server at {endpoint} "
                f"using model '{settings.model}'."
            )
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


def _ollama_chat_http(
    settings: object,
    instruction: str,
    text: str,
    media_path: Optional[Path] = None,
    timeout: Optional[float] = None,
) -> tuple[str, dict]:
    url = "http://localhost:11434/api/chat"

    model = settings.model if hasattr(settings, "model") else str(settings)
    ctx_limit = _context_limit(settings)
    predict_limit = _generation_limit(settings, ctx_limit)

    if media_path:
        import base64

        try:
            media_bytes = media_path.read_bytes()
            media_base64 = base64.b64encode(media_bytes).decode("utf-8")
        except OSError as exc:
            raise RuntimeError(f"Failed to read image file for multimodal input: {exc}")
        messages = [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": text or "Extract all text visible in this image.",
                "images": [media_base64],
            },
        ]
    else:
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text},
        ]

    payload = {
        "model": model,
        "messages": messages,
        "options": {
            "temperature": 0.1,
            "num_ctx": ctx_limit,
            "num_predict": predict_limit,
        },
        "stream": False,
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))

        done_reason = data.get("done_reason")
        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            "truncated": (done_reason == "length"),
        }
        return str(data["message"]["content"]).strip() + "\n", usage
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
            err_data = json.loads(err_body)
            msg = err_data.get("error", "")
            if not msg and isinstance(err_data, dict):
                msg = err_data.get("message", "")
            if not msg:
                msg = err_body
        except Exception:
            msg = exc.reason

        if "does not support" in msg.lower() or "image" in msg.lower() or exc.code == 400:
            raise RuntimeError(
                f"Local model '{model}' does not support vision/multimodal inputs. "
                "Please configure a vision model (e.g. 'gemma4:12b', 'qwen3.6:27b', or 'llama3.2-vision:11b') "
                "or switch the OCR/decode mode to a text-based/OCR mode."
            )
        raise RuntimeError(f"Ollama local API error (HTTP {exc.code}): {msg}")
    except urllib.error.URLError:
        if media_path:
            raise RuntimeError(
                f"Could not connect to Ollama local server at {url}. "
                "Make sure Ollama is running (`ollama serve`) and the model is downloaded."
            )
        return "", {}


def openai_compatible_chat(
    endpoint: str,
    settings: object,
    instruction: str,
    text: str,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
    media_path: Optional[Path] = None,
) -> tuple[str, dict]:
    url = endpoint.rstrip("/") + "/chat/completions"

    model = settings.model if hasattr(settings, "model") else str(settings)
    ctx_limit = _context_limit(settings)
    max_tokens = _generation_limit(settings, ctx_limit)

    if media_path:
        suffix = media_path.suffix.lower()
        mime_type = "image/png"
        if suffix in {".jpg", ".jpeg"}:
            mime_type = "image/jpeg"
        elif suffix == ".webp":
            mime_type = "image/webp"

        import base64

        try:
            media_bytes = media_path.read_bytes()
            media_base64 = base64.b64encode(media_bytes).decode("utf-8")
        except OSError as exc:
            raise RuntimeError(f"Failed to read image file: {exc}")

        messages = [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text or "Please restore and clean up the text in this document.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{media_base64}"},
                    },
                ],
            },
        ]
    else:
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text},
        ]

    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": messages,
        "max_tokens": max_tokens,
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
            err_data = json.loads(err_body)
            msg = ""
            if isinstance(err_data, dict):
                msg = (
                    err_data.get("error", {}).get("message")
                    or err_data.get("error")
                    or err_data.get("message")
                    or ""
                )
            if not msg:
                msg = err_body
        except Exception:
            msg = exc.reason

        msg_lower = msg.lower()
        if (
            "vision" in msg_lower
            or "image" in msg_lower
            or "does not support" in msg_lower
            or exc.code == 400
        ):
            raise RuntimeError(
                f"Model '{model}' at endpoint '{endpoint}' does not support multimodal/vision inputs. "
                "Please configure a vision-capable model (e.g. gpt-5.5, claude-sonnet-5, gemma4:12b, or qwen3.6:27b) "
                "or switch the OCR/decode mode."
            )
        raise RuntimeError(f"OpenAI-compatible API error (HTTP {exc.code}): {msg}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        if media_path:
            raise RuntimeError(f"Failed to connect to local/cloud endpoint at {url}: {exc}")
        return "", {}

    choices = data.get("choices") or []
    usage_data = data.get("usage") or {}
    if not choices:
        return "", usage_data
    message = choices[0].get("message") or {}
    content = message.get("content") or choices[0].get("text") or ""

    finish_reason = choices[0].get("finish_reason")
    usage = {
        "prompt_tokens": usage_data.get("prompt_tokens", 0),
        "completion_tokens": usage_data.get("completion_tokens", 0),
        "total_tokens": usage_data.get("total_tokens", 0),
        "truncated": (finish_reason == "length"),
    }
    return str(content).strip() + ("\n" if content else ""), usage


def _provider_timeout(execution_mode: str) -> int:
    if execution_mode not in EXECUTION_MODES:
        execution_mode = "balanced"
    return {
        "fast": 120,
        "balanced": 240,
        "quality": 480,
    }[execution_mode]


def _context_limit(settings: object) -> int:
    value = (
        getattr(settings, "context_window", None) if hasattr(settings, "context_window") else None
    )
    if value is None:
        return 16384
    try:
        return max(2048, int(value))
    except (TypeError, ValueError):
        return 16384


def _generation_limit(settings: object, context_limit: int) -> int:
    value = (
        getattr(settings, "generation_limit", None)
        if hasattr(settings, "generation_limit")
        else None
    )
    if value is None:
        value = context_limit
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = context_limit
    return min(16384, max(1024, requested))
