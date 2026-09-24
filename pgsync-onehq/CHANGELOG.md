### [2026-09-09] - [PD-30147]

#### Added PGSync Selective Reindex Support

Added selective reindex support through `REINDEX_TARGETS`, allowing the Reindex Job to rebuild only the Elasticsearch indexes affected by a schema change instead of always processing the complete PGSync schema.

Supported usage:

```text
REINDEX_TARGETS=all
REINDEX_TARGETS=brand_index
REINDEX_TARGETS=brand_index,project_index
```

`all` remains the default when `REINDEX_TARGETS` is not defined, preserving the existing Full Reindex behavior.

The implementation:

* keeps `schema.json` as the single source of truth;
* generates a temporary filtered schema only for the Reindex workflow;
* applies the selected scope to `bootstrap`, `parallel_sync`, and `finalize_reindex.py`;
* keeps Producer and Consumer using the complete `SCHEMA_PATH`;
* fails closed for empty, unknown, or ambiguous target configurations.

Validation completed:

```text
35 / 35 tests passed
97.10% coverage
Docker build and runtime smoke test passed
```

This reduces unnecessary PostgreSQL and Elasticsearch workload and avoids rebuilding expensive indexes such as `phone_index` when they are not affected.

Full implementation and local test details are documented under `docs/`.


### [2026-08-24] - [PD-28436]

#### Updated

##### Reindex Candidate Resolution

The reindex resolver now distinguishes the currently published index from unpublished timestamped generations.

##### Flow

```text
START
  |
  v
Resolve the physical index currently published by the alias
  |
  v
live_index = aliased_index(alias)
  |
  v
Query Elasticsearch for <alias>_*
  |
  v
Inspect each timestamped physical index
  |
  +--> Matches <alias>_<17-digit timestamp>?
  |        |
  |        +-- NO  --> Ignore
  |        |
  |        +-- YES
  |              |
  |              v
  |        Is it live_index?
  |              |
  |              +-- YES --> Ignore
  |              |
  |              +-- NO  --> Reindex candidate
  |
  v
Candidates available?
  |
  +-- NO  --> Create a new timestamped generation
  |
  +-- YES --> Select the newest unpublished candidate -> Return newest timestamped candidate
                    |
                    v
              Resume if incomplete
```

##### Example

Current Elasticsearch state:

```text
phone_index_staging
        |
        +--> phone_index_staging_20260821134111449   LIVE

phone_index_staging_20260824143000123                UNPUBLISHED
```

Resolution:

```text
live_index = phone_index_staging_20260821134111449
---
search = phone_index_staging_*
---
results = 20260821134111449, 20260824143000123
---
exclude live index = phone_index_staging_20260821134111449
    ↓
candidate = phone_index_staging_20260824143000123
```

This prevents the reindex process from treating the currently published generation as a build candidate and allows an existing unpublished generation to be identified for recovery or resume handling.

#### PGSync Runtime Responsibilities

| Component   | Responsibility                                                                                                                                                  | Creates Physical Index | Runs Bootstrap  | Publishes Alias | Normal Usage                                  |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- | --------------- | --------------- | --------------------------------------------- |
| `reindex`   | Creates or resumes a new physical generation, prepares PGSync objects, performs the full backfill, validates completion, and publishes the completed generation | Yes                    | Yes, internally | Yes             | Initial indexing and controlled full rebuilds |
| `bootstrap` | Recreates or maintains PGSync PostgreSQL objects for the currently published generation                                                                         | No                     | Yes             | No              | Maintenance and recovery only                 |
| `producer`  | Captures PostgreSQL changes for the currently published PGSync generation                                                                                       | No                     | No              | No              | Continuous runtime                            |
| `consumer`  | Processes PGSync change events for the currently published generation                                                                                           | No                     | No              | No              | Continuous runtime                            |
| `teardown`  | Removes PGSync-managed resources for the currently published generation                                                                                         | No                     | No              | No              | Explicit destructive maintenance              |

#### Improved

* Prevented `bootstrap`, `producer`, `consumer`, and `teardown` from independently allocating timestamped Elasticsearch indexes.
* Added support for detecting and resuming an incomplete unpublished reindex generation while excluding the currently published index from candidate selection.
* Established Elasticsearch aliases as the source of truth for identifying the active physical index generation.
* Reduced the risk of abandoned physical indexes and replication slots caused by multiple components independently generating timestamped index names.
* Simplified normal PGSync operation so Producer and Consumer restarts reuse the existing published generation without triggering bootstrap or reindex operations.


### [2026-08-24] - [PD-28391]

#### Added

* Added a dedicated Docker image build for PGSync, replacing the previous Docker Compose-only execution model and enabling PGSync to be packaged and deployed consistently across environments.

#### Changed

* Added the PGSync container build to use a dedicated multi-stage build flow, separating build-time dependencies from the final runtime image.
* Added `PGSYNC_REF` support to build PGSync from a specific OneHQ fork revision, allowing tested releases or commit SHAs to be pinned instead of always building from the latest `main` branch.
* Updated the runtime image to copy only the pre-built Python virtual environment and required PGSync application files from the builder stage.
* Simplified `scripts` and `plugins` directory copies to preserve their complete directory structure.
* Moved PGSync from a local Docker Compose-oriented setup to a reusable container image suitable for CI/CD and Kubernetes/OKD deployment.

#### Improved

* Improved PGSync image reproducibility by allowing the exact OneHQ PGSync source revision to be controlled at build time.
* Reduced unnecessary build tooling in the final runtime image by keeping Git and Python package installation dependencies isolated in the builder stage.
* Improved container security and OpenShift compatibility by retaining the non-root runtime user and group-based filesystem permissions.
* Improved deployment portability by decoupling PGSync execution from Docker Compose and providing a standalone image that can be promoted across staging and production.
* Prepared the PGSync image build process for reliable CI/CD promotion using the same tested PGSync revision across environments.
