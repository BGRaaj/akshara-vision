"""Vision-capability discovery used by the model picker.

Akshara needs a model that accepts *image input* for OCR and page restoration, so
the picker should only offer those. Detection prefers machine-readable metadata
published by the provider itself ("api"). Only when a provider publishes nothing do
we fall back to conservative model-name rules ("name-rules"). Providers we cannot
judge at all report "unknown" and the caller shows every model with a warning.

Metadata sources (verified against provider docs):
  anthropic  GET /v1/models              capabilities.image_input.supported
  openrouter GET /api/v1/models          architecture.input_modalities contains "image"
  mistral    GET /v1/models              capabilities.vision
  xai        GET /v1/language-models     input_modalities contains "image"
  fireworks  GET /inference/v1/models    supports_image_input (best effort)
  ollama     POST /api/show              capabilities contains "vision"
  lm-studio  GET /api/v0/models          type == "vlm"
  llama.cpp  GET /props                  modalities.vision
No usable metadata (name rules): openai, gemini, groq, together, perplexity,
deepseek, cerebras, jan.
"""

import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from akshara_vision.core.constants import NON_VISION_MODEL_PATTERNS, VISION_MODEL_PATTERNS
from akshara_vision.providers.cloud import _endpoint_env_var
from akshara_vision.providers.local import (
    _urlopen_json,
    get_ollama_base_url,
    get_openai_compatible_endpoint,
)

SOURCE_API = "api"
SOURCE_NAME_RULES = "name-rules"
SOURCE_UNKNOWN = "unknown"

_TIMEOUT = 6.0

# Extractors return True/False when the entry carries capability metadata and None
# when it does not, so "no metadata" is never mistaken for "not a vision model".
Extractor = Callable[[dict], Optional[bool]]


@dataclass
class VisionLookup:
    """Result of a vision lookup.

    models is None when nothing could be determined (source == "unknown").
    """

    models: Optional[List[str]]
    source: str
    detail: str = field(default="")


def lookup_vision_models(provider_name: str, models: List[str]) -> VisionLookup:
    """Return the vision-capable subset of *models* for *provider_name*."""
    probe = _PROBES.get(provider_name)
    if probe is not None:
        try:
            found = probe(provider_name, models)
        except Exception:  # network/parse problems must never break the picker
            found = None
        if found is not None:
            return VisionLookup(found, SOURCE_API)
    by_name = filter_by_name(provider_name, models)
    if by_name is not None:
        return VisionLookup(by_name, SOURCE_NAME_RULES)
    return VisionLookup(None, SOURCE_UNKNOWN)


def filter_by_name(provider_name: str, models: List[str]) -> Optional[List[str]]:
    """Filter by model-name regexes; None when the provider has no rules."""
    include = VISION_MODEL_PATTERNS.get(provider_name)
    if not include:
        return None
    exclude = NON_VISION_MODEL_PATTERNS.get(provider_name, [])
    return [m for m in models if _matches(m, include) and not _matches(m, exclude)]


def _matches(model: str, patterns: List[str]) -> bool:
    return any(re.search(pattern, model, re.IGNORECASE) for pattern in patterns)


# --------------------------------------------------------------------------- helpers


def _get_json(url: str, headers: Optional[Dict[str, str]] = None):
    request = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    return _urlopen_json(request, timeout=_TIMEOUT, attempts=1)


def _items(payload) -> List[dict]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get("data") or payload.get("models") or []
    else:
        values = []
    return [item for item in values if isinstance(item, dict)]


def _ordered(ids: List[str], models: List[str]) -> List[str]:
    """Keep the picker's original ordering; fall back to API order if it was empty."""
    if not models:
        return ids
    wanted = set(ids)
    return [m for m in models if m in wanted]


def _metadata_probe(
    url: str,
    headers: Dict[str, str],
    extract: Extractor,
    models: List[str],
) -> Optional[List[str]]:
    verdicts: Dict[str, bool] = {}
    for item in _items(_get_json(url, headers)):
        model_id = item.get("id")
        verdict = extract(item)
        if model_id and verdict is not None:
            verdicts[str(model_id)] = verdict
    if not verdicts:
        return None
    return _ordered([mid for mid, ok in verdicts.items() if ok], models)


def _bearer(env_var: str) -> Dict[str, str]:
    key = os.environ.get(env_var)
    return {"Authorization": f"Bearer {key}"} if key else {}


def _cloud_target(provider_name: str):
    """Return (endpoint, env_var) for an OpenAI-compatible cloud provider."""
    from akshara_vision.registries.providers import get_provider

    provider = get_provider(provider_name)
    endpoint = os.environ.get(_endpoint_env_var(provider_name)) or getattr(
        provider, "default_endpoint", ""
    )
    return str(endpoint).rstrip("/"), getattr(provider, "env_var", "")


# ------------------------------------------------------------------ cloud extractors


def _anthropic_vision(item: dict) -> Optional[bool]:
    caps = item.get("capabilities")
    if not isinstance(caps, dict):
        return None
    return bool((caps.get("image_input") or {}).get("supported"))


def _openrouter_vision(item: dict) -> Optional[bool]:
    arch = item.get("architecture")
    modalities = arch.get("input_modalities") if isinstance(arch, dict) else None
    if not isinstance(modalities, list):
        return None
    return "image" in modalities


def _mistral_vision(item: dict) -> Optional[bool]:
    caps = item.get("capabilities")
    if not isinstance(caps, dict) or "vision" not in caps:
        return None
    return bool(caps.get("vision"))


def _xai_vision(item: dict) -> Optional[bool]:
    modalities = item.get("input_modalities")
    if not isinstance(modalities, list):
        return None
    return "image" in modalities


def _fireworks_vision(item: dict) -> Optional[bool]:
    value = item.get("supports_image_input")
    return value if isinstance(value, bool) else None


# --------------------------------------------------------------------- cloud probes


def _probe_anthropic(provider_name: str, models: List[str]) -> Optional[List[str]]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return _metadata_probe(
        "https://api.anthropic.com/v1/models?limit=1000", headers, _anthropic_vision, models
    )


def _make_openai_style_probe(url_suffix: str, extract: Extractor):
    def probe(provider_name: str, models: List[str]) -> Optional[List[str]]:
        endpoint, env_var = _cloud_target(provider_name)
        if not endpoint:
            return None
        return _metadata_probe(f"{endpoint}{url_suffix}", _bearer(env_var), extract, models)

    return probe


# --------------------------------------------------------------------- local probes


def _probe_ollama(provider_name: str, models: List[str]) -> Optional[List[str]]:
    base = get_ollama_base_url()

    def capabilities(name: str) -> Optional[bool]:
        try:
            request = urllib.request.Request(
                f"{base}/api/show",
                data=json.dumps({"model": name}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            caps = _urlopen_json(request, timeout=_TIMEOUT, attempts=1).get("capabilities")
        except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError):
            return None
        # Ollama < 0.6.4 does not report capabilities at all.
        return ("vision" in caps) if isinstance(caps, list) else None

    if not models:
        return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        verdicts = list(pool.map(capabilities, models))
    if all(v is None for v in verdicts):
        return None
    # Per-model gaps (e.g. a transient error) fall back to the name rules.
    rules = VISION_MODEL_PATTERNS.get("ollama", [])
    vision: List[str] = []
    for name, verdict in zip(models, verdicts):
        if verdict is None:
            verdict = _matches(name, rules)
        if verdict:
            vision.append(name)
    return vision


def _local_root(provider_name: str) -> str:
    from akshara_vision.registries.providers import get_provider

    default = getattr(get_provider(provider_name), "default_endpoint", "http://localhost:1234/v1")
    endpoint = get_openai_compatible_endpoint(provider_name, default)
    return re.sub(r"/v1/?$", "", endpoint.rstrip("/"))


def _probe_openai_compatible_local(provider_name: str, models: List[str]) -> Optional[List[str]]:
    root = _local_root(provider_name)

    # LM Studio: type is "vlm" for vision models, "llm" / "embeddings" otherwise.
    try:
        found = _metadata_probe(
            f"{root}/api/v0/models",
            {},
            lambda item: (item.get("type") == "vlm") if item.get("type") else None,
            models,
        )
        if found is not None:
            return found
    except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError):
        pass

    # llama.cpp llama-server hosts one model and reports whether it has a projector.
    try:
        props = _get_json(f"{root}/props")
    except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError):
        return None
    if not isinstance(props, dict):
        return None
    modalities = props.get("modalities")
    if isinstance(modalities, dict) and "vision" in modalities:
        return list(models) if modalities.get("vision") else []
    if "has_multimodal" in props:
        return list(models) if props.get("has_multimodal") else []
    return None


_PROBES: Dict[str, Callable[[str, List[str]], Optional[List[str]]]] = {
    "anthropic": _probe_anthropic,
    "openrouter": _make_openai_style_probe("/models", _openrouter_vision),
    "mistral": _make_openai_style_probe("/models", _mistral_vision),
    "xai": _make_openai_style_probe("/language-models", _xai_vision),
    "fireworks": _make_openai_style_probe("/models", _fireworks_vision),
    "ollama": _probe_ollama,
    "lm-studio": _probe_openai_compatible_local,
    "openai-compatible-local": _probe_openai_compatible_local,
    "llama-cpp": _probe_openai_compatible_local,
    "jan": _probe_openai_compatible_local,
}
