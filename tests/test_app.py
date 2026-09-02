from app.main import create_app


async def test_application_documents_dialog_read_routes() -> None:
    application = create_app()
    async with application.router.lifespan_context(application):
        schema = application.openapi()

    assert set(schema["paths"]) == {"/dialogs", "/dialogs/{dialog_id}"}
    assert set(schema["paths"]["/dialogs"]) == {"get"}
    assert set(schema["paths"]["/dialogs/{dialog_id}"]) == {"get"}

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
