"""The provider adapter — configuration in, ready-to-use model clients out.

This is deliberately NOT a wire-format adapter. LangChain already normalises
that: every OpenAI-compatible provider is one ChatOpenAI differing only by
base_url, and an Anthropic-native one would be ChatAnthropic — both are a
BaseChatModel. So nothing here translates messages, tool schemas, or streaming
chunks.

What it does own is the part LangChain has no opinion about:

  * which provider is active, and where its key and base URL come from
  * which concrete model serves which ROLE (main / fast / judge / select / embed)
  * what each model can actually do (see CAPABILITIES below — this is not
    theoretical: qwen3-5-27b refuses native structured output while the other
    two GreenNode models accept it)
  * timeout / retry / concurrency, applied uniformly

Two builders, because two stacks need models from the same configuration:
chat_model() for the LangGraph agent, judge_model() for pydantic-evals' judge.

Adding a provider means adding a descriptor to PROVIDERS. It must never mean
editing a call site.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from miku.runtime.config import Settings

ROLES = ("main", "fast", "judge", "select", "embed")

Capability = Literal["yes", "no", "unknown"]


class ProviderError(RuntimeError):
    """Configuration is wrong. Raised before any request is attempted."""


@dataclass(frozen=True)
class ModelCapabilities:
    """What a specific model can do.

    'unknown' is a real answer and is treated as unsupported by callers — an
    unprobed capability must never be assumed present. GreenNode's prompt-cache
    support was never verified, so it is unknown, not no.
    """

    native_structured_output: Capability = "unknown"
    prompt_cache: Capability = "unknown"
    embeddings: Capability = "no"

    def supports(self, name: str) -> bool:
        return getattr(self, name, "unknown") == "yes"


@dataclass(frozen=True)
class Provider:
    """One provider, declared rather than coded."""

    name: str
    wire: Literal["openai", "anthropic"]
    key_env: str
    base_url_env: str | None
    default_base_url: str | None
    # role -> model id
    models: dict[str, str]
    # model id -> what it can do
    capabilities: dict[str, ModelCapabilities] = field(default_factory=dict)

    def caps(self, model_id: str) -> ModelCapabilities:
        """Capabilities for a model; unknown-everything if undeclared."""
        return self.capabilities.get(model_id, ModelCapabilities())


# GreenNode's catalog, confirmed live during the design spike. The whole menu is
# five models: three chat, two embedding. All three chat models handle tool
# calling, parallel tool calls, and streaming.
_GEMMA = "google/gemma-4-31b-it"
_QWEN = "qwen/qwen3-5-27b"
_GPT4O_MINI = "openai/gpt-4o-mini"
_BGE_M3 = "baai/bge-m3"

GREENNODE = Provider(
    name="greennode",
    wire="openai",
    key_env="GREENNODE_API_KEY",
    base_url_env="GREENNODE_BASE_URL",
    default_base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
    models={
        "main": _GEMMA,
        "fast": _GEMMA,
        # The same model as main, against the usual advice, because the usual
        # advice lost to a measurement. `judge` pointed at gpt-4o-mini on the
        # sound principle that a model grading its own output tends to flatter
        # it. Measured over 18 cases x 2 runs, that model returned "fail" for
        # every case on any dimension needing temporal reasoning -- correct and
        # incorrect alike -- scoring 3/6, exactly the accuracy of a constant
        # function. Gemma scored 18/18 twice. A flattering judge still carries
        # signal; a constant one carries none. See "Measured: the judge could
        # not judge" in the exploration doc. Reversible in one line the moment
        # this provider offers a third capable chat model.
        "judge": _GEMMA,
        # Separate from `judge` even though both name gemma today, because they
        # are chosen for different work and will diverge. `judge` grades evals
        # and moves the moment a better evaluator exists; `select` picks a slot
        # for a real user and must not follow it there. They were one role until
        # the judge remap moved production behaviour as a side effect.
        "select": _GEMMA,
        "embed": _BGE_M3,
    },
    capabilities={
        _GEMMA: ModelCapabilities(native_structured_output="yes"),
        # Verified: returns 400 "'messages' must contain the word 'json'".
        _QWEN: ModelCapabilities(native_structured_output="no"),
        _GPT4O_MINI: ModelCapabilities(native_structured_output="yes"),
        _BGE_M3: ModelCapabilities(embeddings="yes"),
    },
)

PROVIDERS: dict[str, Provider] = {
    GREENNODE.name: GREENNODE,
}


def get_provider(settings: Settings) -> Provider:
    """The active descriptor, or a named error listing what is registered."""
    try:
        return PROVIDERS[settings.provider]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise ProviderError(
            f"Unknown provider {settings.provider!r}. Registered providers: {known}"
        ) from None


def resolve_model(settings: Settings, role: str) -> str:
    """The model id for a role: the configured override, else the default.

    An unmapped role is an error, never a silent fall back to another role.
    """
    if role not in ROLES:
        raise ProviderError(f"Unknown model role {role!r}. Roles: {', '.join(ROLES)}")

    override = settings.model_override(role)
    if override:
        return override

    provider = get_provider(settings)
    model = provider.models.get(role)
    if not model:
        raise ProviderError(
            f"Provider {provider.name!r} maps no model to role {role!r}. "
            f"Set MIKU_MODEL_{role.upper()} or add it to the descriptor."
        )
    return model


def resolve_capabilities(settings: Settings, role: str) -> ModelCapabilities:
    """What the model serving this role can do."""
    return get_provider(settings).caps(resolve_model(settings, role))


def _credentials(provider: Provider) -> tuple[str, str | None]:
    """(api_key, base_url), failing loudly on a missing key."""
    key = os.environ.get(provider.key_env, "").strip()
    if not key:
        raise ProviderError(
            f"Missing API key: set {provider.key_env} in your environment or .env"
        )

    base_url = provider.default_base_url
    if provider.base_url_env:
        base_url = os.environ.get(provider.base_url_env, "").strip() or base_url
    return key, base_url


def chat_model(settings: Settings, role: str = "main"):
    """A LangChain chat model for a role, with the configured limits applied.

    Returns BaseChatModel. Imported lazily so that configuration errors and
    descriptor lookups stay testable without the LangChain import cost.
    """
    provider = get_provider(settings)
    model_id = resolve_model(settings, role)
    key, base_url = _credentials(provider)

    if provider.wire == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_id,
            api_key=key,
            base_url=base_url,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
            temperature=0,
        )

    if provider.wire == "anthropic":
        from langchain_anthropic import ChatAnthropic  # pragma: no cover - not registered yet

        return ChatAnthropic(
            model=model_id,
            api_key=key,
            base_url=base_url,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
        )

    raise ProviderError(f"Provider {provider.name!r} has unsupported wire {provider.wire!r}")


def judge_model(settings: Settings):
    """A pydantic-ai model for the judge role.

    pydantic-evals' LLMJudge resolves models through pydantic-ai, not LangChain.
    Same descriptor, same credentials — so the agent and the judge cannot drift
    onto different providers by accident.
    """
    provider = get_provider(settings)
    model_id = resolve_model(settings, "judge")
    key, base_url = _credentials(provider)

    if provider.wire != "openai":
        raise ProviderError(
            f"No judge model wiring for wire {provider.wire!r} yet (provider {provider.name!r})"
        )

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    return OpenAIChatModel(model_id, provider=OpenAIProvider(api_key=key, base_url=base_url))
