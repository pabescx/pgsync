# test_filter_schema.py
import json
from pathlib import Path
import pytest

from scripts.filter_schema import (
    filter_documents,
    logical_index_name,
    parse_targets,
)

ROOT = Path(__file__).resolve().parents[2]

REAL_SCHEMA = (
    ROOT
    / "schemas"
    / "readygop"
    / "schema.json"
)

@pytest.fixture
def schema(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")

    return [
        {
            "database": "${DB_NAME}",
            "index": "brand_index_${APP_ENV}",
        },
        {
            "database": "${DB_NAME}",
            "index": "campaign_index_${APP_ENV}",
        },
        {
            "database": "${DB_NAME}",
            "index": "phone_index_${APP_ENV}",
        },
        {
            "database": "${DB_NAME}",
            "index": "project_index_${APP_ENV}",
        },
    ]

def logical_names(docs):
    return [
        logical_index_name(doc["index"])
        for doc in docs
    ]

def test_single_target(schema):
    targets = parse_targets("brand_index")

    result = filter_documents(
        schema,
        targets,
    )

    assert logical_names(result) == [
        "brand_index",
    ]

def test_multiple_targets(schema):
    targets = parse_targets(
        "brand_index,project_index"
    )

    result = filter_documents(
        schema,
        targets,
    )

    assert logical_names(result) == [
        "brand_index",
        "project_index",
    ]

def test_multiple_targets_accept_spaces(schema):
    targets = parse_targets(
        "brand_index, project_index"
    )

    result = filter_documents(
        schema,
        targets,
    )

    assert logical_names(result) == [
        "brand_index",
        "project_index",
    ]

def test_all_selects_every_index(schema):
    targets = parse_targets("all")

    result = filter_documents(
        schema,
        targets,
    )

    assert result == schema

    assert logical_names(result) == [
        "brand_index",
        "campaign_index",
        "phone_index",
        "project_index",
    ]

def test_phone_index_is_not_selected_implicitly(
    schema,
):
    targets = parse_targets(
        "brand_index,project_index"
    )

    result = filter_documents(
        schema,
        targets,
    )

    assert "phone_index" not in logical_names(result)

def test_phone_index_can_be_selected_explicitly(
    schema,
):
    targets = parse_targets(
        "phone_index"
    )

    result = filter_documents(
        schema,
        targets,
    )

    assert logical_names(result) == [
        "phone_index",
    ]

def test_unknown_target_fails(schema):
    targets = parse_targets(
        "fake_index"
    )

    with pytest.raises(
        ValueError,
        match="unknown REINDEX_TARGETS",
    ):
        filter_documents(
            schema,
            targets,
        )

def test_empty_targets_fail():
    with pytest.raises(
        ValueError,
        match="REINDEX_TARGETS cannot be empty",
    ):
        parse_targets("")

def test_whitespace_only_targets_fail():
    with pytest.raises(
        ValueError,
        match="REINDEX_TARGETS cannot be empty",
    ):
        parse_targets("   ")

def test_all_cannot_be_combined_with_index():
    with pytest.raises(
        ValueError,
        match="'all' cannot be combined",
    ):
        parse_targets(
            "all,brand_index"
        )

def test_duplicate_requested_targets_are_safe(
    schema,
):
    targets = parse_targets(
        "brand_index,brand_index"
    )

    result = filter_documents(
        schema,
        targets,
    )

    assert logical_names(result) == [
        "brand_index",
    ]

def test_schema_order_is_preserved(schema):
    targets = parse_targets(
        "project_index,brand_index"
    )

    result = filter_documents(
        schema,
        targets,
    )

    assert logical_names(result) == [
        "brand_index",
        "project_index",
    ]

def test_schema_root_must_be_array():
    targets = parse_targets(
        "brand_index"
    )

    with pytest.raises(
        ValueError,
        match="schema root must be a JSON array",
    ):
        filter_documents(
            {
                "index": "brand_index",
            },
            targets,
        )

def test_schema_entry_must_be_object():
    targets = parse_targets(
        "brand_index"
    )

    with pytest.raises(
        ValueError,
        match="schema entries must be JSON objects",
    ):
        filter_documents(
            [
                "brand_index",
            ],
            targets,
        )

def test_schema_entry_requires_index():
    targets = parse_targets(
        "brand_index"
    )

    with pytest.raises(
        ValueError,
        match="schema entry is missing 'index'",
    ):
        filter_documents(
            [
                {
                    "database": "${DB_NAME}",
                }
            ],
            targets,
        )

def test_duplicate_logical_indexes_fail(
    monkeypatch,
):
    monkeypatch.setenv(
        "APP_ENV",
        "staging",
    )

    docs = [
        {
            "index": "brand_index_${APP_ENV}",
        },
        {
            "index": "brand_index_${APP_ENV}",
        },
    ]

    targets = parse_targets(
        "brand_index"
    )

    with pytest.raises(
        ValueError,
        match="duplicate logical index",
    ):
        filter_documents(
            docs,
            targets,
        )

def test_logical_index_name_expands_app_env(
    monkeypatch,
):
    monkeypatch.setenv(
        "APP_ENV",
        "production",
    )

    result = logical_index_name(
        "brand_index_${APP_ENV}"
    )

    assert result == "brand_index"

def test_missing_app_env_fails(monkeypatch):
    monkeypatch.delenv(
        "APP_ENV",
        raising=False,
    )

    with pytest.raises(
        SystemExit,
        match="APP_ENV",
    ):
        logical_index_name(
            "brand_index_${APP_ENV}"
        )

def test_real_readygop_schema_indexes(
    monkeypatch,
):
    monkeypatch.setenv(
        "APP_ENV",
        "test",
    )

    with REAL_SCHEMA.open(
        encoding="utf-8"
    ) as handle:
        docs = json.load(handle)

    indexes = {
        logical_index_name(doc["index"])
        for doc in docs
    }

    assert indexes == {
        "brand_index",
        "campaign_index",
        "client_index",
        "list_index",
        "phone_index",
        "project_index",
        "team_index",
        "user_index",
        "user_view_index",
    }

def test_real_schema_selective_reindex(
    monkeypatch,
):
    monkeypatch.setenv(
        "APP_ENV",
        "test",
    )

    with REAL_SCHEMA.open(
        encoding="utf-8"
    ) as handle:
        docs = json.load(handle)

    targets = parse_targets(
        "brand_index,project_index"
    )

    selected = filter_documents(
        docs,
        targets,
    )

    selected_names = [
        logical_index_name(doc["index"])
        for doc in selected
    ]

    assert selected_names == [
        "brand_index",
        "project_index",
    ]

    assert "phone_index" not in selected_names
