from app.main import create_app


async def test_application_starts_with_no_business_routes() -> None:
    application = create_app()
    async with application.router.lifespan_context(application):
        schema = application.openapi()

    assert schema["paths"] == {}
