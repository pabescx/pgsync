#!/usr/bin/env python3
"""Resolve every `index` in schema.json to a concrete, timestamped index name.

schema.json declares the LOGICAL index, which is also the alias name:

    "index": "brand_index_dev"

This writes schema.resolved.json with the PHYSICAL index pgsync should read and
write, e.g. `brand_index_dev_20260805121013600`, chosen from the state of
Elasticsearch itself:

    alias exists            -> the index it points to      (steady state, no reindex)
    bootstrap, no alias     -> resume a build with no checkpoint, else mint a new name
    consumer, no alias      -> wait: the alias is the readiness gate

Elasticsearch is the only source of truth, so producer and consumers converge on
the same name with no shared state.

Deleting an alias by hand is therefore what triggers a full reindex on the next
restart.

Uses urllib rather than elasticsearch-py: the image ships the 8.x client and the
cluster is 7.17, and the 8.x client refuses mismatched server versions.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import redis

from datetime import datetime, timezone

# has_helpers uses strftime('%Y%m%d%H%M%S%L') -- 17 digits, milliseconds included
STAMP = re.compile(r"^(?P<alias>.+)_(?P<stamp>\d{17})$")

def _request(base, path, method="GET"):
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as error:
        return error.code, {}

VARIABLE = re.compile(r"\$\{(\w+)\}")

def expand(value):
    """Substitute ${VAR} from the environment.

    This is what makes the environment part of the name, e.g.

        "index": "client_index_${APP_ENV}"  ->  client_index_production

    An unset variable is a hard error rather than the empty string envsubst
    would silently produce -- `client_index_` would be a valid but wrong index
    name, and the same string names the replication slot and checkpoint.
    (envsubst is not in the image anyway.)
    """

    def replace(match):
        name = match.group(1)
        try:
            return os.environ[name]
        except KeyError:
            raise SystemExit(f"{match.group(0)} used in schema.json but not set")

    return VARIABLE.sub(replace, value)

def timestamp():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"

def aliased_index(base, alias):
    """The concrete index an alias points to, or None."""
    status, payload = _request(base, f"_alias/{alias}")
    if status != 200:
        return None
    # {"brand_index_dev_2026...": {"aliases": {"brand_index_dev": {}}}}
    names = sorted(payload)
    return names[-1] if names else None

def newest_candidate(base, alias, live_index=None):
    """Newest `<alias>_<17 digits>` index, or None."""
    status, payload = _request(base, f"_cat/indices/{alias}_*?format=json&h=index")

    if status != 200:
        return None

    candidates = []
    for row in payload:
        name = row.get("index", "")
        match = STAMP.match(name)

        if not match:
            continue

        if match.group("alias") != alias:
            continue

        # Never consider the currently published index as a reindex candidate.
        if name == live_index:
            continue

        candidates.append(name)

    return max(candidates) if candidates else None

def has_checkpoint(database, index):
    """True once pgsync finished pull() for this index (sync.py:1541)."""
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
    )
    name = re.sub("[^0-9a-zA-Z_]+", "", f"{database.lower()}_{index}")
    return client.hget(f"queue:{name}:meta", "checkpoint") is not None

def resolve(base, database, alias, role, stamp):
    """Resolve an alias to the physical index required by the current role.

    reindex:
        - Resume the newest unpublished incomplete generation, if one exists.
        - Otherwise create a new timestamped generation.

    bootstrap / producer / consumer / teardown:
        - Use only the physical index currently published through the alias.
        - Never create a new physical index.
    """

    live_index = aliased_index(base, alias)

    # Reindex is the only role allowed to create or resume
    # an unpublished physical index generation.
    if role == "reindex":
        candidate = newest_candidate(
            base,
            alias,
            live_index=live_index,
        )

        # Resume an interrupted reindex instead of creating
        # another timestamped generation.
        if candidate and not has_checkpoint(database, candidate):
            return candidate, "resume"

        # No unfinished generation exists: create a new one.
        return f"{alias}_{stamp}", "new"

    # All other roles must operate against the generation
    # currently published through the Elasticsearch alias.
    if live_index:
        return live_index, "alias"

    raise SystemExit(
        f"no published alias for '{alias}' yet; run reindex first"
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--schema", default="/app/schema.json")
    parser.add_argument("--out", default="/tmp/schema.resolved.json")
    parser.add_argument(
        "--wait",
        action="store_true",
        help=(
            "Block until every alias exists. For the consumer: there is no "
            "depends_on in Kubernetes, and the alias only appears once the "
            "backfill finished, so it doubles as the readiness gate."
        ),
    )
    parser.add_argument("--wait-interval", type=float, default=5.0)
    args = parser.parse_args()

    base = os.environ["ELASTICSEARCH_URL"]
    docs = json.loads(open(args.schema).read())

    for doc in docs:
        for key in ("index", "database"):
            doc[key] = expand(doc[key])
        if STAMP.match(doc["index"]):
            raise SystemExit(
                f"'{doc['index']}' already carries a timestamp. schema.json must "
                "hold the alias name only."
            )

    while True:
        # One timestamp per attempt keeps a freshly built set coherent.
        stamp = timestamp()
        resolved, pending = [], []

        for doc in docs:
            alias = doc["index"]
            try:
                concrete, how = resolve(
                    base, doc["database"], alias, args.role, stamp
                )
            except SystemExit:
                if not args.wait:
                    raise
                pending.append(alias)
                continue

            resolved.append((doc, alias, concrete, how))

        if not pending:
            for doc, alias, concrete, how in resolved:
                doc["index"] = concrete
                # No extra keys: pgsync validates the document shape, and the
                # alias is recoverable by stripping the timestamp (see STAMP).
                print(f"###   {alias:28} -> {concrete}  ({how})", file=sys.stderr)
            break

        print(f"### waiting on: {', '.join(pending)}", file=sys.stderr, flush=True)
        time.sleep(args.wait_interval)

    with open(args.out, "w") as handle:
        json.dump(docs, handle, indent=2)

if __name__ == "__main__":
    main()
