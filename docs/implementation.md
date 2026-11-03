# Implementation decisions and verification

Verified on 2026-11-03, macOS host with Linux ARM64 Docker containers.
Branch: `feat/initial-mlaas-poc`. No changes pushed to main.

## Architecture and files

- `compose.yaml`, `Dockerfile`, pinned requirements: local coraine, MLflow serving,
  FastAPI adapter and static UI. Broker data and adapter journal use named volumes.
- `mlaas/train.py`: explicit download, split, fit, metrics and MLflow artifacts.
- `mlaas/serve.py`: artifact checksum/version check, then standard MLflow serving.
- `mlaas/cli.py`: separate model and processing registration; water replay builder.
- `mlaas/adapter.py`, `common.py`: generic mapping, subscriptions, durable jobs,
  HTTP inference, idempotent prediction publication and event log.
- `mlaas/demo.py`, `web/`: dataset replay control and dashboard using actual APIs.
- `contexts/`, `examples/`: explicit vocabulary and real-broker-tested payloads.
- `tests/`: direct broker, training, end-to-end, recovery and dashboard DOM checks.

## Broker and JSON-LD decisions

The pinned image is `quay.io/seamware/coraine:0.6.1-post-2026-11-03-7299e496`.
CorDB uses `--dbDir /data` and survives restart, including subscriptions. No
MongoDB service is required. Matching creations notify. Unwatched updates and
q nonmatches do not notify. Watched updates into a matching state do notify.
This is current-state selection, not threshold-crossing detection.

Sources checked:

- [coraine installation](https://github.com/SEAMWARE/coraine/blob/main/doc/installation.md)
- [Water Quality schema](https://github.com/smart-data-models/dataModel.WaterQuality/blob/master/WaterQualityObserved/schema.json)
- [Water Quality context](https://github.com/smart-data-models/dataModel.WaterQuality/blob/master/context.jsonld)
- [Machine Learning context](https://github.com/smart-data-models/dataModel.MachineLearning/blob/master/context.jsonld)
- [ETSI core context](https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.9.jsonld)
- [MLflow local serving](https://mlflow.org/docs/latest/ml/deployment/deploy-model-locally/)

Examples embed the context in `contexts/context.jsonld`. GET and bodyless requests
use a Link to the adapter's context URL. Coraine rejects simultaneous inline
context and Link with `application/ld+json`; request helpers avoid that conflict.
The smoke receiver must use HTTP/1.1 and Content-Length; its initial HTTP/1.0
response was rejected by the broker context fetcher. Docker host-gateway resolves
its test callback. Service-to-service production callbacks use Compose DNS.

Coraine compaction collapses singleton arrays, including nested
`subscriptionData.entities` and `watchedAttributes`. The adapter normalizes only
the contract's declared array fields. It does not evaluate q itself.

Coraine stores IEEE doubles but serializes responses with at most nine decimal
places. Replay posts original CSV numeric values; the immutable raw CSV and
checksum stay on disk. The runtime reads the broker's numeric representation.
End-to-end tests check absolute serialization error <= 5.1e-10 and verify that
source API representations remain identical before/after inference. Exact decimal
text roundtrip is not provided by this broker version. Do not claim byte-identical
CSV-to-API measurements or use this PoC for precision-sensitive metrology.

The project namespace is
`https://github.com/dncampo/fiware-mlaas-template/terms/`, a vocabulary identifier.
Official terms are reused for the existing ML types/metadata/relationships,
WaterQualityObserved, conductivity and turbidity. `ph` remains a project term
(official spelling: `pH`); `sulfate` also remains a project term (official spelling:
`sulphate`, whose schema specifies mg/l). No unsupported units, acquisition dates
or full SDM conformance are claimed. MLPrediction and the added fields are project
extensions.

## Missing data and immutable provenance

Missing CSV measurements are omitted from NGSI-LD observations; JSON null is not
posted as a measurement. The model's `missingInputFeatures` extension explicitly
allows those omitted features to become HTTP nulls for the trained imputer.
Other missing, boolean, nonnumeric or nonfinite features fail visibly.

Model and processing configurations are pinned in the adapter journal. Reusing
an ID with changed binding/configuration is rejected. Prediction IDs hash
processing ID, model ID, exact version and source ID. The journal deduplicates
accepted notifications, and create-only broker publication checks conflicts and
lineage. Repeated notifications after restart do not run a completed job again.
The original observation is never written by the adapter. A fingerprint detects
changes after the first fetch; application immutability still requires writers to
respect the contract. Broker authorization is outside this local demo.

The single worker retries transport/server failures with a delay capped at 60s;
malformed inputs and model HTTP 4xx become failed jobs. Jobs are acknowledged only
after journal commit. `/state` exposes statuses and attempt counts. `/events`
retains the latest 2000 timestamped events. The dashboard displays at most 100
observation rows and 100 trace rows, with broker queries capped at 1000 entities.
It reports a missing notification as unobserved, not definitive evidence that an
entity failed q. Already accepted jobs drain after pause.

## Executed checks

- `docker compose config --quiet`: passed.
- Real broker smoke test: passed, including all five example payloads (four
  entity types plus the Subscription API resource), CRUD, relationships,
  matching/nonmatching creations, watched/unwatched updates and deletion.
- Python suite with all integration/restart/recovery flags: passed (6 tests,
  28.27 seconds). No integration skips.
  It checks reproducible/disjoint splits, training-only imputation, ten matching
  samples, exact lineage, unchanged sources, duplicate delivery, no output loops,
  pause/removal, invalid notifications/inputs, model pin tampering, broker restart,
  and accepted-job recovery after adapter restart and serving outage.
- Independent MLflow `/invocations`: returned `{"predictions":[0]}` for the
  documented synthetic row with missing pH.
- `npm run test:dashboard`: passed against live services. Verifies live models,
  configuration, inspector, one/batch injection, predictions and unobserved
  notifications using the actual application JavaScript in a DOM.
- `git diff --check`: passed.

Visual browser verification is unavailable: the in-app browser host is not
connected, and the native browser inspection returned an unrelated stale image.
No visual screenshot approval is claimed. The DOM test covers frontend behavior,
not browser-specific appearance or layout. MLflow emits upstream Pydantic
deprecation warnings; the tested flows pass.

See the acceptance table for the final run results.

## Troubleshooting and fallback

- Docker socket missing: start Docker Desktop and complete its administrator
  networking prompt. This happened initially; it is not a broker failure.
- Serving unhealthy: train first; inspect `docker compose logs ml-serving` and
  artifact version/checksum. Do not overwrite a deployed version to fix it.
- Registration 409: the create-only resource exists. Inspect it; reconcile an
  existing processing rather than registering it again. New configuration needs
  a new ID.
- No prediction: inspect q, source type, subscription endpoint, `/events` and job
  errors. A nonmatching observation is expected to have no notification.
- Invalid data: retain the failed trace and post a corrected sample with a new
  immutable ID. Do not patch the original measurement.
- Serving unavailable: restore the same pinned deployment. Accepted pending jobs
  retry automatically; this does not train, promote or redeploy a model.
- Broker volume intentionally replaced: explicitly reconcile each registered
  processing again after restoring entities. Unknown processings are not scanned
  or silently registered on startup.
- Dependency issue: MLflow 2.17.2 was incompatible with newly resolved SQLAlchemy
  2.1.6 during initial setup. The requirements pin the tested SQLAlchemy 2.0.36;
  MLflow tracking uses its local file backend. Do not refresh pins blindly.

Coraine passes the essential integration checks, so no Orion-LD fallback was
activated. If a future environment fails those checks after network/context
issues are excluded, record the failing request/response and run the same direct
API suite against a pinned Orion-LD deployment following its
[official installation instructions](https://github.com/FIWARE/context.Orion-LD).
Orion-LD and its storage deployment have not been tested here; no fallback Compose
configuration is claimed to work. Retest examples, subscriptions, persistence and
recovery before switching brokers.
