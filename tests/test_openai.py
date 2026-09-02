import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.openai import build_openai_provider


def test_openai_provider_requires_an_api_key() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_openai_provider(Settings(_env_file=None, openai_api_key=None))


def test_openai_provider_uses_configured_model() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr("test-key"),
        openai_model="test-model",
    )

    provider = build_openai_provider(settings)

    assert provider.model == "test-model"
