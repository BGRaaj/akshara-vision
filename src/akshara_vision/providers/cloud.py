import gc
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from akshara_vision.core.constants import EXECUTION_MODES
from akshara_vision.core.models import ModelSettings
from akshara_vision.providers.base import ProviderStatus
from akshara_vision.providers.local import (
    _context_limit,
    _fetch_openai_compatible_models,
    _generation_limit,
    _media_mime_type,
    openai_compatible_chat,
)


class CloudProvider:
    def __init__(self, name: str, env_var: str, default_models: list) -> None:
        self.name = name
        self.env_var = env_var
        self.default_models = default_models

    def status(self) -> ProviderStatus:
        has_key = bool(os.environ.get(self.env_var))
        detail = f"{self.env_var} is configured." if has_key else f"{self.env_var} is not set."
        models = []
        if has_key:
            models = _fetch_native_models(self.name, os.environ[self.env_var])
        return ProviderStatus(self.name, has_key, detail, models or (self.default_models if has_key else []))

    def restore_text(
        self,
        text: str,
        instruction: str,
        settings: ModelSettings,
        media_path: Optional[Path] = None,
    ) -> tuple[str, dict]:
        api_key = os.environ.get(self.env_var)
        if not api_key:
            raise RuntimeError(
                f"Cloud provider '{self.name}' requires environment variable '{self.env_var}'."
            )
        if self.name == "openai":
            result = openai_compatible_chat(
                endpoint="https://api.openai.com/v1",
                settings=settings,
                instruction=instruction,
                text=text,
                api_key=api_key,
                timeout=_request_timeout(settings),
                media_path=media_path,
            )
        elif self.name == "anthropic":
            result = _anthropic_message(
                api_key,
                settings.model,
                instruction,
                text,
                _request_timeout(settings),
                _generation_limit(settings, _context_limit(settings)),
                media_path=media_path,
            )
        elif self.name == "gemini":
            result = _gemini_generate(
                api_key,
                settings.model,
                instruction,
                text,
                _request_timeout(settings),
                _generation_limit(settings, _context_limit(settings)),
                media_path=media_path,
            )
        else:
            result = ("", {})
        if result and isinstance(result, tuple):
            text_out, usage = result
            if text_out:
                return text_out, usage
        if media_path:
            raise RuntimeError(
                f"Failed to obtain response from cloud provider '{self.name}' "
                f"using model '{settings.model}'."
            )
        raise RuntimeError(
            f"Cloud provider '{self.name}' returned an empty response using model '{settings.model}'."
        )


class OpenAICompatibleCloudProvider:
    def __init__(
        self,
        name: str,
        env_var: str,
        default_endpoint: str,
        default_models: Optional[list] = None,
    ) -> None:
        self.name = name
        self.env_var = env_var
        self.default_endpoint = default_endpoint
        self.default_models = default_models or []

    def status(self) -> ProviderStatus:
        api_key = os.environ.get(self.env_var)
        endpoint = os.environ.get(_endpoint_env_var(self.name)) or self.default_endpoint
        if not api_key:
            return ProviderStatus(self.name, False, f"{self.env_var} is not set.", [])
        models = _fetch_openai_compatible_models(endpoint, api_key)
        detail = f"Connected to {endpoint}." if models else f"{self.env_var} is configured."
        return ProviderStatus(self.name, True, detail, models or self.default_models)

    def restore_text(
        self,
        text: str,
        instruction: str,
        settings: ModelSettings,
        media_path: Optional[Path] = None,
    ) -> tuple[str, dict]:
        api_key = os.environ.get(self.env_var)
        endpoint = settings.endpoint or os.environ.get(_endpoint_env_var(self.name)) or self.default_endpoint
        if not api_key:
            raise RuntimeError(
                f"Cloud provider '{self.name}' requires environment variable '{self.env_var}'."
            )
        result = openai_compatible_chat(
            endpoint=endpoint,
            settings=settings,
            instruction=instruction,
            text=text,
            api_key=api_key,
            timeout=_request_timeout(settings),
            media_path=media_path,
        )
        if result and result[0]:
            return result
        if media_path:
            raise RuntimeError(
                f"Failed to obtain response from cloud provider '{self.name}' "
                f"using model '{settings.model}'."
            )
        raise RuntimeError(
            f"Cloud provider '{self.name}' returned an empty response using model '{settings.model}'."
        )


def _endpoint_env_var(provider_name: str) -> str:
    normalized = provider_name.upper().replace("-", "_")
    return f"AKSHARA_{normalized}_BASE_URL"


def _fetch_native_models(provider_name: str, api_key: str) -> list:
    if provider_name == "openai":
        return _fetch_openai_compatible_models("https://api.openai.com/v1", api_key)
    if provider_name == "gemini":
        return _fetch_gemini_models(api_key)
    if provider_name == "anthropic":
        return _fetch_anthropic_models(api_key)
    return []


def _fetch_gemini_models(api_key: str) -> list:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        f"?key={urllib.parse.quote(api_key, safe='')}"
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    models = []
    for item in data.get("models", []):
        name = item.get("name") if isinstance(item, dict) else ""
        if name:
            models.append(str(name).removeprefix("models/"))
    return models


def _fetch_anthropic_models(api_key: str) -> list:
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "Accept": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    return [
        str(item.get("id"))
        for item in data.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]


def _anthropic_message(
    api_key: str,
    model: str,
    instruction: str,
    text: str,
    timeout: Optional[float],
    max_tokens: int,
    media_path: Optional[Path] = None,
) -> tuple[str, dict]:
    if media_path:
        mime_type = _media_mime_type(media_path)

        import base64

        try:
            media_bytes = media_path.read_bytes()
            media_base64 = base64.b64encode(media_bytes).decode("utf-8")
        except OSError as exc:
            raise RuntimeError(f"Failed to read image file: {exc}")

        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": media_base64,
                },
            },
            {
                "type": "text",
                "text": text or "Please restore and clean up the text in this document.",
            },
        ]
    else:
        content = text

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "system": instruction,
        "messages": [{"role": "user", "content": content}],
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
            err_data = json.loads(err_body)
            msg = err_data.get("error", {}).get("message") or err_data.get("error") or err_body
        except Exception:
            msg = exc.reason

        msg_lower = str(msg).lower()
        if (
            "image" in msg_lower
            or "vision" in msg_lower
            or "does not support" in msg_lower
            or exc.code == 400
        ):
            raise RuntimeError(
                f"Anthropic model '{model}' does not support vision/image inputs. "
                "Please configure a vision model (e.g. Claude 3.5 Sonnet, Claude 3.5 Haiku) "
                "or switch the OCR/decode mode."
            )
        raise RuntimeError(f"Anthropic API error (HTTP {exc.code}): {msg}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        if media_path:
            raise RuntimeError(f"Failed to connect to Anthropic API: {exc}")
        return "", {}
    finally:
        if media_path:
            media_bytes = None
            media_base64 = None
            content = None
            payload = None
            gc.collect()

    content_parts = data.get("content") or []
    parts = [part.get("text", "") for part in content_parts if isinstance(part, dict)]
    result = "\n".join(part for part in parts if part).strip()

    # Extract usage
    usage_data = data.get("usage") or {}
    prompt_tokens = usage_data.get("input_tokens", 0)
    completion_tokens = usage_data.get("output_tokens", 0)
    stop_reason = data.get("stop_reason")
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "truncated": (stop_reason == "max_tokens"),
    }
    return result + ("\n" if result else ""), usage


def _gemini_generate(
    api_key: str,
    model: str,
    instruction: str,
    text: str,
    timeout: Optional[float],
    max_tokens: int,
    media_path: Optional[Path] = None,
) -> tuple[str, dict]:
    model_name = urllib.parse.quote(model, safe="")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    )
    parts = [{"text": text or "Please restore and clean up the text in this document."}]

    if media_path:
        suffix = media_path.suffix.lower()
        mime_type = _media_mime_type(media_path)
        if suffix == ".pdf":
            mime_type = "application/pdf"

        import base64

        try:
            media_bytes = media_path.read_bytes()
            media_base64 = base64.b64encode(media_bytes).decode("utf-8")
        except OSError as exc:
            raise RuntimeError(f"Failed to read file: {exc}")

        parts.append(
            {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": media_base64,
                }
            }
        )

    payload = {
        "systemInstruction": {"parts": [{"text": instruction}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens},
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
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
            err_data = json.loads(err_body)
            # Gemini errors are structured as {"error": {"code": 400, "message": "...", "status": "INVALID_ARGUMENT"}}
            msg = err_data.get("error", {}).get("message") or err_data.get("error") or err_body
        except Exception:
            msg = exc.reason

        msg_lower = str(msg).lower()
        if (
            "image" in msg_lower
            or "vision" in msg_lower
            or "does not support" in msg_lower
            or exc.code == 400
        ):
            raise RuntimeError(
                f"Gemini model '{model}' does not support vision/image inputs. "
                "Please configure a vision model (e.g. gemini-2.5-flash or gemini-2.5-pro) "
                "or switch the OCR/decode mode."
            )
        raise RuntimeError(f"Gemini API error (HTTP {exc.code}): {msg}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        if media_path:
            raise RuntimeError(f"Failed to connect to Gemini API: {exc}")
        return "", {}
    finally:
        if media_path:
            media_bytes = None
            media_base64 = None
            parts = None
            payload = None
            gc.collect()

    candidates = data.get("candidates") or []
    if not candidates:
        return "", {}
    parts = (candidates[0].get("content") or {}).get("parts") or []
    result = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()

    # Extract usage
    usage_data = data.get("usageMetadata") or {}
    prompt_tokens = usage_data.get("promptTokenCount", 0)
    completion_tokens = usage_data.get("candidatesTokenCount", 0)
    finish_reason = candidates[0].get("finishReason") if candidates else ""
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": usage_data.get("totalTokenCount", 0) or (prompt_tokens + completion_tokens),
        "truncated": (finish_reason == "MAX_TOKENS"),
    }
    return result + ("\n" if result else ""), usage


def _provider_timeout(execution_mode: str) -> int:
    if execution_mode not in EXECUTION_MODES:
        execution_mode = "balanced"
    return {
        "fast": 120,
        "balanced": 240,
        "quality": 480,
    }[execution_mode]


def _request_timeout(settings: object) -> Optional[float]:
    value = getattr(settings, "request_timeout_seconds", None)
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return float(seconds) if seconds > 0 else None
