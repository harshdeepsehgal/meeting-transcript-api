import json
from pathlib import Path
from typing import cast

from app.main import create_app

COLLECTION_PATH = (
    Path(__file__).parents[2] / "postman" / "meeting-transcript-api.postman_collection.json"
)
EXPECTED_OPERATIONS = {
    ("GET", "/dialogs"),
    ("GET", "/dialogs/{dialog_id}"),
    ("POST", "/dialogs/{dialog_id}/summary"),
    ("POST", "/dialogs/{dialog_id}/responses"),
}


def test_postman_collection_is_v21_and_covers_all_api_operations() -> None:
    collection = json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))

    assert collection["info"]["schema"].endswith("/v2.1.0/collection.json")
    assert {variable["key"] for variable in collection["variable"]} >= {
        "base_url",
        "dialog_id",
        "cursor",
    }

    operations = {
        (item["request"]["method"], _postman_path(item["request"]["url"]))
        for item in collection["item"]
    }
    assert operations == EXPECTED_OPERATIONS
    assert {item["name"] for item in collection["item"]} == {
        "List dialogs",
        "Get dialog",
        "Get cached summary",
        "Refresh summary",
        "Generate dialog responses",
    }

    list_item = next(item for item in collection["item"] if item["name"] == "List dialogs")
    query = {parameter["key"]: parameter for parameter in list_item["request"]["url"]["query"]}
    assert query["limit"]["value"] == "20"
    assert query["cursor"]["disabled"] is True

    summary_item = next(item for item in collection["item"] if item["name"] == "Refresh summary")
    assert summary_item["request"]["url"]["query"] == [{"key": "refresh", "value": "true"}]

    openapi_operations = {
        (method.upper(), path)
        for path, path_item in create_app().openapi()["paths"].items()
        for method in path_item
    }
    assert openapi_operations >= EXPECTED_OPERATIONS


def _postman_path(url: dict[str, object]) -> str:
    raw_path = url.get("path")
    if not isinstance(raw_path, list) or not all(isinstance(part, str) for part in raw_path):
        raise AssertionError("Postman URL path must be a list of strings")
    path = "/" + "/".join(cast(list[str], raw_path))
    return path.replace("{{dialog_id}}", "{dialog_id}")


def test_gitignore_excludes_local_credentials_and_dataset() -> None:
    gitignore = (Path(__file__).parents[2] / ".gitignore").read_text(encoding="utf-8")

    assert "local/.env" in gitignore
    assert "mised/*" in gitignore
