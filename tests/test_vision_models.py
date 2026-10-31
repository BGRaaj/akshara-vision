import json
import urllib.error

import pytest

from akshara_vision.providers import vision
from akshara_vision.providers.vision import (
    SOURCE_API,
    SOURCE_NAME_RULES,
    SOURCE_UNKNOWN,
    lookup_vision_models,
)


def _names(provider, models):
    result = lookup_vision_models(provider, models)
    return result.models, result.source


def test_openai_name_rules_keep_vision_chat_models_only(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    models = [
        "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-5.5", "o3", "o3-pro", "o4-mini", "o1",
        "o1-mini", "o3-mini", "gpt-3.5-turbo", "text-embedding-3-small", "whisper-1",
        "tts-1", "dall-e-3", "gpt-image-1", "gpt-4o-audio-preview", "gpt-4o-realtime-preview",
        "gpt-4o-transcribe", "gpt-4o-search-preview", "gpt-3.5-turbo-instruct",
    ]
    found, source = _names("openai", models)
    assert source == SOURCE_NAME_RULES
    assert found == ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-5.5", "o3", "o3-pro", "o4-mini", "o1"]


def test_gemini_name_rules_drop_non_text_output_models():
    models = [
        "gemini-2.5-flash", "gemini-3.5-pro", "gemini-3.1-flash-lite",
        "gemini-embedding-001", "gemini-2.5-flash-preview-tts", "gemini-3.1-flash-image",
        "gemini-3.1-flash-live-preview", "gemini-1.0-pro", "aqa", "imagen-4.0", "veo-3",
    ]
    found, source = _names("gemini", models)
    assert source == SOURCE_NAME_RULES
    assert found == ["gemini-2.5-flash", "gemini-3.5-pro", "gemini-3.1-flash-lite"]


def test_deepseek_only_vision_variant():
    found, _ = _names("deepseek", ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"])
    assert found == ["deepseek-v4-flash-vision-exp"]


def test_unknown_provider_reports_unknown():
    result = lookup_vision_models("custom-openai-compatible", ["some-model"])
    assert result.models is None
    assert result.source == SOURCE_UNKNOWN


def test_anthropic_uses_api_capabilities(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {
        "data": [
            {"id": "claude-a", "capabilities": {"image_input": {"supported": True}}},
            {"id": "claude-b", "capabilities": {"image_input": {"supported": False}}},
        ]
    }
    monkeypatch.setattr(vision, "_get_json", lambda url, headers=None: payload)
    found, source = _names("anthropic", ["claude-b", "claude-a"])
    assert (found, source) == (["claude-a"], SOURCE_API)


def test_anthropic_without_key_falls_back_to_name_rules(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    found, source = _names("anthropic", ["claude-sonnet-5", "claude-2.1"])
    assert (found, source) == (["claude-sonnet-5"], SOURCE_NAME_RULES)


def test_openrouter_uses_input_modalities(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    payload = {
        "data": [
            {"id": "a/vision", "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "b/text", "architecture": {"input_modalities": ["text"]}},
        ]
    }
    monkeypatch.setattr(vision, "_get_json", lambda url, headers=None: payload)
    found, source = _names("openrouter", ["b/text", "a/vision"])
    assert (found, source) == (["a/vision"], SOURCE_API)


def test_mistral_uses_capabilities_vision(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    payload = {
        "data": [
            {"id": "pixtral-large", "capabilities": {"vision": True}},
            {"id": "mistral-embed", "capabilities": {"vision": False}},
        ]
    }
    monkeypatch.setattr(vision, "_get_json", lambda url, headers=None: payload)
    found, source = _names("mistral", ["pixtral-large", "mistral-embed"])
    assert (found, source) == (["pixtral-large"], SOURCE_API)


def test_api_failure_falls_back_to_name_rules(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")

    def boom(url, headers=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(vision, "_get_json", boom)
    found, source = _names("mistral", ["pixtral-12b", "mistral-embed"])
    assert (found, source) == (["pixtral-12b"], SOURCE_NAME_RULES)


def test_metadata_absent_is_not_treated_as_text_only(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "k")
    payload = {"data": [{"id": "accounts/fireworks/models/qwen2p5-vl-32b"}]}
    monkeypatch.setattr(vision, "_get_json", lambda url, headers=None: payload)
    found, source = _names("fireworks", ["accounts/fireworks/models/qwen2p5-vl-32b"])
    assert source == SOURCE_NAME_RULES
    assert found == ["accounts/fireworks/models/qwen2p5-vl-32b"]


def test_ollama_uses_show_capabilities(monkeypatch):
    caps = {"llava:7b": ["completion", "vision"], "llama3:8b": ["completion"]}

    def fake_urlopen(request, timeout=None, attempts=1):
        return {"capabilities": caps[json.loads(request.data)["model"]]}

    monkeypatch.setattr(vision, "_urlopen_json", fake_urlopen)
    found, source = _names("ollama", ["llama3:8b", "llava:7b"])
    assert (found, source) == (["llava:7b"], SOURCE_API)


def test_ollama_old_server_without_capabilities_uses_name_rules(monkeypatch):
    monkeypatch.setattr(vision, "_urlopen_json", lambda *a, **k: {"details": {}})
    found, source = _names("ollama", ["llava:7b", "llama3:8b", "qwen2.5vl:7b"])
    assert source == SOURCE_NAME_RULES
    assert found == ["llava:7b", "qwen2.5vl:7b"]


def test_lm_studio_uses_vlm_type(monkeypatch):
    payload = {
        "data": [
            {"id": "qwen2-vl-7b-instruct", "type": "vlm"},
            {"id": "llama-3.1-8b", "type": "llm"},
            {"id": "nomic-embed", "type": "embeddings"},
        ]
    }
    monkeypatch.setattr(vision, "_get_json", lambda url, headers=None: payload)
    found, source = _names("lm-studio", ["llama-3.1-8b", "qwen2-vl-7b-instruct", "nomic-embed"])
    assert (found, source) == (["qwen2-vl-7b-instruct"], SOURCE_API)


@pytest.mark.parametrize("has_vision, expected", [(True, ["gemma-3-4b"]), (False, [])])
def test_llama_cpp_props_modalities(monkeypatch, has_vision, expected):
    def fake_get(url, headers=None):
        if url.endswith("/props"):
            return {"modalities": {"vision": has_vision}}
        raise urllib.error.URLError("not lm studio")

    monkeypatch.setattr(vision, "_get_json", fake_get)
    found, source = _names("llama-cpp", ["gemma-3-4b"])
    assert (found, source) == (expected, SOURCE_API)
