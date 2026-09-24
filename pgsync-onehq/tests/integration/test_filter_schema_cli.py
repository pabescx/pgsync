# test_filter_schema_cli.py

import json
import os
import subprocess
import sys
import pytest
from pathlib import Path
from scripts import filter_schema

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = (
    ROOT
    / "scripts"
    / "filter_schema.py"
)

def create_schema(path):
    docs = [
        {
            "database": "${DB_NAME}",
            "index": "brand_index_${APP_ENV}",
        },
        {
            "database": "${DB_NAME}",
            "index": "phone_index_${APP_ENV}",
        },
    ]

    path.write_text(
        json.dumps(docs),
        encoding="utf-8",
    )

def run_filter(
    schema,
    output,
    targets,
):
    env = os.environ.copy()
    env["APP_ENV"] = "staging"

    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--schema",
            str(schema),
            "--targets",
            targets,
            "--out",
            str(output),
        ],
        env=env,
        text=True,
        capture_output=True,
    )

def test_cli_single_target(tmp_path):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    create_schema(schema)

    result = run_filter(
        schema,
        output,
        "brand_index",
    )

    assert result.returncode == 0

    docs = json.loads(
        output.read_text(
            encoding="utf-8"
        )
    )

    assert len(docs) == 1

    assert docs[0]["index"] == (
        "brand_index_${APP_ENV}"
    )

def test_cli_invalid_target_returns_nonzero(
    tmp_path,
):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    create_schema(schema)

    result = run_filter(
        schema,
        output,
        "fake_index",
    )

    assert result.returncode != 0

    assert (
        "unknown REINDEX_TARGETS"
        in result.stderr
    )

    assert not output.exists()

def test_cli_all_plus_index_returns_nonzero(
    tmp_path,
):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    create_schema(schema)

    result = run_filter(
        schema,
        output,
        "all,brand_index",
    )

    assert result.returncode != 0

    assert (
        "'all' cannot be combined"
        in result.stderr
    )

def test_cli_empty_target_returns_nonzero(
    tmp_path,
):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    create_schema(schema)

    result = run_filter(
        schema,
        output,
        "",
    )

    assert result.returncode != 0

    assert (
        "REINDEX_TARGETS cannot be empty"
        in result.stderr
    )

def test_cli_preserves_schema_placeholder(
    tmp_path,
):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    create_schema(schema)

    result = run_filter(
        schema,
        output,
        "brand_index",
    )

    assert result.returncode == 0

    docs = json.loads(
        output.read_text(
            encoding="utf-8"
        )
    )

    assert docs[0]["index"] == (
        "brand_index_${APP_ENV}"
    )

def test_main_single_target(
    tmp_path,
    monkeypatch,
    capsys,
):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    create_schema(schema)

    monkeypatch.setenv(
        "APP_ENV",
        "staging",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "filter_schema.py",
            "--schema",
            str(schema),
            "--targets",
            "brand_index",
            "--out",
            str(output),
        ],
    )

    filter_schema.main()

    assert output.exists()

    docs = json.loads(
        output.read_text(
            encoding="utf-8"
        )
    )

    assert len(docs) == 1

    assert docs[0]["index"] == (
        "brand_index_${APP_ENV}"
    )

    captured = capsys.readouterr()

    assert (
        "### selected reindex targets: brand_index"
        in captured.err
    )

def test_main_invalid_target_exits(
    tmp_path,
    monkeypatch,
):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    create_schema(schema)

    monkeypatch.setenv(
        "APP_ENV",
        "staging",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "filter_schema.py",
            "--schema",
            str(schema),
            "--targets",
            "fake_index",
            "--out",
            str(output),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        filter_schema.main()

    assert (
        "unknown REINDEX_TARGETS"
        in str(exc.value)
    )

    assert not output.exists()

def test_main_empty_schema_fails(
    tmp_path,
    monkeypatch,
):
    schema = tmp_path / "schema.json"
    output = tmp_path / "filtered.json"

    schema.write_text(
        "[]",
        encoding="utf-8",
    )

    monkeypatch.setenv(
        "APP_ENV",
        "staging",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "filter_schema.py",
            "--schema",
            str(schema),
            "--targets",
            "all",
            "--out",
            str(output),
        ],
    )

    with pytest.raises(
        SystemExit,
        match="no indexes selected for reindex",
    ):
        filter_schema.main()

    assert not output.exists()

