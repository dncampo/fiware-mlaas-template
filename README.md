# FIWARE NGSI-LD MLaaS Template

A small local demonstration of conventional ML inference driven by real NGSI-LD
subscriptions. Water Potability illustrates the pattern; model features are
mapped through configuration.

```
Observation -> Broker subscription -> Adapter -> MLflow HTTP serving
                                        |
                                        +-> MLPrediction -> Broker
```

There are four application entity types: `MLModel`, `MLProcessing`,
`WaterQualityObserved`, `MLPrediction`. Subscriptions use the standard broker API.
Training, serving, and registration are separate explicit operations. Source
observations are immutable; predictions reference their inputs without copying
measurements. No retraining, promotion, registry or distributed infrastructure.

## Setup

Requires Docker Compose and Python 3.12. On the tested machine, Docker Desktop
runs Linux ARM64 containers. Ports 1026, 5001, 8000 and test port 18080 must be free.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

docker compose up -d broker
RUN_BROKER_TESTS=1 python -m unittest tests.test_broker -v
```

The smoke test uses `host.docker.internal`; Compose supplies the host-gateway
alias. It checks all four examples through direct broker APIs, relationships,
matching/nonmatching creations and watched/unwatched updates. No model is needed.

## A. Train and evaluate

```sh
python -m mlaas.train --version v1
python -m pytest tests/test_training.py -q
```

The explicit command downloads a checksum-verified dataset, creates disjoint
60/15/15/10 train/validation/test/demo partitions, fits a median imputer and
Random Forest using training rows only, and evaluates the untouched holdouts.
The demo CSV excludes labels. Results, split manifests, preprocessing and model
are saved under `artifacts/water-potability/v1`; local MLflow tracking is in
`mlruns/`. Existing artifact versions cannot be overwritten by the command.

Measured test accuracy is 0.644, balanced accuracy 0.571 and F1 0.345; class-1
recall is 0.240. This is a teaching dataset and cannot establish water safety.
See [lifecycle](docs/ml-lifecycle.md) for provenance and limitations.

## B. Deploy, verify and register

```sh
docker compose up -d --build --wait

# Checks the endpoint independently before publishing metadata.
python -m mlaas.cli register-model

# Separate operation: publish processing, then reconcile its subscription.
python -m mlaas.cli register-processing
```

MLflow serves the fixed artifact mounted read-only at
`/artifacts/water-potability/v1/model`. Host requests use
`http://localhost:5001/invocations`; adapter metadata uses the container address
`http://ml-serving:5000/invocations`. Registration does not launch serving.
A new artifact requires a new model ID, new deployment endpoint and new
processing ID. Do not repoint an existing serving endpoint to a different model.

Registration uses create-only broker operations. Repeating registration fails
with 409 rather than replacing a pinned model. Subscription reconciliation is
safe to repeat:

```sh
curl -fsS http://localhost:8000/processings/reconcile \
  -H 'Content-Type: application/json' \
  -d '{"id":"urn:ngsi-ld:MLProcessing:water-demo-01"}'
```

## C. Inject and inspect

Open [the dashboard](http://localhost:8000). Use **Inject one** or **Replay batch**.
The start row indexes the reserved demo partition. Alternatively:

```sh
python -m mlaas.cli inject --count 10 --start 0
curl -fsS http://localhost:8000/state
curl -fsS http://localhost:8000/events
```

The broker applies `entities`, `watchedAttributes` and the demonstration
`turbidity>4` q filter. Some replayed samples do not match. Matching creations
produce real notifications, HTTP inference and new prediction entities. The UI
polls actual state and timestamped events every second; it does not trigger ML.
Select payloads to inspect all three provenance relationships.

For pause/remove and explicit model serving checks, see [commands](scripts/README.md).

## Verification

After training and registration:

```sh
RUN_BROKER_TESTS=1 RUN_RESTART_TESTS=1 RUN_E2E_TESTS=1 RUN_RECOVERY_TESTS=1 \
  RUN_DEMO_TESTS=1 python -m pytest tests -q --disable-warnings

# Optional dashboard DOM test, Node.js 18+.
npm ci --ignore-scripts
npm run test:dashboard
```

Recovery tests stop/restart the local Compose services. Use a development stack.
Without the environment flags, integration tests explicitly skip. A skip is not
a pass. [Acceptance results](docs/implementation.md) record actual execution,
including the visual-browser verification limitation.

`docker compose down` stops services and preserves volumes and local artifacts.
Restart with `docker compose up -d --wait`. Do not delete volumes to resume.

## Specifications and adaptation

Read [architecture](docs/architecture.md), [data models](docs/data-models.md),
[lifecycle](docs/ml-lifecycle.md), [acceptance criteria](docs/acceptance-criteria.md),
[agent instructions](AGENTS.md), then [examples](examples/).
[Adaptation guide](docs/adaptation.md) explains replacing the dataset, classifier
and input model without changing the inference adapter.

The existing SDM vocabulary is reused selectively; project extensions are
explicit. No full official SDM conformance is claimed. See
[implementation decisions and troubleshooting](docs/implementation.md).

## Dashboard inspection and demo reset

Click a model, processing, subscription, sample or prediction to open the side
inspector. It fetches the current broker resource, shows its properties and raw
JSON-LD, and links relationships. From a prediction, follow `refInputEntity`,
`refMLModel` or `refMLProcessing`; use Back, Close or Escape to navigate. The
processing's `refSubscription` opens the real subscription API resource. The
serving endpoint is displayed as text because inference requires an HTTP POST.
The flow buttons move to their corresponding dashboard sections.

**Clear View** closes the inspector, clears the observation filter, displayed
messages and loaded trace. It only changes this browser's view. Broker entities,
journal, replay position and counters remain intact; new events still appear.

**Reset Demo** asks for confirmation, stops replay, waits for any active inference,
and removes this demo's tracked observations, generated predictions and associated
jobs/events. Model/processing metadata, subscriptions, unrelated data, serving
and artifacts stay intact. Progress reports how many samples have been removed.
After success, counters and start row return to zero; replaying the same rows
reuses the same sample IDs and produces predictions again.

A failed reset retains the affected dedup records and blocks demo replay/work.
Resolve the reported error and click **Retry Reset Demo**. This also works after
an adapter restart. Reset can finish while model serving is offline; it needs the
broker for deletion and verification. Unrelated pending work continues after a
failed reset. This is a single-process local demo.

Dashboard and CLI injections now share the adapter's ownership journal. Replay
uses the selected adapter's configured `DEMO_DIR` and broker; `--artifact` and
`--broker` configure the registration commands:

```sh
python -m mlaas.cli inject --adapter http://localhost:8000 --count 10 --start 0
curl -fsS http://localhost:8000/demo
curl -fsS http://localhost:8000/demo/reset \
  -H 'Content-Type: application/json' -d '{"confirm":true}'
```

The reset request returns HTTP 202; poll `/demo` until `reset.status` is
`succeeded` or `failed`. Duplicate reset requests while running return current
progress. HTTP 202 alone does not mean deletion has finished.

Records injected before this ownership journal was added, or posted directly to
the broker, are deliberately preserved. Reset cannot safely infer ownership from
their type or ID. Do not delete the adapter volume to clear the dashboard: that
would lose the ownership and deduplication records.

Run the extended safety tests on the local development stack:

```sh
RUN_BROKER_TESTS=1 RUN_RESTART_TESTS=1 RUN_E2E_TESTS=1 RUN_RECOVERY_TESTS=1 \
  RUN_DEMO_TESTS=1 python -m pytest tests -q --disable-warnings
npm run test:dashboard
```

Run the Python and dashboard suites sequentially; both exercise Reset Demo on the
same local stack. The reset unit tests inject failures; `RUN_DEMO_TESTS=1` adds
real broker/serving inspection, metadata preservation, ownership and ID reuse
checks. The recovery flag also tests reset during a serving outage, broker outage
and adapter restart. The dashboard suite follows every rendered resource/trace
link and checks navigation, confirmation and Clear View isolation.
