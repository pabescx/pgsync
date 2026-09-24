#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys

VARIABLE = re.compile(r"\$\{(\w+)\}")

def expand(value):
    """Expand ${VAR} using environment variables."""

    def replace(match):
        name = match.group(1)

        try:
            return os.environ[name]
        except KeyError:
            raise SystemExit(
                f"{match.group(0)} used in schema but {name} is not set"
            )

    return VARIABLE.sub(replace, value)

def logical_index_name(index):
    """
    Convert:

        brand_index_${APP_ENV}

    to:

        brand_index
    """

    resolved = expand(index)

    app_env = os.environ.get("APP_ENV")

    if app_env:
        suffix = f"_{app_env}"

        if resolved.endswith(suffix):
            return resolved[:-len(suffix)]

    return resolved

def parse_targets(value):
    """
    Parse and validate REINDEX_TARGETS.

    Supported examples:

        all
        brand_index
        brand_index,project_index
        brand_index, project_index

    Invalid examples:

        ""
        all,brand_index
    """

    requested = {
        target.strip()
        for target in value.split(",")
        if target.strip()
    }

    if not requested:
        raise ValueError("REINDEX_TARGETS cannot be empty")

    if "all" in requested and len(requested) != 1:
        raise ValueError(
            "'all' cannot be combined with specific indexes"
        )

    return requested

def filter_documents(docs, targets):
    """
    Return only the PGSync schema entries requested by REINDEX_TARGETS.

    The original schema ordering is preserved.
    """

    if not isinstance(docs, list):
        raise ValueError("schema root must be a JSON array")

    if targets == {"all"}:
        return list(docs)

    available = {}

    for doc in docs:
        if not isinstance(doc, dict):
            raise ValueError(
                "schema entries must be JSON objects"
            )

        if "index" not in doc:
            raise ValueError(
                "schema entry is missing 'index'"
            )

        logical = logical_index_name(doc["index"])

        if logical in available:
            raise ValueError(
                f"duplicate logical index in schema: {logical}"
            )

        available[logical] = doc

    missing = targets - set(available)

    if missing:
        raise ValueError(
            "unknown REINDEX_TARGETS: "
            + ", ".join(sorted(missing))
            + ". Available targets: "
            + ", ".join(sorted(available))
        )

    # Preserve original schema ordering.
    return [
        doc
        for doc in docs
        if logical_index_name(doc["index"]) in targets
    ]

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--schema",
        required=True,
        help="Original full PGSync schema",
    )

    parser.add_argument(
        "--targets",
        required=True,
        help="all or comma-separated logical index names",
    )

    parser.add_argument(
        "--out",
        required=True,
        help="Filtered schema output",
    )

    args = parser.parse_args()

    with open(args.schema, encoding="utf-8") as handle:
        docs = json.load(handle)

    try:
        requested = parse_targets(args.targets)
        selected = filter_documents(
            docs,
            requested,
        )
    except ValueError as error:
        raise SystemExit(str(error))

    if not selected:
        raise SystemExit(
            "no indexes selected for reindex"
        )

    with open(
        args.out,
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            selected,
            handle,
            indent=2,
        )

    selected_names = [
        logical_index_name(doc["index"])
        for doc in selected
    ]

    print(
        "### selected reindex targets: "
        + ", ".join(selected_names),
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
