from pydantic import SecretStr

from app.core.config import Settings


def test_settings_load_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.openai_api_key == SecretStr("test-secret")
    assert "test-secret" not in repr(settings)
