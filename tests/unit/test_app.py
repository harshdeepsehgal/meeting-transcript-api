from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from app import main
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def lifespan_database(monkeypatch) -> SimpleNamespace:
    resources = SimpleNamespace(
        engine=SimpleNamespace(dispose=AsyncMock()),
        session_factory=object(),
    )
    monkeypatch.setattr(main, "build_engine", Mock(return_value=resources.engine))
    monkeypatch.setattr(
        main,
        "build_session_factory",
        Mock(return_value=resources.session_factory),
    )
    return resources


def test_application_documents_dialog_routes() -> None:
    schema = create_app().openapi()

    assert set(schema["paths"]) == {
        "/dialogs",
        "/dialogs/{dialog_id}",
        "/dialogs/{dialog_id}/responses",
        "/dialogs/{dialog_id}/summary",
    }
    assert set(schema["paths"]["/dialogs"]) == {"get"}
    assert set(schema["paths"]["/dialogs/{dialog_id}"]) == {"get"}
    assert set(schema["paths"]["/dialogs/{dialog_id}/summary"]) == {"post"}
    assert set(schema["paths"]["/dialogs/{dialog_id}/responses"]) == {"post"}

    list_operation = schema["paths"]["/dialogs"]["get"]
    list_parameters = {parameter["name"]: parameter for parameter in list_operation["parameters"]}
    assert list_parameters["limit"]["schema"] == {
        "type": "integer",
        "maximum": 100,
        "minimum": 1,
        "description": "Maximum number of dialogs to return.",
        "default": 20,
        "title": "Limit",
    }
    cursor_schema = list_parameters["cursor"]["schema"]
    assert {schema.get("minLength") for schema in cursor_schema["anyOf"]} == {1, None}
    assert list_operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/DialogListResponse")

    detail_operation = schema["paths"]["/dialogs/{dialog_id}"]["get"]
    assert detail_operation["responses"]["404"]["description"] == "Dialog not found"
    assert detail_operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/DialogDetailResponse")

    summary_operation = schema["paths"]["/dialogs/{dialog_id}/summary"]["post"]
    summary_parameters = {
        parameter["name"]: parameter for parameter in summary_operation["parameters"]
    }
    assert summary_parameters["refresh"]["schema"]["default"] is False
    assert "requestBody" not in summary_operation
    assert summary_operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/SummaryResponse")
    assert {
        summary_operation["responses"][status]["description"]
        for status in ("404", "422", "502", "503")
    } == {
        "Dialog not found",
        "Validation error or transcript exceeds model context limit",
        "Summary provider failed",
        "OpenAI API key is not configured",
    }

    responses_operation = schema["paths"]["/dialogs/{dialog_id}/responses"]["post"]
    assert "requestBody" not in responses_operation
    response_schema = responses_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema["type"] == "array"
    assert response_schema["items"]["$ref"].endswith("/DialogResponseItem")
    response_properties = schema["components"]["schemas"]["DialogResponseItem"]["properties"]
    assert set(response_properties) == {
        "query",
        "storedResponse",
        "generatedResponse",
        "err",
    }


async def test_application_lifespan_initializes_and_closes_openai_provider(
    monkeypatch,
    lifespan_database,
) -> None:
    settings = Settings.model_construct(openai_api_key=SecretStr("test-key"))
    provider = SimpleNamespace(close=AsyncMock())
    build_provider = Mock(return_value=provider)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "build_openai_provider", build_provider)
    application = create_app()

    async with application.router.lifespan_context(application) as state:
        assert state == {
            "db_session_factory": lifespan_database.session_factory,
            "openai_provider": provider,
        }

    build_provider.assert_called_once_with(settings)
    provider.close.assert_awaited_once_with()


async def test_application_lifespan_allows_missing_openai_key(
    monkeypatch,
    lifespan_database,
) -> None:
    settings = Settings.model_construct(openai_api_key=None)
    build_provider = Mock()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "build_openai_provider", build_provider)
    application = create_app()

    async with application.router.lifespan_context(application) as state:
        assert state is not None
        assert state["openai_provider"] is None

    build_provider.assert_not_called()


async def test_application_lifespan_disposes_database_if_provider_close_fails(
    monkeypatch,
    lifespan_database,
) -> None:
    settings = Settings.model_construct(openai_api_key=SecretStr("test-key"))
    provider = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "build_openai_provider", Mock(return_value=provider))
    application = create_app()

    with pytest.raises(RuntimeError, match="close failed"):
        async with application.router.lifespan_context(application):
            pass

    lifespan_database.engine.dispose.assert_awaited_once_with()
