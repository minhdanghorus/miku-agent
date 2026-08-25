"""Provider adapter: configuration resolution. No network calls anywhere here."""

from __future__ import annotations

import pytest

from miku.runtime.config import load_settings
from miku.runtime.providers import (
    GREENNODE,
    ProviderError,
    chat_model,
    get_provider,
    judge_model,
    resolve_capabilities,
    resolve_model,
)

KEY_ENV = GREENNODE.key_env


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")
    return "test-key-not-real"


def test_unknown_provider_names_the_registered_ones():
    settings = load_settings(provider="nope")
    with pytest.raises(ProviderError) as excinfo:
        get_provider(settings)
    assert "nope" in str(excinfo.value)
    assert "greennode" in str(excinfo.value)


def test_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    settings = load_settings(provider="greennode")
    with pytest.raises(ProviderError) as excinfo:
        chat_model(settings)
    assert KEY_ENV in str(excinfo.value)


def test_blank_key_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "   ")
    with pytest.raises(ProviderError):
        chat_model(load_settings(provider="greennode"))


def test_unknown_role_is_an_error():
    with pytest.raises(ProviderError) as excinfo:
        resolve_model(load_settings(), "supervisor")
    assert "supervisor" in str(excinfo.value)


def test_unmapped_role_does_not_fall_back(monkeypatch):
    """A role the descriptor does not map must raise, not borrow another role."""
    monkeypatch.delitem(GREENNODE.models, "embed")
    try:
        with pytest.raises(ProviderError) as excinfo:
            resolve_model(load_settings(), "embed")
        assert "embed" in str(excinfo.value)
        assert "greennode" in str(excinfo.value)
    finally:
        GREENNODE.models["embed"] = "baai/bge-m3"


def test_role_default_comes_from_the_descriptor():
    settings = load_settings(provider="greennode")
    assert resolve_model(settings, "main") == GREENNODE.models["main"]


def test_role_override_wins_over_the_default():
    settings = load_settings(provider="greennode", model_main="qwen/qwen3-5-27b")
    assert resolve_model(settings, "main") == "qwen/qwen3-5-27b"


def test_capability_flags_are_readable_per_model():
    """The spike found qwen refuses native structured output; gemma accepts it."""
    gemma = load_settings(model_main="google/gemma-4-31b-it")
    qwen = load_settings(model_main="qwen/qwen3-5-27b")

    assert resolve_capabilities(gemma, "main").supports("native_structured_output")
    assert not resolve_capabilities(qwen, "main").supports("native_structured_output")


def test_unknown_capability_counts_as_unsupported():
    """Prompt caching was never probed, so it must not read as available."""
    caps = resolve_capabilities(load_settings(), "main")
    assert caps.prompt_cache == "unknown"
    assert not caps.supports("prompt_cache")


def test_undeclared_model_has_all_capabilities_unknown():
    settings = load_settings(model_main="some/model-we-never-tested")
    caps = resolve_capabilities(settings, "main")
    assert caps.native_structured_output == "unknown"
    assert not caps.supports("native_structured_output")


def test_chat_model_applies_configured_limits(key):
    settings = load_settings(provider="greennode", request_timeout=12.0, max_retries=5)
    model = chat_model(settings, "main")
    assert model.request_timeout == 12.0
    assert model.max_retries == 5
    assert model.model_name == GREENNODE.models["main"]


def test_base_url_override_is_honoured(key, monkeypatch):
    monkeypatch.setenv(GREENNODE.base_url_env, "https://example.invalid/v1")
    model = chat_model(load_settings(provider="greennode"))
    assert "example.invalid" in str(model.openai_api_base)


def test_judge_and_chat_resolve_from_one_config(key):
    """Same descriptor, same credentials — the two stacks cannot drift apart."""
    settings = load_settings(provider="greennode")
    chat = chat_model(settings, "main")
    judge = judge_model(settings)

    assert chat.model_name == resolve_model(settings, "main")
    assert judge.model_name == resolve_model(settings, "judge")


def test_judge_defaults_to_a_different_model_than_the_agent(key):
    """A model grading its own output is the bias we are avoiding."""
    settings = load_settings(provider="greennode")
    assert resolve_model(settings, "judge") != resolve_model(settings, "main")


def test_judge_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(ProviderError):
        judge_model(load_settings(provider="greennode"))
