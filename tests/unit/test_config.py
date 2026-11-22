from pydantic import SecretStr

from app.core.config import Settings, get_settings


def test_settings_load_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.openai_api_key == SecretStr("test-secret")
    assert "test-secret" not in repr(settings)


def test_get_settings_returns_one_process_scoped_instance(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "cached-test")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.app_env == "cached-test"
