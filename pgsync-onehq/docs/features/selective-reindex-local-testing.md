# Selective Reindex - Local Test Strategy and Acceptance Criteria

## 1. Why are we testing this?

The Selective Reindex feature introduces a new operational behavior in PGSync.

Previously, the Reindex Job always processed the complete ReadyGOP schema:

```text
schema.json
    |
    v
All indexes
    |
    v
bootstrap
    |
    v
parallel_sync
    |
    v
finalize
```

This means that even when a schema change affects only one small Elasticsearch index, the workflow would still rebuild every index, including `phone_index`.

Since `phone_index` represents most of the total reindex execution time, this creates unnecessary processing time and operational cost.

The new behavior introduces:

```text
REINDEX_TARGETS
```

which allows the Reindex Job to process:

```text
all
```

or only specific indexes:

```text
brand_index
```

or multiple indexes:

```text
brand_index,project_index
```

Before integrating this behavior into the Kubernetes Reindex Job, we need to prove that the schema-selection layer is deterministic, safe, and fails closed when invalid input is provided.

---

# 2. What problem are these tests solving?

The main risk of Selective Reindex is not only selecting the wrong index.

The more serious risk is accidentally selecting **more indexes than intended**.

For example:

```text
Expected:

REINDEX_TARGETS=campaign_index
        |
        v
campaign_index only
```

An incorrect implementation could produce:

```text
campaign_index
phone_index
brand_index
...
```

which would effectively turn a selective operation back into a Full Reindex.

For ReadyGOP this is especially important because accidentally including:

```text
phone_index
```

can significantly increase execution time.

For this reason, the filtering logic follows a fail-closed model:

```text
Valid configuration
        |
        v
continue
```

but:

```text
Invalid / ambiguous configuration
        |
        v
FAIL
```

It must never silently fall back to:

```text
all
```

---

# 3. What are we validating?

The tests validate the behavior of:

```text
scripts/filter_schema.py
```

which is responsible for transforming:

```text
schema.json
+
REINDEX_TARGETS
```

into:

```text
filtered schema
```

Example:

```text
schema.json

brand_index
campaign_index
client_index
list_index
phone_index
project_index
team_index
user_index
user_view_index
```

with:

```text
REINDEX_TARGETS=brand_index,project_index
```

must generate a runtime schema containing only:

```text
brand_index
project_index
```

The source `schema.json` remains unchanged.

---

# 4. Test layers

The feature is currently validated through three local layers:

```text
             Selective Reindex tests
                      |
          +-----------+-----------+
          |                       |
          v                       v
      Unit Tests              CLI Tests
          |                       |
          |                       |
          +-----------+-----------+
                      |
                      v
               Docker Smoke Test
```

Each layer validates a different concern.

---

## 4.1 Unit tests

Location:

```text
tests/unit/test_filter_schema.py
```

These tests validate the Python filtering and validation logic directly.

They test scenarios such as:

```text
all
single target
multiple targets
duplicates
whitespace
unknown target
invalid schema
APP_ENV expansion
phone_index exclusion
real ReadyGOP schema
```

They are fast and isolate the filtering logic from the rest of PGSync.

---

## 4.2 CLI integration tests

Location:

```text
tests/integration/test_filter_schema_cli.py
```

These tests execute the script through its actual command-line interface.

Conceptually:

```text
subprocess
    |
    v
filter_schema.py
    |
    +-- arguments
    +-- exit code
    +-- stderr/stdout
    `-- generated output
```

This validates behavior closer to how the script will be invoked by:

```text
entrypoint.sh
```

and later by the Kubernetes Reindex Job.

These are component-level integration tests.

They are **not** a full PGSync integration test involving PostgreSQL, Redis, or Elasticsearch.

---

## 4.3 Docker smoke test

The Docker smoke test verifies something different:

```text
Can the actual production image be built,
and does it contain a working filter_schema.py?
```

This catches issues that unit tests cannot detect, such as:

```text
script not copied into image
wrong permissions
incorrect Dockerfile path
missing runtime dependency
broken image build
```

---

# 5. Why run the tests inside `python:3.12-slim`?

The following command is used:

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest \
      --cov=scripts.filter_schema \
      --cov-report=term-missing \
      --cov-fail-under=90
  '
```

This creates an ephemeral Python 3.12 environment and runs the test suite against the current repository.

This approach has several advantages.

### Reproducibility

Every developer runs the tests with:

```text
Python 3.12
```

regardless of which Python version is installed locally.

This is particularly useful because the PGSync Dockerfile also uses:

```dockerfile
FROM python:3.12-slim
```

so the local test environment closely matches the runtime Python version.

---

### No local Python environment required

The developer does not need to install:

```text
pytest
pytest-cov
development dependencies
```

directly on the workstation.

Dependencies exist only inside the temporary container.

---

### Clean environment

Because the command uses:

```text
--rm
```

the container is removed automatically after execution.

The test does not depend on packages that may already exist in a developer's local Python environment.

This helps avoid:

```text
works on my machine
```

differences.

---

### Current repository is tested directly

This part:

```bash
-v "$PWD:/workspace"
```

mounts the current repository inside the container.

And:

```bash
-w /workspace
```

makes the repository the working directory.

Therefore:

```text
Local repository
       |
       v
/workspace
       |
       v
pytest
```

tests exactly the code currently checked out by the developer.

---

### Python imports resolve from the repository

This:

```bash
-e PYTHONPATH=/workspace
```

allows pytest to import:

```text
scripts.filter_schema
```

directly from the mounted repository.

---

# 6. Why do we have `requirements-dev.txt`?

The test container is intentionally minimal.

`python:3.12-slim` does not contain test tooling such as:

```text
pytest
pytest-cov
```

Therefore:

```bash
pip install --quiet -r requirements-dev.txt
```

installs only the development/test dependencies required to run the suite.

These dependencies are separate from the production PGSync image requirements.

---

# 7. About the pip root warning

During the test you may see:

```text
WARNING: Running pip as the 'root' user...
```

This warning is expected in this specific test workflow.

The installation happens inside an ephemeral Docker container that is removed after the tests because of:

```text
--rm
```

It does not install packages into the host operating system.

The warning does not indicate a failed test.

The important result is the pytest exit status.

---

# 8. Coverage validation

The command also runs:

```bash
--cov=scripts.filter_schema
```

This measures how much of:

```text
scripts/filter_schema.py
```

is exercised by the automated tests.

The current result is:

```text
Name                       Stmts   Miss  Cover
------------------------------------------------
scripts/filter_schema.py      69      2    97%
------------------------------------------------
TOTAL                         69      2    97%
```

Current coverage:

```text
97.10%
```

The minimum accepted coverage is:

```text
90%
```

enforced by:

```bash
--cov-fail-under=90
```

Therefore:

```text
Coverage >= 90%
        |
        v
PASS
```

while:

```text
Coverage < 90%
        |
        v
FAIL
```

This makes coverage an actual acceptance criterion instead of informational output.

---

# 9. Why enforce a coverage threshold?

High coverage alone does not prove that software is correct.

However, for this component it helps ensure that important validation branches are not added without corresponding tests.

For example, if future changes introduce:

```text
new target validation
new schema validation
new error handling
```

but no tests exercise those paths, the coverage threshold can detect the regression.

The current target is:

```text
>= 90%
```

and the current implementation achieves:

```text
97.10%
```

---

# 10. Current automated result

The current suite executes:

```text
28 tests
```

with:

```text
28 passed
0 failed
```

Example:

```text
============================= test session starts ==============================
collected 28 items

tests/integration/test_filter_schema_cli.py ........
tests/unit/test_filter_schema.py ....................

============================== 28 passed ======================================
```

This validates both:

```text
Python filtering logic
```

and:

```text
CLI behavior
```

against the expected Selective Reindex contract.

---

# 11. Why run `pytest -v` separately?

The second command is:

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest -v
  '
```

The purpose is not to provide additional coverage.

Its purpose is to make every acceptance scenario visible.

Instead of:

```text
............................
```

the developer can see:

```text
test_single_target PASSED
test_multiple_targets PASSED
test_phone_index_is_not_selected_implicitly PASSED
test_unknown_target_fails PASSED
test_duplicate_logical_indexes_fail PASSED
...
```

This makes the test suite useful as both:

```text
automated validation
```

and:

```text
executable documentation
```

A developer can immediately understand which behaviors are protected.

---

# 12. What the current tests prove

The current 28 tests prove that the Selective Reindex filtering layer correctly handles the main production scenarios.

For example:

```text
REINDEX_TARGETS=brand_index
```

selects exactly:

```text
brand_index
```

while:

```text
phone_index
```

is not implicitly included.

They also prove that invalid operator input fails before PGSync receives a runtime schema.

---

# 13. Acceptance matrix

The automated behavior can be formalized as follows.

| Requirement                | Test Layer | Expected Behavior                             | Why It Matters                                       |
| -------------------------- | ---------- | --------------------------------------------- | ---------------------------------------------------- |
| `all`                      | Unit       | Select every configured index                 | Preserves existing Full Reindex behavior             |
| Single target              | Unit / CLI | Select exactly one requested index            | Core Selective Reindex requirement                   |
| Multiple targets           | Unit       | Select all requested indexes and nothing else | Supports changes affecting several indexes           |
| Whitespace                 | Unit       | Normalize operator input                      | Avoid failures caused by human formatting            |
| Duplicate targets          | Unit       | Deduplicate requested targets                 | Prevent duplicate execution                          |
| Unknown target             | Unit / CLI | Fail with non-zero exit code                  | Prevent accidental incorrect reindex                 |
| Empty target               | Unit / CLI | Fail with non-zero exit code                  | Prevent ambiguous behavior                           |
| Whitespace-only target     | Unit       | Fail                                          | Prevent empty effective configuration                |
| `all,index`                | Unit / CLI | Fail                                          | Prevent ambiguous Full vs Selective intent           |
| Source ordering            | Unit       | Preserve source schema order                  | Deterministic runtime output                         |
| Schema root validation     | Unit       | Reject non-array root                         | Prevent malformed runtime configuration              |
| Schema entry validation    | Unit       | Reject non-object entries                     | Protect schema contract                              |
| Missing `index`            | Unit       | Reject schema entry                           | Every target must be identifiable                    |
| Duplicate schema indexes   | Unit       | Fail                                          | Prevent ambiguous logical targets                    |
| `APP_ENV` expansion        | Unit       | Resolve logical index name correctly          | Allows environment-specific schemas                  |
| Missing `APP_ENV`          | Unit       | Fail explicitly                               | Prevent malformed target resolution                  |
| `phone_index` exclusion    | Unit       | Do not select unless requested                | Prevent accidental expensive reindex                 |
| Explicit `phone_index`     | Unit       | Select when requested                         | Ensure large index remains intentionally reindexable |
| Placeholder preservation   | CLI        | Output schema keeps `${APP_ENV}`              | Filtering must not assume resolver responsibility    |
| Non-zero exit on errors    | CLI        | Return failure to caller                      | Kubernetes Job can detect configuration errors       |
| Invalid target CLI         | CLI        | Command fails visibly                         | Operational fail-closed behavior                     |
| Real schema inventory      | Unit       | Detect all ReadyGOP indexes                   | Validate against production schema contract          |
| Real schema selective case | Unit       | Correct subset from actual schema             | Prevent fixture-only confidence                      |
| Coverage threshold         | Test Suite | Coverage must remain >= 90%                   | Prevent untested logic growth                        |
| Docker build               | Smoke      | Production image builds successfully          | Validate Dockerfile and build context                |
| Docker runtime             | Smoke      | Production image contains executable filter   | Validate final artifact, not only source code        |

---

# 14. Important safety scenario: `phone_index`

One test deserves special attention:

```text
test_phone_index_is_not_selected_implicitly
```

This test exists because Selective Reindex was introduced specifically to avoid unnecessary expensive rebuilds.

If the operator requests:

```text
brand_index
```

the resulting schema must never contain:

```text
phone_index
```

unless it was explicitly requested.

The contract is:

```text
Requested:
brand_index

Result:
brand_index
```

not:

```text
brand_index
phone_index
```

This test directly protects the operational value of the feature.

---

# 15. Why preserve `${APP_ENV}`?

The test:

```text
test_cli_preserves_schema_placeholder
```

verifies that `filter_schema.py` selects indexes but does not take ownership of physical environment resolution.

For example, the source contains:

```text
brand_index_${APP_ENV}
```

The filter may use:

```text
APP_ENV=staging
```

to determine that its logical name is:

```text
brand_index
```

but the generated filtered schema must preserve:

```text
brand_index_${APP_ENV}
```

The next stage remains responsible for resolving it:

```text
filter_schema.py
        |
        | keeps placeholder
        v
brand_index_${APP_ENV}
        |
        v
resolve_schema.py
        |
        v
brand_index_staging_<timestamp>
```

This keeps responsibilities separated.

---

# 16. Why test exit codes?

For a local developer, an error message may look sufficient.

For Kubernetes, it is not.

The Reindex Job determines success or failure from the process exit code.

Therefore:

```text
invalid configuration
        |
        v
filter_schema.py
        |
        v
exit != 0
        |
        v
entrypoint.sh fails
        |
        v
Kubernetes Job = Failed
```

This is why the CLI tests explicitly validate non-zero exits.

Without this behavior, the script could print an error while the Job incorrectly continues.

---

# 17. What these tests do NOT validate

These tests intentionally stop at the schema-selection boundary.

They do not validate:

```text
PostgreSQL
    |
bootstrap
    |
replication slot
    |
parallel_sync
    |
Redis checkpoint
    |
Elasticsearch
    |
alias promotion
```

They prove:

```text
REINDEX_TARGETS
       |
       v
filter_schema.py
       |
       v
correct schema subset
```

A complete PGSync end-to-end test is a different layer and requires:

```text
PostgreSQL
Redis
Elasticsearch
PGSync runtime
```

# 18. Docker image build validation

Build the local image

```bash
docker build \
  --tag pgsync-onehq:local \
  .
```

---

# 19. Docker runtime smoke test

Once the image builds successfully, validate that the final production artifact contains and can execute the selective filtering script.

Example:

```bash
docker run --rm \
  --entrypoint /bin/sh \
  -e APP_ENV=staging \
  pgsync-onehq:local \
  -c '
    /app/scripts/filter_schema.py \
      --schema /app/schema.json \
      --targets "brand_index,project_index" \
      --out /tmp/schema.filtered.json &&
    python3 - <<'"'"'PY'"'"'
import json

with open("/tmp/schema.filtered.json") as f:
    docs = json.load(f)

print([doc["index"] for doc in docs])
PY
  '
```

Expected:

```text
[
  'brand_index_${APP_ENV}',
  'project_index_${APP_ENV}'
]
```

And importantly:

```text
phone_index_${APP_ENV}
```

must not appear.

---

# 20. Complete local validation flow

The recommended developer workflow is:

```text
Source changes
     |
     v
Unit tests
     |
     v
CLI integration tests
     |
     v
Coverage >= 90%
     |
     v
Production Docker build
     |
     v
Docker runtime smoke test
     |
     v
Ready for PR review
```

Commands:
### Step 3 - Build local image

```bash
docker build --tag pgsync-onehq:local .
```

Expected:

```text
successful build
```

### Step 2 - Automated tests + coverage

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest \
      --cov=scripts.filter_schema \
      --cov-report=term-missing \
      --cov-fail-under=90
  '
```

Expected:

```text
28 passed
coverage >= 90%
exit code 0
```

Current validated result:

```text
28 passed
97.10% coverage
```

### Step 3 - Review individual scenarios

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest -v
  '
```

Expected:

```text
all tests PASSED
```

### Step 4 - Production image smoke test

```bash
docker run --rm \
  --entrypoint /bin/sh \
  -e APP_ENV=staging \
  pgsync-onehq:local \
  -c '
    /app/scripts/filter_schema.py \
      --schema /app/schema.json \
      --targets "brand_index,project_index" \
      --out /tmp/schema.filtered.json &&
    python3 - <<'"'"'PY'"'"'
import json

with open("/tmp/schema.filtered.json") as f:
    docs = json.load(f)

print([doc["index"] for doc in docs])
PY
  '
```

Expected:

```text
brand_index_${APP_ENV}
project_index_${APP_ENV}
```

with no:

```text
phone_index_${APP_ENV}
```

---

# 21. Definition of Done

The Selective Reindex schema-selection layer can be considered ready for integration when:

```text
28 automated tests pass
        +
coverage >= 90%
        +
real ReadyGOP schema validation passes
        +
production image builds
        +
Docker runtime smoke test passes
```

Current automated validation:

```text
Tests:     28 / 28 passed
Coverage:  97.10%
Threshold: 90%
```

This gives us confidence that the Selective Reindex filtering logic is safe to connect to the existing PGSync Reindex workflow while maintaining the previous Full Reindex behavior through:

```text
REINDEX_TARGETS=all
```

and allowing isolated schema changes to avoid unnecessarily rebuilding unrelated indexes such as `phone_index`.


---

# 22. Entrypoint integration validation

After validating `filter_schema.py` independently through Unit, CLI, real-schema, coverage, and Docker smoke tests, additional integration tests were added to validate how the selective-reindex feature is connected to the real `entrypoint.sh` workflow.

These tests are located at:

```text
tests/integration/test_entrypoint_reindex.py
```

The purpose of this layer is different from the existing `filter_schema.py` tests.

The existing tests prove that:

```text
REINDEX_TARGETS
       |
       v
filter_schema.py
       |
       v
correct filtered schema
```

The new entrypoint integration tests prove that:

```text
PGSYNC_ROLE=reindex
       |
       v
entrypoint.sh
       |
       v
prepare_reindex_schema()
       |
       v
filter_schema.py
       |
       v
REINDEX_SCHEMA
       |
       v
resolve_schema_from()
       |
       v
bootstrap
       |
       v
parallel_sync
       |
       v
finalize
```

is wired correctly.

The external/destructive PGSync components are mocked during these tests, so the test suite does not connect to PostgreSQL, Redis, or Elasticsearch.

The real `filter_schema.py` implementation is still executed.

This gives us confidence in the orchestration logic without performing a real reindex.

---

# 23. What the entrypoint tests validate

The entrypoint integration suite currently contains seven scenarios.

```text
test_reindex_invalid_target_fails_before_bootstrap
test_reindex_empty_target_fails_before_bootstrap
test_reindex_single_target_uses_filtered_schema
test_reindex_multiple_targets_use_filtered_schema
test_reindex_unset_targets_defaults_to_all
test_producer_uses_full_source_schema
test_consumer_uses_full_source_schema
```

These tests protect the following runtime contract:

```text
REINDEX_TARGETS
      |
      +--> Reindex
      |      |
      |      v
      |   filtered schema
      |
      +--> Producer
      |      |
      |      v
      |   full SCHEMA_PATH
      |
      `--> Consumer
             |
             v
          full SCHEMA_PATH
```

`REINDEX_TARGETS` must only change the scope of a reindex operation.

It must never change the schema used by Producer or Consumer.

---

# 24. Invalid target must fail before bootstrap

Test:

```text
test_reindex_invalid_target_fails_before_bootstrap
```

Scenario:

```text
REINDEX_TARGETS=fake_index
```

Expected behavior:

```text
entrypoint.sh
    |
    v
prepare_reindex_schema()
    |
    v
filter_schema.py
    |
    v
unknown target
    |
    v
FAIL
```

The following steps must never execute:

```text
bootstrap
parallel_sync
finalize
```

This validates fail-closed behavior at the entrypoint orchestration layer, not only inside `filter_schema.py`.

Operationally, this means that an invalid target cannot accidentally start a PGSync rebuild.

---

# 25. Empty target must fail before bootstrap

Test:

```text
test_reindex_empty_target_fails_before_bootstrap
```

Scenario:

```text
REINDEX_TARGETS=""
```

Expected:

```text
FAIL
```

The entrypoint must not silently interpret an explicitly empty value as:

```text
all
```

This protects against an operator or configuration error accidentally triggering a Full Reindex.

The intended behavior is:

```text
REINDEX_TARGETS unset
        |
        v
       all
```

but:

```text
REINDEX_TARGETS=""
        |
        v
       FAIL
```

This distinction is why the entrypoint uses the shell default behavior equivalent to:

```sh
targets="${REINDEX_TARGETS-all}"
```

instead of treating both unset and empty values as `all`.

---

# 26. Single target entrypoint validation

Test:

```text
test_reindex_single_target_uses_filtered_schema
```

Scenario:

```text
REINDEX_TARGETS=brand_index
```

Expected flow:

```text
schema.json
    |
    v
filter_schema.py
    |
    v
brand_index only
    |
    v
resolve_schema_from()
    |
    v
bootstrap
    |
    v
parallel_sync
    |
    v
finalize
```

The generated reindex schema must contain exactly:

```text
brand_index_${APP_ENV}
```

and must not contain:

```text
phone_index_${APP_ENV}
```

This validates that the entrypoint actually uses the filtered schema rather than falling back to the complete ReadyGOP schema.

---

# 27. Multiple targets entrypoint validation

Test:

```text
test_reindex_multiple_targets_use_filtered_schema
```

Scenario:

```text
REINDEX_TARGETS=brand_index,project_index
```

Expected filtered schema:

```text
brand_index_${APP_ENV}
project_index_${APP_ENV}
```

The entrypoint must pass that filtered schema to the resolver and continue through the normal reindex lifecycle.

Unrelated indexes, especially:

```text
phone_index_${APP_ENV}
```

must not participate.

This validates the primary optimization provided by Selective Reindex.

---

# 28. Backward compatibility: unset targets use `all`

Test:

```text
test_reindex_unset_targets_defaults_to_all
```

Scenario:

```text
REINDEX_TARGETS is not defined
```

Expected:

```text
all ReadyGOP indexes selected
```

This protects backward compatibility.

Deploying the new image without explicitly setting `REINDEX_TARGETS` must preserve the existing Full Reindex behavior.

The expected inventory remains:

```text
brand_index
campaign_index
client_index
list_index
phone_index
project_index
team_index
user_index
user_view_index
```

---

# 29. Producer must continue using the full source schema

Test:

```text
test_producer_uses_full_source_schema
```

The test intentionally configures:

```text
PGSYNC_ROLE=producer
REINDEX_TARGETS=brand_index
```

Expected:

```text
REINDEX_TARGETS ignored by Producer
```

Producer must continue resolving:

```text
SCHEMA_PATH
```

which points to the complete source schema.

It must not use:

```text
REINDEX_SCHEMA
```

and the selective filtering step must not run for Producer.

Expected architecture:

```text
PGSYNC_ROLE=producer
        |
        v
resolve_schema()
        |
        v
SCHEMA_PATH
        |
        v
complete PGSync schema
```

This protects the runtime synchronization contract.

Producer is responsible for producing change events for the complete PGSync configuration, not only for the indexes selected during a previous reindex.

---

# 30. Consumer must continue using the full source schema

Test:

```text
test_consumer_uses_full_source_schema
```

The test intentionally configures:

```text
PGSYNC_ROLE=consumer
REINDEX_TARGETS=brand_index
```

Expected:

```text
REINDEX_TARGETS ignored by Consumer
```

Consumer must continue resolving the complete:

```text
SCHEMA_PATH
```

and must not use:

```text
REINDEX_SCHEMA
```

Expected architecture:

```text
PGSYNC_ROLE=consumer
        |
        v
resolve_schema()
        |
        v
SCHEMA_PATH
        |
        v
complete PGSync schema
```

This proves that Selective Reindex is an execution scope used only by the Reindex Job.

It does not become a second PGSync source of truth.

---

# 31. Run Producer and Consumer contract tests

To validate only the Producer and Consumer contract:

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest -v \
      tests/integration/test_entrypoint_reindex.py \
      -k "producer or consumer"
  '
```

Validated result:

```text
collected 7 items / 5 deselected / 2 selected

test_producer_uses_full_source_schema PASSED
test_consumer_uses_full_source_schema PASSED

2 passed, 5 deselected
```

This confirms:

```text
Producer -> full SCHEMA_PATH
Consumer -> full SCHEMA_PATH
```

even when `REINDEX_TARGETS` is defined.

---

# 32. Run the complete entrypoint integration suite

Command:

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest -v tests/integration/test_entrypoint_reindex.py
  '
```

Validated result:

```text
collected 7 items

test_reindex_invalid_target_fails_before_bootstrap PASSED
test_reindex_empty_target_fails_before_bootstrap PASSED
test_reindex_single_target_uses_filtered_schema PASSED
test_reindex_multiple_targets_use_filtered_schema PASSED
test_reindex_unset_targets_defaults_to_all PASSED
test_producer_uses_full_source_schema PASSED
test_consumer_uses_full_source_schema PASSED

7 passed
```

This proves that both the Selective Reindex path and the unchanged Producer/Consumer paths are protected.

---

# 33. Run the complete automated test suite

After adding the entrypoint integration tests, run the entire repository test suite:

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest -v
  '
```

Current validated result:

```text
35 tests collected
35 passed
0 failed
```

Breakdown:

```text
tests/integration/test_entrypoint_reindex.py     7 passed
tests/integration/test_filter_schema_cli.py      8 passed
tests/unit/test_filter_schema.py                20 passed
---------------------------------------------------------
TOTAL                                           35 passed
```

This validates the complete local automated test inventory currently implemented for the Selective Reindex feature.

---

# 34. Coverage after entrypoint integration tests

Run:

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace \
  python:3.12-slim \
  /bin/sh -c '
    pip install --quiet -r requirements-dev.txt &&
    pytest \
      --cov=scripts.filter_schema \
      --cov-report=term-missing \
      --cov-fail-under=90
  '
```

Current validated result:

```text
Name                       Stmts   Miss  Cover
------------------------------------------------
scripts/filter_schema.py      69      2    97%
------------------------------------------------
TOTAL                         69      2    97%

Required test coverage of 90% reached.
Total coverage: 97.10%

35 passed
```

The coverage threshold remains:

```text
>= 90%
```

Current coverage remains:

```text
97.10%
```

The additional entrypoint tests therefore extend orchestration validation without reducing the existing filtering coverage guarantees.

---

# 35. Extended acceptance matrix

The original acceptance matrix remains valid.

The following requirements are added for entrypoint integration and runtime-scope isolation.

| Requirement | Test Layer | Expected Behavior | Why It Matters |
| --- | --- | --- | --- |
| Invalid target stops entrypoint | Entrypoint Integration | Reindex exits before bootstrap | Invalid configuration must never begin a rebuild |
| Empty target stops entrypoint | Entrypoint Integration | Explicit empty value fails before bootstrap | Prevent accidental Full Reindex |
| Single target entrypoint wiring | Entrypoint Integration | Resolver receives one-index filtered schema | Proves filter is connected to real reindex flow |
| Multiple target entrypoint wiring | Entrypoint Integration | Resolver receives exactly requested indexes | Proves selective scope survives orchestration |
| Unset target backward compatibility | Entrypoint Integration | Defaults to complete schema | Preserves existing Full Reindex behavior |
| Filter failure prevents bootstrap | Entrypoint Integration | `bootstrap` is never executed | Fail before touching PGSync/PostgreSQL objects |
| Filter failure prevents parallel sync | Entrypoint Integration | `parallel_sync` is never executed | Prevent unintended data rebuild |
| Filter failure prevents finalize | Entrypoint Integration | `finalize_reindex.py` is never executed | Prevent alias/cleanup operations after invalid input |
| Resolver receives `REINDEX_SCHEMA` | Entrypoint Integration | Reindex resolves filtered runtime scope | Ensures full schema is not accidentally reused |
| Producer ignores `REINDEX_TARGETS` | Entrypoint Integration | Producer resolves full `SCHEMA_PATH` | Selective Reindex must not reduce CDC scope |
| Consumer ignores `REINDEX_TARGETS` | Entrypoint Integration | Consumer resolves full `SCHEMA_PATH` | Selective Reindex must not reduce consumer scope |
| Producer does not create `REINDEX_SCHEMA` | Entrypoint Integration | Filtering is never invoked for Producer | Keeps selective logic isolated to Reindex |
| Consumer does not create `REINDEX_SCHEMA` | Entrypoint Integration | Filtering is never invoked for Consumer | Keeps selective logic isolated to Reindex |
| Full automated suite | Test Suite | 35 / 35 tests pass | Protects the combined feature contract |
| Coverage after integration | Test Suite | `filter_schema.py` remains >= 90% | Prevents regression while extending orchestration tests |

---

# 36. Updated local validation architecture

The complete local validation strategy is now:

```text
                         Source code
                             |
              +--------------+--------------+
              |                             |
              v                             v
        Unit tests                    CLI integration
              |                             |
              +--------------+--------------+
                             |
                             v
                  Entrypoint integration
                             |
                 +-----------+-----------+
                 |                       |
                 v                       v
              Reindex               Producer/Consumer
            scope tests              isolation tests
                 |                       |
                 +-----------+-----------+
                             |
                             v
                     Coverage gate
                         >= 90%
                             |
                             v
                    Docker image build
                             |
                             v
                   Docker runtime smoke
                             |
                             v
                      Ready for PR
```

The testing responsibility can now be summarized as:

```text
Unit
  -> Is the filtering logic correct?

CLI
  -> Does the script behave correctly as a command?

Entrypoint Integration
  -> Is the filter correctly connected to the Reindex workflow?

Producer/Consumer Isolation
  -> Does REINDEX_TARGETS remain exclusive to Reindex?

Coverage
  -> Are the filtering code paths sufficiently exercised?

Docker Smoke
  -> Does the final production artifact contain and execute the feature?
```

---

# 37. Updated validated status

Current local validation status:

```text
Filter Unit Tests:             PASS
Filter CLI Integration Tests:  PASS
Entrypoint Integration Tests:  PASS
Producer Full-Schema Contract: PASS
Consumer Full-Schema Contract: PASS
Real ReadyGOP Schema:          PASS
Fail-Closed Validation:        PASS
Docker Build:                  PASS
Docker Runtime Smoke Test:     PASS

Automated Tests:               35 / 35 PASSED
Coverage:                      97.10%
Required Coverage:             90%
```

The local test strategy now validates both the Selective Reindex filtering implementation and its integration into the PGSync entrypoint while explicitly protecting the unchanged full-schema behavior required by Producer and Consumer.

---

# 38. Updated Definition of Done - integration stage

The Selective Reindex local implementation and entrypoint integration can be considered validated when all of the following are true:

```text
35 automated tests pass
        +
7 entrypoint integration tests pass
        +
Producer continues using full SCHEMA_PATH
        +
Consumer continues using full SCHEMA_PATH
        +
invalid and empty targets fail before bootstrap
        +
REINDEX_TARGETS unset preserves Full Reindex
        +
coverage remains >= 90%
        +
real ReadyGOP schema validation passes
        +
production image builds
        +
Docker runtime smoke test passes
```

Current validated result:

```text
Tests:                35 / 35 passed
Entrypoint tests:      7 / 7 passed
Coverage:              97.10%
Threshold:             90%
Docker build:          PASS
Docker runtime smoke:  PASS
```

At this point, the next validation layer is a controlled Selective Reindex execution against a non-production environment with the real PGSync dependencies:

```text
PostgreSQL
Redis
Elasticsearch
```

That stage is outside the scope of the local automated tests documented here.
