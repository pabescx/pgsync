# PGSync OKD Preview PoC Technical Analysis

This document captures a read-only technical analysis of the `pgsync-onehq`
repository for evaluating PGSync and designing a proof of concept for an OKD
preview environment.

Scope reviewed:

- `README.md`
- `docker-compose.yml`
- `entrypoint.sh`
- `k8s-resources.yaml`
- `plugins/readygop/*.py`
- `schemas/readygop/schema.json`
- `scripts/alias_watcher.py`
- `scripts/resolve_schema.py`
- hidden/config files present in the repository
- expected files such as `Dockerfile`, `Containerfile`, `Makefile`,
  `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, and CI/GitOps files

No files were modified during the analysis phase. No deployments, bootstrap
operations, teardown operations, `oc`, `kubectl`, PostgreSQL, Redis, or
Elasticsearch operations were executed.

## Executive Finding

Confirmed PGSync flow in this repository:

```text
PostgreSQL/WAL -> PGSync producer -> Redis -> PGSync consumer -> Elasticsearch
```

That flow is implemented for local Docker Compose, but it is not yet represented
as PGSync workloads in `k8s-resources.yaml`. The OKD manifest includes Redis,
Elasticsearch, Postgres, Rails, search service, jobs, secrets, and ingresses, but
no PGSync producer/consumer Deployment, ConfigMap/Secret mounts for
`schema.json`, plugins, scripts, or `entrypoint.sh`.

The repository does not contain a `Dockerfile`, `Containerfile`, `Makefile`,
`pyproject.toml`, `requirements*.txt`, `AGENTS.md`, CI config, or GitOps
directory. The compose file uses `toluaina1/pgsync:latest`; a custom PGSync
image is not confirmed by the checked-in files.

## Secret Handling Note

`k8s-resources.yaml` contains Kubernetes Secret data and secret references. This
document does not reproduce secret values.

Risk locations:

- `k8s-resources.yaml:18-87`: S3, Docker registry, Elasticsearch secret data.
- `k8s-resources.yaml:89-151`: Rails/application secret data.
- `k8s-resources.yaml:175-210`: search, Vault, websocket secret data.
- `k8s-resources.yaml:1976-2085`: job decodes DB credentials and writes them to
  `/data/dbvars.sh`.
- `.env.example:8`: example PostgreSQL URL includes a default username/password
  pattern.

## Part 1: Repository Inventory

| File / Directory | Purpose | Consumed By | Inputs | Outputs | Dependencies | Potential Impact | Local Usage | Intended OKD Usage | Observations / Risks |
|---|---|---|---|---|---|---|---|---|---|
| `README.md` | Repo title only | Humans | None | None | None | Minimal | Documentation placeholder | None confirmed | No operational instructions |
| `docker-compose.yml` | Defines producer/consumer PGSync services | Docker Compose | `.env`, schema, scripts, plugins | Running producer/consumer containers | PGSync image, Redis, ES, Postgres | Core local runtime | Yes | Pattern for OKD design | Uses `latest`; external Docker network |
| `entrypoint.sh` | Role dispatcher | PGSync containers | `PGSYNC_ROLE`, env, mounted files | Starts bootstrap/watcher/producer or consumer | `bootstrap`, `pgsync`, Python scripts | Core runtime behavior | Yes | Should be container entrypoint | No signal trap for watcher |
| `schemas/readygop/schema.json` | PGSync schema and ES index definitions | `bootstrap`, `pgsync`, resolver | `DB_NAME`, `APP_ENV` | Resolved physical index config | PGSync schema format | Defines data model | Mounted read-only | Should be ConfigMap/image file | Large repeated ES settings |
| `plugins/readygop/*.py` | PGSync transform plugins | PGSync plugin loader | PGSync document dicts | Transformed ES documents | `pgsync.plugin` | Controls ES document shape | Mounted under `/app/plugins` | Should be in image or ConfigMap | Plugin names must match schema |
| `scripts/resolve_schema.py` | Resolves alias names to timestamped physical indices | `entrypoint.sh` | Schema, ES state, Redis checkpoints | `/tmp/schema.resolved.json` | Redis, ES HTTP | Determines index lifecycle | Yes | Needed in producer/consumer pods | No ES auth support in script |
| `scripts/alias_watcher.py` | Promotes aliases and cleans stale indices/slots | Producer container | Resolved schema, Redis checkpoint, DB URL | ES aliases, deleted stale indices, dropped slots | Redis, ES HTTP, psycopg2 | Important lifecycle owner | Background process | One producer only | Needs DB slot privileges |
| `.env.example` | Local env sample | Docker Compose/user | Env values | None | None | Developer setup | Yes | Reference only | Contains example credential pattern |
| `k8s-resources.yaml` | Consolidated OKD preview manifest | Argo/OKD, if applied | Secrets, images, PVCs, CRDs | Preview app infra | OKD, Crunchy PGO, ES, Redis | Preview environment baseline | No | Yes, but no PGSync workload | Untracked; contains secret material |
| `.git` | Version metadata | Git | Repo history | Status/logs | Git | Audit context | Yes | None | Current branch `main`; manifest untracked |

### Component Relationships

`schema.json` is the declarative description of what PGSync should read from
PostgreSQL and write into Elasticsearch. It declares logical index names using
environment placeholders such as `${APP_ENV}` and `${DB_NAME}`.

`resolve_schema.py` converts those logical index aliases into physical,
timestamped index names. It uses Elasticsearch aliases as the shared source of
truth and Redis checkpoints to distinguish an interrupted build from a fresh
reindex.

`entrypoint.sh` is the runtime controller. It chooses producer or consumer mode
from `PGSYNC_ROLE`. The producer resolves the schema, runs PGSync bootstrap,
starts the alias watcher, and then runs PGSync producer mode. The consumer waits
for aliases to exist before running PGSync consumer mode.

`alias_watcher.py` runs only with the producer. It waits for Redis checkpoints,
promotes Elasticsearch aliases, deletes old timestamped indices, and drops stale
PostgreSQL replication slots.

`plugins/readygop/*.py` transform PGSync documents into the shape expected by
the search application. Most plugins build a `result_type` JSON string that
mirrors Rails search index behavior described in the plugin comments.

`docker-compose.yml` wires those pieces together for local use. It mounts the
schema, scripts, plugins, and entrypoint into the PGSync image.

`k8s-resources.yaml` is a preview environment manifest, but it does not currently
include PGSync workloads.

## Part 2: Current Architecture

### Confirmed Flow

The repository confirms this intended flow:

```text
PostgreSQL/WAL -> PGSync producer -> Redis -> PGSync consumer -> Elasticsearch
```

Evidence:

- `docker-compose.yml:47-51` defines `pgsync-producer` with
  `PGSYNC_ROLE=producer`.
- `docker-compose.yml:54-59` defines `pgsync-consumer` with
  `PGSYNC_ROLE=consumer`.
- `entrypoint.sh:53` runs `pgsync --config "${RESOLVED}" --daemon --producer`.
- `entrypoint.sh:65` runs `pgsync --config "${RESOLVED}" --daemon --consumer`.
- `docker-compose.yml:34-38` configures Redis and `REDIS_CHECKPOINT=true`.
- `resolve_schema.py:109-116` and `alias_watcher.py:72-74` read PGSync
  checkpoint metadata from Redis.

### Mermaid Diagram

```mermaid
flowchart LR
  PG[(PostgreSQL / WAL)]
  ES[(Elasticsearch)]
  Redis[(Redis)]
  Schema[schema.json]
  Resolver[resolve_schema.py]
  Entrypoint[entrypoint.sh]
  Bootstrap[bootstrap --config]
  Producer[pgsync --daemon --producer]
  Consumer[pgsync --daemon --consumer]
  Watcher[alias_watcher.py]
  Plugins[plugins/readygop]

  Schema --> Resolver
  Entrypoint --> Resolver
  Resolver -->|/tmp/schema.resolved.json| Bootstrap
  Resolver --> Producer
  Resolver --> Consumer
  Bootstrap --> PG
  Bootstrap --> ES
  Producer --> PG
  Producer --> Redis
  Redis --> Consumer
  Consumer --> ES
  Plugins --> Producer
  Plugins --> Consumer
  Redis --> Watcher
  PG --> Watcher
  Watcher --> ES
```

### Exact Entrypoint Commands

Producer path from `entrypoint.sh:25-53`:

```sh
python3 /scripts/resolve_schema.py --role bootstrap --schema /app/schema.json --out /tmp/schema.resolved.json
bootstrap --config /tmp/schema.resolved.json
python3 /scripts/alias_watcher.py &
exec pgsync --config /tmp/schema.resolved.json --daemon --producer
```

Consumer path from `entrypoint.sh:56-65`:

```sh
python3 /scripts/resolve_schema.py --role consumer --schema /app/schema.json --out /tmp/schema.resolved.json --wait
exec pgsync --config /tmp/schema.resolved.json --daemon --consumer
```

### Role, Schema, and Database Selection

Facts confirmed by the repository:

- `PGSYNC_ROLE` selects `producer` or `consumer` in `entrypoint.sh:24-71`.
- Mounted schema path is fixed to `/app/schema.json` in `entrypoint.sh:13`.
- Local compose mounts `./schemas/${SCHEMA_NAME:-readygop}/schema.json` to
  `/app/schema.json` in `docker-compose.yml:23`.
- `schema.json` uses `${DB_NAME}` and `${APP_ENV}` placeholders for database and
  index selection.
- `resolve_schema.py` expands `${VAR}` from the environment and fails if unset
  at `resolve_schema.py:54-77`.

### Required and Optional Environment Variables

Required by runtime or schema:

| Variable | Default Confirmed | Used By | Purpose |
|---|---:|---|---|
| `PGSYNC_ROLE` | None | `entrypoint.sh` | Must be `producer` or `consumer` |
| `ELASTICSEARCH_URL` | Compose: `http://elasticsearch:9200` | resolver, watcher, PGSync | ES endpoint |
| `PG_URL` | Compose has local URL | watcher, PGSync/bootstrap | PostgreSQL connection |
| `APP_ENV` | Compose/env example: `dev` | schema resolver | Index alias suffix |
| `DB_NAME` | Compose/env example: `readygop` | schema resolver | PGSync database name |
| `REDIS_CHECKPOINT` | `true` in compose/env example | PGSync | Stores checkpoints in Redis |
| `PYTHONPATH` | `/app` | PGSync Python import path | Plugin discovery |
| `REDIS_HOST` | Scripts fallback `localhost`; compose `redis` | resolver/watcher | Redis host |
| `REDIS_PORT` | Scripts fallback `6379`; compose `6379` | resolver/watcher | Redis port |

Optional:

| Variable | Default | Used By | Purpose |
|---|---:|---|---|
| `NUM_WORKERS` | Compose consumer `2`; shell echo fallback `2` | PGSync env | Consumer workers per index |
| `LOG_INTERVAL` | `10` | PGSync | Status interval |
| `ALIAS_WATCHER_INTERVAL` | `5` | `alias_watcher.py` | Poll interval |
| `RESOLVED_SCHEMA` | `/tmp/schema.resolved.json` | `alias_watcher.py` | Resolved schema path |
| `PHONE_INVALID_STATUS_IDS` | Empty | phone plugin | Skip invalid phone docs |
| `SCHEMA_NAME` | Compose default `readygop` | Compose only | Select local schema directory |
| `PGSYNC_CONSUMERS` | `.env.example` only | Not consumed by compose | Intended scaling hint |

### Schema Resolution

Confirmed behavior from `scripts/resolve_schema.py`:

- Reads schema JSON from `--schema`, default `/app/schema.json`.
- Expands `${VAR}` placeholders in `database` and `index`.
- Rejects schema indices that already include a timestamp.
- Checks Elasticsearch for an existing alias.
- If an alias exists, resolves to the index pointed to by that alias.
- If role is `bootstrap` and no alias exists:
  - finds the newest `<alias>_<17 digits>` index candidate;
  - if that candidate exists and has no Redis checkpoint, resumes it;
  - otherwise creates a new timestamped index name.
- If role is `consumer` and no alias exists:
  - exits immediately unless `--wait` is set;
  - with `--wait`, loops until all aliases exist.
- Writes `/tmp/schema.resolved.json`.

### Bootstrap, Producer, and Consumer Behavior

Producer:

- Resolves a concrete timestamped schema.
- Runs `bootstrap --config /tmp/schema.resolved.json`.
- Treats bootstrap output containing `already exists` as benign.
- Starts `alias_watcher.py` in the background.
- Executes PGSync producer mode.

Consumer:

- Runs schema resolution with `--wait`.
- Waits for aliases, not just indices.
- Executes PGSync consumer mode.

Inference:

- The producer reads PostgreSQL WAL changes and writes to Redis.
- The consumer reads Redis and writes to Elasticsearch.
- This inference is based on PGSync command roles and the compose/script comments;
  the internal PGSync implementation is not part of this repository.

### Plugin Discovery and Loading

Facts:

- `docker-compose.yml:26` mounts `./plugins` to `/app/plugins`.
- `docker-compose.yml:39` sets `PYTHONPATH=/app`.
- Plugins subclass `pgsync.plugin.Plugin`.
- `schema.json` names plugins by their `name` attributes, for example
  `BrandResultType`.

Inference:

- PGSync discovers plugin classes via its plugin loader using the Python import
  path and the names declared in `schema.json`.

### Elasticsearch Indices and Aliases

Declared alias templates:

| Alias Template | Root Table | Plugin |
|---|---|---|
| `brand_index_${APP_ENV}` | `public.brands` | `BrandResultType` |
| `campaign_index_${APP_ENV}` | `public.campaigns` | `CampaignResultType` |
| `client_index_${APP_ENV}` | `public.clients` | `ClientResultType` |
| `list_index_${APP_ENV}` | `public.lists` | `ListResultType` |
| `team_index_${APP_ENV}` | `public.teams` | `TeamResultType` |
| `user_index_${APP_ENV}` | `public.users` | `UserResultType` |
| `project_index_${APP_ENV}` | `public.projects` | `ProjectResultType` |
| `user_view_index_${APP_ENV}` | `public.user_views` | `UserView` |

Physical index format:

```text
<alias>_<17-digit UTC timestamp>
```

Example:

```text
brand_index_dev_20260805121013600
```

### Alias Watcher

Confirmed behavior from `scripts/alias_watcher.py`:

- Waits for `/tmp/schema.resolved.json` by default.
- Reads timestamped index names from the resolved schema.
- Connects to Redis using `REDIS_HOST` and `REDIS_PORT`.
- Connects to PostgreSQL using `PG_URL`.
- For each target index:
  - waits for Redis checkpoint field `checkpoint`;
  - promotes alias to the new index through Elasticsearch `_aliases`;
  - deletes older timestamped indices;
  - drops stale replication slots.
- Keeps retrying stale slot cleanup if a slot is still in use.

Checkpoint key format:

```text
queue:<sanitized database + "_" + index>:meta checkpoint
```

### Restart and Failure Behavior

Facts:

- Producer/bootstrap can resume a newest timestamped candidate if it has no
  checkpoint.
- Consumer waits for aliases and therefore avoids consuming from an incomplete
  build.
- Alias promotion happens after checkpoint presence.
- Stale slot cleanup retries `ObjectInUse`.
- Bootstrap failure exits the container unless output contains `already exists`.

Risks:

- `alias_watcher.py` has no explicit signal handling.
- The watcher is started in the background and is not supervised after PGSync is
  `exec`'d.
- ES and Redis transient connection errors are not handled with robust backoff.
- `resolve_schema.py` and `alias_watcher.py` do not support ES authentication.

### Ports, Endpoints, Volumes, and Dependencies

Local Docker Compose:

- PGSync image: `toluaina1/pgsync:latest`.
- PostgreSQL URL:
  `postgresql://postgres:postgres@host.docker.internal:5432/${DB_NAME:-readygop}`.
- Elasticsearch URL: `http://elasticsearch:9200`.
- Redis host/port: `redis:6379`.
- Mounted volumes:
  - schema to `/app/schema.json`
  - entrypoint to `/entrypoint.sh`
  - scripts to `/scripts`
  - plugins to `/app/plugins`
- Network: external `downloads_default`.

OKD manifest:

- Elasticsearch service: `elastic-svc`, port `9200`.
- Redis service: `redis-svc`, port `6379`.
- Elasticsearch StatefulSet: one replica, ES `7.17.26`.
- Redis StatefulSet: one replica, Redis `7.4-rc2-alpine`.
- Main PostgresCluster has `wal_level: logical`.
- No PGSync producer/consumer workload is currently present.

## Part 3: File-by-File Review

### `README.md`

- Contains only the title at `README.md:1`.
- No setup, deployment, image build, schema explanation, rollback, or OKD
  instructions.
- Cannot determine intended custom image source from README.

### `docker-compose.yml`

Main sections:

- `x-pgsync-common` defines shared PGSync image, volumes, entrypoint, env, and
  network.
- `pgsync-producer` sets `PGSYNC_ROLE=producer`.
- `pgsync-consumer` sets `PGSYNC_ROLE=consumer` and `NUM_WORKERS`.
- `networks.search` uses an external Docker network.

Confirmed behavior:

- Uses image `toluaina1/pgsync:latest`.
- Mounts schema, entrypoint, scripts, and plugins read-only.
- Enables Redis checkpointing.
- Splits producer and consumer into separate services.

Risks:

- `latest` is mutable.
- `host.docker.internal` is local-only and not OKD-compatible.
- No healthchecks or resource limits.
- External network must already exist.

### `entrypoint.sh`

Main sections:

- Constants:
  - `SCHEMA=/app/schema.json`
  - `RESOLVED=/tmp/schema.resolved.json`
- `resolve()` helper.
- `producer` case.
- `consumer` case.
- invalid role case.

Confirmed behavior:

- Uses `set -eu`.
- Producer resolves schema, bootstraps, starts alias watcher, and runs PGSync
  producer.
- Consumer resolves schema with `--wait` and runs PGSync consumer.

Risks:

- Background watcher is not explicitly supervised.
- No signal trap for watcher shutdown.
- Bootstrap error handling relies on string matching `already exists`.

Cannot determine statically:

- Exact PGSync process behavior after receiving termination signals.
- Whether bootstrap output can contain `already exists` together with a more
  serious failure.

### `scripts/resolve_schema.py`

Main sections:

- ES HTTP helper.
- environment variable expansion.
- timestamp generation.
- alias lookup.
- candidate index lookup.
- Redis checkpoint lookup.
- role-specific resolution.
- wait loop and output writer.

Confirmed behavior:

- Uses `urllib` rather than `elasticsearch-py`.
- Fails if schema references an unset environment variable.
- Rejects already timestamped schema index names.
- Uses Elasticsearch aliases as source of truth.
- Uses Redis checkpoint presence to decide resume vs new build.
- Writes a resolved schema JSON file.

Risks:

- No ES authentication support.
- No explicit retry/backoff for ES or Redis.
- Redis connection errors are not handled.
- Uses bare `open(...).read()`.

Cannot determine statically:

- Whether all ES endpoints are reachable from OKD.
- Whether Redis checkpoint semantics exactly match the installed PGSync version.

### `scripts/alias_watcher.py`

Main sections:

- ES HTTP helper.
- Redis checkpoint lookup.
- alias promotion.
- stale index lookup.
- stale replication slot lookup.
- cleanup loop.

Confirmed behavior:

- Waits for resolved schema file.
- Waits for Redis checkpoint before alias promotion.
- Promotes aliases through Elasticsearch `_aliases`.
- Deletes stale timestamped indices.
- Drops stale PostgreSQL replication slots.
- Retries slots that are still in use.

Risks:

- No ES authentication support.
- No reconnect handling for long-running PostgreSQL connection.
- No explicit graceful shutdown handling.
- Requires database privileges to inspect and drop replication slots.
- Deletes stale indices after alias promotion; this is intentional but still
  operationally sensitive.

Cannot determine statically:

- Whether the preview database user has enough privileges.
- Whether secured Elasticsearch will accept unauthenticated in-cluster HTTP.

### `schemas/readygop/schema.json`

Main sections:

- Eight PGSync documents, one per index.
- Each document includes:
  - `database`
  - `index`
  - Elasticsearch `setting`
  - `plugins`
  - `pipeline`
  - `routing`
  - `nodes`

Confirmed structure:

- Root nodes:
  - `brands`
  - `campaigns`
  - `clients`
  - `lists`
  - `teams`
  - `users`
  - `projects`
  - `user_views`
- Most root nodes omit `columns`, so PGSync reflects all columns.
- Child relationships supply joined or aggregated fields used by plugins.

Relationships:

- `brands -> clients`, one-to-one scalar, renamed to `client_name`.
- `projects -> clients`, one-to-one scalar, renamed to `client_name`.
- `users -> user_organizations`, one-to-many scalar, labeled
  `organization_ids`.
- `user_views -> client_tree_mview -> clients -> projects/brands -> campaigns`.

Risks:

- Repeated Elasticsearch analyzer/settings blocks increase drift risk.
- Schema depends on actual tables/views/materialized views existing.
- No checked-in tests validate schema against a database.

Cannot determine statically:

- Whether every foreign key path matches the real database.
- Whether the `client_tree_mview` data shape is representative.

### `plugins/readygop/result_type.py`

Main sections:

- `_encode()` helper for JSON serialization.
- `ResultTypePlugin` base class.
- `skip()`, `merged()`, `result_type()`, and `transform()` hooks.

Confirmed behavior:

- Subclasses `pgsync.plugin.Plugin`.
- Builds a top-level document from `TOP_LEVEL`.
- Adds `result_type` as a JSON string.
- Drops `_meta` from `result_type`.
- Encodes date/time values with ISO format and UUID/Decimal as strings.

Risks:

- Missing expected fields are silently omitted.
- Downstream search behavior depends on `result_type` being a string.

### Plugin Review

| Plugin | Class / Name | Input Fields | Transformation | Expected Output | Schema Dependency | Potential Errors | Representative Example |
|---|---|---|---|---|---|---|---|
| Brand | `BrandResultTypePlugin`, `BrandResultType` | brand row + `client_name` | Builds JSON-string `result_type`, merges `client_name` | Top-level `id`, `name`, `organization_id`, `client_id`, `updated_at`, `client_name`, `result_type` | `brand_index` child `clients.name -> client_name` | Missing child gives null `client_name` | Brand with joined client name |
| Campaign | `CampaignResultTypePlugin`, `CampaignResultType` | campaign row | Plain `to_jsonb` style result_type | Campaign top-level fields plus `result_type` | `campaign_index` root only | None specific | Campaign row mirrored |
| Client | `ClientResultTypePlugin`, `ClientResultType` | client row, `note`, `client_type_id` | Adds computed `details` preview | Client fields plus `result_type` string | `client_index` root only | SQL parity depends on note truncation semantics | Long note first 20 chars, else `...` |
| List | `ListResultTypePlugin`, `ListResultType` | list row | Plain result_type | List fields plus `result_type` | `list_index` | None specific | List row mirrored |
| Team | `TeamResultTypePlugin`, `TeamResultType` | team row | Plain result_type | Team fields plus `result_type` | `team_index` | None specific | Team row mirrored |
| User | `UserResultTypePlugin`, `UserResultType` | user row + `organization_ids` | Normalizes org IDs to string list; excludes from result_type | User fields plus `result_type` | `user_index` child label `organization_ids` | Non-list coerced to one-element list | `42` becomes `["42"]` |
| Project | `ProjectResultTypePlugin`, `ProjectResultType` | project row + `client_name`, `message` | Merges client name, literals, computed details | Project fields plus `result_type` | `project_index` child `clients.name -> client_name` | Missing child gives null `client_name` | Long message preview |
| Phone | `PhoneResultTypePlugin`, `PhoneResultType` | phone row, `description`, status | Skips invalid status, hand-builds result_type | Phone fields plus `result_type` | No matching schema entry found | Existing invalid docs are not deleted; status prefix needs confirmation | Invalid status skipped only on new writes |
| User View | `UserViewPlugin`, `UserView` | user_view row + nested tree | Builds allowed client/project/brand/campaign ID arrays | User view fields only, no `result_type` | `user_view_index` nested tree | Plugin comments say not validated with real rows | Seeds own `client_id`, walks descendants |

## OKD Manifest Findings

Facts confirmed in `k8s-resources.yaml`:

- Namespace: `readygop-preview-1984`.
- Redis service: `redis-svc`, port `6379`.
- Elasticsearch service: `elastic-svc`, port `9200`.
- Elasticsearch StatefulSet:
  `docker.elastic.co/elasticsearch/elasticsearch:7.17.26`, one replica.
- Redis StatefulSet: `redis:7.4-rc2-alpine`, one replica.
- Main PostgresCluster has `wal_level: logical`, which is necessary for logical
  replication.
- Rails/search deployments consume DB, ES, and Redis settings.
- No PGSync Deployment, StatefulSet, Job, ConfigMap, Secret, volume, or image is
  defined.

## Issues and Risks

- PGSync OKD workload is missing from the manifest.
- ES auth mismatch: OKD ES has username/password env, but `resolve_schema.py`
  and `alias_watcher.py` only call unauthenticated HTTP.
- Local `PG_URL` pattern is not OKD-ready; OKD should use the
  Crunchy-generated service/secret or a carefully chosen direct DB connection.
- PGSync logical replication should likely avoid PgBouncer unless confirmed
  compatible; PGSync needs logical replication slot behavior, and PgBouncer
  transaction pooling can break assumptions.
- Producer must be exactly one replica with a non-overlapping rollout strategy.
- Alias watcher must not run in multiple pods.
- Replication slot cleanup requires elevated DB privileges.
- `k8s-resources.yaml` contains secret data and secret-handling jobs; keep it
  out of casual sharing.
- The schema has an unused phone plugin and no phone index.
- `result_type` behavior is custom and downstream-sensitive.
- No tests are present for plugins, schema resolution, alias promotion, or OKD
  manifests.

## Information Still Requiring Confirmation

- Exact PGSync package version and image contents.
- Whether the intended custom image exists outside this repo.
- Whether OKD Elasticsearch requires basic auth for in-cluster calls.
- Correct PG connection string for logical replication: direct primary service
  vs PgBouncer.
- Required DB privileges for bootstrap, trigger creation, slot creation, and slot
  drop.
- Whether `user_views` has representative data; plugin comments say it has not
  been validated.
- Whether `PHONE_INVALID_STATUS_IDS` and phone index are intentionally omitted.
- Whether `APP_ENV` should be `staging`, `preview`, `preview-1984`, or another
  stable namespace-specific value.

## Recommendations for the PoC

Treat this topology as preview-only.

1. Add PGSync as two separate OKD Deployments:
   - `pgsync-producer`: `replicas: 1`, `strategy: Recreate`.
   - `pgsync-consumer`: scalable, start with `replicas: 1`.
2. Package or mount:
   - `/entrypoint.sh`
   - `/scripts/resolve_schema.py`
   - `/scripts/alias_watcher.py`
   - `/app/schema.json`
   - `/app/plugins/readygop/*.py`
3. Use preview services:
   - `REDIS_HOST=redis-svc`
   - `REDIS_PORT=6379`
   - `ELASTICSEARCH_URL=http://elastic-svc:9200` unless auth/TLS requires
     otherwise.
4. Use `REDIS_CHECKPOINT=true`.
5. Confirm direct PostgreSQL logical replication connection and privileges before
   deployment.
6. Add ES auth support to both Python scripts before using secured preview
   Elasticsearch.
7. Keep alias naming preview-scoped with `APP_ENV`, for example `preview_1984`
   or another agreed value.
8. Add basic tests before deployment:
   - schema JSON parses
   - env placeholder expansion
   - plugin transform examples
   - alias/index name derivation
   - checkpoint key derivation

## Future Production Recommendations

- Pin PGSync image by version or digest.
- Build a controlled custom image instead of runtime mounting scripts/plugins.
- Add readiness/liveness probes for producer and consumer.
- Add structured logging.
- Add explicit graceful shutdown handling for alias watcher.
- Add ES auth/TLS support and retries with backoff.
- Add production runbooks for reindex, rollback, stale slots, Redis loss, and
  alias recovery.
- Do not use this preview topology as production-grade without HA,
  observability, capacity planning, and tested recovery procedures.
