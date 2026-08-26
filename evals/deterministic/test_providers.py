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


def test_two_roles_may_resolve_to_one_model(key):
    """The previous version of this test asserted judge != main, which the spec
    never required -- its scenario is conditional ("may differ"). It encoded a
    policy, and the policy lost to a measurement: the distinct judge returned a
    constant "fail" on anything involving a date. What the spec does require is
    that each role resolves to a declared id, and that two roles sharing one is
    not an error."""
    settings = load_settings(provider="greennode")
    judge = resolve_model(settings, "judge")

    assert judge == resolve_model(settings, "main")
    assert judge in GREENNODE.capabilities


def test_the_judge_role_can_be_pointed_elsewhere(monkeypatch, key):
    """How the judge-strength spike compared two judges without editing the
    descriptor. Keeping it exercisable is what makes the remap reversible."""
    monkeypatch.setenv("MIKU_MODEL_JUDGE", "openai/gpt-4o-mini")
    settings = load_settings(provider="greennode")

    assert resolve_model(settings, "judge") == "openai/gpt-4o-mini"
    assert judge_model(settings).model_name == "openai/gpt-4o-mini"
    assert resolve_model(settings, "main") == GREENNODE.models["main"]


def test_judge_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(ProviderError):
        judge_model(load_settings(provider="greennode"))


def test_selection_and_grading_are_separate_roles(key, monkeypatch):
    """They name gemma both, and that is not the same as being one role. `judge`
    moves whenever a better evaluator appears; `select` picks a slot a real
    person is offered. Pointing one elsewhere must not drag the other with it --
    the whole reason the roles were split."""
    settings = load_settings(provider="greennode")
    assert resolve_model(settings, "select") == GREENNODE.models["select"]

    monkeypatch.setenv("MIKU_MODEL_JUDGE", "openai/gpt-4o-mini")
    moved = load_settings(provider="greennode")

    assert resolve_model(moved, "judge") == "openai/gpt-4o-mini"
    assert resolve_model(moved, "select") == GREENNODE.models["select"]
