import json
import os
import stat
import subprocess
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "entrypoint.sh"
REAL_SCHEMA = ROOT / "schemas" / "readygop" / "schema.json"
REAL_FILTER = ROOT / "scripts" / "filter_schema.py"

def make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)

@pytest.fixture
def entrypoint_env(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    trace_file = tmp_path / "trace.log"
    reindex_schema = tmp_path / "schema.reindex.json"
    resolved_schema = tmp_path / "schema.resolved.json"

    #
    # Fake python3
    #
    # filter_schema.py:
    #   Execute the REAL implementation from the repository.
    #
    # resolve_schema.py:
    #   Do not connect to Elasticsearch. Record which schema was received
    #   and copy it to RESOLVED_SCHEMA.
    #
    # finalize_reindex.py:
    #   Record that finalize would have been executed.
    #
    make_executable(
        fake_bin / "python3",
        """#!/bin/sh
set -eu

script="$1"
shift

case "${script}" in
  /app/scripts/filter_schema.py)
    exec "${REAL_PYTHON}" "${REAL_FILTER}" "$@"
    ;;

  /app/scripts/resolve_schema.py)
    role=""
    schema=""
    out=""

    while [ "$#" -gt 0 ]; do
      case "$1" in
        --role)
          role="$2"
          shift 2
          ;;
        --schema)
          schema="$2"
          shift 2
          ;;
        --out)
          out="$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done

    printf 'resolve:%s:%s\\n' "${role}" "${schema}" >> "${TRACE_FILE}"
    cp "${schema}" "${out}"
    ;;

  /app/scripts/finalize_reindex.py)
    printf 'finalize\\n' >> "${TRACE_FILE}"
    exit 0
    ;;

  *)
    exec "${REAL_PYTHON}" "${script}" "$@"
    ;;
esac
""",
    )

    # Fake PGSync bootstrap.
    #
    make_executable(
        fake_bin / "bootstrap",
        """#!/bin/sh
set -eu
printf 'bootstrap:%s\\n' "$*" >> "${TRACE_FILE}"
exit 0
""",
    )

    # Fake parallel_sync.
    #
    make_executable(
        fake_bin / "parallel_sync",
        """#!/bin/sh
set -eu
printf 'parallel_sync:%s\\n' "$*" >> "${TRACE_FILE}"
exit 0
""",
    )

    # Fake PGSync runtime used by producer and consumer.
    #
    make_executable(
        fake_bin / "pgsync",
        """#!/bin/sh
set -eu
printf 'pgsync:%s\\n' "$*" >> "${TRACE_FILE}"
exit 0
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "REAL_PYTHON": sys.executable,
            "REAL_FILTER": str(REAL_FILTER),
            "TRACE_FILE": str(trace_file),
            "PGSYNC_ROLE": "reindex",
            "APP_ENV": "staging",
            "DB_NAME": "readygopdb",
            "PG_URL": "postgresql://fake",
            "ELASTICSEARCH_URL": "http://fake-elasticsearch",
            "REDIS_HOST": "fake-redis",
            "SCHEMA_PATH": str(REAL_SCHEMA),
            "REINDEX_SCHEMA": str(reindex_schema),
            "RESOLVED_SCHEMA": str(resolved_schema),
            "PARALLEL_SYNC_MODE": "multiprocess",
            "PARALLEL_SYNC_PROCESSES": "2",
        }
    )

    return {
        "env": env,
        "trace": trace_file,
        "reindex_schema": reindex_schema,
        "resolved_schema": resolved_schema,
    }

def run_entrypoint(config):
    return subprocess.run(
        ["/bin/sh", str(ENTRYPOINT)],
        env=config["env"],
        text=True,
        capture_output=True,
        check=False,
    )

def read_trace(config):
    trace = config["trace"]

    if not trace.exists():
        return ""

    return trace.read_text(encoding="utf-8")

def read_reindex_schema(config):
    with config["reindex_schema"].open(encoding="utf-8") as handle:
        return json.load(handle)

def test_reindex_invalid_target_fails_before_bootstrap(entrypoint_env):
    config = entrypoint_env
    config["env"]["REINDEX_TARGETS"] = "fake_index"

    result = run_entrypoint(config)

    assert result.returncode != 0

    trace = read_trace(config)

    assert "bootstrap:" not in trace
    assert "parallel_sync:" not in trace
    assert "finalize" not in trace

    assert "failed to prepare reindex schema" in result.stderr

    assert not config["reindex_schema"].exists()

def test_reindex_empty_target_fails_before_bootstrap(entrypoint_env):
    config = entrypoint_env
    config["env"]["REINDEX_TARGETS"] = ""

    result = run_entrypoint(config)

    assert result.returncode != 0

    trace = read_trace(config)

    assert "bootstrap:" not in trace
    assert "parallel_sync:" not in trace
    assert "finalize" not in trace

    assert not config["reindex_schema"].exists()

def test_reindex_single_target_uses_filtered_schema(entrypoint_env):
    config = entrypoint_env
    config["env"]["REINDEX_TARGETS"] = "brand_index"

    result = run_entrypoint(config)

    assert result.returncode == 0, result.stderr

    docs = read_reindex_schema(config)

    assert len(docs) == 1
    assert docs[0]["index"] == "brand_index_${APP_ENV}"

    indexes = [doc["index"] for doc in docs]

    assert "phone_index_${APP_ENV}" not in indexes

    trace = read_trace(config)

    assert f"resolve:reindex:{config['reindex_schema']}" in trace
    assert "bootstrap:" in trace
    assert "parallel_sync:" in trace
    assert "finalize" in trace

def test_reindex_multiple_targets_use_filtered_schema(entrypoint_env):
    config = entrypoint_env
    config["env"]["REINDEX_TARGETS"] = "brand_index,project_index"

    result = run_entrypoint(config)

    assert result.returncode == 0, result.stderr

    docs = read_reindex_schema(config)

    indexes = [doc["index"] for doc in docs]

    assert indexes == [
        "brand_index_${APP_ENV}",
        "project_index_${APP_ENV}",
    ]

    assert "phone_index_${APP_ENV}" not in indexes

    trace = read_trace(config)

    assert f"resolve:reindex:{config['reindex_schema']}" in trace
    assert "bootstrap:" in trace
    assert "parallel_sync:" in trace
    assert "finalize" in trace

def test_reindex_unset_targets_defaults_to_all(entrypoint_env):
    config = entrypoint_env

    config["env"].pop("REINDEX_TARGETS", None)

    result = run_entrypoint(config)

    assert result.returncode == 0, result.stderr

    docs = read_reindex_schema(config)

    indexes = [doc["index"] for doc in docs]

    assert indexes == [
        "brand_index_${APP_ENV}",
        "campaign_index_${APP_ENV}",
        "client_index_${APP_ENV}",
        "list_index_${APP_ENV}",
        "phone_index_${APP_ENV}",
        "team_index_${APP_ENV}",
        "user_index_${APP_ENV}",
        "project_index_${APP_ENV}",
        "user_view_index_${APP_ENV}",
    ]

    trace = read_trace(config)

    assert f"resolve:reindex:{config['reindex_schema']}" in trace
    assert "bootstrap:" in trace
    assert "parallel_sync:" in trace
    assert "finalize" in trace

def test_producer_uses_full_source_schema(entrypoint_env):
    config = entrypoint_env

    config["env"]["PGSYNC_ROLE"] = "producer"

    # Deliberately set a selective target.
    # Producer must completely ignore it.
    config["env"]["REINDEX_TARGETS"] = "brand_index"

    result = run_entrypoint(config)

    assert result.returncode == 0, result.stderr

    trace = read_trace(config)

    # Producer must resolve the complete source schema,
    # not the temporary selective reindex schema.
    assert f"resolve:producer:{REAL_SCHEMA}" in trace

    assert (
        f"resolve:producer:{config['reindex_schema']}"
        not in trace
    )

    # Selective filtering must never run for Producer.
    assert not config["reindex_schema"].exists()

    # Producer must eventually start PGSync.
    assert "pgsync:" in trace
    assert "--producer" in trace

def test_consumer_uses_full_source_schema(entrypoint_env):
    config = entrypoint_env

    config["env"]["PGSYNC_ROLE"] = "consumer"

    # Deliberately set a selective target.
    # Consumer must completely ignore it.
    config["env"]["REINDEX_TARGETS"] = "brand_index"

    result = run_entrypoint(config)

    assert result.returncode == 0, result.stderr

    trace = read_trace(config)

    # Consumer must resolve the complete source schema,
    # including the --wait path.
    assert f"resolve:consumer:{REAL_SCHEMA}" in trace

    assert (
        f"resolve:consumer:{config['reindex_schema']}"
        not in trace
    )

    # Selective filtering must never run for Consumer.
    assert not config["reindex_schema"].exists()

    # Consumer must eventually start PGSync.
    assert "pgsync:" in trace
    assert "--consumer" in trace