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
