# Acceptance criteria — minimal v1

All checks refer to **real** observed behavior, not mocked frontend animations. Tests must report actual execution outcomes.

| ID | Check |
|---|---|
| AC-01 | A documented local setup starts Context Broker, ML serving, adapter and minimal UI; services are mutually reachable. |
| AC-02 | Context Broker can create/query `MLModel`, `MLProcessing` and `WaterQualityObserved` with real JSON-LD properties and relationships. |
| AC-03 | Adapter turns an active `MLProcessing` into a real broker subscription and records its ID; it can pause/remove it. |
| AC-04 | Subscription `entities`, `watchedAttributes` and `q` trigger on a matching observation and do **not** trigger on a nonmatching one; creation/update behavior documented. |
| AC-05 | Offline dataset split is reproducible; demo partition is excluded from fit, validation and test; preprocessing leak checks pass. |
| AC-06 | Trained model artifact and metrics exist; a version-specific local HTTP endpoint returns an inference. |
| AC-07 | An injected source observation causes adapter to call serving and write one `MLPrediction` to broker. |
| AC-08 | Prediction references **exact** input observation ID, `MLProcessing` ID and immutable `MLModel` version ID; lineage can be traversed via API/UI. |
| AC-09 | Ten matching samples produce ten distinct predictions; duplicate notifications do not duplicate the predictions. |
| AC-10 | No inference is triggered by output `MLPrediction` insertion; no self-triggering loops. |
| AC-11 | Dashboard shows registered model, processing/subscription, live observations, actual triggering events and resulting predictions. |
| AC-12 | README + data-model reference + lifecycle + examples + agent instructions permit a developer to substitute a second conventional classifier and another input entity type without changing generic adapter logic. |

## Deferred

Training initiated from UI, automatic redeployment, threshold crossing detection (as opposed to current-state `q` filtering), geospatial demo, multiple input observations per inference, multi-version comparisons, windows, drift, automatic retraining, advanced auth and scalability.

## Verification record (2026-11-03)

The complete flagged Python suite passed: **6 tests, 28.27 seconds, no skips**.
The dashboard DOM suite also passed against live services. See
[implementation notes](implementation.md) for commands, environment and limits.

| ID | Result and evidence |
|---|---|
| AC-01 | Passed: Compose built and started all services with readiness checks; adapter, broker and native MLflow HTTP serving communicate. Dashboard HTML and JavaScript tested through HTTP. |
| AC-02 | Passed: direct API smoke creates/retrieves/deletes all four types and preserves Relationship objects using real JSON-LD contexts. |
| AC-03 | Passed: processing registration/reconciliation creates a real subscription and records its ID; repeat reconciliation and pause/deletion tested. |
| AC-04 | Passed: matching/nonmatching creations and watched/unwatched updates tested against coraine; behavior documented in architecture. |
| AC-05 | Passed: deterministic split manifest, complete/disjoint row sets and training-only imputer statistics checked against saved pipeline. |
| AC-06 | Passed: artifact and metrics generated; independent `/invocations` returns a class. Serving verifies version and model checksum; corrupt artifact rejected. |
| AC-07 | Passed: real broker notifications cause model HTTP requests and prediction publication. Serving outage and subsequent recovery also tested. |
| AC-08 | Passed: each prediction's source/model/processing IDs checked; source API representation unchanged; payload inspector and model link tested. |
| AC-09 | Passed: ten matching samples yield ten prediction IDs for the tested processing; three duplicate deliveries add no inference. Journal deduplication also survives restart. |
| AC-10 | Passed: generated outputs do not cause additional inference or appear as input notification types. Output/system types also rejected in processing selectors. |
| AC-11 | Functional DOM check passed with real model/processing/subscription data, timestamps, events, one/batch replay and outcomes. Visual browser layout inspection unavailable in this session; no visual QA claim. |
| AC-12 | Adaptation guide and contracts reviewed against generic adapter behavior. Dataset-specific replay code is isolated. A second dataset/classifier has not been executed; portability beyond the tested water example is unverified. |

No full official SDM schema conformance or exact decimal-text roundtrip is
claimed. No Orion-LD fallback was needed or tested. MLflow's dependency
Pydantic deprecation warnings do not affect the tested flows.

## Dashboard/reset verification update (2026-11-03)

The extended Python suite passed **21 tests, 46.19 seconds, no skips**, with
`RUN_BROKER_TESTS`, `RUN_RESTART_TESTS`, `RUN_E2E_TESTS`, `RUN_RECOVERY_TESTS` and
`RUN_DEMO_TESTS` all enabled. This includes 13 controlled reset safety tests and
two real broker/serving demo tests in addition to the original six checks.

- AC-08: the live dashboard suite follows prediction -> exact source, model and
  processing, plus processing -> actual subscription. The prediction inspector
  also reads the exact model's version from the broker.
- AC-09: real reset/replay checks recreate the same source and prediction IDs with
  a fresh prediction timestamp after verified deletion and dedup cleanup.
- AC-11: resource/trace buttons and section navigation act; inspector history,
  visible lookup errors, Clear View without backend writes, reset confirmation,
  progress/error handling and replay reuse are exercised. Clear View also ignores
  an older poll response arriving after the action.
- Reset safety: unrelated same-type entities, foreign outputs referencing a demo
  source, configuration pins, subscription configuration, serving and artifacts
  are preserved. Broker/serving failure, pending and in-flight inference, adapter
  restart, partial deletion, lost replies and retry behavior are tested.

See [implementation results](implementation.md) for actual dashboard link counts
and scope boundaries. DOM verification does not establish browser visual layout;
visual QA and a second dataset remain unverified.
