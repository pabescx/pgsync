#!/usr/bin/env python3
"""Finalize a `reindex` Job: publish aliases, then collect the garbage.

Runs once, after parallel_sync exits (see entrypoint.sh's `reindex` role).
Replaces the old alias_watcher.py, which ran as a background poller inside
the producer -- that polling loop is not needed here because the reindex
Job already blocked until parallel_sync returned. What still is needed:

  1. verify the checkpoint. PARALLEL_SYNC_MODE=multiprocess swallows worker
     exceptions and writes the checkpoint anyway (pgsync's bin/parallel_sync),
     so a 0 exit code alone is not proof the backfill finished. Only
     `synchronous` fails loudly. The checkpoint in Redis -- written by pgsync
     at the END of pull() -- is the actual signal, and every target is
     checked before anything is published.
  2. publish the alias, atomically removing it from any other
     `<alias>_<timestamp>` index -- never from unrelated indexes.
  3. delete the superseded indexes and drop their replication slots. pgsync
     creates a slot per index and never drops it, so a timestamped index name
     leaks one slot per reindex if nobody does this.

Ordering matters: the alias moves BEFORE anything is deleted, so a failed
build never leaves traffic pointing at nothing. A slot still held by a
lingering pod raises ObjectInUse; that's retried a bounded number of times
and, if still stuck, left for the next reindex to pick up rather than
failing the whole Job over cleanup.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import psycopg2
import redis

STAMP = re.compile(r"^(?P<alias>.+)_(?P<stamp>\d{17})$")
CLEANUP_RETRIES = int(os.environ.get("FINALIZE_CLEANUP_RETRIES", "5"))
CLEANUP_RETRY_INTERVAL = float(os.environ.get("FINALIZE_CLEANUP_RETRY_INTERVAL", "5"))


def log(message):
    print(f"### finalize-reindex: {message}", file=sys.stderr, flush=True)


def es(path, method="GET", body=None):
    base = os.environ["ELASTICSEARCH_URL"].rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{base}/{path.lstrip('/')}",
        method=method,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            return response.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            body = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            body = {"raw": payload.decode(errors="replace")}
        return error.code, body


def index_exists(index):
    status, _ = es(index, method="HEAD")
    return status == 200


def sanitize(database, index):
    """Sync.__name -- the slot, queue and checkpoint key (sync.py:100)."""
    return re.sub("[^0-9a-zA-Z_]+", "", f"{database.lower()}_{index}")


def backfill_done(client, database, index):
    key = f"queue:{sanitize(database, index)}:meta"
    return client.hget(key, "checkpoint") is not None


def promote(alias, index):
    """Move the alias onto `index`, removing it only from `<alias>_*`."""
    actions = [{"remove": {"index": f"{alias}_*", "alias": alias}}]
    status, response = es(
        "_aliases", "POST", {"actions": actions + [{"add": {"index": index, "alias": alias}}]}
    )
    if status != 200:
        # `remove` fails when the alias does not exist yet -- first build.
        status, response = es("_aliases", "POST", {"actions": [{"add": {"index": index, "alias": alias}}]})
    return status == 200, response


def stale_indices(alias, keep):
    status, payload = es(f"_cat/indices/{alias}_*?format=json&h=index")
    if status != 200:
        return []
    names = []
    for row in payload:
        name = row.get("index", "")
        match = STAMP.match(name)
        if match and match.group("alias") == alias and name != keep:
            names.append(name)
    return names


def stale_slots(cursor, database, alias, keep):
    """Slots this alias superseded, and nothing else.

    The prefix alone is not a safe filter: it would also match a slot someone
    created by hand, e.g. `readygop_brand_index_dev_manual_backup`. The suffix
    must be a 17-digit timestamp, the same rule stale_indices() applies.
    """
    prefix = sanitize(database, alias)
    cursor.execute(
        """
        SELECT slot_name FROM pg_replication_slots
        WHERE slot_name LIKE %s AND slot_name <> %s
        """,
        (f"{prefix}\\_%", sanitize(database, keep)),
    )
    return [
        row[0]
        for row in cursor.fetchall()
        if re.fullmatch(rf"{re.escape(prefix)}_\d{{17}}", row[0])
    ]

def collect(connection, database, alias, keep):
    """Best-effort cleanup, retried a bounded number of times.

    A slot still held by a lingering pod is expected during a rollout, not a
    failure -- if it is still stuck after the retries, it is left alone and
    the next reindex's finalize picks it up.
    """
    for attempt in range(1, CLEANUP_RETRIES + 1):
        for name in stale_indices(alias, keep):
            status, _ = es(name, "DELETE")
            log(f"index {name} deleted (http {status})")

        leftover = False
        with connection.cursor() as cursor:
            for slot in stale_slots(cursor, database, alias, keep):
                try:
                    cursor.execute("SELECT pg_drop_replication_slot(%s)", (slot,))
                    connection.commit()
                    log(f"slot {slot} dropped")
                except psycopg2.errors.ObjectInUse:
                    connection.rollback()
                    leftover = True
                except psycopg2.Error as error:
                    connection.rollback()
                    log(f"slot {slot} not dropped: {error}")

        if not leftover:
            return
        if attempt < CLEANUP_RETRIES:
            log(f"stale slot(s) still in use, retrying ({attempt}/{CLEANUP_RETRIES})")
            time.sleep(CLEANUP_RETRY_INTERVAL)

    log(f"{alias}: stale slot(s) still in use after {CLEANUP_RETRIES} attempts, leaving for next reindex")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()

    with open(args.schema) as file:
        docs = json.load(file)

    targets = []
    for doc in docs:
        index = doc["index"]
        match = STAMP.match(index)
        if not match:
            log(f"index has no timestamp, refusing to finalize: {index}")
            return 1
        targets.append((doc["database"], match.group("alias"), index))

    if not targets:
        log("no indexes found in schema")
        return 1

    for _, _, index in targets:
        if not index_exists(index):
            log(f"index does not exist: {index}")
            return 1

    redis_client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
    )
    for database, alias, index in targets:
        if not backfill_done(redis_client, database, index):
            log(f"no checkpoint for {index} yet -- backfill did not finish, refusing to promote")
            return 1

    for database, alias, index in targets:
        log(f"promoting {alias} -> {index}")
        ok, response = promote(alias, index)
        if not ok:
            log(f"alias update failed for {alias}: {response}")
            return 1
        log(f"{alias} -> {index}")

    connection = psycopg2.connect(os.environ["PG_URL"])
    for database, alias, index in targets:
        collect(connection, database, alias, index)

    log("finalize complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
