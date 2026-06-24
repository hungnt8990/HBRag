import asyncio

from app.core.config import Settings
from app.services.llms import llm_factory
from app.services.llms.llm_fake_llm import FakeLLM
from app.services.llms.llm_openai_llm import OpenAICompatibleLLM


def test_build_llm_provider_uses_profile_model_for_openai_compatible(monkeypatch) -> None:
    configured_settings = Settings(
        _env_file=None,
        llm_provider="fake",
        llm_base_url="http://llm.test/v1",
        llm_api_key="secret",
        llm_model="runtime-model",
    )
    monkeypatch.setattr(llm_factory, "settings", configured_settings)

    provider = llm_factory.build_llm_provider(
        provider="openai_compatible",
        model="profile-model",
    )

    assert isinstance(provider, OpenAICompatibleLLM)
    assert provider._model == "profile-model"
    assert provider._base_url == "http://llm.test/v1"

def test_build_llm_provider_uses_override_base_url_for_openai_compatible(monkeypatch) -> None:
    configured_settings = Settings(
        _env_file=None,
        llm_provider="fake",
        llm_base_url="http://llm.test/v1",
        llm_api_key="secret",
        llm_model="runtime-model",
    )
    monkeypatch.setattr(llm_factory, "settings", configured_settings)

    provider = llm_factory.build_llm_provider(
        provider="openai_compatible",
        base_url="http://enrich.test/v1",
        model="profile-model",
    )

    assert isinstance(provider, OpenAICompatibleLLM)
    assert provider._base_url == "http://enrich.test/v1"
    assert provider._model == "profile-model"


def test_build_llm_provider_keeps_fake_when_profile_provider_is_fake(monkeypatch) -> None:
    configured_settings = Settings(_env_file=None, llm_provider="openai_compatible")
    monkeypatch.setattr(llm_factory, "settings", configured_settings)

    provider = llm_factory.build_llm_provider(provider="fake", model="profile-model")

    assert isinstance(provider, FakeLLM)


def test_build_llm_provider_or_error_defers_config_errors_to_generate(monkeypatch) -> None:
    configured_settings = Settings(
        _env_file=None,
        llm_provider="fake",
        llm_base_url=None,
        llm_model=None,
    )
    monkeypatch.setattr(llm_factory, "settings", configured_settings)

    provider = llm_factory.build_llm_provider_or_error(
        provider="openai_compatible",
        model="profile-model",
    )

    try:
        asyncio.run(provider.generate(system_prompt="", user_prompt=""))
    except RuntimeError as exc:
        assert "LLM_BASE_URL" in str(exc)
    else:
        raise AssertionError("Expected provider generate to fail with config error.")
